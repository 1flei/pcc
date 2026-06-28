#!/usr/bin/env python3
"""Triage harness: measure how each tinycc core TU fares through pcc.

For every core translation unit we preprocess it (keeping line markers) and
run pcc's origin-filtered implementation-body emission, then bucket the gaps
that block compilation. We deliberately use origin-filtered emission (only the
decls that originate in the TU itself) rather than whole-file emission: a
fully expanded TU drags in all of glibc, whose declarations would drown out
tinycc's own constructs and tell us nothing about what pcc must learn next.

The two headline buckets map directly to the current backend gaps:
  - global_comments: variables emitted as a `# name: Type` comment by the
    emit_var_decl stub (i.e. globals pcc cannot yet lower).
  - unsupported: occurrences of the __pcc_unsupported__ sentinel (goto and any
    other construct the backend rejects).

This is a diagnostic dashboard, not a pass/fail test: it always exits 0 and
never edits tinycc sources.
"""

import os
import re
import subprocess
import sys
import tempfile

PCC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PCC_ROOT not in sys.path:
    sys.path.insert(0, PCC_ROOT)

from pcc import driver

TINYCC_DIR = os.environ.get(
    "PCC_TINYCC", os.path.join(os.path.dirname(PCC_ROOT), "tinycc")
)

# Core, host-portable TUs (skip non-x86_64 code generators).
CORE_TUS = [
    "tcc.c", "libtcc.c", "tccpp.c", "tccgen.c", "tccelf.c",
    "tccasm.c", "tccrun.c", "tccdbg.c", "x86_64-gen.c",
]

# ONE_SOURCE=0 makes each .c a standalone TU referencing tcc.h decls.
CPP_ARGS = ["-DONE_SOURCE=0", "-I", TINYCC_DIR]

UNSUPPORTED = "__pcc_unsupported__"
# emit_var_decl currently writes "# <name>: <type>" for globals it cannot lower.
GLOBAL_COMMENT_RE = re.compile(r"^# [A-Za-z_]\w*: ", re.M)

# Stage ordering for ranking (higher = further through the pipeline).
_STAGE_RANK = {"preprocess": 0, "generate": 1, "emitted": 2}


def _ensure_build_headers():
    """tinycc TUs include generated headers (config.h, tccdefs_.h).

    These come from the normal build (./configure and a make rule); generate
    them on demand so the harness is self-contained. Returns True if config.h
    (the universally required one) is present.
    """
    config_h = os.path.join(TINYCC_DIR, "config.h")
    if not os.path.exists(config_h):
        configure = os.path.join(TINYCC_DIR, "configure")
        if os.path.exists(configure):
            subprocess.run([configure], cwd=TINYCC_DIR,
                           capture_output=True, text=True)
    # tccpp.c includes the generated "tccdefs_.h"; best-effort generate it.
    if not os.path.exists(os.path.join(TINYCC_DIR, "tccdefs_.h")):
        subprocess.run(["make", "tccdefs_.h"], cwd=TINYCC_DIR,
                       capture_output=True, text=True)
    return os.path.exists(config_h)


def _emit_impl_body(pp_path, target, out_path):
    """Run the native origin-filtered impl emitter; return (rc, stderr)."""
    launcher = driver._BODY_LAUNCHER.format(
        input=pp_path, target=target, mode=driver._MODE_IMPL,
        lib="c", output=out_path, defs="",
    )
    fd, launcher_path = tempfile.mkstemp(prefix="pcc_triage_", suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(launcher)
    try:
        result = subprocess.run(
            [sys.executable, launcher_path],
            env=driver._subprocess_env(), capture_output=True, text=True,
        )
        return result.returncode, result.stderr
    finally:
        os.unlink(launcher_path)


def triage_tu(src, workdir):
    base = os.path.splitext(os.path.basename(src))[0]
    rec = {
        "tu": os.path.basename(src), "stage": "preprocess", "error": "",
        "unsupported": 0, "global_comments": 0, "body_lines": 0,
    }

    try:
        text = driver.preprocess(src, extra_args=CPP_ARGS, keep_markers=True)
    except Exception as exc:  # noqa: BLE001 - bucket any cpp failure
        rec["error"] = str(exc).splitlines()[-1][:200]
        return rec

    pp_path = os.path.join(workdir, base + ".i")
    with open(pp_path, "w") as f:
        f.write(text)

    body_path = os.path.join(workdir, base + ".body")
    rc, stderr = _emit_impl_body(pp_path, os.path.basename(src), body_path)
    if rc != 0 or not os.path.exists(body_path):
        rec["stage"] = "generate"
        tail = [ln for ln in stderr.splitlines() if ln.strip()]
        rec["error"] = ("rc=%d " % rc) + (tail[-1][:200] if tail else "")
        return rec

    with open(body_path) as f:
        txt = f.read()
    rec["stage"] = "emitted"
    rec["body_lines"] = txt.count("\n")
    rec["unsupported"] = txt.count(UNSUPPORTED)
    rec["global_comments"] = len(GLOBAL_COMMENT_RE.findall(txt))
    return rec


def _closeness_key(rec):
    """Sort key: furthest stage first, then fewest blocking constructs."""
    return (
        -_STAGE_RANK.get(rec["stage"], 0),
        rec["unsupported"] + rec["global_comments"],
        -rec["body_lines"],
    )


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    tus = argv or CORE_TUS

    if not os.path.isdir(TINYCC_DIR):
        print("tinycc dir not found: %s (set PCC_TINYCC)" % TINYCC_DIR)
        return 0
    if not _ensure_build_headers():
        print("could not obtain tinycc config.h; run ./configure in %s"
              % TINYCC_DIR)
        return 0

    workdir = tempfile.mkdtemp(prefix="pcc_triage_")
    records = []
    for name in tus:
        src = name if os.path.isabs(name) else os.path.join(TINYCC_DIR, name)
        if not os.path.exists(src):
            print("skip (missing): %s" % name)
            continue
        records.append(triage_tu(src, workdir))

    records.sort(key=_closeness_key)

    print("\n=== tinycc triage report ===")
    print("%-16s %-10s %8s %8s %8s  %s" % (
        "TU", "stage", "unsup", "globals", "lines", "error"))
    print("-" * 78)
    for r in records:
        print("%-16s %-10s %8d %8d %8d  %s" % (
            r["tu"], r["stage"], r["unsupported"], r["global_comments"],
            r["body_lines"], r["error"]))

    emitted = [r for r in records if r["stage"] == "emitted"]
    tot_unsup = sum(r["unsupported"] for r in records)
    tot_glob = sum(r["global_comments"] for r in records)
    print("-" * 78)
    print("totals: %d TU(s), %d emitted, unsupported=%d, global-comments=%d"
          % (len(records), len(emitted), tot_unsup, tot_glob))
    if emitted:
        best = emitted[0]
        print("closest-to-passing: %s (unsupported=%d, globals=%d, lines=%d)"
              % (best["tu"], best["unsupported"], best["global_comments"],
                 best["body_lines"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
