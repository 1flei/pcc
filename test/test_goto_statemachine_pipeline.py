#!/usr/bin/env python3
"""End-to-end pipeline test for non-structural goto -> state-machine lowering.

Compiles example/goto/goto_state_machine.c through the full pcc driver, runs
the binary, and checks output. When a system C compiler is available the same
source is built with it and the outputs are compared. The source mixes shapes
that cannot map onto PythoC's scoped label/goto (both-direction labels, jumps
between switch cases, backward jumps across a loop with an inner switch) with
one laminar cleanup jump, so it validates both the `__pcc_pc` state-machine
fallback and the hybrid selection between it and the structured path.
"""

import os
import shutil
import subprocess
import sys
import tempfile

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(WORKSPACE, "example", "goto", "goto_state_machine.c")

EXPECTED = (
    "bidir(3)=10\n"
    "bidir(5)=10\n"
    "bidir(20)=22\n"
    "pick(1,0)=11\n"
    "pick(2,0)=21\n"
    "pick(9,0)=0\n"
    "reparse(4)=63\n"
    "reparse(0)=0\n"
    "findpair(5,5)=203\n"
    "findpair(2,2)=0\n"
)


def _run_binary(path):
    result = subprocess.run([path], capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout


def build_with_pcc(out_dir):
    if WORKSPACE not in sys.path:
        sys.path.insert(0, WORKSPACE)
    from pcc.driver import compile_file

    out_path = os.path.join(out_dir, "goto_sm_pcc")
    module_path = os.path.join(out_dir, "goto_sm_pcc_module.py")
    return compile_file(
        SOURCE, output_path=out_path, emit="exe", module_path=module_path
    ), module_path


def build_with_cc(out_dir):
    cc = os.environ.get("CC", "cc")
    if shutil.which(cc) is None:
        return None
    out_path = os.path.join(out_dir, "goto_sm_cc")
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

    out_dir = tempfile.mkdtemp(prefix="pcc_goto_sm_test_")

    pcc_bin, module_path = build_with_pcc(out_dir)
    if not pcc_bin or not os.path.exists(pcc_bin):
        print("FAIL: pcc did not produce a binary")
        return 1

    with open(module_path) as f:
        module_src = f.read()
    if "__pcc_pc" not in module_src:
        print("FAIL: expected a state-machine (__pcc_pc) in the generated module")
        return 1
    if "__pcc_unsupported__" in module_src:
        print("FAIL: generated module leaked __pcc_unsupported__")
        return 1
    # The laminar cleanup function must stay on the scoped-label path.
    if 'with label("done")' not in module_src:
        print("FAIL: laminar goto should stay structured (with label)")
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
        print("PASS: test_goto_statemachine_pipeline (pcc output matches cc)")
    else:
        print("PASS: test_goto_statemachine_pipeline (cc unavailable; matched expected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
