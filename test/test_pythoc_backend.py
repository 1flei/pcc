# varargs ABI v2
"""
Integration test for pythoc_backend - C to pythoc code generation

Tests:
1. Basic function declarations with configurable lib parameter
2. Struct declarations
3. Enum declarations
4. Complex C headers (similar to base_binary_tree_test.c)
5. Validation that generated pythoc code matches expected output
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from pythoc import compile, i8, i32, i64, ptr, void, array
from pythoc.libc.stdio import printf
from pythoc.libc.string import strcmp, strstr
from pcc.c_parser import parse_declarations
from pcc.c_ast import decl_free
from pcc.pythoc_backend import (
    StringBuffer, strbuf_init, strbuf_destroy, strbuf_to_cstr,
    emit_module_header, emit_decl
)


@compile
def test_simple_function() -> i32:
    """Test simple function declaration with lib='c'"""
    buf: StringBuffer
    strbuf_init(ptr(buf))
    
    emit_module_header(ptr(buf))
    
    for decl_prf, decl in parse_declarations("int add(int a, int b);"):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)
    
    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Simple function ===\n%s\n", result)
    
    # Verify lib='c' is in the output
    if strstr(result, "@extern(lib='c')") == ptr[i8](0):
        printf("FAIL: Expected @extern(lib='c') in output\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    strbuf_destroy(ptr(buf))
    printf("PASS: test_simple_function\n\n")
    return 0


@compile
def test_math_lib() -> i32:
    """Test function declaration with lib='m' for math functions"""
    buf: StringBuffer
    strbuf_init(ptr(buf))
    
    emit_module_header(ptr(buf))
    
    for decl_prf, decl in parse_declarations("double sin(double x);"):
        emit_decl(ptr(buf), decl, "m")
        decl_free(decl_prf, decl)
    
    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Math lib function ===\n%s\n", result)
    
    # Verify lib='m' is in the output
    if strstr(result, "@extern(lib='m')") == ptr[i8](0):
        printf("FAIL: Expected @extern(lib='m') in output\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    strbuf_destroy(ptr(buf))
    printf("PASS: test_math_lib\n\n")
    return 0


@compile
def test_struct() -> i32:
    """Test struct declaration"""
    buf: StringBuffer
    strbuf_init(ptr(buf))
    
    emit_module_header(ptr(buf))
    
    header: ptr[i8] = """
struct Point {
    int x;
    int y;
};
"""
    
    for decl_prf, decl in parse_declarations(header):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)
    
    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Struct ===\n%s\n", result)
    
    # Verify struct is generated correctly
    if strstr(result, "Point = struct[") == ptr[i8](0):
        printf("FAIL: Expected 'Point = struct['\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "\"x\": i32") == ptr[i8](0):
        printf("FAIL: Expected '\"x\": i32'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "\"y\": i32") == ptr[i8](0):
        printf("FAIL: Expected '\"y\": i32'\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    strbuf_destroy(ptr(buf))
    printf("PASS: test_struct\n\n")
    return 0


@compile
def test_enum() -> i32:
    """Test enum declaration"""
    buf: StringBuffer
    strbuf_init(ptr(buf))
    
    emit_module_header(ptr(buf))
    
    header: ptr[i8] = """
enum Color {
    RED,
    GREEN = 5,
    BLUE
};
"""
    
    for decl_prf, decl in parse_declarations(header):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)
    
    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Enum ===\n%s\n", result)
    
    # Verify enum is generated correctly
    if strstr(result, "@enum(i32)") == ptr[i8](0):
        printf("FAIL: Expected @enum(i32) decorator\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "class Color") == ptr[i8](0):
        printf("FAIL: Expected 'class Color'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "RED") == ptr[i8](0):
        printf("FAIL: Expected 'RED'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "GREEN") == ptr[i8](0):
        printf("FAIL: Expected 'GREEN'\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    strbuf_destroy(ptr(buf))
    printf("PASS: test_enum\n\n")
    return 0


@compile
def test_binary_tree_style() -> i32:
    """Test complex C code similar to base_binary_tree_test.c
    
    This tests:
    - typedef struct with self-referential pointers
    - Multiple function declarations
    - Various return types and parameter types
    """
    buf: StringBuffer
    strbuf_init(ptr(buf))
    
    emit_module_header(ptr(buf))
    
    # C code similar to base_binary_tree_test.c
    header: ptr[i8] = """
