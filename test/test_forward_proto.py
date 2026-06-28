#!/usr/bin/env python3
"""Multi-file regression: a .c that forward-declares and defines its functions.

fwdlib.c prototypes add_one (before its definition) and square (after its
definition). pcc must emit each function once - as the @compile def - and drop
the redundant prototypes; otherwise an @extern(lib='c') binding for the same
name is emitted too, and for `square` (prototype after definition) it shadows
the real definition, breaking the cross-module call from main.c.

The test compiles [fwdlib.c, main.c] via compile_project, runs the binary,
asserts the generated implementation module carries no stray @extern for the
defined functions, and matches a cc-built reference.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(WORKSPACE, "example", "fwdproto")
SOURCES = [
    os.path.join(DIR, "fwdlib.c"),
    os.path.join(DIR, "main.c"),
]

EXPECTED = "add_one(41) = 42\nsquare(7) = 49\n"


def _run_binary(path):
    result = subprocess.run([path], capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout


def build_with_pcc(out_dir, emit="exe", output=None):
    if WORKSPACE not in sys.path:
        sys.path.insert(0, WORKSPACE)
    from pcc.driver import compile_project

    return compile_project(SOURCES, output=output, emit=emit, workdir=out_dir)


def build_with_cc(out_dir):
    cc = os.environ.get("CC", "cc")
    if shutil.which(cc) is None:
        return None
    out_path = os.path.join(out_dir, "fwdproto_cc")
    result = subprocess.run(
        [cc, "-std=c11", "-O0", "-I", DIR, "-o", out_path] + SOURCES,
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return None
    return out_path


def _impl_module_has_no_stray_extern(pkg_dir):
    """The fwdlib implementation module must not redeclare add_one/square."""
    impl = os.path.join(pkg_dir, "fwdlib_c.py")
    if not os.path.exists(impl):
        print("FAIL: missing generated impl module %s" % impl)
        return False
    with open(impl) as f:
        text = f.read()
    for name in ("add_one", "square"):
        if re.search(r"@extern[^\n]*\n\s*def %s\b" % name, text):
            print("FAIL: redundant @extern prototype for %s emitted" % name)
            print(text)
            return False
        if text.count("def %s(" % name) != 1:
            print("FAIL: %s emitted %d times (expected 1)"
                  % (name, text.count("def %s(" % name)))
            print(text)
            return False
    return True


def main():
    for src in SOURCES:
        if not os.path.exists(src):
            print("FAIL: missing source %s" % src)
            return 1

    # First check the generated implementation module (emit=py).
    py_dir = tempfile.mkdtemp(prefix="pcc_fwdproto_py_")
    pkg_dir = build_with_pcc(py_dir, emit="py")
    if not pkg_dir or not _impl_module_has_no_stray_extern(pkg_dir):
        return 1

    out_dir = tempfile.mkdtemp(prefix="pcc_fwdproto_")
    pcc_bin = build_with_pcc(out_dir, emit="exe",
                             output=os.path.join(out_dir, "fwdproto_pcc"))
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
        print("PASS: test_forward_proto (pcc matches cc reference)")
    else:
        print("PASS: test_forward_proto (cc unavailable; matched expected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
