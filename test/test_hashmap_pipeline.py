#!/usr/bin/env python3
"""End-to-end pipeline regression test: C -> PythoC -> native binary.

Compiles example/hashmap/hashmap_single.c through the full pcc driver, runs the
resulting binary, and checks its output. When a system C compiler is available
the same source is also built with it and the two outputs are compared, so the
pcc-produced program is validated against a reference implementation.

The hashmap exercises a representative slice of real C: self-referential and
mutually referencing structs, malloc/calloc/free, string functions, pointer and
double-pointer arithmetic, a djb2 hash with the `while ((c = *s++) != 0)` idiom
(assignment-as-expression + postfix ++ in a loop condition), a `? :` whose
branches need type unification, casts, and dynamic resize.
"""

import os
import shutil
import subprocess
import sys
import tempfile

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(WORKSPACE, "example", "hashmap", "hashmap_single.c")

EXPECTED = (
    "two = 22\n"
    "three = 3\n"
    "key7 = 49\n"
    "missing not found\n"
    "size = 53\n"
)


def _run_binary(path):
    result = subprocess.run([path], capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout


def build_with_pcc(out_dir):
    if WORKSPACE not in sys.path:
        sys.path.insert(0, WORKSPACE)
    from pcc.driver import compile_file

    out_path = os.path.join(out_dir, "hashmap_pcc")
    module_path = os.path.join(out_dir, "hashmap_pcc_module.py")
    return compile_file(
        SOURCE, output_path=out_path, emit="exe", module_path=module_path
    )


def build_with_cc(out_dir):
    cc = os.environ.get("CC", "cc")
    if shutil.which(cc) is None:
        return None
    out_path = os.path.join(out_dir, "hashmap_cc")
    result = subprocess.run(
        [cc, "-std=c11", "-O0", "-o", out_path, SOURCE],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return None
    return out_path


def main():
    if not os.path.exists(SOURCE):
        print("FAIL: missing source %s" % SOURCE)
        return 1

    out_dir = tempfile.mkdtemp(prefix="pcc_hashmap_test_")

    pcc_bin = build_with_pcc(out_dir)
    if not pcc_bin or not os.path.exists(pcc_bin):
        print("FAIL: pcc did not produce a binary")
        return 1

    rc, pcc_out = _run_binary(pcc_bin)
    if rc != 0:
        print("FAIL: pcc binary exited with %d" % rc)
        print(pcc_out)
        return 1

    if pcc_out != EXPECTED:
        print("FAIL: pcc output mismatch")
        print("--- expected ---")
        print(EXPECTED)
        print("--- actual ---")
        print(pcc_out)
        return 1

    # Cross-check against the system C compiler when present.
    cc_bin = build_with_cc(out_dir)
    if cc_bin:
        rc, cc_out = _run_binary(cc_bin)
        if rc != 0 or cc_out != pcc_out:
            print("FAIL: pcc output differs from cc reference")
            print("--- cc ---")
            print(cc_out)
            print("--- pcc ---")
            print(pcc_out)
            return 1
        print("PASS: test_hashmap_pipeline (pcc output matches cc reference)")
    else:
        print("PASS: test_hashmap_pipeline (cc unavailable; matched expected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