struct tn {
    struct tn* left;
    struct tn* right;
};

struct tn* NewTreeNode(struct tn* left, struct tn* right);
long ItemCheck(struct tn* tree);
struct tn* BottomUpTree(unsigned depth);
void DeleteTree(struct tn* tree);
"""
    
    for decl_prf, decl in parse_declarations(header):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)
    
    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Binary Tree Style ===\n%s\n", result)
    
    # Verify struct tn is generated
    if strstr(result, "tn = struct[") == ptr[i8](0):
        printf("FAIL: Expected 'tn = struct['\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    # Verify self-referential pointer fields (named struct pointees are quoted)
    if strstr(result, "\"left\": ptr[\"tn\"]") == ptr[i8](0):
        printf("FAIL: Expected '\"left\": ptr[\"tn\"]'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "\"right\": ptr[\"tn\"]") == ptr[i8](0):
        printf("FAIL: Expected '\"right\": ptr[\"tn\"]'\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    # Verify function declarations
    if strstr(result, "def NewTreeNode") == ptr[i8](0):
        printf("FAIL: Expected 'def NewTreeNode'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "def ItemCheck") == ptr[i8](0):
        printf("FAIL: Expected 'def ItemCheck'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "def BottomUpTree") == ptr[i8](0):
        printf("FAIL: Expected 'def BottomUpTree'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "def DeleteTree") == ptr[i8](0):
        printf("FAIL: Expected 'def DeleteTree'\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    # Verify return types
    if strstr(result, "-> ptr[\"tn\"]") == ptr[i8](0):
        printf("FAIL: Expected '-> ptr[\"tn\"]' return type\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "-> i64") == ptr[i8](0):
        printf("FAIL: Expected '-> i64' return type for ItemCheck\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "-> void") == ptr[i8](0):
        printf("FAIL: Expected '-> void' return type for DeleteTree\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    strbuf_destroy(ptr(buf))
    printf("PASS: test_binary_tree_style\n\n")
    return 0


@compile
def test_complex_header() -> i32:
    """Test complex C header with multiple declarations"""
    buf: StringBuffer
    strbuf_init(ptr(buf))
    
    emit_module_header(ptr(buf))
    
    header: ptr[i8] = """
struct Point {
    int x;
    int y;
};

struct Rectangle {
    int width;
    int height;
};

enum Color {
    RED,
    GREEN,
    BLUE
};

int add(int a, int b);
void* malloc(unsigned long size);
int printf(const char* format, ...);
"""
    
    for decl_prf, decl in parse_declarations(header):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)
    
    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Complex header ===\n%s\n", result)
    
    # Verify all declarations are present
    if strstr(result, "Point = struct[") == ptr[i8](0):
        printf("FAIL: Expected 'Point = struct['\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "Rectangle = struct[") == ptr[i8](0):
        printf("FAIL: Expected 'Rectangle = struct['\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "class Color") == ptr[i8](0):
        printf("FAIL: Expected 'class Color'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "def add") == ptr[i8](0):
        printf("FAIL: Expected 'def add'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "def malloc") == ptr[i8](0):
        printf("FAIL: Expected 'def malloc'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "def printf") == ptr[i8](0):
        printf("FAIL: Expected 'def printf'\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    # Verify variadic function
    if strstr(result, "*args") == ptr[i8](0):
        printf("FAIL: Expected '*args' for variadic printf\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    strbuf_destroy(ptr(buf))
    printf("PASS: test_complex_header\n\n")
    return 0


@compile
def test_pointer_types() -> i32:
    """Test various pointer type conversions"""
    buf: StringBuffer
    strbuf_init(ptr(buf))
    
    emit_module_header(ptr(buf))
    
    header: ptr[i8] = """
