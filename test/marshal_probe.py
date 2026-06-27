"""Probe how runtime string args marshal into generate_bindings_to_file."""

import sys
import os
import ctypes
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from pcc.bindgen import generate_bindings_to_file

SRC = "int add(int a, int b);\n"


def try_variant(name, source, lib, out):
    try:
        rc = generate_bindings_to_file(source, lib, out)
        ok = os.path.exists("/tmp/probe_out.py")
        print("%-20s rc=%r exists=%r" % (name, rc, ok))
        if ok:
            os.remove("/tmp/probe_out.py")
    except Exception as e:
        print("%-20s EXC %s: %s" % (name, type(e).__name__, e))


def main():
    out = "/tmp/probe_out.py"
    try_variant("str", SRC, "c", out)
    try_variant("bytes", SRC.encode(), b"c", out.encode())
    try_variant(
        "string_buffer",
        ctypes.create_string_buffer(SRC.encode()),
        ctypes.create_string_buffer(b"c"),
        ctypes.create_string_buffer(out.encode()),
    )


if __name__ == "__main__":
    main()
