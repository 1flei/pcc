"""Smoke test: generate PythoC source for C with function bodies and print it."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from pythoc import compile, i32, ptr, i8
from pythoc.libc.stdio import printf
from pcc.c_parser import parse_declarations
from pcc.c_ast import decl_free
from pcc.pythoc_backend import (
    StringBuffer, strbuf_init, strbuf_destroy, strbuf_to_cstr,
    emit_module_header, emit_decl,
)

_SRC = """
int add(int a, int b);

int compute(int n) {
    int total = 0;
    for (int i = 0; i < n; i++) {
        if (i % 2 == 0)
            total += i;
        else
            total -= 1;
    }
    while (total > 100) {
        total = total - 10;
    }
    return total;
}
"""


@compile
def gen_smoke() -> i32:
    buf: StringBuffer
    strbuf_init(ptr(buf))
    emit_module_header(ptr(buf))
    for decl_prf, decl in parse_declarations(_SRC):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)
    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("%s\n", result)
    strbuf_destroy(ptr(buf))
    return 0


if __name__ == "__main__":
    gen_smoke()
