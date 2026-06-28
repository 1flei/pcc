#!/usr/bin/env python3
"""Stepping-stone regression: compile a real tinycc TU (lib/libtcc1.c) with pcc.

libtcc1.c is tinycc's own runtime-support library. On x86_64 the portable part
that pcc emits is the family of float/double -> 64-bit integer conversion
intrinsics (__fixsfdi, __fixunssfdi, __fixdfdi, __fixunsdfdi, ...). This test
drives the unmodified tinycc source through pcc.driver.compile_project together
with a small driver (example/libtcc1/main.c) that calls those intrinsics, links
the two into a native binary, runs it, and checks the output against both a
hardcoded reference and a cc-built reference of the same two sources.

The long double helpers in libtcc1.c (__fixxfdi, __floatundixf, ...) are
deliberately not exercised: pcc maps `long double` to f64, so they would
diverge from cc's true 80-bit extended precision. They still must *compile*,
which this test implicitly verifies (compile_to_executable builds every group).
"""

import os
import shutil
import subprocess
import sys
import tempfile

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_DIR = os.path.join(WORKSPACE, "example", "libtcc1")
TINYCC_DIR = os.environ.get(
    "PCC_TINYCC", os.path.join(os.path.dirname(WORKSPACE), "tinycc")
)
LIBTCC1 = os.path.join(TINYCC_DIR, "lib", "libtcc1.c")
MAIN = os.path.join(EXAMPLE_DIR, "main.c")
SOURCES = [LIBTCC1, MAIN]

EXPECTED = (
    "fixsfdi 0 = 0\n"
    "fixsfdi 1 = 1\n"
    "fixsfdi 2.5 = 2\n"
    "fixsfdi -2.5 = -2\n"
    "fixsfdi 100 = 100\n"
    "fixsfdi -100 = -100\n"
    "fixsfdi 2p24 = 16777216\n"
    "fixsfdi -2p24 = -16777216\n"
    "fixunssfdi 0 = 0\n"
    "fixunssfdi 1 = 1\n"
    "fixunssfdi 2.5 = 2\n"
    "fixunssfdi 100 = 100\n"
    "fixunssfdi 2p24 = 16777216\n"
    "fixdfdi 0 = 0\n"
    "fixdfdi 2.5 = 2\n"
    "fixdfdi -2.5 = -2\n"
    "fixdfdi 1e6 = 1000000\n"
    "fixdfdi -1e6 = -1000000\n"
    "fixdfdi 1e15 = 1000000000000000\n"
    "fixdfdi -1e15 = -1000000000000000\n"
    "fixunsdfdi 0 = 0\n"
    "fixunsdfdi 2.5 = 2\n"
    "fixunsdfdi 1e6 = 1000000\n"
    "fixunsdfdi 1e15 = 1000000000000000\n"
    "fixunsdfdi 2p60 = 1152921504606846976\n"
)


def _run_binary(path):
    result = subprocess.run([path], capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout


def build_with_pcc(out_dir):
    if WORKSPACE not in sys.path:
        sys.path.insert(0, WORKSPACE)
    from pcc.driver import compile_project

    out_path = os.path.join(out_dir, "libtcc1_stone_pcc")
    return compile_project(SOURCES, output=out_path, emit="exe", workdir=out_dir)


def build_with_cc(out_dir):
    cc = os.environ.get("CC", "cc")
    if shutil.which(cc) is None:
        return None
    out_path = os.path.join(out_dir, "libtcc1_stone_cc")
    result = subprocess.run(
        [cc, "-std=c11", "-O0", "-I", EXAMPLE_DIR, "-o", out_path] + SOURCES,
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return None
    return out_path


def main():
    for src in SOURCES:
        if not os.path.exists(src):
            print("SKIP: missing source %s" % src)
            return 0

    out_dir = tempfile.mkdtemp(prefix="pcc_libtcc1_stone_")

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
        print("PASS: test_libtcc1_stepping_stone (pcc matches cc reference)")
    else:
        print("PASS: test_libtcc1_stepping_stone (cc unavailable; matched expected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