void* get_ptr(void);
char* get_string(void);
int** get_ptr_ptr(void);
const char* get_const_string(void);
"""
    
    for decl_prf, decl in parse_declarations(header):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)
    
    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Pointer types ===\n%s\n", result)
    
    # Verify pointer types
    if strstr(result, "-> ptr[void]") == ptr[i8](0):
        printf("FAIL: Expected '-> ptr[void]'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "-> ptr[i8]") == ptr[i8](0):
        printf("FAIL: Expected '-> ptr[i8]'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "-> ptr[ptr[i32]]") == ptr[i8](0):
        printf("FAIL: Expected '-> ptr[ptr[i32]]'\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    strbuf_destroy(ptr(buf))
    printf("PASS: test_pointer_types\n\n")
    return 0


@compile
def test_c_source_file() -> i32:
    """Test parsing a full C source file with function definitions
    
    This tests:
    - typedef struct parsing
    - Function definitions (with bodies that get skipped)
    - Functions using typedef names as return/parameter types
    """
    buf: StringBuffer
    strbuf_init(ptr(buf))
    
    emit_module_header(ptr(buf))
    
    # C source code similar to base_binary_tree_test.c
    source: ptr[i8] = """
typedef struct tn {
    struct tn*    left;
    struct tn*    right;
} treeNode;

treeNode* NewTreeNode(treeNode* left, treeNode* right)
{
    treeNode*    new;
    new = (treeNode*)malloc(sizeof(treeNode));
    new->left = left;
    new->right = right;
    return new;
}

long ItemCheck(treeNode* tree)
{
    if (tree->left == NULL)
        return 1;
    else
        return 1 + ItemCheck(tree->left) + ItemCheck(tree->right);
}

treeNode* BottomUpTree(unsigned depth)
{
    if (depth > 0)
        return NewTreeNode(BottomUpTree(depth - 1), BottomUpTree(depth - 1));
    else
        return NewTreeNode(NULL, NULL);
}

void DeleteTree(treeNode* tree)
{
    if (tree->left != NULL)
    {
        DeleteTree(tree->left);
        DeleteTree(tree->right);
    }
    free(tree);
}

int main(int argc, char* argv[])
{
    return 0;
}
"""
    
    for decl_prf, decl in parse_declarations(source):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)
    
    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== C Source File ===\n%s\n", result)
    
    # Verify typedef is generated
    if strstr(result, "treeNode = tn") == ptr[i8](0):
        printf("FAIL: Expected 'treeNode = tn' typedef\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    # Verify function declarations are generated
    if strstr(result, "def NewTreeNode") == ptr[i8](0):
        printf("FAIL: Expected 'def NewTreeNode'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "def ItemCheck") == ptr[i8](0):
        printf("FAIL: Expected 'def ItemCheck'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "def BottomUpTree") == ptr[i8](0):
        printf("FAIL: Expected 'def BottomUpTree'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "def DeleteTree") == ptr[i8](0):
        printf("FAIL: Expected 'def DeleteTree'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "def main") == ptr[i8](0):
        printf("FAIL: Expected 'def main'\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    # Verify typedef usage in function signatures
    # Functions should use treeNode (typedef name) in their signatures
    if strstr(result, "ptr[treeNode]") == ptr[i8](0):
        printf("FAIL: Expected 'ptr[treeNode]' in function signatures\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    strbuf_destroy(ptr(buf))
    printf("PASS: test_c_source_file\n\n")
    return 0


@compile
def test_typedef_variants() -> i32:
    """Test various typedef patterns"""
    buf: StringBuffer
    strbuf_init(ptr(buf))
    
    emit_module_header(ptr(buf))
    
    source: ptr[i8] = """
typedef int myint;
typedef unsigned long size_t;
typedef char* string;
typedef void (*callback)(int);
typedef struct Point { int x; int y; } Point;
typedef enum Color { RED, GREEN, BLUE } Color;
"""
    
    for decl_prf, decl in parse_declarations(source):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)
    
    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Typedef Variants ===\n%s\n", result)
    
    # Verify basic typedefs
    if strstr(result, "myint = i32") == ptr[i8](0):
        printf("FAIL: Expected 'myint = i32'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "size_t = u64") == ptr[i8](0):
        printf("FAIL: Expected 'size_t = u64'\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "string = ptr[i8]") == ptr[i8](0):
        printf("FAIL: Expected 'string = ptr[i8]'\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    # Verify struct typedef emits the aggregate definition under the tag/name
    if strstr(result, "Point = struct[") == ptr[i8](0):
        printf("FAIL: Expected 'Point = struct[' typedef\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    # Verify enum typedef
    if strstr(result, "Color = Color") == ptr[i8](0):
        printf("FAIL: Expected 'Color = Color' typedef\n")
        strbuf_destroy(ptr(buf))
        return 1
    
    strbuf_destroy(ptr(buf))
    printf("PASS: test_typedef_variants\n\n")
    return 0


@compile
def test_global_vars() -> i32:
    """File-scope globals lower to static[T]+accessor and references rewrite."""
    buf: StringBuffer
    strbuf_init(ptr(buf))

    emit_module_header(ptr(buf))

    source: ptr[i8] = """
