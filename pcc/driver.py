"""pcc driver: C source -> PythoC module -> native binary / library.

Pipeline:
    1. Preprocess the C source with an external C preprocessor (cc -E).
    2. Generate a PythoC module from the preprocessed text (native backend).
    3. Optionally drive PythoC to emit a binary, shared library, or static
       library from the generated module.

This module is plain Python (the "preprocessor" half of the project): it is
allowed to shell out to tools. The lexer/parser/backend it invokes are all
compiled PythoC.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

_DEFAULT_CPP = os.environ.get("PCC_CPP", "cc")

_EMIT_EXE = "exe"
_EMIT_SO = "so"
_EMIT_A = "a"
_EMIT_PY = "py"

# Native emission modes (mirror pcc.bindgen).
_MODE_TYPES = 0
_MODE_IMPL = 1

# System headers mapped to PythoC's libc bindings (by stem, without ".h").
_LIBC_MAP = {
    "stdio": "stdio",
    "stdlib": "stdlib",
    "string": "string",
    "math": "math",
    "ctype": "ctype",
    "stddef": "stddef",
}

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+([<"])([^>"]+)[>"]', re.MULTILINE)


def preprocess(input_path, cpp=None, extra_args=None, keep_markers=True):
    """Run the external C preprocessor and return the expanded source text.

    Line markers (`# N "file"`) are kept by default so declarations can be
    attributed to their origin file; the native lexer skips them.
    """
    cpp = cpp or _DEFAULT_CPP
    cmd = [cpp, "-E"]
    if not keep_markers:
        cmd.append("-P")
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(input_path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "C preprocessor failed (%s):\n%s" % (" ".join(cmd), result.stderr)
        )
    return result.stdout


def _subprocess_env():
    """Environment that lets a child Python import pcc and pythoc."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in sys.path if p
    ) + os.pathsep + env.get("PYTHONPATH", "")
    return env


# Launcher template: paths are baked in as module-level string constants so the
# compiled @compile wrapper sees them as compile-time string literals (runtime
# Python strings cannot be marshalled into native ptr[i8] arguments).
_GEN_LAUNCHER = '''import sys
from pythoc import compile, i32
from pcc.bindgen import generate_bindings_file

_INPUT = {input!r}
_LIB = {lib!r}
_OUTPUT = {output!r}
_DEFS = {defs!r}


@compile
def _pcc_generate() -> i32:
    return generate_bindings_file(_INPUT, _LIB, _OUTPUT, _DEFS)


if __name__ == "__main__":
    sys.exit(int(_pcc_generate()))
'''


