"""
Bindings Generator - Generate pythoc bindings from C source

This module provides compiled functions to parse C headers/sources
and generate pythoc binding code. It wraps the c_parser and pythoc_backend
modules to provide an end-to-end bindgen pipeline.

Usage:
    The main entry point is generate_bindings_to_file() which:
    1. Reads C source text
    2. Parses declarations using c_parser
    3. Emits pythoc code using pythoc_backend
    4. Writes output to a file

Note: These functions must be called from @compile context because
parse_declarations is a yield-based @compile function.
"""

from pythoc import (
    compile, i32, i64, i8, u64, ptr, void, char, nullptr
)
from pythoc.libc.stdio import (
    fopen, fclose, fwrite, fprintf, fseek, ftell, fread
)
from pythoc.libc.stdlib import malloc, free
from pythoc.libc.string import strlen
from pythoc.std.vector import Vector

from .c_parser import parse_declarations
from .c_ast import decl_free, Decl, DeclKind, Span, span_eq
from .pythoc_backend import (
    StringBuffer, strbuf_init, strbuf_destroy, strbuf_to_cstr,
    emit_module_header, emit_module_footer, emit_decl, strbuf_size
)


# Emission modes for origin-filtered generation.
_MODE_TYPES: i32 = 0   # aggregates + typedefs (interface modules from .h)
_MODE_IMPL: i32 = 1    # functions + variables + file-local types (.c modules)

# Growable set of function names (zero-copy Spans into the source text) used to
# drop a forward prototype when the same translation unit also defines it. A
# small inline capacity keeps tiny units allocation-free; larger ones spill to
# the heap automatically.
_FuncNames = Vector(Span, 16)


@compile
def _origin_basename_eq(origin: Span, target: ptr[i8]) -> i8:
    """Compare the file-name component of an origin span to a basename cstr.

    Line markers may spell paths absolutely or relatively, so provenance is
    matched on the basename only.
    """
    bstart: i32 = 0
    i: i32 = 0
    while i < origin.len:
        if origin.start[i] == char("/"):
            bstart = i + 1
        i = i + 1
    j: i32 = bstart
    k: i32 = 0
    while j < origin.len:
        if target[k] == 0:
            return 0
        if origin.start[j] != target[k]:
            return 0
        j = j + 1
        k = k + 1
    if target[k] != 0:
        return 0
    return 1


@compile
def _decl_selected(decl: ptr[Decl], mode: i32) -> i8:
    """Whether a declaration should be emitted for the given module mode."""
    is_type: i8 = 0
    match decl.kind:
        case DeclKind.Struct:
            is_type = 1
        case DeclKind.Union:
            is_type = 1
        case DeclKind.Enum:
            is_type = 1
        case DeclKind.Typedef:
            is_type = 1
        case _:
            is_type = 0
    if mode == _MODE_TYPES:
        return is_type
    # Implementation modules emit everything originating in the .c: function
    # and variable definitions plus any file-local types.
    return 1


@compile
def _decl_kind_cstr(decl: ptr[Decl]) -> ptr[i8]:
    """Stable textual kind tag for the manifest."""
    match decl.kind:
        case DeclKind.Struct:
            return "struct"
        case DeclKind.Union:
            return "union"
        case DeclKind.Enum:
            return "enum"
        case DeclKind.Typedef:
            return "typedef"
        case DeclKind.Func:
            return "func"
        case DeclKind.Var:
            return "var"
        case _:
            return "other"


@compile
def _write_manifest_line(fp: ptr[i8], decl: ptr[Decl]) -> void:
    """Write one `origin_basename|kind|name|has_body` manifest record."""
    origin: Span = decl.origin_file
    bstart: i32 = 0
    i: i32 = 0
    while i < origin.len:
        if origin.start[i] == char("/"):
            bstart = i + 1
        i = i + 1
    blen: i32 = origin.len - bstart
    has_body: i32 = 0
    if decl.body != nullptr:
        has_body = 1
    fprintf(fp, "%.*s|%s|%.*s|%d\n",
            blen, origin.start + bstart,
            _decl_kind_cstr(decl),
            decl.name.len, decl.name.start,
            has_body)


@compile
def _is_func_proto(decl: ptr[Decl]) -> i8:
    """Whether a declaration is a bare function prototype (no body)."""
    match decl.kind:
        case DeclKind.Func:
            if decl.body == nullptr:
                return 1
            return 0
        case _:
            return 0