int g_count = 5;
int *g_ptr = 0;
int g_zero;
int read_count(void) { return g_count; }
int compute(int g_count) { return g_count + 1; }
"""
    for decl_prf, decl in parse_declarations(source):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)

    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Global Vars ===\n%s\n", result)

    # Accessor with the integer initializer.
    if strstr(result, "def _pcc_g_g_count() -> ptr[i32]:") == ptr[i8](0):
        printf("FAIL: missing g_count accessor\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "s: static[i32] = 5") == ptr[i8](0):
        printf("FAIL: missing g_count static seed\n")
        strbuf_destroy(ptr(buf))
        return 1
    # Pointer initialized with C's 0 becomes nullptr.
    if strstr(result, "s: static[ptr[i32]] = nullptr") == ptr[i8](0):
        printf("FAIL: pointer global not seeded with nullptr\n")
        strbuf_destroy(ptr(buf))
        return 1
    # Tentative definition is zero-seeded.
    if strstr(result, "def _pcc_g_g_zero() -> ptr[i32]:") == ptr[i8](0):
        printf("FAIL: missing g_zero accessor\n")
        strbuf_destroy(ptr(buf))
        return 1
    # A real global reference rewrites to the accessor deref.
    if strstr(result, "return _pcc_g_g_count()[0]") == ptr[i8](0):
        printf("FAIL: global reference not rewritten\n")
        strbuf_destroy(ptr(buf))
        return 1
    # A parameter shadowing the global keeps the bare name.
    if strstr(result, "return (g_count + 1)") == ptr[i8](0):
        printf("FAIL: shadowing parameter was wrongly rewritten\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "_pcc_g_g_count()[0] + 1") != ptr[i8](0):
        printf("FAIL: shadowed param must not use the accessor\n")
        strbuf_destroy(ptr(buf))
        return 1

    strbuf_destroy(ptr(buf))
    printf("PASS: test_global_vars\n\n")
    return 0


@compile
def test_aggregate_globals() -> i32:
    """Uninitialized aggregate globals lower to a seedless static[T] slot.

    Arrays/structs/unions have no scalar zero, so PythoC implicitly zero-inits
    the static storage; the accessor must declare `s: static[T]` with no seed
    rather than emitting the __pcc_unsupported__ sentinel it used to.
    """
    buf: StringBuffer
    strbuf_init(ptr(buf))

    emit_module_header(ptr(buf))

    source: ptr[i8] = """
int g_table[4];
char g_name[16];
struct Point { int x; int y; };
struct Point g_origin;
"""
    for decl_prf, decl in parse_declarations(source):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)

    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Aggregate Globals ===\n%s\n", result)

    # Array global: seedless static slot, no scalar seed, no sentinel.
    if strstr(result, "s: static[array[i32, 4]]\n") == ptr[i8](0):
        printf("FAIL: array global not lowered to seedless static slot\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "s: static[array[i8, 16]]\n") == ptr[i8](0):
        printf("FAIL: char-array global not lowered to seedless static slot\n")
        strbuf_destroy(ptr(buf))
        return 1
    # Struct global likewise gets a seedless slot.
    if strstr(result, "def _pcc_g_g_origin() -> ptr[Point]:") == ptr[i8](0):
        printf("FAIL: missing struct global accessor\n")
        strbuf_destroy(ptr(buf))
        return 1
    # The aggregate zero-init path must not leak the unsupported sentinel.
    if strstr(result, "__pcc_unsupported__") != ptr[i8](0):
        printf("FAIL: aggregate global leaked __pcc_unsupported__\n")
        strbuf_destroy(ptr(buf))
        return 1

    strbuf_destroy(ptr(buf))
    printf("PASS: test_aggregate_globals\n\n")
    return 0


@compile
def test_aggregate_initializers() -> i32:
    """Explicit aggregate initializers: positional lists lower to a seed;
    designated initializers are rejected loudly rather than misassigned.
    """
    buf: StringBuffer
    strbuf_init(ptr(buf))

    emit_module_header(ptr(buf))

    source: ptr[i8] = """