def generate_module(preprocessed_path, module_path, lib="c"):
    """Generate a PythoC module from a preprocessed C source file.

    Runs a small launcher subprocess whose input/output paths are baked in as
    string literals, so no runtime string crosses the Python/native boundary.
    """
    launcher_src = _GEN_LAUNCHER.format(
        input=preprocessed_path, lib=lib, output=module_path, defs=""
    )
    fd, launcher_path = tempfile.mkstemp(prefix="pcc_gen_", suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(launcher_src)
    try:
        result = subprocess.run(
            [sys.executable, launcher_path],
            env=_subprocess_env(),
            capture_output=True,
            text=True,
        )
        sys.stderr.write(result.stderr)
        if result.returncode != 0 or not os.path.exists(module_path):
            raise RuntimeError(
                "code generation failed (rc=%d):\n%s"
                % (result.returncode, result.stdout)
            )
    finally:
        os.unlink(launcher_path)
    return module_path


_BUILD_TAIL = '''

if __name__ == "__main__":
    import sys as _sys
    from pythoc import (
        compile_to_executable,
        compile_to_dynamic_library,
        compile_to_static_library,
    )

    _emit = _sys.argv[1] if len(_sys.argv) > 1 else "exe"
    _out = _sys.argv[2] if len(_sys.argv) > 2 else None
    if _emit == "so":
        print(compile_to_dynamic_library(output_path=_out))
    elif _emit == "a":
        print(compile_to_static_library(output_path=_out))
    else:
        print(compile_to_executable(_out))
'''


def append_build_tail(module_path):
    """Append a __main__ block that drives PythoC to build the artifact."""
    with open(module_path, "a") as f:
        f.write(_BUILD_TAIL)


def build_artifact(module_path, emit, output_path=None):
    """Run the generated module to produce a binary/library via PythoC."""
    cmd = [sys.executable, module_path, emit]
    if output_path:
        cmd.append(output_path)
    result = subprocess.run(
        cmd, env=_subprocess_env(), capture_output=True, text=True
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise RuntimeError("PythoC build failed for %s" % module_path)
    # The generated module prints the produced artifact path on its last line.
    produced = None
    for line in result.stdout.strip().splitlines():
        produced = line.strip()
    return produced


def compile_file(input_path, output_path=None, emit=_EMIT_EXE, lib="c",
                 module_path=None, cpp=None, cpp_args=None):
    """Full pipeline for a single C file.

    Returns the path of the produced artifact (or the .py module for emit=py).
    """
    text = preprocess(input_path, cpp=cpp, extra_args=cpp_args)

    base = os.path.splitext(os.path.basename(input_path))[0]
    if module_path is None:
        module_path = os.path.join(
            tempfile.gettempdir(), "pcc_%s.py" % base
        )

    # Persist the preprocessed text so the generator subprocess can read it
    # natively (avoids marshalling a large runtime string into compiled code).
    fd, pp_path = tempfile.mkstemp(prefix="pcc_%s_" % base, suffix=".i")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    try:
        generate_module(pp_path, module_path, lib=lib)
    finally:
        os.unlink(pp_path)

    if emit == _EMIT_PY:
        return module_path

    append_build_tail(module_path)
    return build_artifact(module_path, emit, output_path)


# =============================================================================
# Multi-file separate compilation
# =============================================================================

_BODY_LAUNCHER = '''import sys
from pythoc import compile, i32
from pcc.bindgen import generate_body_file

_INPUT = {input!r}
_TARGET = {target!r}
_MODE = {mode}
_LIB = {lib!r}
_OUTPUT = {output!r}
_DEFS = {defs!r}


@compile
def _pcc_gen_body() -> i32:
    return generate_body_file(_INPUT, _TARGET, _MODE, _LIB, _OUTPUT, _DEFS)


if __name__ == "__main__":
    sys.exit(int(_pcc_gen_body()))
'''

_MANIFEST_LAUNCHER = '''import sys
from pythoc import compile, i32
from pcc.bindgen import dump_manifest_file

_INPUT = {input!r}
_OUTPUT = {output!r}


@compile
def _pcc_manifest() -> i32:
    return dump_manifest_file(_INPUT, _OUTPUT)


if __name__ == "__main__":
    sys.exit(int(_pcc_manifest()))
'''

_MODULE_HEADER = ('"""Auto-generated by pcc (C -> PythoC)"""\n\n'
                  "from pythoc import (\n"
                  "    compile, extern, enum, i8, i16, i32, i64,\n"
                  "    u8, u16, u32, u64, f32, f64, ptr, array,\n"
                  "    void, char, nullptr, sizeof, typeof, struct, union, func\n"
                  ")\n")

# Footer that adopts each named aggregate as an identified type (mirrors
# pythoc_backend.emit_module_footer). Re-marking an imported type is a no-op,
# so the footer is safe to run in every generated module.
_MODULE_FOOTER = (
    "\n\n"
    "def _pcc_register_named_types(_ns):\n"
    "    from pythoc.forward_ref import mark_type_defined\n"
    "    for _name, _ty in list(_ns.items()):\n"
    "        if isinstance(_ty, type) and "
    "getattr(_ty, '_field_types', None) is not None:\n"
    "            _ty._canonical_name = _name\n"
    "            _ty._force_identified = True\n"
    "            mark_type_defined(_name, _ty)\n"
    "\n\n"
    "_pcc_register_named_types(globals())\n"
)


def _run_launcher(launcher_src, expect_path=None):
    """Run a generated launcher subprocess; raise on failure."""
    fd, launcher_path = tempfile.mkstemp(prefix="pcc_run_", suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(launcher_src)
    try:
        result = subprocess.run(
            [sys.executable, launcher_path],
            env=_subprocess_env(), capture_output=True, text=True,
        )
        sys.stderr.write(result.stderr)
        if result.returncode != 0 or (expect_path and not os.path.exists(expect_path)):
            raise RuntimeError(
                "launcher failed (rc=%d):\n%s" % (result.returncode, result.stdout)
            )
    finally:
        os.unlink(launcher_path)


def _module_stem(path):
    """Module stem for a C file: hashmap.c -> hashmap_c, hashmap.h -> hashmap_h."""
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    return "%s_%s" % (stem, ext.lstrip(".") or "c")


def _scan_includes(path):
    """Return (project_includes, system_includes) as basenames for a C file."""
    with open(path) as f:
        text = f.read()
    project, system = [], []
    for quote, name in _INCLUDE_RE.findall(text):
        (project if quote == '"' else system).append(os.path.basename(name))
    return project, system


def _resolve_header(name, search_dirs):
    """Locate a quoted project header by basename within the search dirs."""
    for d in search_dirs:
        candidate = os.path.join(d, name)
        if os.path.exists(candidate):
            return candidate
    return None


def _transitive_headers(direct_includes, header_includes):
    """Transitive closure of project headers reachable from direct includes.

    `header_includes` maps a header basename to its own (project, system)
    includes; walking the project edges yields every header a source pulls in,
    directly or indirectly.
    """
    seen = set()
    pending = list(direct_includes)
    while pending:
        h = pending.pop()
        if h in seen:
            continue
        seen.add(h)
        pending.extend(header_includes.get(h, ([], []))[0])
    return seen


def _parse_manifest(manifest_path):
    """Yield (origin, kind, name, has_body) records from a manifest file."""
    with open(manifest_path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("|")
            if len(parts) != 4:
                continue
            origin, kind, name, has_body = parts
            yield origin, kind, name, has_body == "1"


def _write_local_defs(manifest_path, origin_basename, defs_path):
    """Write the names of functions defined in `origin_basename` to a file.

    The implementation emitter reads this to drop a forward prototype when the
    same unit also defines the function (otherwise both an @extern declaration
    and the @compile def are emitted for one name).
    """
    names = sorted({
        name
        for origin, kind, name, has_body in _parse_manifest(manifest_path)
        if kind == "func" and has_body and name and origin == origin_basename
    })
    with open(defs_path, "w") as f:
        f.write("\n".join(names))
    return defs_path


def compile_project(sources, output=None, emit=_EMIT_EXE, cpp=None,
                    cpp_args=None, workdir=None):
    """Compile several C files as separate modules and link them.

    Each `.c` becomes an implementation module and each project `.h` an
    interface module; cross-references are satisfied by computed imports, and
    PythoC links every `@compile` group via compile_to_executable.
    """
    sources = [os.path.abspath(s) for s in sources]
    search_dirs = list(dict.fromkeys(os.path.dirname(s) for s in sources))

    owns_workdir = workdir is None
    workdir = workdir or tempfile.mkdtemp(prefix="pcc_proj_")
    pkg_dir = os.path.join(workdir, "pcc_modules")
    os.makedirs(pkg_dir, exist_ok=True)

    type_names = {}   # header basename -> set of exported type names
    func_decls = {}   # header basename -> set of declared function names
    func_def_module = {}  # function name -> defining module stem
    project_headers = {}  # header basename -> resolved path
    header_includes = {}  # header basename -> (project includes, system includes)

    # Preprocess each .c, dump its manifest, emit its implementation body.
    impl_modules = {}   # source path -> module stem
    pp_paths = {}       # source path -> preprocessed .i path
    for src in sources:
        base = os.path.splitext(os.path.basename(src))[0]
        text = preprocess(src, cpp=cpp, extra_args=cpp_args, keep_markers=True)
        pp_path = os.path.join(workdir, "%s.i" % base)
        with open(pp_path, "w") as f:
            f.write(text)
        pp_paths[src] = pp_path

        manifest_path = os.path.join(workdir, "%s.manifest" % base)
        _run_launcher(
            _MANIFEST_LAUNCHER.format(input=pp_path, output=manifest_path),
            expect_path=manifest_path,
        )

        stem = _module_stem(src)
        impl_modules[src] = stem
        defs_path = os.path.join(workdir, "%s.defs" % base)
        _write_local_defs(manifest_path, os.path.basename(src), defs_path)
        body_path = os.path.join(workdir, "%s.body" % stem)
        _run_launcher(
            _BODY_LAUNCHER.format(
                input=pp_path, target=os.path.basename(src),
                mode=_MODE_IMPL, lib="c", output=body_path, defs=defs_path,
            ),
            expect_path=body_path,
        )

        # Resolve project headers referenced by this source (transitively).
        pending = list(_scan_includes(src)[0])
        while pending:
            h = pending.pop()
            if h in project_headers:
                continue
            resolved = _resolve_header(h, search_dirs)
            if resolved is None:
                continue
            project_headers[h] = resolved
            header_includes[h] = _scan_includes(resolved)
            pending.extend(header_includes[h][0])

    # Aggregate manifests (project files only) into the symbol tables.
    project_basenames = {os.path.basename(s) for s in sources}
    project_basenames |= set(project_headers)
    for src in sources:
        base = os.path.splitext(os.path.basename(src))[0]
        manifest_path = os.path.join(workdir, "%s.manifest" % base)
        for origin, kind, name, has_body in _parse_manifest(manifest_path):
            if origin not in project_basenames:
                continue
            if kind in ("struct", "union", "enum", "typedef") and name:
                if origin.endswith(".h"):
                    type_names.setdefault(origin, set()).add(name)
            elif kind == "func" and name:
                if has_body:
                    func_def_module[name] = _module_stem(
                        os.path.join(".", origin)
                    )
                elif origin.endswith(".h"):
                    func_decls.setdefault(origin, set()).add(name)

    # Emit interface bodies for each project header, reusing a .i that includes
    # it. The header's declarations only appear (with the right origin markers)
    # in a preprocessed source that pulls it in, so a source must be chosen
    # whose *transitive* include closure contains the header - a header that is
    # only included indirectly has no direct includer, and falling back to
    # sources[0] would emit it from a .i that never saw it.
    src_closures = {
        s: _transitive_headers(_scan_includes(s)[0], header_includes)
        for s in sources
    }
    header_modules = {}  # header basename -> module stem
    for h, hpath in project_headers.items():
        stem = _module_stem(hpath)
        header_modules[h] = stem
        # Pick a preprocessed source that (transitively) includes this header.
        src_for_h = next(
            (s for s in sources if h in src_closures[s]), sources[0]
        )
        body_path = os.path.join(workdir, "%s.body" % stem)
        _run_launcher(
            _BODY_LAUNCHER.format(
                input=pp_paths[src_for_h], target=h,
                mode=_MODE_TYPES, lib="c", output=body_path, defs="",
            ),
            expect_path=body_path,
        )

    # Assemble interface modules.
    for h, hpath in project_headers.items():
        stem = header_modules[h]
        proj_inc, sys_inc = _scan_includes(hpath)
        imports = _compute_imports(
            proj_inc, sys_inc, stem, header_modules, type_names,
            func_decls, func_def_module, header_includes,
        )
        _write_module(pkg_dir, stem, imports,
                      os.path.join(workdir, "%s.body" % stem))

    # Assemble implementation modules.
    for src in sources:
        stem = impl_modules[src]
        proj_inc, sys_inc = _scan_includes(src)
        imports = _compute_imports(
            proj_inc, sys_inc, stem, header_modules, type_names,
            func_decls, func_def_module, header_includes,
        )
        _write_module(pkg_dir, stem, imports,
                      os.path.join(workdir, "%s.body" % stem))

    if emit == _EMIT_PY:
        return pkg_dir

    return _build_project(pkg_dir, func_def_module, emit, output)


def _compute_imports(proj_inc, sys_inc, this_module, header_modules,
                     type_names, func_decls, func_def_module, header_includes):
    """Build the import lines for one module from its #includes.

    Project types/functions are imported from their interface/implementation
    modules; system headers map to pythoc.libc. System includes are gathered
    transitively through project headers so typedef names like size_t resolve
    even when only pulled in indirectly.
    """
    # A module sees every header it includes directly or indirectly, so symbols
    # must be imported from the whole transitive closure - a type declared in a
    # base header reached only through an umbrella header is still referenced.
    proj_all = _transitive_headers(proj_inc, header_includes)

    type_imports = {}  # header module -> set of type names to import
    by_module = {}     # defining module -> set of function names to import
    for h in proj_all:
        hmod = header_modules.get(h)
        if hmod is not None and hmod != this_module:
            for t in type_names.get(h, ()):
                type_imports.setdefault(hmod, set()).add(t)
        for fn in func_decls.get(h, ()):
            defmod = func_def_module.get(fn)
            if defmod and defmod != this_module:
                by_module.setdefault(defmod, set()).add(fn)

    lines = []
    for hmod in sorted(type_imports):
        lines.append("from %s import %s"
                     % (hmod, ", ".join(sorted(type_imports[hmod]))))
    for defmod in sorted(by_module):
        lines.append("from %s import %s"
                     % (defmod, ", ".join(sorted(by_module[defmod]))))

    sys_all = set(sys_inc)
    for h in proj_all:
        sys_all.update(header_includes.get(h, ([], []))[1])

    for s in sorted(sys_all):
        mod = _LIBC_MAP.get(os.path.splitext(s)[0])
        if mod:
            lines.append("from pythoc.libc.%s import *" % mod)

    return lines


def _write_module(pkg_dir, stem, imports, body_path):
    """Write a complete generated module: header + imports + body + footer."""
    with open(body_path) as f:
        body = f.read()
    path = os.path.join(pkg_dir, "%s.py" % stem)
    with open(path, "w") as f:
        f.write(_MODULE_HEADER)
        if imports:
            f.write("\n")
            f.write("\n".join(imports))
            f.write("\n")
        f.write("\n")
        f.write(body)
        f.write(_MODULE_FOOTER)
    return path


def _build_project(pkg_dir, func_def_module, emit, output):
    """Generate and run a build entry that links every module's group."""
    root_module = func_def_module.get("main")
    if root_module is None:
        raise RuntimeError("no module defines main(); cannot build executable")

    entry_src = (
        "import sys, os, importlib\n"
        "sys.path.insert(0, %r)\n" % pkg_dir
        + "import %s  # noqa: F401  (pulls in the rest transitively)\n" % root_module
        + "for _m in sorted(os.listdir(%r)):\n" % pkg_dir
        + "    if _m.endswith('.py'):\n"
        + "        importlib.import_module(_m[:-3])\n"
        + "from pythoc import compile_to_executable\n"
        + "print(compile_to_executable(%r))\n" % output
    )
    fd, entry_path = tempfile.mkstemp(prefix="pcc_build_", suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(entry_src)
    try:
        result = subprocess.run(
            [sys.executable, entry_path],
            env=_subprocess_env(), capture_output=True, text=True,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            raise RuntimeError("PythoC project build failed")
    finally:
        os.unlink(entry_path)
    produced = None
    for line in result.stdout.strip().splitlines():
        produced = line.strip()
    return produced


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="pcc", description="Compile C to a native binary/library via PythoC."
    )
    parser.add_argument("input", help="C source file (.c)")
    parser.add_argument("-o", "--output", help="output path for the artifact")
    parser.add_argument(
        "--emit", choices=[_EMIT_EXE, _EMIT_SO, _EMIT_A, _EMIT_PY],
        default=_EMIT_EXE,
        help="artifact kind: exe (default), so, a, or py (generated module only)",
    )
    parser.add_argument(
        "--lib", default="c",
        help="library name for @extern declarations (default: c)",
    )
    parser.add_argument(
        "--module", help="path to write the generated PythoC module to",
    )
    parser.add_argument(
        "--cpp", help="C preprocessor command (default: $PCC_CPP or cc)",
    )
    parser.add_argument(
        "-I", dest="includes", action="append", default=[],
        help="include directory passed to the preprocessor",
    )
    parser.add_argument(
        "-D", dest="defines", action="append", default=[],
        help="macro definition passed to the preprocessor",
    )
    args = parser.parse_args(argv)

    cpp_args = []
    for inc in args.includes:
        cpp_args.extend(["-I", inc])
    for d in args.defines:
        cpp_args.append("-D" + d)

    result = compile_file(
        args.input,
        output_path=args.output,
        emit=args.emit,
        lib=args.lib,
        module_path=args.module,
        cpp=args.cpp,
        cpp_args=cpp_args,
    )
    if args.emit == _EMIT_PY:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
