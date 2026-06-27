#!/usr/bin/env python3
"""Multi-file separate-compilation regression: .h/.c -> linked native binary.

Compiles the three-file hashmap (hashmap.h interface, hashmap.c implementation,
main.c driver) through pcc.driver.compile_project, which maps each header to an
interface module and each source to an implementation module and links every
PythoC group together. The produced binary is run and its output compared to a
gcc-built reference of the same sources.
"""

import os
import shutil
import subprocess
import sys
import tempfile

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HASHMAP_DIR = os.path.join(WORKSPACE, "example", "hashmap")
SOURCES = [
    os.path.join(HASHMAP_DIR, "hashmap.c"),
    os.path.join(HASHMAP_DIR, "main.c"),
]

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
    from pcc.driver import compile_project

    out_path = os.path.join(out_dir, "hashmap_multifile_pcc")
    return compile_project(SOURCES, output=out_path, emit="exe", workdir=out_dir)


def build_with_cc(out_dir):
    cc = os.environ.get("CC", "cc")
    if shutil.which(cc) is None:
        return None
    out_path = os.path.join(out_dir, "hashmap_multifile_cc")
    result = subprocess.run(
        [cc, "-std=c11", "-O0", "-I", HASHMAP_DIR, "-o", out_path] + SOURCES,
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return None
    return out_path


def main():
    for src in SOURCES:
        if not os.path.exists(src):
            print("FAIL: missing source %s" % src)
            return 1

    out_dir = tempfile.mkdtemp(prefix="pcc_hashmap_multifile_")

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
        print("PASS: test_hashmap_multifile (pcc output matches cc reference)")
    else:
        print("PASS: test_hashmap_multifile (cc unavailable; matched expected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
