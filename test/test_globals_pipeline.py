#!/usr/bin/env python3
"""End-to-end pipeline test for file-scope global lowering.

Compiles example/globals/globals_single.c through the full pcc driver, runs the
binary, and checks output. When a system C compiler is available the same source
is built with it and the outputs are compared, validating pcc's global lowering
(static[T]+accessor) against a reference, including parameter shadowing.
"""

import os
import shutil
import subprocess
import sys
import tempfile

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(WORKSPACE, "example", "globals", "globals_single.c")

EXPECTED = (
    "counter=7\n"
    "ratio=3.0\n"
    "total=20\n"
    "via_ptr=7\n"
    "shadow=200\n"
)


def _run_binary(path):
    result = subprocess.run([path], capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout


def build_with_pcc(out_dir):
    if WORKSPACE not in sys.path:
        sys.path.insert(0, WORKSPACE)
    from pcc.driver import compile_file

    out_path = os.path.join(out_dir, "globals_pcc")
    module_path = os.path.join(out_dir, "globals_pcc_module.py")
    return compile_file(
        SOURCE, output_path=out_path, emit="exe", module_path=module_path
    )


def build_with_cc(out_dir):
    cc = os.environ.get("CC", "cc")
    if shutil.which(cc) is None:
        return None
    out_path = os.path.join(out_dir, "globals_cc")
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

    out_dir = tempfile.mkdtemp(prefix="pcc_globals_test_")

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
        print("PASS: test_globals_pipeline (pcc output matches cc reference)")
    else:
        print("PASS: test_globals_pipeline (cc unavailable; matched expected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
