#!/usr/bin/env python3
"""Multi-file regression: a transitively-included project header.

base.h is included only through umbrella.h; no .c includes it directly. An
unrelated solo.c (which includes nothing) is listed first so it is sources[0].
The driver must still (a) emit base.h's interface from a preprocessed source
that actually pulls it in - not blindly from sources[0] - and (b) import its
type (Pt) and function (pt_sum) into every consuming module even though they
arrive only transitively. Output is checked against a cc-built reference.
"""

import os
import shutil
import subprocess
import sys
import tempfile

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(WORKSPACE, "example", "transitive")
# solo.c first on purpose: it is the sources[0] that lacks base.h.
SOURCES = [
    os.path.join(DIR, "solo.c"),
    os.path.join(DIR, "helper.c"),
    os.path.join(DIR, "main.c"),
]

EXPECTED = "helper = 7\npt_sum = 30\n"


def _run_binary(path):
    result = subprocess.run([path], capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout


def build_with_pcc(out_dir):
    if WORKSPACE not in sys.path:
        sys.path.insert(0, WORKSPACE)
    from pcc.driver import compile_project

    out_path = os.path.join(out_dir, "transitive_pcc")
    return compile_project(SOURCES, output=out_path, emit="exe", workdir=out_dir)


def build_with_cc(out_dir):
    cc = os.environ.get("CC", "cc")
    if shutil.which(cc) is None:
        return None
    out_path = os.path.join(out_dir, "transitive_cc")
    result = subprocess.run(
        [cc, "-std=c11", "-O0", "-I", DIR, "-o", out_path] + SOURCES,
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

    out_dir = tempfile.mkdtemp(prefix="pcc_transitive_")

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
        print("PASS: test_transitive_header (pcc matches cc reference)")
    else:
        print("PASS: test_transitive_header (cc unavailable; matched expected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
