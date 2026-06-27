"""Probe: does parsing+freeing a single struct decl crash (no emit)?"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pythoc import compile, i32, ptr
from pythoc.libc.stdio import printf
from pcc.c_parser import parse_declarations
from pcc.c_ast import decl_free
from pcc.pythoc_backend import (
    StringBuffer, strbuf_init, strbuf_destroy, emit_decl, emit_module_header,
    strbuf_to_cstr
)


@compile
def parse_only() -> i32:
    n: i32 = 0
    for decl_prf, decl in parse_declarations("struct Point { int x; int y; };"):
        n = n + 1
        decl_free(decl_prf, decl)
    printf("parse_only ok, decls=%d\n", n)
    return 0


@compile
def parse_and_emit() -> i32:
    buf: StringBuffer
    strbuf_init(ptr(buf))
    emit_module_header(ptr(buf))
    for decl_prf, decl in parse_declarations("struct Point { int x; int y; };"):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)
    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("parse_and_emit ok:\n%s\n", result)
    strbuf_destroy(ptr(buf))
    return 0


@compile
def main() -> i32:
    parse_only()
    parse_and_emit()
    return 0


if __name__ == "__main__":
    main()