int g_tab[3] = {1, 2, 3};
struct Pt { int x; int y; };
struct Pt g_pt = {4, 5};
int g_desig[3] = {[1] = 9};
"""
    for decl_prf, decl in parse_declarations(source):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)

    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Aggregate Initializers ===\n%s\n", result)

    # Positional array initializer keeps its constant seed.
    if strstr(result, "s: static[array[i32, 3]] = (1, 2, 3)") == ptr[i8](0):
        printf("FAIL: positional array initializer not seeded\n")
        strbuf_destroy(ptr(buf))
        return 1
    # Positional struct initializer is captured (not dropped) and seeded.
    if strstr(result, "s: static[Pt] = (4, 5)") == ptr[i8](0):
        printf("FAIL: struct initializer dropped or not seeded\n")
        strbuf_destroy(ptr(buf))
        return 1
    # Designated initializer must be rejected loudly, never misassigned.
    if strstr(result, "__pcc_unsupported__") == ptr[i8](0):
        printf("FAIL: designated initializer was not rejected\n")
        strbuf_destroy(ptr(buf))
        return 1

    strbuf_destroy(ptr(buf))
    printf("PASS: test_aggregate_initializers\n\n")
    return 0


@compile
def test_goto_lowering() -> i32:
    """C goto/label reconstructs onto scoped label/goto/goto_end."""
    buf: StringBuffer
    strbuf_init(ptr(buf))

    emit_module_header(ptr(buf))

    source: ptr[i8] = """
int classify(int x) {
    int result = 0;
    if (x < 0) goto neg;
    result = 1;
    goto done;
neg:
    result = -1;
done:
    return result;
}
int sum_to(int n) {
    int i = 1;
    int total = 0;
loop:
    if (i > n) goto end;
    total = total + i;
    i = i + 1;
    goto loop;
end:
    return total;
}
"""
    for decl_prf, decl in parse_declarations(source):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)

    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Goto Lowering ===\n%s\n", result)

    # Forward gotos to ordered cleanup labels become scoped goto_end.
    if strstr(result, "with label(\"done\"):") == ptr[i8](0):
        printf("FAIL: missing 'done' label scope\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "goto_end(\"done\")") == ptr[i8](0):
        printf("FAIL: forward goto not lowered to goto_end\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "goto_end(\"neg\")") == ptr[i8](0):
        printf("FAIL: forward goto 'neg' not lowered\n")
        strbuf_destroy(ptr(buf))
        return 1
    # A backward loop goto becomes scoped goto (jump to begin).
    if strstr(result, "goto(\"loop\")") == ptr[i8](0):
        printf("FAIL: backward goto not lowered to goto\n")
        strbuf_destroy(ptr(buf))
        return 1
    # Nothing was rejected; the unsupported sentinel must be absent.
    if strstr(result, "__pcc_unsupported__") != ptr[i8](0):
        printf("FAIL: supported goto shapes leaked __pcc_unsupported__\n")
        strbuf_destroy(ptr(buf))
        return 1

    strbuf_destroy(ptr(buf))
    printf("PASS: test_goto_lowering\n\n")
    return 0


@compile
def test_goto_state_machine() -> i32:
    """Irreducible goto lowers to a __pcc_pc state machine, not a loud failure.

    `mid` is targeted both forward (before it) and backward (after it); no
    single scoped label can represent it, so the whole function dissolves into a
    state-machine dispatch instead of emitting the unsupported sentinel.
    """
    buf: StringBuffer
    strbuf_init(ptr(buf))

    emit_module_header(ptr(buf))

    source: ptr[i8] = """
int f(int x) {
    if (x == 5) goto mid;
    x = x + 1;
mid:
    x = x + 1;
    if (x < 10) goto mid;
    return x;
}
"""
    for decl_prf, decl in parse_declarations(source):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)

    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Goto State Machine ===\n%s\n", result)

    # The both-direction label is now handled by state-machine lowering.
    if strstr(result, "__pcc_pc: i32 = 0") == ptr[i8](0):
        printf("FAIL: irreducible goto did not lower to a state machine\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "while True:") == ptr[i8](0):
        printf("FAIL: state machine missing dispatch loop\n")
        strbuf_destroy(ptr(buf))
        return 1
    # Nothing was rejected and no scoped label was emitted for the function.
    if strstr(result, "__pcc_unsupported__") != ptr[i8](0):
        printf("FAIL: state machine leaked __pcc_unsupported__\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "with label(\"mid\")") != ptr[i8](0):
        printf("FAIL: irreducible label must not become a scope\n")
        strbuf_destroy(ptr(buf))
        return 1

    strbuf_destroy(ptr(buf))
    printf("PASS: test_goto_state_machine\n\n")
    return 0


@compile
def test_goto_into_switch() -> i32:
    """A goto from one switch case to a label inside another case forces the
    whole function into the state machine (emit_switch cannot scope labels)."""
    buf: StringBuffer
    strbuf_init(ptr(buf))

    emit_module_header(ptr(buf))

    source: ptr[i8] = """