@compile
def _decl_in_scope(decl: ptr[Decl], target: ptr[i8], use_filter: i32) -> i8:
    """Whether a declaration belongs to the unit being emitted."""
    if use_filter == 0:
        return 1
    return _origin_basename_eq(decl.origin_file, target)


@compile
def _name_recorded(defined: ptr[_FuncNames.type], name: Span) -> i8:
    """Whether a function name was recorded as locally defined."""
    i: u64 = 0
    n: u64 = _FuncNames.size(defined)
    while i < n:
        if span_eq(_FuncNames.get(defined, i), name):
            return 1
        i = i + 1
    return 0


@compile
def _should_emit(decl: ptr[Decl], defined: ptr[_FuncNames.type],
                 target: ptr[i8], mode: i32, use_filter: i32) -> i8:
    """Selection predicate, evaluated entirely from the declaration value.

    Skips out-of-unit declarations, ones not selected for the module mode, and a
    bare prototype whose function the unit also defines (which would otherwise
    emit both an @extern and the @compile def for one name).
    """
    if _decl_in_scope(decl, target, use_filter) == 0:
        return 0
    if _decl_selected(decl, mode) == 0:
        return 0
    if _is_func_proto(decl) != 0 and _name_recorded(defined, decl.name) != 0:
        return 0
    return 1


@compile
def _load_def_names(defs_text: ptr[i8], out: ptr[_FuncNames.type]) -> void:
    """Populate `out` with newline-separated function names from `defs_text`.

    The names stay zero-copy Spans into `defs_text`, so that buffer must outlive
    `out`. This is a plain string split - it deliberately does NOT reparse C, so
    the emitter keeps a single parse_declarations loop (the yield-based parser
    threads a linear proof that cannot survive a second loop in one function).
    """
    i: i32 = 0
    start: i32 = 0
    while defs_text[i] != 0:
        if defs_text[i] == char("\n"):
            if i > start:
                nm: Span
                nm.start = defs_text + start
                nm.len = i - start
                _FuncNames.push_back(out, nm)
            start = i + 1
        i = i + 1
    if i > start:
        tail: Span
        tail.start = defs_text + start
        tail.len = i - start
        _FuncNames.push_back(out, tail)


@compile
def generate_bindings_to_file(source: ptr[i8], lib: ptr[i8], output_path: ptr[i8],
                              defs_text: ptr[i8]) -> i32:
    """Generate pythoc bindings and write to file.

    Args:
        source: C source text (null-terminated)
        lib: Library name for @extern decorators (null-terminated)
        output_path: Path to output .py file (null-terminated)
        defs_text: newline-separated names of functions defined in this unit
            (null to suppress nothing); used to drop redundant prototypes.

    Returns:
        0 on success, non-zero on error
    """
    defined: _FuncNames.type
    _FuncNames.init(ptr(defined))
    if defs_text != nullptr:
        _load_def_names(defs_text, ptr(defined))

    buf: StringBuffer
    strbuf_init(ptr(buf))
    emit_module_header(ptr(buf))

    # Whole file, no origin filter; skip prototypes the file also defines.
    for decl_prf, decl in parse_declarations(source):
        if _should_emit(decl, ptr(defined), nullptr, _MODE_IMPL, 0) != 0:
            emit_decl(ptr(buf), decl, lib)
        decl_free(decl_prf, decl)

    emit_module_footer(ptr(buf))

    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    size: i64 = strbuf_size(ptr(buf))

    fp: ptr[i8] = fopen(output_path, "w")
    if fp == nullptr:
        strbuf_destroy(ptr(buf))
        _FuncNames.destroy(ptr(defined))
        return 1

    written: i64 = fwrite(result, 1, size - 1, fp)
    fclose(fp)
    strbuf_destroy(ptr(buf))
    _FuncNames.destroy(ptr(defined))

    if written != size - 1:
        return 2

    return 0


# whence values for fseek
_SEEK_SET: i32 = 0
_SEEK_END: i32 = 2


@compile
def read_file_to_cstr(path: ptr[i8]) -> ptr[i8]:
    """Read an entire file into a malloc'd, NUL-terminated buffer.

    Returns nullptr on failure; caller frees the buffer.
    """
    fp: ptr[i8] = fopen(path, "rb")
    if fp == nullptr:
        return nullptr
    fseek(fp, 0, _SEEK_END)
    size: i64 = ftell(fp)
    fseek(fp, 0, _SEEK_SET)
    if size < 0:
        fclose(fp)
        return nullptr
    buf: ptr[i8] = ptr[i8](malloc(size + 1))
    if buf == nullptr:
        fclose(fp)
        return nullptr
    got: i64 = fread(buf, 1, size, fp)
    buf[got] = 0
    fclose(fp)
    return buf


@compile
def generate_bindings_file(input_path: ptr[i8], lib: ptr[i8],
                           output_path: ptr[i8], defs_path: ptr[i8]) -> i32:
    """Read C source from a file, generate a PythoC module, write it out.

    All arguments stay native (file contents are read inside compiled code),
    avoiding any Python-string-to-pointer marshalling at the call boundary.
    `defs_path` (may be empty) lists this unit's defined function names so that
    redundant forward prototypes are not also emitted as @extern.
    """
    source: ptr[i8] = read_file_to_cstr(input_path)
    if source == nullptr:
        return 3
    defs_text: ptr[i8] = nullptr
    if defs_path != nullptr and defs_path[0] != 0:
        defs_text = read_file_to_cstr(defs_path)
    rc: i32 = generate_bindings_to_file(source, lib, output_path, defs_text)
    if defs_text != nullptr:
        free(defs_text)
    free(source)
    return rc


@compile
def generate_body_to_file(source: ptr[i8], target: ptr[i8], mode: i32,
                          lib: ptr[i8], output_path: ptr[i8],
                          defs_text: ptr[i8]) -> i32:
    """Emit only the declarations originating in `target`, in `mode`.

    Writes just the module body (no header/footer/imports); the driver
    assembles the full module around it. `defs_text` (newline-separated, may be
    null) names the unit's defined functions so their prototypes are skipped.
    """
    defined: _FuncNames.type
    _FuncNames.init(ptr(defined))
    if defs_text != nullptr:
        _load_def_names(defs_text, ptr(defined))

    buf: StringBuffer
    strbuf_init(ptr(buf))

    for decl_prf, decl in parse_declarations(source):
        if _should_emit(decl, ptr(defined), target, mode, 1) != 0:
            emit_decl(ptr(buf), decl, lib)
        decl_free(decl_prf, decl)

    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    size: i64 = strbuf_size(ptr(buf))

    fp: ptr[i8] = fopen(output_path, "w")
    if fp == nullptr:
        strbuf_destroy(ptr(buf))
        _FuncNames.destroy(ptr(defined))
        return 1
    written: i64 = fwrite(result, 1, size - 1, fp)
    fclose(fp)
    strbuf_destroy(ptr(buf))
    _FuncNames.destroy(ptr(defined))
    if written != size - 1:
        return 2
    return 0


@compile
def generate_body_file(input_path: ptr[i8], target: ptr[i8], mode: i32,
                       lib: ptr[i8], output_path: ptr[i8],
                       defs_path: ptr[i8]) -> i32:
    """File-to-file wrapper for generate_body_to_file."""
    source: ptr[i8] = read_file_to_cstr(input_path)
    if source == nullptr:
        return 3
    defs_text: ptr[i8] = nullptr
    if defs_path != nullptr and defs_path[0] != 0:
        defs_text = read_file_to_cstr(defs_path)
    rc: i32 = generate_body_to_file(source, target, mode, lib, output_path,
                                    defs_text)
    if defs_text != nullptr:
        free(defs_text)
    free(source)
    return rc


@compile
def dump_manifest_file(input_path: ptr[i8], output_path: ptr[i8]) -> i32:
    """Parse a preprocessed translation unit and dump a declaration manifest.

    Each line is `origin_basename|kind|name|has_body`, letting the driver build
    the cross-module import graph.
    """
    source: ptr[i8] = read_file_to_cstr(input_path)
    if source == nullptr:
        return 3
    fp: ptr[i8] = fopen(output_path, "w")
    if fp == nullptr:
        free(source)
        return 1
    for decl_prf, decl in parse_declarations(source):
        _write_manifest_line(fp, decl)
        decl_free(decl_prf, decl)
    fclose(fp)
    free(source)
    return 0