int pick(int k, int x) {
    switch (k) {
        case 1:
            x = 10;
        set_it:
            x = x + 1;
            break;
        case 2:
            x = 20;
            goto set_it;
        default:
            x = 0;
    }
    return x;
}
"""
    for decl_prf, decl in parse_declarations(source):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)

    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Goto Into Switch ===\n%s\n", result)

    if strstr(result, "__pcc_pc: i32 = 0") == ptr[i8](0):
        printf("FAIL: cross-case goto did not lower to a state machine\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "__pcc_unsupported__") != ptr[i8](0):
        printf("FAIL: cross-case goto leaked __pcc_unsupported__\n")
        strbuf_destroy(ptr(buf))
        return 1

    strbuf_destroy(ptr(buf))
    printf("PASS: test_goto_into_switch\n\n")
    return 0


@compile
def test_storage_class_hints() -> i32:
    """register/auto are ignorable storage-class hints, not identifiers.

    They appear on locals and parameters in real C (e.g. tinycc's libtcc1.c);
    the parser must skip them so the declaration lowers as a plain variable
    rather than leaking a bare `register`/`auto` name into the output.
    """
    buf: StringBuffer
    strbuf_init(ptr(buf))

    emit_module_header(ptr(buf))

    source: ptr[i8] = """
int f(register int a) {
    register int exp = a + 1;
    auto int y = exp;
    return y;
}
"""
    for decl_prf, decl in parse_declarations(source):
        emit_decl(ptr(buf), decl, "c")
        decl_free(decl_prf, decl)

    result: ptr[i8] = strbuf_to_cstr(ptr(buf))
    printf("=== Storage Class Hints ===\n%s\n", result)

    # The register parameter lowers to a normal typed parameter.
    if strstr(result, "a: i32") == ptr[i8](0):
        printf("FAIL: register parameter not lowered\n")
        strbuf_destroy(ptr(buf))
        return 1
    # register/auto locals lower to plain typed locals.
    if strstr(result, "exp: i32") == ptr[i8](0):
        printf("FAIL: register local not lowered\n")
        strbuf_destroy(ptr(buf))
        return 1
    if strstr(result, "y: i32") == ptr[i8](0):
        printf("FAIL: auto local not lowered\n")
        strbuf_destroy(ptr(buf))
        return 1
    # The hint keywords must never survive as emitted identifiers.
    if strstr(result, "__pcc_unsupported__") != ptr[i8](0):
        printf("FAIL: storage-class hint leaked __pcc_unsupported__\n")
        strbuf_destroy(ptr(buf))
        return 1

    strbuf_destroy(ptr(buf))
    printf("PASS: test_storage_class_hints\n\n")
    return 0


@compile
def main() -> i32:
    printf("=== Pythoc Backend Tests ===\n\n")
    
    failed: i32 = 0
    
    failed = failed + test_simple_function()
    failed = failed + test_math_lib()
    failed = failed + test_struct()
    failed = failed + test_enum()
    failed = failed + test_binary_tree_style()
    failed = failed + test_complex_header()
    failed = failed + test_pointer_types()
    failed = failed + test_c_source_file()
    failed = failed + test_typedef_variants()
    failed = failed + test_global_vars()
    failed = failed + test_aggregate_globals()
    failed = failed + test_aggregate_initializers()
    failed = failed + test_goto_lowering()
    failed = failed + test_goto_state_machine()
    failed = failed + test_goto_into_switch()
    failed = failed + test_storage_class_hints()
    
    if failed > 0:
        printf("\n%d test(s) FAILED!\n", failed)
        return 1
    
    printf("\nAll tests PASSED!\n")
    return 0


if __name__ == "__main__":
    main()
