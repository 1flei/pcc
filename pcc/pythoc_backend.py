"""
Pythoc Backend - Generate pythoc code from C AST (pythoc compiled)

This module provides code generation from C header AST to pythoc source code.
It translates C type declarations, structs, enums, and function signatures
into equivalent pythoc code.

Design:
- All code generation functions are @compile decorated
- Uses StringBuffer for dynamic string building
- Generates @compile decorated classes for structs/unions
- Generates @enum decorated classes for enums
- Generates @extern decorated function declarations
- Type mapping from C primitives to pythoc types

Usage:
    from pcc.pythoc_backend import (
        StringBuffer, strbuf_init, strbuf_destroy, strbuf_to_cstr,
        emit_module_header, emit_decl
    )
    from pcc.c_parser import parse_declarations
    from pcc.c_ast import decl_free
    
    @compile
    def generate_bindings(source: ptr[i8]) -> ptr[i8]:
        buf: StringBuffer
        strbuf_init(ptr(buf))
        
        emit_module_header(ptr(buf))
        for decl_prf, decl in parse_declarations(source):
            emit_decl(ptr(buf), decl)
            decl_free(decl_prf, decl)
        
        result: ptr[i8] = strbuf_to_cstr(ptr(buf))
        # Note: caller should copy result before destroying buf
        strbuf_destroy(ptr(buf))
        return result
"""

from pythoc import (
    compile, inline, i32, i64, i8, bool, ptr, array, nullptr, sizeof, void,
    char, refine, assume, struct, consume, linear, effect
)
from pythoc.std import mem  # noqa: F401  (sets up default mem effect)
from pythoc.std.vector import Vector

from .c_token import TokenType
from .c_ast import (
    Span, span_empty, span_is_empty, span_eq,
    CType, QualType, PtrType, ArrayType, FuncType,
    StructType, EnumType, EnumValue, FieldInfo, ParamInfo,
    Decl, DeclKind,
    CTypeRef, QualTypeRef, StructTypeRef, EnumTypeRef,
    DeclRef, decl_nonnull, decl_free,
    QUAL_NONE, QUAL_CONST, QUAL_VOLATILE,
    STORAGE_NONE, STORAGE_EXTERN, STORAGE_STATIC, STORAGE_INLINE,
    ExprKind, Expr,
    StmtKind, Stmt,
)


# =============================================================================
# StringBuffer - Dynamic string builder using Vector
# =============================================================================

_CharVec = Vector(i8, inline_capacity=256)
StringBuffer = _CharVec.type

# Export Vector API as module-level functions
strbuf_init = _CharVec.init
strbuf_destroy = _CharVec.destroy
strbuf_size = _CharVec.size
_strbuf_push_back = _CharVec.push_back
_strbuf_get = _CharVec.get
# Pointer to the contiguous character storage (not null-terminated). The Vector
# accessor is the single source of truth for the inline/heap boundary.
strbuf_data = _CharVec.data


@compile
def strbuf_push_char(buf: ptr[StringBuffer], c: i8) -> void:
    """Append a single character"""
    _strbuf_push_back(buf, c)


@compile
def strbuf_push_cstr(buf: ptr[StringBuffer], s: ptr[i8]) -> void:
    """Append a null-terminated C string"""
    i: i64 = 0
    while s[i] != 0:
        _strbuf_push_back(buf, s[i])
        i = i + 1


@compile
def strbuf_push_span(buf: ptr[StringBuffer], s: Span) -> void:
    """Append a Span"""
    i: i32 = 0
    while i < s.len:
        _strbuf_push_back(buf, s.start[i])
        i = i + 1


@compile
def strbuf_push_i32(buf: ptr[StringBuffer], val: i32) -> void:
    """Append an i32 as decimal string"""
    if val < 0:
        _strbuf_push_back(buf, 45)  # '-'
        val = -val
    if val == 0:
        _strbuf_push_back(buf, 48)  # '0'
        return
    # Reverse digits into temp buffer
    digits: array[i8, 12]
    count: i32 = 0
    while val > 0:
        digits[count] = i8(48 + (val % 10))
        val = val / 10
        count = count + 1
    # Push in reverse order
    while count > 0:
        count = count - 1
        _strbuf_push_back(buf, digits[count])


@compile
def strbuf_push_i64(buf: ptr[StringBuffer], val: i64) -> void:
    """Append an i64 as decimal string"""
    if val < 0:
        _strbuf_push_back(buf, 45)  # '-'
        val = -val
    if val == 0:
        _strbuf_push_back(buf, 48)  # '0'
        return
    digits: array[i8, 24]
    count: i32 = 0
    while val > 0:
        digits[count] = i8(48 + i32(val % 10))
        val = val / 10
        count = count + 1
    while count > 0:
        count = count - 1
        _strbuf_push_back(buf, digits[count])


@compile
def strbuf_push_newline(buf: ptr[StringBuffer]) -> void:
    """Append a newline"""
    _strbuf_push_back(buf, 10)  # '\n'


@compile
def strbuf_push_indent(buf: ptr[StringBuffer], level: i32) -> void:
    """Append indentation (4 spaces per level)"""
    i: i32 = 0
    while i < level * 4:
        _strbuf_push_back(buf, 32)  # ' '
        i = i + 1


@compile
def strbuf_null_terminate(buf: ptr[StringBuffer]) -> void:
    """Add null terminator (for C string compatibility)"""
    _strbuf_push_back(buf, 0)


@compile
def strbuf_to_cstr(buf: ptr[StringBuffer]) -> ptr[i8]:
    """Get null-terminated C string (adds terminator if needed)"""
    sz: i64 = strbuf_size(buf)
    if sz == 0:
        _strbuf_push_back(buf, 0)
        return strbuf_data(buf)
    last: i8 = _strbuf_get(buf, sz - 1)
    if last != 0:
        _strbuf_push_back(buf, 0)
    return strbuf_data(buf)


# =============================================================================
# Type emission - recursive type to string
# =============================================================================

@compile
def emit_anon_aggregate(buf: ptr[StringBuffer], st: ptr[StructType], is_union: i8) -> void:
    """Emit an anonymous struct/union as an inline PythoC type: struct[...] /
    union[...]. Field types are emitted recursively, so nested anonymous
    aggregates (common in glibc) are handled naturally."""
    if is_union != 0:
        strbuf_push_cstr(buf, "union[")
    else:
        strbuf_push_cstr(buf, "struct[")
    if st == nullptr or st.field_count == 0:
        strbuf_push_cstr(buf, "\"_pad\": i8")
    else:
        i: i32 = 0
        while i < st.field_count:
            if i > 0:
                strbuf_push_cstr(buf, ", ")
            field: ptr[FieldInfo] = ptr(st.fields[i])
            # Field names are quoted: struct["name": T] uses Python slice syntax
            # whose key must be a string literal, not an evaluated identifier.
            strbuf_push_char(buf, 34)  # '"'
            if not span_is_empty(field.name):
                strbuf_push_span(buf, field.name)
            else:
                strbuf_push_cstr(buf, "_field")
                strbuf_push_i32(buf, i)
            strbuf_push_char(buf, 34)  # '"'
            strbuf_push_cstr(buf, ": ")
            emit_qualtype(buf, field.type)
            i = i + 1
    strbuf_push_char(buf, 93)  # ']'


@compile
def emit_pointee(buf: ptr[StringBuffer], qt: ptr[QualType]) -> void:
    """Emit a pointer's pointee type.

    Named struct/union pointees are emitted as a quoted forward reference
    (e.g. ptr["Tag"]) so self-referential and mutually-recursive aggregates
    resolve lazily instead of requiring the name to already be bound.
    """
    if qt != nullptr and qt.type != nullptr:
        match qt.type[0]:
            case (CType.Struct, st):
                if st != nullptr and not span_is_empty(st.name):
                    strbuf_push_char(buf, 34)  # '"'
                    strbuf_push_span(buf, st.name)
                    strbuf_push_char(buf, 34)
                    return
            case (CType.Union, st):
                if st != nullptr and not span_is_empty(st.name):
                    strbuf_push_char(buf, 34)
                    strbuf_push_span(buf, st.name)
                    strbuf_push_char(buf, 34)
                    return
            case _:
                pass
    emit_qualtype(buf, qt)


@compile
def emit_qualtype(buf: ptr[StringBuffer], qt: ptr[QualType]) -> void:
    """Emit a QualType to the buffer"""
    if qt == nullptr:
        strbuf_push_cstr(buf, "void")
        return
    emit_ctype(buf, qt.type)


@compile
def emit_ctype(buf: ptr[StringBuffer], ty: ptr[CType]) -> void:
    """Emit a CType to the buffer"""
    if ty == nullptr:
        strbuf_push_cstr(buf, "void")
        return
    
    match ty[0]:
        # Primitive types
        case CType.Void:
            strbuf_push_cstr(buf, "void")
        case CType.Char:
            # PythoC `char` is a builtin for char literals, not a pointee type;
            # C `char` is modeled as i8.
            strbuf_push_cstr(buf, "i8")
        case CType.SChar:
            strbuf_push_cstr(buf, "i8")
        case CType.UChar:
            strbuf_push_cstr(buf, "u8")
        case CType.Short:
            strbuf_push_cstr(buf, "i16")
        case CType.UShort:
            strbuf_push_cstr(buf, "u16")
        case CType.Int:
            strbuf_push_cstr(buf, "i32")
        case CType.UInt:
            strbuf_push_cstr(buf, "u32")
        case CType.Long:
            strbuf_push_cstr(buf, "i64")
        case CType.ULong:
            strbuf_push_cstr(buf, "u64")
        case CType.LongLong:
            strbuf_push_cstr(buf, "i64")
        case CType.ULongLong:
            strbuf_push_cstr(buf, "u64")
        case CType.Float:
            strbuf_push_cstr(buf, "f32")
        case CType.Double:
            strbuf_push_cstr(buf, "f64")
        case CType.LongDouble:
            strbuf_push_cstr(buf, "f64")
        
        # Pointer type: ptr[T]
        case (CType.Ptr, pt):
            strbuf_push_cstr(buf, "ptr[")
            if pt != nullptr and pt.pointee != nullptr:
                emit_pointee(buf, pt.pointee)
            else:
                strbuf_push_cstr(buf, "void")
            strbuf_push_char(buf, 93)  # ']'
        
        # Array type: array[T, N] or ptr[T] for unsized / flexible (size 0)
        case (CType.Array, at):
            if at != nullptr:
                if at.size <= 0:
                    # Unsized or flexible array member -> ptr
                    strbuf_push_cstr(buf, "ptr[")
                    if at.elem != nullptr:
                        emit_pointee(buf, at.elem)
                    else:
                        strbuf_push_cstr(buf, "void")
                    strbuf_push_char(buf, 93)  # ']'
                else:
                    strbuf_push_cstr(buf, "array[")
                    if at.elem != nullptr:
                        emit_qualtype(buf, at.elem)
                    else:
                        strbuf_push_cstr(buf, "void")
                    strbuf_push_cstr(buf, ", ")
                    strbuf_push_i32(buf, at.size)
                    strbuf_push_char(buf, 93)  # ']'
            else:
                strbuf_push_cstr(buf, "ptr[void]")
        
        # Function type: func[param_types..., ret_type]
        case (CType.Func, ft):
            if ft != nullptr:
                strbuf_push_cstr(buf, "func[")
                i: i32 = 0
                while i < ft.param_count:
                    if i > 0:
                        strbuf_push_cstr(buf, ", ")
                    param: ptr[ParamInfo] = ptr(ft.params[i])
                    emit_qualtype(buf, param.type)
                    i = i + 1
                if ft.param_count > 0:
                    strbuf_push_cstr(buf, ", ")
                emit_qualtype(buf, ft.ret)
                strbuf_push_char(buf, 93)  # ']'
            else:
                strbuf_push_cstr(buf, "func[void]")
        
        # Struct type
        case (CType.Struct, st):
            if st != nullptr and not span_is_empty(st.name):
                strbuf_push_span(buf, st.name)
            else:
                emit_anon_aggregate(buf, st, 0)
        
        # Union type
        case (CType.Union, st):
            if st != nullptr and not span_is_empty(st.name):
                strbuf_push_span(buf, st.name)
            else:
                emit_anon_aggregate(buf, st, 1)
        
        # Enum type
        case (CType.Enum, et):
            if et != nullptr and not span_is_empty(et.name):
                strbuf_push_span(buf, et.name)
            else:
                strbuf_push_cstr(buf, "i32")
        
        # Typedef reference
        case (CType.Typedef, name):
            if not span_is_empty(name):
                strbuf_push_span(buf, name)
            else:
                strbuf_push_cstr(buf, "i32")
        
        case _:
            strbuf_push_cstr(buf, "i32")


# =============================================================================
# Expression emission
# =============================================================================

# C binary operator token -> PythoC operator text
_binop_to_str = {
    TokenType.PLUS: "+",
    TokenType.MINUS: "-",
    TokenType.STAR: "*",
    TokenType.SLASH: "/",
    TokenType.PERCENT: "%",
    TokenType.LSHIFT: "<<",
    TokenType.RSHIFT: ">>",
    TokenType.AMP: "&",
    TokenType.PIPE: "|",
    TokenType.CARET: "^",
    TokenType.LT: "<",
    TokenType.GT: ">",
    TokenType.LE: "<=",
    TokenType.GE: ">=",
    TokenType.EQ: "==",
    TokenType.NE: "!=",
    TokenType.LAND: "and",
    TokenType.LOR: "or",
}


@compile
def strbuf_push_float_literal(buf: ptr[StringBuffer], s: Span) -> void:
    """Append a float literal, stripping C 'f'/'F'/'l'/'L' suffixes."""
    n: i32 = s.len
    while n > 0:
        c: i8 = s.start[n - 1]
        if c == char("f") or c == char("F") or c == char("l") or c == char("L"):
            n = n - 1
        else:
            break
    i: i32 = 0
    while i < n:
        _strbuf_push_back(buf, s.start[i])
        i = i + 1


@compile
def emit_unsupported(buf: ptr[StringBuffer]) -> void:
    """Emit a sentinel identifier so unsupported constructs fail loudly in PythoC."""
    strbuf_push_cstr(buf, "__pcc_unsupported__")


@compile
def emit_global_accessor_name(buf: ptr[StringBuffer], name: Span) -> void:
    """Emit the accessor-function name for a file-scope global."""
    strbuf_push_cstr(buf, "_pcc_g_")
    strbuf_push_span(buf, name)


@compile
def emit_global_ref(buf: ptr[StringBuffer], name: Span) -> void:
    """Emit a read/lvalue reference to a global as <accessor>()[0]."""
    emit_global_accessor_name(buf, name)
    strbuf_push_cstr(buf, "()[0]")


@compile
def emit_binop_str(buf: ptr[StringBuffer], op: i32) -> void:
    """Emit the PythoC text for a C binary operator token."""
    for op_tok, op_str in _binop_to_str.items():
        if op == op_tok:
            strbuf_push_cstr(buf, op_str)
            return
    emit_unsupported(buf)


@compile
def emit_unary(buf: ptr[StringBuffer], e: ptr[Expr]) -> void:
    """Emit a unary-operator expression."""
    match e.op:
        case TokenType.MINUS:
            strbuf_push_cstr(buf, "(-")
            emit_expr(buf, e.lhs)
            strbuf_push_char(buf, 41)  # ')'
        case TokenType.PLUS:
            strbuf_push_cstr(buf, "(+")
            emit_expr(buf, e.lhs)
            strbuf_push_char(buf, 41)
        case TokenType.TILDE:
            strbuf_push_cstr(buf, "(~")
            emit_expr(buf, e.lhs)
            strbuf_push_char(buf, 41)
        case TokenType.EXCLAIM:
            strbuf_push_cstr(buf, "(not ")
            emit_expr(buf, e.lhs)
            strbuf_push_char(buf, 41)
        case TokenType.STAR:
            # Pointer dereference -> [0]
            emit_expr(buf, e.lhs)
            strbuf_push_cstr(buf, "[0]")
        case TokenType.AMP:
            # Address-of -> ptr(...)
            strbuf_push_cstr(buf, "ptr(")
            emit_expr(buf, e.lhs)
            strbuf_push_char(buf, 41)
        case _:
            # Prefix ++/-- only valid as a statement; here just emit the operand.
            emit_expr(buf, e.lhs)


@compile
def emit_expr(buf: ptr[StringBuffer], e: ptr[Expr]) -> void:
    """Emit a C expression as PythoC source. Compound forms are parenthesized
    so evaluation order is preserved regardless of PythoC precedence."""
    if e == nullptr:
        strbuf_push_char(buf, 48)  # '0'
        return

    match e.kind[0]:
        case ExprKind.IntLit:
            strbuf_push_i64(buf, e.int_val)
        case ExprKind.FloatLit:
            strbuf_push_float_literal(buf, e.span)
        case ExprKind.CharLit:
            strbuf_push_i64(buf, e.int_val)
        case ExprKind.StringLit:
            strbuf_push_span(buf, e.span)
        case ExprKind.Ident:
            if e.is_global != 0:
                emit_global_ref(buf, e.span)
            else:
                strbuf_push_span(buf, e.span)
        case ExprKind.UnaryOp:
            emit_unary(buf, e)
        case ExprKind.PostfixOp:
            # Postfix ++/-- only valid as a statement; emit the operand value.
            emit_expr(buf, e.lhs)
        case ExprKind.Cast:
            emit_qualtype(buf, e.type_ref)
            strbuf_push_char(buf, 40)  # '('
            emit_expr(buf, e.lhs)
            strbuf_push_char(buf, 41)  # ')'
        case ExprKind.SizeofExpr:
            strbuf_push_cstr(buf, "sizeof(")
            if e.type_ref != nullptr:
                emit_qualtype(buf, e.type_ref)
            else:
                strbuf_push_cstr(buf, "typeof(")
                emit_expr(buf, e.lhs)
                strbuf_push_char(buf, 41)
            strbuf_push_char(buf, 41)
        case ExprKind.BinaryOp:
            strbuf_push_char(buf, 40)  # '('
            emit_expr(buf, e.lhs)
            strbuf_push_char(buf, 32)  # ' '
            emit_binop_str(buf, e.op)
            strbuf_push_char(buf, 32)
            emit_expr(buf, e.rhs)
            strbuf_push_char(buf, 41)  # ')'
        case ExprKind.Assign:
            # Residual of an assignment expression is its lvalue: the assignment
            # itself is hoisted to a statement by emit_pre_effects, and the value
            # of `(lval = rval)` is the (already updated) lvalue.
            emit_expr(buf, e.lhs)
        case ExprKind.Ternary:
            strbuf_push_char(buf, 40)  # '('
            emit_expr(buf, e.rhs)      # value-if-true
            strbuf_push_cstr(buf, " if ")
            emit_expr(buf, e.lhs)      # condition
            strbuf_push_cstr(buf, " else ")
            emit_expr(buf, e.extra)    # value-if-false
            strbuf_push_char(buf, 41)  # ')'
        case ExprKind.Call:
            emit_expr(buf, e.lhs)
            strbuf_push_char(buf, 40)  # '('
            i: i32 = 0
            while i < e.arg_count:
                if i > 0:
                    strbuf_push_cstr(buf, ", ")
                emit_expr(buf, ptr(e.args[i]))
                i = i + 1
            strbuf_push_char(buf, 41)  # ')'
        case ExprKind.Index:
            emit_expr(buf, e.lhs)
            strbuf_push_char(buf, 91)  # '['
            emit_expr(buf, e.rhs)
            strbuf_push_char(buf, 93)  # ']'
        case ExprKind.Member:
            emit_expr(buf, e.lhs)
            strbuf_push_char(buf, 46)  # '.'
            strbuf_push_span(buf, e.span)
        case ExprKind.Arrow:
            # PythoC uses '.' for pointer member access too.
            emit_expr(buf, e.lhs)
            strbuf_push_char(buf, 46)  # '.'
            strbuf_push_span(buf, e.span)
        case ExprKind.InitList:
            # Designated initializers (.field=/[idx]=) carry a target position
            # the positional tuple lowering cannot represent; reject loudly
            # (op != 0 is the designator flag set by the parser).
            if e.op != 0:
                emit_unsupported(buf)
                return
            # Aggregate initializer -> PythoC tuple.
            strbuf_push_char(buf, 40)  # '('
            i2: i32 = 0
            while i2 < e.arg_count:
                if i2 > 0:
                    strbuf_push_cstr(buf, ", ")
                emit_expr(buf, ptr(e.args[i2]))
                i2 = i2 + 1
            if e.arg_count == 1:
                strbuf_push_char(buf, 44)  # ',' single-element tuple
            strbuf_push_char(buf, 41)  # ')'
        case ExprKind.Comma:
            # Comma operator only handled at statement level.
            emit_unsupported(buf)
        case _:
            emit_unsupported(buf)


# =============================================================================
# Statement emission
# =============================================================================

# C assignment-operator token -> PythoC augmented-assignment text
_assignop_to_str = {
    TokenType.ASSIGN: "=",
    TokenType.PLUS_ASSIGN: "+=",
    TokenType.MINUS_ASSIGN: "-=",
    TokenType.STAR_ASSIGN: "*=",
    TokenType.SLASH_ASSIGN: "/=",
    TokenType.PERCENT_ASSIGN: "%=",
    TokenType.LSHIFT_ASSIGN: "<<=",
    TokenType.RSHIFT_ASSIGN: ">>=",
    TokenType.AND_ASSIGN: "&=",
    TokenType.OR_ASSIGN: "|=",
    TokenType.XOR_ASSIGN: "^=",
}


@compile
def emit_assignop_str(buf: ptr[StringBuffer], op: i32) -> void:
    """Emit the PythoC augmented-assignment text for a C assignment operator."""
    for op_tok, op_str in _assignop_to_str.items():
        if op == op_tok:
            strbuf_push_cstr(buf, op_str)
            return
    strbuf_push_char(buf, 61)  # '='


@compile
def expr_is_incdec(e: ptr[Expr]) -> bool:
    """True if e is a pre/postfix ++/-- node."""
    if e == nullptr:
        return False
    match e.kind[0]:
        case ExprKind.UnaryOp:
            return e.op == TokenType.INC or e.op == TokenType.DEC
        case ExprKind.PostfixOp:
            return e.op == TokenType.INC or e.op == TokenType.DEC
        case _:
            return False


@compile
def expr_has_side_effects(e: ptr[Expr]) -> bool:
    """True if e contains an assignment or ++/-- anywhere it is evaluated."""
    if e == nullptr:
        return False
    match e.kind[0]:
        case ExprKind.Assign:
            return True
        case ExprKind.UnaryOp:
            if e.op == TokenType.INC or e.op == TokenType.DEC:
                return True
            return expr_has_side_effects(e.lhs)
        case ExprKind.PostfixOp:
            return True
        case ExprKind.BinaryOp:
            return expr_has_side_effects(e.lhs) or expr_has_side_effects(e.rhs)
        case ExprKind.Cast:
            return expr_has_side_effects(e.lhs)
        case ExprKind.Index:
            return expr_has_side_effects(e.lhs) or expr_has_side_effects(e.rhs)
        case ExprKind.Member:
            return expr_has_side_effects(e.lhs)
        case ExprKind.Arrow:
            return expr_has_side_effects(e.lhs)
        case ExprKind.Ternary:
            return (expr_has_side_effects(e.lhs)
                    or expr_has_side_effects(e.rhs)
                    or expr_has_side_effects(e.extra))
        case ExprKind.Call:
            if expr_has_side_effects(e.lhs):
                return True
            i: i32 = 0
            while i < e.arg_count:
                if expr_has_side_effects(ptr(e.args[i])):
                    return True
                i = i + 1
            return False
        case ExprKind.InitList:
            j: i32 = 0
            while j < e.arg_count:
                if expr_has_side_effects(ptr(e.args[j])):
                    return True
                j = j + 1
            return False
        case _:
            return False


@compile
def expr_has_post_effect(e: ptr[Expr]) -> bool:
    """True if e contains a postfix ++/-- whose update is *not* consumed by an
    enclosing assignment or prefix ++/-- (which hoist their whole subtree's
    effects in emit_pre_effects). Such a "leftover" post-update would have to run
    after the residual value is read, which the simple lowering cannot express
    without a temporary; callers reject those positions instead of miscompiling."""
    if e == nullptr:
        return False
    match e.kind[0]:
        case ExprKind.PostfixOp:
            return True
        case ExprKind.Assign:
            return False
        case ExprKind.UnaryOp:
            if e.op == TokenType.INC or e.op == TokenType.DEC:
                return False
            return expr_has_post_effect(e.lhs)
        case ExprKind.BinaryOp:
            return expr_has_post_effect(e.lhs) or expr_has_post_effect(e.rhs)
        case ExprKind.Cast:
            return expr_has_post_effect(e.lhs)
        case ExprKind.Index:
            return expr_has_post_effect(e.lhs) or expr_has_post_effect(e.rhs)
        case ExprKind.Member:
            return expr_has_post_effect(e.lhs)
        case ExprKind.Arrow:
            return expr_has_post_effect(e.lhs)
        case ExprKind.Ternary:
            return (expr_has_post_effect(e.lhs)
                    or expr_has_post_effect(e.rhs)
                    or expr_has_post_effect(e.extra))
        case ExprKind.Call:
            if expr_has_post_effect(e.lhs):
                return True
            i: i32 = 0
            while i < e.arg_count:
                if expr_has_post_effect(ptr(e.args[i])):
                    return True
                i = i + 1
            return False
        case ExprKind.InitList:
            j: i32 = 0
            while j < e.arg_count:
                if expr_has_post_effect(ptr(e.args[j])):
                    return True
                j = j + 1
            return False
        case _:
            return False


@compile
def emit_incdec_line(buf: ptr[StringBuffer], e: ptr[Expr], indent: i32) -> void:
    """Emit a single `lvalue += 1` / `lvalue -= 1` statement for a ++/-- node."""
    strbuf_push_indent(buf, indent)
    emit_expr(buf, e.lhs)
    if e.op == TokenType.DEC:
        strbuf_push_cstr(buf, " -= 1")
    else:
        strbuf_push_cstr(buf, " += 1")
    strbuf_push_newline(buf)


@compile
def emit_assign_line(buf: ptr[StringBuffer], e: ptr[Expr], indent: i32) -> void:
    """Emit a single `lvalue op rvalue` assignment statement for an Assign node.

    The rvalue is the residual of e.rhs; its own side effects are emitted by the
    surrounding emit_pre_effects / emit_post_effects calls."""
    strbuf_push_indent(buf, indent)
    emit_expr(buf, e.lhs)
    strbuf_push_char(buf, 32)  # ' '
    emit_assignop_str(buf, e.op)
    strbuf_push_char(buf, 32)
    emit_expr(buf, e.rhs)
    strbuf_push_newline(buf)


@compile
def emit_pre_effects(buf: ptr[StringBuffer], e: ptr[Expr], indent: i32) -> void:
    """Emit, in evaluation order, the statements that must run before e's
    residual value is read: nested side effects, assignments (as statements) and
    prefix ++/--. Postfix ++/-- are deferred to emit_post_effects, except when
    nested inside an assignment / prefix ++/-- which hoist their full subtree
    here so the residual lvalue stays valid."""
    if e == nullptr:
        return
    match e.kind[0]:
        case ExprKind.Assign:
            # Evaluate rhs side effects, perform the assignment, then flush any
            # postfix updates from rhs/lhs - all before the residual lvalue read.
            emit_pre_effects(buf, e.rhs, indent)
            emit_pre_effects(buf, e.lhs, indent)
            emit_assign_line(buf, e, indent)
            emit_post_effects(buf, e.rhs, indent)
            emit_post_effects(buf, e.lhs, indent)
        case ExprKind.UnaryOp:
            if e.op == TokenType.INC or e.op == TokenType.DEC:
                emit_pre_effects(buf, e.lhs, indent)
                emit_incdec_line(buf, e, indent)
                emit_post_effects(buf, e.lhs, indent)
            else:
                emit_pre_effects(buf, e.lhs, indent)
        case ExprKind.PostfixOp:
            emit_pre_effects(buf, e.lhs, indent)
        case ExprKind.BinaryOp:
            emit_pre_effects(buf, e.lhs, indent)
            emit_pre_effects(buf, e.rhs, indent)
        case ExprKind.Cast:
            emit_pre_effects(buf, e.lhs, indent)
        case ExprKind.Index:
            emit_pre_effects(buf, e.lhs, indent)
            emit_pre_effects(buf, e.rhs, indent)
        case ExprKind.Member:
            emit_pre_effects(buf, e.lhs, indent)
        case ExprKind.Arrow:
            emit_pre_effects(buf, e.lhs, indent)
        case ExprKind.Call:
            emit_pre_effects(buf, e.lhs, indent)
            i: i32 = 0
            while i < e.arg_count:
                emit_pre_effects(buf, ptr(e.args[i]), indent)
                i = i + 1
        case ExprKind.Ternary:
            emit_pre_effects(buf, e.lhs, indent)
        case ExprKind.InitList:
            j: i32 = 0
            while j < e.arg_count:
                emit_pre_effects(buf, ptr(e.args[j]), indent)
                j = j + 1
        case _:
            pass


@compile
def emit_post_effects(buf: ptr[StringBuffer], e: ptr[Expr], indent: i32) -> void:
    """Emit postfix ++/-- updates that run after e's residual value is read.

    Assignments and prefix ++/-- (and the subtrees they own) are skipped here:
    emit_pre_effects already flushed their effects."""
    if e == nullptr:
        return
    match e.kind[0]:
        case ExprKind.PostfixOp:
            emit_post_effects(buf, e.lhs, indent)
            emit_incdec_line(buf, e, indent)
        case ExprKind.Assign:
            pass
        case ExprKind.UnaryOp:
            if e.op == TokenType.INC or e.op == TokenType.DEC:
                pass
            else:
                emit_post_effects(buf, e.lhs, indent)
        case ExprKind.BinaryOp:
            emit_post_effects(buf, e.lhs, indent)
            emit_post_effects(buf, e.rhs, indent)
        case ExprKind.Cast:
            emit_post_effects(buf, e.lhs, indent)
        case ExprKind.Index:
            emit_post_effects(buf, e.lhs, indent)
            emit_post_effects(buf, e.rhs, indent)
        case ExprKind.Member:
            emit_post_effects(buf, e.lhs, indent)
        case ExprKind.Arrow:
            emit_post_effects(buf, e.lhs, indent)
        case ExprKind.Call:
            emit_post_effects(buf, e.lhs, indent)
            i: i32 = 0
            while i < e.arg_count:
                emit_post_effects(buf, ptr(e.args[i]), indent)
                i = i + 1
        case ExprKind.Ternary:
            emit_post_effects(buf, e.lhs, indent)
        case ExprKind.InitList:
            j: i32 = 0
            while j < e.arg_count:
                emit_post_effects(buf, ptr(e.args[j]), indent)
                j = j + 1
        case _:
            pass


@compile
def emit_cond(buf: ptr[StringBuffer], e: ptr[Expr], indent: i32) -> void:
    """Emit the residual of a condition expression, after emit_pre_effects has
    hoisted its side effects. A leftover postfix update cannot be represented
    here without a temporary, so flag it loudly instead of miscompiling."""
    if expr_has_post_effect(e):
        emit_unsupported(buf)
        return
    emit_expr(buf, e)


@compile
def emit_expr_stmt(buf: ptr[StringBuffer], e: ptr[Expr], indent: i32) -> void:
    """Emit an expression used as a statement (assignment, call, ++/--, comma).

    Side effects are linearized: emit_pre_effects writes assignments and prefix
    ++/-- as statements, emit_post_effects writes postfix updates afterwards. A
    pure residual (e.g. a bare call) is emitted on its own line so its value-less
    evaluation is preserved."""
    if e == nullptr:
        return
    match e.kind[0]:
        case ExprKind.Comma:
            # C comma operator: evaluate each side as its own statement.
            emit_expr_stmt(buf, e.lhs, indent)
            emit_expr_stmt(buf, e.rhs, indent)
        case ExprKind.Assign:
            emit_pre_effects(buf, e, indent)
            emit_post_effects(buf, e, indent)
        case ExprKind.UnaryOp:
            emit_pre_effects(buf, e, indent)
            if not expr_is_incdec(e):
                strbuf_push_indent(buf, indent)
                emit_expr(buf, e)
                strbuf_push_newline(buf)
            emit_post_effects(buf, e, indent)
        case ExprKind.PostfixOp:
            emit_pre_effects(buf, e, indent)
            emit_post_effects(buf, e, indent)
        case _:
            emit_pre_effects(buf, e, indent)
            strbuf_push_indent(buf, indent)
            emit_expr(buf, e)
            strbuf_push_newline(buf)
            emit_post_effects(buf, e, indent)


@compile
def emit_suite(buf: ptr[StringBuffer], body: ptr[Stmt], indent: i32) -> void:
    """Emit a statement as an indented suite; guarantees a non-empty block."""
    if body == nullptr:
        strbuf_push_indent(buf, indent)
        strbuf_push_cstr(buf, "pass")
        strbuf_push_newline(buf)
        return
    match body.kind[0]:
        case StmtKind.Block:
            if body.stmt_count == 0:
                strbuf_push_indent(buf, indent)
                strbuf_push_cstr(buf, "pass")
                strbuf_push_newline(buf)
            else:
                emit_block_stmts(buf, body.stmts, body.stmt_count, indent)
        case _:
            emit_stmt(buf, body, indent)


@compile
def emit_local_decl(buf: ptr[StringBuffer], s: ptr[Stmt], indent: i32) -> void:
    """Emit a local variable declaration: `name: Type = init`."""
    if s.expr != nullptr:
        emit_pre_effects(buf, s.expr, indent)
    strbuf_push_indent(buf, indent)
    strbuf_push_span(buf, s.decl_name)
    strbuf_push_cstr(buf, ": ")
    emit_qualtype(buf, s.decl_type)
    if s.expr != nullptr:
        strbuf_push_cstr(buf, " = ")
        emit_cond(buf, s.expr, indent)
    strbuf_push_newline(buf)
    if s.expr != nullptr:
        emit_post_effects(buf, s.expr, indent)


@compile
def emit_if(buf: ptr[StringBuffer], s: ptr[Stmt], indent: i32) -> void:
    """Emit if / elif / else, turning C `else if` chains into `elif`."""
    emit_pre_effects(buf, s.expr, indent)
    strbuf_push_indent(buf, indent)
    strbuf_push_cstr(buf, "if ")
    emit_cond(buf, s.expr, indent)
    strbuf_push_cstr(buf, ":")
    strbuf_push_newline(buf)
    emit_suite(buf, s.body, indent + 1)

    els: ptr[Stmt] = s.else_body
    done: bool = False
    while not done and els != nullptr:
        match els.kind[0]:
            case StmtKind.If:
                # An `else if` condition is only evaluated when prior ones are
                # false, so its side effects cannot be hoisted before the chain.
                strbuf_push_indent(buf, indent)
                strbuf_push_cstr(buf, "elif ")
                if expr_has_side_effects(els.expr):
                    emit_unsupported(buf)
                else:
                    emit_expr(buf, els.expr)
                strbuf_push_cstr(buf, ":")
                strbuf_push_newline(buf)
                emit_suite(buf, els.body, indent + 1)
                els = els.else_body
            case _:
                strbuf_push_indent(buf, indent)
                strbuf_push_cstr(buf, "else:")
                strbuf_push_newline(buf)
                emit_suite(buf, els, indent + 1)
                done = True


@compile
def emit_for(buf: ptr[StringBuffer], s: ptr[Stmt], indent: i32) -> void:
    """Lower C `for (init; cond; incr) body` to init + `while cond:` + body + incr."""
    # Init clause: declaration or expression
    if s.decl_type != nullptr:
        if s.init_expr != nullptr:
            emit_pre_effects(buf, s.init_expr, indent)
        strbuf_push_indent(buf, indent)
        strbuf_push_span(buf, s.decl_name)
        strbuf_push_cstr(buf, ": ")
        emit_qualtype(buf, s.decl_type)
        if s.init_expr != nullptr:
            strbuf_push_cstr(buf, " = ")
            emit_cond(buf, s.init_expr, indent)
        strbuf_push_newline(buf)
        if s.init_expr != nullptr:
            emit_post_effects(buf, s.init_expr, indent)
    elif s.init_expr != nullptr:
        emit_expr_stmt(buf, s.init_expr, indent)

    # Condition with side effects is re-evaluated each iteration, so it is
    # hoisted inside a `while True:` and turned into an early break.
    if s.expr != nullptr and expr_has_side_effects(s.expr):
        strbuf_push_indent(buf, indent)
        strbuf_push_cstr(buf, "while True:")
        strbuf_push_newline(buf)
        emit_pre_effects(buf, s.expr, indent + 1)
        strbuf_push_indent(buf, indent + 1)
        strbuf_push_cstr(buf, "if not (")
        emit_cond(buf, s.expr, indent + 1)
        strbuf_push_cstr(buf, "):")
        strbuf_push_newline(buf)
        strbuf_push_indent(buf, indent + 2)
        strbuf_push_cstr(buf, "break")
        strbuf_push_newline(buf)
    else:
        strbuf_push_indent(buf, indent)
        strbuf_push_cstr(buf, "while ")
        if s.expr != nullptr:
            emit_expr(buf, s.expr)
        else:
            strbuf_push_cstr(buf, "True")
        strbuf_push_cstr(buf, ":")
        strbuf_push_newline(buf)

    emit_suite(buf, s.body, indent + 1)
    if s.incr_expr != nullptr:
        emit_expr_stmt(buf, s.incr_expr, indent + 1)


@compile
def emit_switch(buf: ptr[StringBuffer], s: ptr[Stmt], indent: i32) -> void:
    """Lower C `switch` to PythoC `match`/`case`.

    The parser leaves a flat block of Case/Default markers interleaved with the
    statements that follow each label and the terminating `break`. We regroup
    those into `match` cases, dropping the `break` delimiters.
    """
    emit_pre_effects(buf, s.expr, indent)
    strbuf_push_indent(buf, indent)
    strbuf_push_cstr(buf, "match ")
    emit_cond(buf, s.expr, indent)
    strbuf_push_cstr(buf, ":")
    strbuf_push_newline(buf)

    body: ptr[Stmt] = s.body
    stmts: ptr[Stmt] = nullptr
    n: i32 = 0
    if body != nullptr:
        match body.kind[0]:
            case StmtKind.Block:
                stmts = body.stmts
                n = body.stmt_count
            case _:
                pass
    if stmts == nullptr or n == 0:
        strbuf_push_indent(buf, indent + 1)
        strbuf_push_cstr(buf, "pass")
        strbuf_push_newline(buf)
        return

    i: i32 = 0
    while i < n:
        cur: ptr[Stmt] = ptr(stmts[i])
        is_label: bool = False
        match cur.kind[0]:
            case StmtKind.Case:
                strbuf_push_indent(buf, indent + 1)
                strbuf_push_cstr(buf, "case ")
                emit_expr(buf, cur.expr)
                strbuf_push_cstr(buf, ":")
                strbuf_push_newline(buf)
                is_label = True
            case StmtKind.Default:
                strbuf_push_indent(buf, indent + 1)
                strbuf_push_cstr(buf, "case _:")
                strbuf_push_newline(buf)
                is_label = True
            case _:
                is_label = False

        if not is_label:
            # Statement preceding the first label is unreachable in C; skip it.
            i = i + 1
            continue

        wrote_any: bool = False
        if cur.body != nullptr:
            emit_stmt(buf, cur.body, indent + 2)
            wrote_any = True
        i = i + 1
        while i < n:
            nxt: ptr[Stmt] = ptr(stmts[i])
            done2: bool = False
            match nxt.kind[0]:
                case StmtKind.Case:
                    done2 = True
                case StmtKind.Default:
                    done2 = True
                case StmtKind.Break:
                    i = i + 1
                    done2 = True
                case _:
                    emit_stmt(buf, nxt, indent + 2)
                    wrote_any = True
                    i = i + 1
            if done2:
                break
        if not wrote_any:
            strbuf_push_indent(buf, indent + 2)
            strbuf_push_cstr(buf, "pass")
            strbuf_push_newline(buf)


# =============================================================================
# goto / label lowering
#
# C's unstructured goto is reconstructed onto PythoC's *scoped* label/goto/
# goto_end primitives. For a label L at the top level of a block:
#   - a forward goto (issued textually before L) becomes goto_end("L"): the
#     region from the earliest such goto up to L is wrapped in
#     `with label("L"):`, whose end block sits exactly at L's position.
#   - a backward goto (issued at/after L) becomes goto("L"): the region from L
#     through the last such goto is wrapped in `with label("L"):`, whose begin
#     block sits at L.
# A label is left unlowered (its gotos emit __pcc_unsupported__) when it is
# targeted in both directions, when its scope would partially overlap another
# (irreducible control flow), or when its scope would enclose a top-level
# declaration -- PythoC scopes locals declared inside a `with`, so post-label
# code could no longer see them. Rejecting loudly avoids miscompilation.
# =============================================================================

GOTO_FORWARD = 1
GOTO_BACKWARD = 2

MAX_BLOCK_LABELS = 128


@compile
def stmt_has_goto_to(s: ptr[Stmt], name: Span) -> i8:
    """Whether a statement subtree contains a `goto name`."""
    if s == nullptr:
        return 0
    match s.kind[0]:
        case StmtKind.Goto:
            if span_eq(s.label, name):
                return 1
            return 0
        case _:
            pass
    if stmt_has_goto_to(s.body, name) != 0:
        return 1
    if stmt_has_goto_to(s.else_body, name) != 0:
        return 1
    if s.stmts != nullptr:
        i: i32 = 0
        while i < s.stmt_count:
            if stmt_has_goto_to(ptr(s.stmts[i]), name) != 0:
                return 1
            i = i + 1
    return 0


@compile
def tag_gotos_to(s: ptr[Stmt], name: Span, direction: i8) -> void:
    """Tag every `goto name` in the subtree with the resolved direction."""
    if s == nullptr:
        return
    match s.kind[0]:
        case StmtKind.Goto:
            if span_eq(s.label, name):
                s.goto_dir = direction
            return
        case _:
            pass
    tag_gotos_to(s.body, name, direction)
    tag_gotos_to(s.else_body, name, direction)
    if s.stmts != nullptr:
        i: i32 = 0
        while i < s.stmt_count:
            tag_gotos_to(ptr(s.stmts[i]), name, direction)
            i = i + 1


@compile
def stmt_decl_in_scope(s: ptr[Stmt]) -> i8:
    """Whether a block element directly introduces a function-scoped local.

    Only a direct declaration matters: declarations nested inside an inner
    if/loop/block already live in their own PythoC scope, so an enclosing
    `with label` does not change their (already restricted) visibility.
    """
    if s == nullptr:
        return 0
    match s.kind[0]:
        case StmtKind.Decl:
            return 1
        case StmtKind.Label:
            return stmt_decl_in_scope(s.body)
        case _:
            return 0


@compile
def emit_block_elem(buf: ptr[StringBuffer], s: ptr[Stmt], indent: i32) -> void:
    """Emit one top-level block element during label lowering.

    A label contributes no code of its own; the scope wrapper already encodes
    its position, so only its labelled statement is emitted.
    """
    match s.kind[0]:
        case StmtKind.Label:
            if s.body != nullptr:
                emit_stmt(buf, s.body, indent)
        case _:
            emit_stmt(buf, s, indent)


@compile
def emit_block_stmts(buf: ptr[StringBuffer], stmts: ptr[Stmt], n: i32, indent: i32) -> void:
    """Emit a block's statement list, reconstructing goto/label control flow."""
    # Fast path: blocks without labels emit straight through.
    has_label: i8 = 0
    i: i32 = 0
    while i < n:
        match stmts[i].kind[0]:
            case StmtKind.Label:
                has_label = 1
            case _:
                pass
        i = i + 1
    if has_label == 0:
        i = 0
        while i < n:
            emit_stmt(buf, ptr(stmts[i]), indent)
            i = i + 1
        return

    # Classify each top-level label and compute its wrap interval [lo, hi).
    lab_idx: array[i32, MAX_BLOCK_LABELS]
    lab_lo: array[i32, MAX_BLOCK_LABELS]
    lab_hi: array[i32, MAX_BLOCK_LABELS]
    lab_dir: array[i8, MAX_BLOCK_LABELS]
    nlab: i32 = 0
    reducible: i8 = 1

    i = 0
    while i < n:
        is_lab: i8 = 0
        match stmts[i].kind[0]:
            case StmtKind.Label:
                is_lab = 1
            case _:
                pass
        if is_lab != 0:
            if nlab >= MAX_BLOCK_LABELS:
                reducible = 0
            else:
                name: Span = stmts[i].label
                min_g: i32 = -1
                max_g: i32 = -1
                t: i32 = 0
                while t < n:
                    if stmt_has_goto_to(ptr(stmts[t]), name) != 0:
                        if min_g < 0:
                            min_g = t
                        max_g = t
                    t = t + 1
                lab_idx[nlab] = i
                if min_g < 0:
                    lab_dir[nlab] = 0
                    lab_lo[nlab] = i
                    lab_hi[nlab] = i
                elif max_g < i:
                    lab_dir[nlab] = GOTO_FORWARD
                    lab_lo[nlab] = min_g
                    lab_hi[nlab] = i
                elif min_g >= i:
                    lab_dir[nlab] = GOTO_BACKWARD
                    lab_lo[nlab] = i
                    lab_hi[nlab] = max_g + 1
                else:
                    reducible = 0
                    lab_dir[nlab] = 0
                    lab_lo[nlab] = i
                    lab_hi[nlab] = i
                nlab = nlab + 1
        i = i + 1

    # Expand same-direction scopes so an overlapping chain nests rather than
    # crosses. Forward cleanup labels (goto a; goto b; ... a:; b:) end at
    # successive labels but their goto regions interleave; widening each scope's
    # start to the chain minimum makes the later (outer) label enclose earlier
    # ones. Backward loop labels are the mirror image on the end bound.
    if reducible != 0:
        run_min: i32 = 0
        prev_hi: i32 = -1
        fj: i32 = 0
        while fj < nlab:
            if lab_dir[fj] == GOTO_FORWARD:
                g: i32 = lab_lo[fj]
                if g >= prev_hi:
                    run_min = g
                elif g < run_min:
                    run_min = g
                lab_lo[fj] = run_min
                prev_hi = lab_idx[fj]
            fj = fj + 1
        run_max: i32 = 0
        prev_lo: i32 = n + 1
        bj: i32 = nlab - 1
        while bj >= 0:
            if lab_dir[bj] == GOTO_BACKWARD:
                h: i32 = lab_hi[bj]
                if h <= prev_lo:
                    run_max = h
                elif h > run_max:
                    run_max = h
                lab_hi[bj] = run_max
                prev_lo = lab_idx[bj]
            bj = bj - 1

    # Reject scopes that would enclose a top-level declaration.
    if reducible != 0:
        j: i32 = 0
        while j < nlab:
            if lab_dir[j] != 0:
                k: i32 = lab_lo[j]
                while k < lab_hi[j]:
                    if stmt_decl_in_scope(ptr(stmts[k])) != 0:
                        reducible = 0
                    k = k + 1
            j = j + 1

    # Reject partially overlapping (non-laminar) scopes.
    if reducible != 0:
        a: i32 = 0
        while a < nlab:
            if lab_dir[a] != 0:
                b: i32 = a + 1
                while b < nlab:
                    if lab_dir[b] != 0:
                        la: i32 = lab_lo[a]
                        ha: i32 = lab_hi[a]
                        lb: i32 = lab_lo[b]
                        hb: i32 = lab_hi[b]
                        intersect: i8 = 0
                        if la < hb and lb < ha:
                            intersect = 1
                        contained: i8 = 0
                        if (la <= lb and hb <= ha) or (lb <= la and ha <= hb):
                            contained = 1
                        if intersect != 0 and contained == 0:
                            reducible = 0
                    b = b + 1
            a = a + 1

    if reducible == 0:
        # Loud fallback: labels degrade to comments, gotos to __pcc_unsupported__.
        i = 0
        while i < n:
            emit_stmt(buf, ptr(stmts[i]), indent)
            i = i + 1
        return

    # Resolve goto directions on the AST before emitting.
    jj: i32 = 0
    while jj < nlab:
        if lab_dir[jj] != 0:
            nm: Span = stmts[lab_idx[jj]].label
            tt: i32 = 0
            while tt < n:
                tag_gotos_to(ptr(stmts[tt]), nm, lab_dir[jj])
                tt = tt + 1
        jj = jj + 1

    # Emit with nested `with label()` scopes via an open/close stack walk.
    stack_hi: array[i32, MAX_BLOCK_LABELS]
    opened: array[i8, MAX_BLOCK_LABELS]
    z: i32 = 0
    while z < nlab:
        opened[z] = 0
        z = z + 1
    depth: i32 = 0
    pos: i32 = 0
    while pos <= n:
        while depth > 0 and stack_hi[depth - 1] == pos:
            depth = depth - 1
        if pos == n:
            pos = pos + 1
            continue
        opening: i8 = 1
        while opening != 0:
            best: i32 = -1
            best_hi: i32 = -1
            jb: i32 = 0
            while jb < nlab:
                if lab_dir[jb] != 0 and opened[jb] == 0 and lab_lo[jb] == pos:
                    if lab_hi[jb] > best_hi:
                        best_hi = lab_hi[jb]
                        best = jb
                jb = jb + 1
            if best < 0:
                opening = 0
            else:
                strbuf_push_indent(buf, indent + depth)
                strbuf_push_cstr(buf, "with label(\"")
                strbuf_push_span(buf, stmts[lab_idx[best]].label)
                strbuf_push_cstr(buf, "\"):")
                strbuf_push_newline(buf)
                opened[best] = 1
                stack_hi[depth] = lab_hi[best]
                depth = depth + 1
        emit_block_elem(buf, ptr(stmts[pos]), indent + depth)
        pos = pos + 1


@compile
def emit_stmt(buf: ptr[StringBuffer], s: ptr[Stmt], indent: i32) -> void:
    """Emit a single C statement as PythoC source lines at the given indent."""
    if s == nullptr:
        return
    match s.kind[0]:
        case StmtKind.Empty:
            strbuf_push_indent(buf, indent)
            strbuf_push_cstr(buf, "pass")
            strbuf_push_newline(buf)
        case StmtKind.Expr:
            emit_expr_stmt(buf, s.expr, indent)
        case StmtKind.Return:
            if s.expr != nullptr:
                emit_pre_effects(buf, s.expr, indent)
            strbuf_push_indent(buf, indent)
            strbuf_push_cstr(buf, "return")
            if s.expr != nullptr:
                strbuf_push_char(buf, 32)  # ' '
                emit_cond(buf, s.expr, indent)
            strbuf_push_newline(buf)
        case StmtKind.Block:
            if s.stmt_count == 0:
                strbuf_push_indent(buf, indent)
                strbuf_push_cstr(buf, "pass")
                strbuf_push_newline(buf)
            else:
                emit_block_stmts(buf, s.stmts, s.stmt_count, indent)
        case StmtKind.If:
            emit_if(buf, s, indent)
        case StmtKind.While:
            if expr_has_side_effects(s.expr):
                strbuf_push_indent(buf, indent)
                strbuf_push_cstr(buf, "while True:")
                strbuf_push_newline(buf)
                emit_pre_effects(buf, s.expr, indent + 1)
                strbuf_push_indent(buf, indent + 1)
                strbuf_push_cstr(buf, "if not (")
                emit_cond(buf, s.expr, indent + 1)
                strbuf_push_cstr(buf, "):")
                strbuf_push_newline(buf)
                strbuf_push_indent(buf, indent + 2)
                strbuf_push_cstr(buf, "break")
                strbuf_push_newline(buf)
                emit_suite(buf, s.body, indent + 1)
            else:
                strbuf_push_indent(buf, indent)
                strbuf_push_cstr(buf, "while ")
                emit_expr(buf, s.expr)
                strbuf_push_cstr(buf, ":")
                strbuf_push_newline(buf)
                emit_suite(buf, s.body, indent + 1)
        case StmtKind.DoWhile:
            strbuf_push_indent(buf, indent)
            strbuf_push_cstr(buf, "while True:")
            strbuf_push_newline(buf)
            emit_suite(buf, s.body, indent + 1)
            emit_pre_effects(buf, s.expr, indent + 1)
            strbuf_push_indent(buf, indent + 1)
            strbuf_push_cstr(buf, "if not (")
            emit_cond(buf, s.expr, indent + 1)
            strbuf_push_cstr(buf, "):")
            strbuf_push_newline(buf)
            strbuf_push_indent(buf, indent + 2)
            strbuf_push_cstr(buf, "break")
            strbuf_push_newline(buf)
        case StmtKind.For:
            emit_for(buf, s, indent)
        case StmtKind.Break:
            strbuf_push_indent(buf, indent)
            strbuf_push_cstr(buf, "break")
            strbuf_push_newline(buf)
        case StmtKind.Continue:
            strbuf_push_indent(buf, indent)
            strbuf_push_cstr(buf, "continue")
            strbuf_push_newline(buf)
        case StmtKind.Switch:
            emit_switch(buf, s, indent)
        case StmtKind.Decl:
            emit_local_decl(buf, s, indent)
        case StmtKind.Label:
            # No general goto support yet: emit the labeled statement, drop the label.
            strbuf_push_indent(buf, indent)
            strbuf_push_cstr(buf, "# label ")
            strbuf_push_span(buf, s.label)
            strbuf_push_newline(buf)
            emit_stmt(buf, s.body, indent)
        case StmtKind.Goto:
            strbuf_push_indent(buf, indent)
            if s.goto_dir == GOTO_FORWARD:
                strbuf_push_cstr(buf, "goto_end(\"")
                strbuf_push_span(buf, s.label)
                strbuf_push_cstr(buf, "\")")
            elif s.goto_dir == GOTO_BACKWARD:
                strbuf_push_cstr(buf, "goto(\"")
                strbuf_push_span(buf, s.label)
                strbuf_push_cstr(buf, "\")")
            else:
                emit_unsupported(buf)
            strbuf_push_newline(buf)
        case _:
            strbuf_push_indent(buf, indent)
            emit_unsupported(buf)
            strbuf_push_newline(buf)


# =============================================================================
# State-machine lowering for non-structural goto
# =============================================================================
#
# PythoC has no unstructured goto, only scoped label/goto/goto_end. The block
# reconstruction above (emit_block_stmts) handles laminar cases; when a function
# has gotos it cannot represent (labels inside a switch body, cross-construct
# jumps, both-direction targets), the whole function body is re-lowered into a
# `__pcc_pc` state machine: every label and synthesized control join becomes a
# numbered basic block in a `while True:` dispatch, and every goto/branch/loop
# edge becomes `__pcc_pc = <state>; continue`. Constructs with no goto/label and
# no loop-escaping break/continue stay structured (emitted verbatim) so output
# stays readable; only goto-entangled constructs dissolve into states.

MAX_SM_BLOCKS = 2048


@compile
class SMCtx:
    """Mutable context for state-machine lowering (heap-backed block buffers)."""
    blocks: ptr[StringBuffer]      # array[MAX_SM_BLOCKS] of per-state text
    nblocks: i32
    lab_names: ptr[Span]           # label name -> state id
    lab_states: ptr[i32]
    nlabels: i32
    h_names: ptr[Span]             # hoisted local decls (name + type)
    h_types: ptr[ptr[QualType]]
    nhoist: i32
    body_indent: i32               # indent of statements inside a dispatch arm
    failed: i8


@compile
def sm_alloc_block(c: ptr[SMCtx]) -> i32:
    """Reserve and initialize a fresh state block; flag overflow as failure."""
    if c.nblocks >= MAX_SM_BLOCKS:
        c.failed = 1
        return 0
    bid: i32 = c.nblocks
    strbuf_init(ptr(c.blocks[bid]))
    c.nblocks = bid + 1
    return bid


@compile
def sm_label_state(c: ptr[SMCtx], name: Span) -> i32:
    """Resolve a label name to its state id; a missing target is a failure."""
    i: i32 = 0
    while i < c.nlabels:
        if span_eq(c.lab_names[i], name):
            return c.lab_states[i]
        i = i + 1
    c.failed = 1
    return 0


@compile
def sm_emit_goto(c: ptr[SMCtx], blk: i32, state: i32, ind: i32) -> void:
    """Emit `__pcc_pc = state; continue` into block `blk` at indent `ind`."""
    b: ptr[StringBuffer] = ptr(c.blocks[blk])
    strbuf_push_indent(b, ind)
    strbuf_push_cstr(b, "__pcc_pc = ")
    strbuf_push_i32(b, state)
    strbuf_push_newline(b)
    strbuf_push_indent(b, ind)
    strbuf_push_cstr(b, "continue")
    strbuf_push_newline(b)


@compile
def _sc_flags(s: ptr[Stmt]) -> i32:
    """Self-containment flags: bit0 label/goto, bit1 escaping break,
    bit2 escaping continue. A loop bounds break and continue from its body; a
    switch bounds break only. A region is structurally emittable inside the
    state machine iff its flags are 0 (no goto/label, no break/continue that
    would target a dissolved enclosing loop)."""
    if s == nullptr:
        return 0
    match s.kind[0]:
        case StmtKind.Goto:
            return 1
        case StmtKind.Label:
            return 1
        case StmtKind.Break:
            return 2
        case StmtKind.Continue:
            return 4
        case StmtKind.While:
            return _sc_flags(s.body) & 1
        case StmtKind.DoWhile:
            return _sc_flags(s.body) & 1
        case StmtKind.For:
            return _sc_flags(s.body) & 1
        case StmtKind.Switch:
            f: i32 = _sc_flags(s.body)
            return (f & 1) | (f & 4)
        case StmtKind.If:
            return _sc_flags(s.body) | _sc_flags(s.else_body)
        case StmtKind.Block:
            r: i32 = 0
            if s.stmts != nullptr:
                i: i32 = 0
                while i < s.stmt_count:
                    r = r | _sc_flags(ptr(s.stmts[i]))
                    i = i + 1
            return r
        case _:
            return 0


@compile
def region_self_contained(s: ptr[Stmt]) -> i8:
    """Whether a statement can be emitted structurally inside the state machine
    (no goto/label, no loop-escaping break/continue)."""
    if _sc_flags(s) == 0:
        return 1
    return 0


@compile
def sm_add_hoist(c: ptr[SMCtx], name: Span, ty: ptr[QualType]) -> void:
    """Record a local declaration to hoist to function entry (deduped by name)."""
    if span_is_empty(name):
        return
    i: i32 = 0
    while i < c.nhoist:
        if span_eq(c.h_names[i], name):
            return
        i = i + 1
    if c.nhoist >= MAX_SM_BLOCKS:
        c.failed = 1
        return
    c.h_names[c.nhoist] = name
    c.h_types[c.nhoist] = ty
    c.nhoist = c.nhoist + 1


@compile
def sm_collect_hoist(c: ptr[SMCtx], s: ptr[Stmt]) -> void:
    """Collect local declarations that live in dissolved (state) regions, so
    they can be declared once at function entry and shared across states.
    Declarations inside self-contained regions stay local (not hoisted)."""
    if s == nullptr:
        return
    if region_self_contained(s) != 0:
        return
    match s.kind[0]:
        case StmtKind.Decl:
            sm_add_hoist(c, s.decl_name, s.decl_type)
        case StmtKind.For:
            if s.decl_type != nullptr:
                sm_add_hoist(c, s.decl_name, s.decl_type)
            sm_collect_hoist(c, s.body)
        case StmtKind.If:
            sm_collect_hoist(c, s.body)
            sm_collect_hoist(c, s.else_body)
        case StmtKind.While:
            sm_collect_hoist(c, s.body)
        case StmtKind.DoWhile:
            sm_collect_hoist(c, s.body)
        case StmtKind.Switch:
            sm_collect_hoist(c, s.body)
        case StmtKind.Label:
            sm_collect_hoist(c, s.body)
        case StmtKind.Block:
            if s.stmts != nullptr:
                i: i32 = 0
                while i < s.stmt_count:
                    sm_collect_hoist(c, ptr(s.stmts[i]))
                    i = i + 1
        case _:
            pass


@compile
def sm_collect_labels(c: ptr[SMCtx], s: ptr[Stmt]) -> void:
    """Pre-assign a state id to every label in the function (any nesting)."""
    if s == nullptr or c.failed != 0:
        return
    match s.kind[0]:
        case StmtKind.Label:
            st: i32 = sm_alloc_block(c)
            if c.nlabels < MAX_SM_BLOCKS:
                c.lab_names[c.nlabels] = s.label
                c.lab_states[c.nlabels] = st
                c.nlabels = c.nlabels + 1
            else:
                c.failed = 1
            sm_collect_labels(c, s.body)
            return
        case _:
            pass
    sm_collect_labels(c, s.body)
    sm_collect_labels(c, s.else_body)
    if s.stmts != nullptr:
        i: i32 = 0
        while i < s.stmt_count:
            sm_collect_labels(c, ptr(s.stmts[i]))
            i = i + 1


@compile
def sm_emit_decl_assign(c: ptr[SMCtx], cur: i32, s: ptr[Stmt]) -> void:
    """Emit a hoisted declaration's initializer as a plain assignment."""
    if s.expr == nullptr:
        return
    b: ptr[StringBuffer] = ptr(c.blocks[cur])
    emit_pre_effects(b, s.expr, c.body_indent)
    strbuf_push_indent(b, c.body_indent)
    strbuf_push_span(b, s.decl_name)
    strbuf_push_cstr(b, " = ")
    emit_cond(b, s.expr, c.body_indent)
    strbuf_push_newline(b)
    emit_post_effects(b, s.expr, c.body_indent)


@compile
def sm_lower_seq(c: ptr[SMCtx], stmts: ptr[Stmt], n: i32,
                 cur: i32, brk: i32, cont: i32) -> i32:
    """Lower a statement sequence, threading the current block; returns the
    block where control falls through after the sequence (-1 if terminated)."""
    i: i32 = 0
    while i < n:
        if c.failed != 0:
            return cur
        if cur < 0:
            # Unreachable code after a terminator still needs a sink block so a
            # following label has a (never-taken) fallthrough source.
            cur = sm_alloc_block(c)
        cur = sm_lower_one(c, ptr(stmts[i]), cur, brk, cont)
        i = i + 1
    return cur


@compile
def sm_lower_one(c: ptr[SMCtx], s: ptr[Stmt],
                 cur: i32, brk: i32, cont: i32) -> i32:
    """Lower one statement into the state machine; returns the fallthrough
    block (-1 if the statement terminates control flow)."""
    if s == nullptr or c.failed != 0:
        return cur
    match s.kind[0]:
        case StmtKind.Label:
            st: i32 = sm_label_state(c, s.label)
            sm_emit_goto(c, cur, st, c.body_indent)
            return sm_lower_one(c, s.body, st, brk, cont)
        case StmtKind.Goto:
            gst: i32 = sm_label_state(c, s.label)
            sm_emit_goto(c, cur, gst, c.body_indent)
            return -1
        case StmtKind.Break:
            if brk < 0:
                c.failed = 1
                return -1
            sm_emit_goto(c, cur, brk, c.body_indent)
            return -1
        case StmtKind.Continue:
            if cont < 0:
                c.failed = 1
                return -1
            sm_emit_goto(c, cur, cont, c.body_indent)
            return -1
        case StmtKind.Return:
            rb: ptr[StringBuffer] = ptr(c.blocks[cur])
            if s.expr != nullptr:
                emit_pre_effects(rb, s.expr, c.body_indent)
            strbuf_push_indent(rb, c.body_indent)
            strbuf_push_cstr(rb, "return")
            if s.expr != nullptr:
                strbuf_push_char(rb, 32)  # ' '
                emit_cond(rb, s.expr, c.body_indent)
            strbuf_push_newline(rb)
            return -1
        case StmtKind.Decl:
            sm_emit_decl_assign(c, cur, s)
            return cur
        case StmtKind.Expr:
            emit_expr_stmt(ptr(c.blocks[cur]), s.expr, c.body_indent)
            return cur
        case StmtKind.Empty:
            return cur
        case StmtKind.Block:
            if region_self_contained(s) != 0:
                emit_stmt(ptr(c.blocks[cur]), s, c.body_indent)
                return cur
            return sm_lower_seq(c, s.stmts, s.stmt_count, cur, brk, cont)
        case StmtKind.If:
            if region_self_contained(s) != 0:
                emit_stmt(ptr(c.blocks[cur]), s, c.body_indent)
                return cur
            return sm_lower_if(c, s, cur, brk, cont)
        case StmtKind.While:
            if region_self_contained(s) != 0:
                emit_stmt(ptr(c.blocks[cur]), s, c.body_indent)
                return cur
            return sm_lower_while(c, s, cur, brk, cont)
        case StmtKind.DoWhile:
            if region_self_contained(s) != 0:
                emit_stmt(ptr(c.blocks[cur]), s, c.body_indent)
                return cur
            return sm_lower_dowhile(c, s, cur, brk, cont)
        case StmtKind.For:
            if region_self_contained(s) != 0:
                emit_stmt(ptr(c.blocks[cur]), s, c.body_indent)
                return cur
            return sm_lower_for(c, s, cur, brk, cont)
        case StmtKind.Switch:
            if region_self_contained(s) != 0:
                emit_stmt(ptr(c.blocks[cur]), s, c.body_indent)
                return cur
            return sm_lower_switch(c, s, cur, brk, cont)
        case _:
            c.failed = 1
            return cur


@compile
def sm_lower_if(c: ptr[SMCtx], s: ptr[Stmt],
                cur: i32, brk: i32, cont: i32) -> i32:
    """Dissolve an if/else into branch-target states joined at a merge state."""
    join: i32 = sm_alloc_block(c)
    then_e: i32 = sm_alloc_block(c)
    else_e: i32 = join
    if s.else_body != nullptr:
        else_e = sm_alloc_block(c)
    b: ptr[StringBuffer] = ptr(c.blocks[cur])
    emit_pre_effects(b, s.expr, c.body_indent)
    strbuf_push_indent(b, c.body_indent)
    strbuf_push_cstr(b, "if ")
    emit_cond(b, s.expr, c.body_indent)
    strbuf_push_cstr(b, ":")
    strbuf_push_newline(b)
    sm_emit_goto(c, cur, then_e, c.body_indent + 1)
    strbuf_push_indent(b, c.body_indent)
    strbuf_push_cstr(b, "else:")
    strbuf_push_newline(b)
    sm_emit_goto(c, cur, else_e, c.body_indent + 1)
    te: i32 = sm_lower_one(c, s.body, then_e, brk, cont)
    if te >= 0:
        sm_emit_goto(c, te, join, c.body_indent)
    if s.else_body != nullptr:
        ee: i32 = sm_lower_one(c, s.else_body, else_e, brk, cont)
        if ee >= 0:
            sm_emit_goto(c, ee, join, c.body_indent)
    return join


@compile
def sm_lower_while(c: ptr[SMCtx], s: ptr[Stmt],
                   cur: i32, brk: i32, cont: i32) -> i32:
    """Dissolve a while loop into header/body/join states."""
    header: i32 = sm_alloc_block(c)
    body_e: i32 = sm_alloc_block(c)
    join: i32 = sm_alloc_block(c)
    sm_emit_goto(c, cur, header, c.body_indent)
    hb: ptr[StringBuffer] = ptr(c.blocks[header])
    emit_pre_effects(hb, s.expr, c.body_indent)
    strbuf_push_indent(hb, c.body_indent)
    strbuf_push_cstr(hb, "if ")
    emit_cond(hb, s.expr, c.body_indent)
    strbuf_push_cstr(hb, ":")
    strbuf_push_newline(hb)
    sm_emit_goto(c, header, body_e, c.body_indent + 1)
    strbuf_push_indent(hb, c.body_indent)
    strbuf_push_cstr(hb, "else:")
    strbuf_push_newline(hb)
    sm_emit_goto(c, header, join, c.body_indent + 1)
    be: i32 = sm_lower_one(c, s.body, body_e, join, header)
    if be >= 0:
        sm_emit_goto(c, be, header, c.body_indent)
    return join


@compile
def sm_lower_dowhile(c: ptr[SMCtx], s: ptr[Stmt],
                     cur: i32, brk: i32, cont: i32) -> i32:
    """Dissolve a do/while loop into body/cond/join states."""
    body_e: i32 = sm_alloc_block(c)
    cond_b: i32 = sm_alloc_block(c)
    join: i32 = sm_alloc_block(c)
    sm_emit_goto(c, cur, body_e, c.body_indent)
    be: i32 = sm_lower_one(c, s.body, body_e, join, cond_b)
    if be >= 0:
        sm_emit_goto(c, be, cond_b, c.body_indent)
    cb: ptr[StringBuffer] = ptr(c.blocks[cond_b])
    emit_pre_effects(cb, s.expr, c.body_indent)
    strbuf_push_indent(cb, c.body_indent)
    strbuf_push_cstr(cb, "if ")
    emit_cond(cb, s.expr, c.body_indent)
    strbuf_push_cstr(cb, ":")
    strbuf_push_newline(cb)
    sm_emit_goto(c, cond_b, body_e, c.body_indent + 1)
    strbuf_push_indent(cb, c.body_indent)
    strbuf_push_cstr(cb, "else:")
    strbuf_push_newline(cb)
    sm_emit_goto(c, cond_b, join, c.body_indent + 1)
    return join


@compile
def sm_lower_for(c: ptr[SMCtx], s: ptr[Stmt],
                 cur: i32, brk: i32, cont: i32) -> i32:
    """Dissolve a for loop into header/body/incr/join states (loop var hoisted)."""
    b: ptr[StringBuffer] = ptr(c.blocks[cur])
    if s.decl_type != nullptr:
        if s.init_expr != nullptr:
            emit_pre_effects(b, s.init_expr, c.body_indent)
            strbuf_push_indent(b, c.body_indent)
            strbuf_push_span(b, s.decl_name)
            strbuf_push_cstr(b, " = ")
            emit_cond(b, s.init_expr, c.body_indent)
            strbuf_push_newline(b)
            emit_post_effects(b, s.init_expr, c.body_indent)
    elif s.init_expr != nullptr:
        emit_expr_stmt(b, s.init_expr, c.body_indent)
    header: i32 = sm_alloc_block(c)
    body_e: i32 = sm_alloc_block(c)
    incr_b: i32 = sm_alloc_block(c)
    join: i32 = sm_alloc_block(c)
    sm_emit_goto(c, cur, header, c.body_indent)
    hb: ptr[StringBuffer] = ptr(c.blocks[header])
    if s.expr != nullptr:
        emit_pre_effects(hb, s.expr, c.body_indent)
        strbuf_push_indent(hb, c.body_indent)
        strbuf_push_cstr(hb, "if ")
        emit_cond(hb, s.expr, c.body_indent)
        strbuf_push_cstr(hb, ":")
        strbuf_push_newline(hb)
        sm_emit_goto(c, header, body_e, c.body_indent + 1)
        strbuf_push_indent(hb, c.body_indent)
        strbuf_push_cstr(hb, "else:")
        strbuf_push_newline(hb)
        sm_emit_goto(c, header, join, c.body_indent + 1)
    else:
        sm_emit_goto(c, header, body_e, c.body_indent)
    be: i32 = sm_lower_one(c, s.body, body_e, join, incr_b)
    if be >= 0:
        sm_emit_goto(c, be, incr_b, c.body_indent)
    ib: ptr[StringBuffer] = ptr(c.blocks[incr_b])
    if s.incr_expr != nullptr:
        emit_expr_stmt(ib, s.incr_expr, c.body_indent)
    sm_emit_goto(c, incr_b, header, c.body_indent)
    return join


@compile
def sm_lower_switch(c: ptr[SMCtx], s: ptr[Stmt],
                    cur: i32, brk: i32, cont: i32) -> i32:
    """Dissolve a switch into a value-dispatch block plus per-case states,
    preserving C fall-through and break (-> join). `continue` keeps targeting
    the enclosing loop."""
    join: i32 = sm_alloc_block(c)
    body: ptr[Stmt] = s.body
    stmts: ptr[Stmt] = nullptr
    m: i32 = 0
    if body != nullptr:
        match body.kind[0]:
            case StmtKind.Block:
                stmts = body.stmts
                m = body.stmt_count
            case _:
                pass
    if stmts == nullptr or m == 0:
        sm_emit_goto(c, cur, join, c.body_indent)
        return join

    b: ptr[StringBuffer] = ptr(c.blocks[cur])
    emit_pre_effects(b, s.expr, c.body_indent)

    # Single pass: the dispatch chain accumulates in block `cur` while each case
    # body is lowered into its own state block (a different buffer), so the
    # interleaving is safe.
    default_state: i32 = -1
    first: i8 = 1
    cur2: i32 = -1
    i: i32 = 0
    while i < m:
        if c.failed != 0:
            return join
        cs: ptr[Stmt] = ptr(stmts[i])
        is_case: i8 = 0
        is_default: i8 = 0
        match cs.kind[0]:
            case StmtKind.Case:
                is_case = 1
            case StmtKind.Default:
                is_default = 1
            case _:
                pass
        if is_case != 0:
            cst: i32 = sm_alloc_block(c)
            strbuf_push_indent(b, c.body_indent)
            if first != 0:
                strbuf_push_cstr(b, "if (")
            else:
                strbuf_push_cstr(b, "elif (")
            first = 0
            emit_cond(b, s.expr, c.body_indent)
            strbuf_push_cstr(b, ") == ")
            emit_expr(b, cs.expr)
            strbuf_push_cstr(b, ":")
            strbuf_push_newline(b)
            sm_emit_goto(c, cur, cst, c.body_indent + 1)
            if cur2 >= 0:
                sm_emit_goto(c, cur2, cst, c.body_indent)
            # A Case/Default carries its labelled statement in `body`; the rest
            # of the case follows as siblings in the switch-body block.
            cur2 = sm_lower_one(c, cs.body, cst, join, cont)
        elif is_default != 0:
            dst: i32 = sm_alloc_block(c)
            default_state = dst
            if cur2 >= 0:
                sm_emit_goto(c, cur2, dst, c.body_indent)
            cur2 = sm_lower_one(c, cs.body, dst, join, cont)
        else:
            if cur2 < 0:
                cur2 = sm_alloc_block(c)
            cur2 = sm_lower_one(c, cs, cur2, join, cont)
        i = i + 1

    strbuf_push_indent(b, c.body_indent)
    strbuf_push_cstr(b, "else:")
    strbuf_push_newline(b)
    if default_state >= 0:
        sm_emit_goto(c, cur, default_state, c.body_indent + 1)
    else:
        sm_emit_goto(c, cur, join, c.body_indent + 1)

    if cur2 >= 0:
        sm_emit_goto(c, cur2, join, c.body_indent)
    return join


@compile
def emit_state_machine(buf: ptr[StringBuffer], body: ptr[Stmt],
                       indent: i32, ret_qt: ptr[QualType]) -> i8:
    """Lower a whole function body to a `__pcc_pc` state machine. Returns 1 on
    success (text written to `buf`), 0 if an unsupported shape was hit (caller
    should fall back); on failure nothing is appended."""
    if body == nullptr:
        return 0

    ctx: SMCtx
    c: ptr[SMCtx] = ptr(ctx)
    c.blocks = ptr[StringBuffer](effect.mem.malloc(sizeof(StringBuffer) * MAX_SM_BLOCKS))
    c.lab_names = ptr[Span](effect.mem.malloc(sizeof(Span) * MAX_SM_BLOCKS))
    c.lab_states = ptr[i32](effect.mem.malloc(sizeof(i32) * MAX_SM_BLOCKS))
    c.h_names = ptr[Span](effect.mem.malloc(sizeof(Span) * MAX_SM_BLOCKS))
    c.h_types = ptr[ptr[QualType]](effect.mem.malloc(sizeof(ptr[QualType]) * MAX_SM_BLOCKS))
    c.nblocks = 0
    c.nlabels = 0
    c.nhoist = 0
    c.body_indent = indent + 2
    c.failed = 0

    entry: i32 = sm_alloc_block(c)
    sm_collect_labels(c, body)
    sm_collect_hoist(c, body)

    exit_blk: i32 = -1
    if body.stmts != nullptr:
        exit_blk = sm_lower_seq(c, body.stmts, body.stmt_count, entry, -1, -1)
    if exit_blk >= 0 and c.failed == 0:
        # Control fell off the function end: emit a default return so the
        # dispatch loop has no fall-through path (C UB for non-void; harmless).
        eb: ptr[StringBuffer] = ptr(c.blocks[exit_blk])
        strbuf_push_indent(eb, c.body_indent)
        strbuf_push_cstr(eb, "return")
        if _qualtype_is_void(ret_qt) == 0:
            strbuf_push_char(eb, 32)  # ' '
            emit_zero_value(eb, ret_qt)
        strbuf_push_newline(eb)

    ok: i8 = 1
    if c.failed != 0:
        ok = 0
    else:
        # Prologue: hoisted locals, then the dispatch loop.
        h: i32 = 0
        while h < c.nhoist:
            strbuf_push_indent(buf, indent)
            strbuf_push_span(buf, c.h_names[h])
            strbuf_push_cstr(buf, ": ")
            emit_qualtype(buf, c.h_types[h])
            strbuf_push_newline(buf)
            h = h + 1
        strbuf_push_indent(buf, indent)
        strbuf_push_cstr(buf, "__pcc_pc: i32 = 0")
        strbuf_push_newline(buf)
        strbuf_push_indent(buf, indent)
        strbuf_push_cstr(buf, "while True:")
        strbuf_push_newline(buf)
        k: i32 = 0
        while k < c.nblocks:
            strbuf_push_indent(buf, indent + 1)
            if k == 0:
                strbuf_push_cstr(buf, "if __pcc_pc == ")
            else:
                strbuf_push_cstr(buf, "elif __pcc_pc == ")
            strbuf_push_i32(buf, k)
            strbuf_push_cstr(buf, ":")
            strbuf_push_newline(buf)
            if strbuf_size(ptr(c.blocks[k])) == 0:
                strbuf_push_indent(buf, indent + 2)
                strbuf_push_cstr(buf, "pass")
                strbuf_push_newline(buf)
            else:
                strbuf_append_buf(buf, ptr(c.blocks[k]))
            k = k + 1

    # Tear down per-block buffers and scratch arrays.
    d: i32 = 0
    while d < c.nblocks:
        strbuf_destroy(ptr(c.blocks[d]))
        d = d + 1
    effect.mem.free(c.blocks)
    effect.mem.free(c.lab_names)
    effect.mem.free(c.lab_states)
    effect.mem.free(c.h_names)
    effect.mem.free(c.h_types)
    return ok


@compile
def stmt_has_any_goto(s: ptr[Stmt]) -> i8:
    """Whether a statement subtree contains any goto."""
    if s == nullptr:
        return 0
    match s.kind[0]:
        case StmtKind.Goto:
            return 1
        case _:
            pass
    if stmt_has_any_goto(s.body) != 0:
        return 1
    if stmt_has_any_goto(s.else_body) != 0:
        return 1
    if s.stmts != nullptr:
        i: i32 = 0
        while i < s.stmt_count:
            if stmt_has_any_goto(ptr(s.stmts[i])) != 0:
                return 1
            i = i + 1
    return 0


@compile
def strbuf_append_buf(dst: ptr[StringBuffer], src: ptr[StringBuffer]) -> void:
    """Append the raw bytes of `src` onto `dst`."""
    n: i64 = strbuf_size(src)
    d: ptr[i8] = strbuf_data(src)
    i: i64 = 0
    while i < n:
        _strbuf_push_back(dst, d[i])
        i = i + 1


@compile
def strbuf_contains_cstr(buf: ptr[StringBuffer], needle: ptr[i8]) -> i8:
    """Whether the (non-terminated) buffer contains the C-string `needle`."""
    nl: i64 = 0
    while needle[nl] != 0:
        nl = nl + 1
    if nl == 0:
        return 1
    hay: ptr[i8] = strbuf_data(buf)
    hn: i64 = strbuf_size(buf)
    i: i64 = 0
    while i + nl <= hn:
        j: i64 = 0
        while j < nl and hay[i + j] == needle[j]:
            j = j + 1
        if j == nl:
            return 1
        i = i + 1
    return 0


@compile
def emit_func_body(buf: ptr[StringBuffer], body: ptr[Stmt],
                   ret_qt: ptr[QualType], indent: i32) -> void:
    """Emit a function body, choosing structured goto reconstruction when it
    suffices and the state-machine lowering otherwise.

    The structured path (scoped label/goto/goto_end) is attempted first into a
    scratch buffer. If it is clean it is used verbatim -- this keeps the common,
    readable laminar output unchanged. If it leaves an unsupported sentinel (an
    unrepresentable goto/label), the body is re-lowered as a `__pcc_pc` state
    machine; whenever that lowering is structurally sound it is used even if
    genuine expression-level gaps remain (those `__pcc_unsupported__` calls
    block compilation in either path, but the state machine still faithfully
    resolves the control flow the structured path could not). Only a structural
    state-machine failure falls back to the structured best-effort output."""
    if body == nullptr or stmt_has_any_goto(body) == 0:
        emit_suite(buf, body, indent)
        return

    tmp: StringBuffer
    strbuf_init(ptr(tmp))
    emit_suite(ptr(tmp), body, indent)
    if strbuf_contains_cstr(ptr(tmp), "__pcc_unsupported__") == 0:
        strbuf_append_buf(buf, ptr(tmp))
        strbuf_destroy(ptr(tmp))
        return
    strbuf_destroy(ptr(tmp))

    sm: StringBuffer
    strbuf_init(ptr(sm))
    ok: i8 = emit_state_machine(ptr(sm), body, indent, ret_qt)
    if ok != 0:
        strbuf_append_buf(buf, ptr(sm))
        strbuf_destroy(ptr(sm))
        return
    strbuf_destroy(ptr(sm))

    # The state machine could not represent the body either; emit the
    # structured best-effort output (loud) so behavior never regresses.
    emit_suite(buf, body, indent)


# =============================================================================
# Declaration emission
# =============================================================================

@compile
def emit_struct_class(buf: ptr[StringBuffer], name: Span, st: ptr[StructType]) -> void:
    """Emit a struct as `Name = struct["f1": T1, ...]`.

    The inline (module-level) form is used rather than an `@compile class` body
    because C identifiers frequently start with `__`, which Python would
    name-mangle inside a class body (e.g. `__off_t` -> `_Cls__off_t`).
    """
    strbuf_push_span(buf, name)
    strbuf_push_cstr(buf, " = ")
    emit_anon_aggregate(buf, st, 0)
    strbuf_push_cstr(buf, "\n\n")


@compile
def emit_struct_decl(buf: ptr[StringBuffer], decl: ptr[Decl]) -> void:
    """Emit a struct declaration as @compile class"""
    if decl == nullptr or decl.type == nullptr:
        return

    ty: ptr[CType] = decl.type.type
    if ty == nullptr:
        return

    st: ptr[StructType] = nullptr
    match ty[0]:
        case (CType.Struct, s):
            st = s
        case _:
            return

    if st == nullptr:
        return

    emit_struct_class(buf, decl.name, st)


@compile
def emit_union_type(buf: ptr[StringBuffer], name: Span, st: ptr[StructType]) -> void:
    """Emit a union as a type alias: Name = union["f1": T1, "f2": T2, ...]."""
    strbuf_push_span(buf, name)
    strbuf_push_cstr(buf, " = union[")
    if st == nullptr or st.field_count == 0:
        strbuf_push_cstr(buf, "\"_pad\": i8")
    else:
        i: i32 = 0
        while i < st.field_count:
            if i > 0:
                strbuf_push_cstr(buf, ", ")
            field: ptr[FieldInfo] = ptr(st.fields[i])
            strbuf_push_char(buf, 34)  # '"'
            if not span_is_empty(field.name):
                strbuf_push_span(buf, field.name)
            else:
                strbuf_push_cstr(buf, "_field")
                strbuf_push_i32(buf, i)
            strbuf_push_char(buf, 34)  # '"'
            strbuf_push_cstr(buf, ": ")
            emit_qualtype(buf, field.type)
            i = i + 1
    strbuf_push_cstr(buf, "]\n\n")


@compile
def emit_union_decl(buf: ptr[StringBuffer], decl: ptr[Decl]) -> void:
    """Emit a union declaration as pythoc union type"""
    if decl == nullptr or decl.type == nullptr:
        return

    ty: ptr[CType] = decl.type.type
    if ty == nullptr:
        return

    st: ptr[StructType] = nullptr
    match ty[0]:
        case (CType.Union, s):
            st = s
        case _:
            return

    if st == nullptr:
        return

    emit_union_type(buf, decl.name, st)


@compile
def emit_enum_decl(buf: ptr[StringBuffer], decl: ptr[Decl]) -> void:
    """Emit an enum declaration as @enum class"""
    if decl == nullptr or decl.type == nullptr:
        return
    
    ty: ptr[CType] = decl.type.type
    if ty == nullptr:
        return
    
    et: ptr[EnumType] = nullptr
    match ty[0]:
        case (CType.Enum, e):
            et = e
        case _:
            return
    
    if et == nullptr:
        return
    
    # @enum(i32)
    strbuf_push_cstr(buf, "@enum(i32)\n")
    
    # class Name:
    strbuf_push_cstr(buf, "class ")
    strbuf_push_span(buf, decl.name)
    strbuf_push_cstr(buf, ":\n")
    
    # Enum values
    if et.value_count == 0:
        strbuf_push_indent(buf, 1)
        strbuf_push_cstr(buf, "pass\n")
    else:
        i: i32 = 0
        while i < et.value_count:
            ev: ptr[EnumValue] = ptr(et.values[i])
            strbuf_push_indent(buf, 1)
            strbuf_push_span(buf, ev.name)
            if ev.has_explicit_value != 0:
                strbuf_push_cstr(buf, " = ")
                strbuf_push_i64(buf, ev.value)
            else:
                strbuf_push_cstr(buf, ": None")
            strbuf_push_newline(buf)
            i = i + 1
    
    strbuf_push_newline(buf)


@compile
def emit_func_decl(buf: ptr[StringBuffer], decl: ptr[Decl], lib: ptr[i8]) -> void:
    """Emit a function declaration as @extern def
    
    Args:
        buf: Output buffer
        decl: Function declaration
        lib: Library name for @extern (e.g. "c", "m", or full path)
    """
    if decl == nullptr or decl.type == nullptr:
        return
    
    ty: ptr[CType] = decl.type.type
    if ty == nullptr:
        return
    
    ft: ptr[FuncType] = nullptr
    match ty[0]:
        case (CType.Func, f):
            ft = f
        case _:
            return
    
    if ft == nullptr:
        return
    
    # @extern(lib='...')
    strbuf_push_cstr(buf, "@extern(lib='")
    strbuf_push_cstr(buf, lib)
    strbuf_push_cstr(buf, "')\n")
    
    # def name(params) -> ret:
    strbuf_push_cstr(buf, "def ")
    strbuf_push_span(buf, decl.name)
    strbuf_push_char(buf, 40)  # '('
    
    # Parameters
    i: i32 = 0
    while i < ft.param_count:
        if i > 0:
            strbuf_push_cstr(buf, ", ")
        param: ptr[ParamInfo] = ptr(ft.params[i])
        if not span_is_empty(param.name):
            strbuf_push_span(buf, param.name)
        else:
            strbuf_push_cstr(buf, "arg")
            strbuf_push_i32(buf, i)
        strbuf_push_cstr(buf, ": ")
        emit_qualtype(buf, param.type)
        i = i + 1
    
    # Variadic
    if ft.is_variadic != 0:
        if ft.param_count > 0:
            strbuf_push_cstr(buf, ", ")
        strbuf_push_cstr(buf, "*args")
    
    strbuf_push_cstr(buf, ") -> ")
    
    # Return type
    emit_qualtype(buf, ft.ret)
    
    strbuf_push_cstr(buf, ":\n")
    strbuf_push_indent(buf, 1)
    strbuf_push_cstr(buf, "pass\n\n")


@compile
def emit_func_def(buf: ptr[StringBuffer], decl: ptr[Decl]) -> void:
    """Emit a function definition (declaration with a body) as @compile def."""
    if decl == nullptr or decl.type == nullptr:
        return

    ty: ptr[CType] = decl.type.type
    if ty == nullptr:
        return

    ft: ptr[FuncType] = nullptr
    match ty[0]:
        case (CType.Func, f):
            ft = f
        case _:
            return

    if ft == nullptr:
        return

    strbuf_push_cstr(buf, "@compile\n")
    strbuf_push_cstr(buf, "def ")
    strbuf_push_span(buf, decl.name)
    strbuf_push_char(buf, 40)  # '('

    i: i32 = 0
    while i < ft.param_count:
        if i > 0:
            strbuf_push_cstr(buf, ", ")
        param: ptr[ParamInfo] = ptr(ft.params[i])
        if not span_is_empty(param.name):
            strbuf_push_span(buf, param.name)
        else:
            strbuf_push_cstr(buf, "arg")
            strbuf_push_i32(buf, i)
        strbuf_push_cstr(buf, ": ")
        emit_qualtype(buf, param.type)
        i = i + 1

    if ft.is_variadic != 0:
        if ft.param_count > 0:
            strbuf_push_cstr(buf, ", ")
        strbuf_push_cstr(buf, "*args")

    strbuf_push_cstr(buf, ") -> ")
    emit_qualtype(buf, ft.ret)
    strbuf_push_cstr(buf, ":\n")

    emit_func_body(buf, decl.body, ft.ret, 1)
    strbuf_push_newline(buf)


@compile
def _emit_alias(buf: ptr[StringBuffer], name: Span, target: Span) -> void:
    """Emit `name = target`."""
    strbuf_push_span(buf, name)
    strbuf_push_cstr(buf, " = ")
    strbuf_push_span(buf, target)
    strbuf_push_cstr(buf, "\n\n")


@compile
def emit_typedef_decl(buf: ptr[StringBuffer], decl: ptr[Decl]) -> void:
    """Emit a typedef declaration.

    A typedef may carry a full aggregate definition (e.g.
    `typedef struct Tag { ... } Name;`). In that case the aggregate is defined
    (under its tag if named, otherwise under the typedef name) and aliased to
    the typedef name. Otherwise a plain `Name = UnderlyingType` alias is emitted.
    """
    if decl == nullptr or decl.type == nullptr:
        return

    ty: ptr[CType] = decl.type.type
    if ty != nullptr:
        match ty[0]:
            case (CType.Struct, s):
                if s != nullptr and s.field_count > 0:
                    if span_is_empty(s.name):
                        emit_struct_class(buf, decl.name, s)
                    else:
                        emit_struct_class(buf, s.name, s)
                        if not span_eq(s.name, decl.name):
                            _emit_alias(buf, decl.name, s.name)
                    return
                # Bare reference `typedef struct S S;` needs no alias.
                if s != nullptr and not span_is_empty(s.name) and span_eq(s.name, decl.name):
                    return
            case (CType.Union, s):
                if s != nullptr and s.field_count > 0:
                    if span_is_empty(s.name):
                        emit_union_type(buf, decl.name, s)
                    else:
                        emit_union_type(buf, s.name, s)
                        if not span_eq(s.name, decl.name):
                            _emit_alias(buf, decl.name, s.name)
                    return
                if s != nullptr and not span_is_empty(s.name) and span_eq(s.name, decl.name):
                    return
            case _:
                pass

    strbuf_push_span(buf, decl.name)
    strbuf_push_cstr(buf, " = ")
    emit_qualtype(buf, decl.type)
    strbuf_push_cstr(buf, "\n\n")


@compile
def emit_zero_value(buf: ptr[StringBuffer], qt: ptr[QualType]) -> void:
    """Emit a compile-time-constant zero for a scalar global without an init.

    static[T] storage needs a constant seed. Aggregates have no scalar zero, so
    they fail loudly (and are deferred to a later aggregate-init stage).
    """
    if qt == nullptr or qt.type == nullptr:
        strbuf_push_char(buf, 48)  # '0'
        return
    match qt.type[0]:
        case CType.Float:
            strbuf_push_cstr(buf, "0.0")
        case CType.Double:
            strbuf_push_cstr(buf, "0.0")
        case CType.LongDouble:
            strbuf_push_cstr(buf, "0.0")
        case (CType.Ptr, _pt):
            strbuf_push_cstr(buf, "nullptr")
        case (CType.Array, _at):
            emit_unsupported(buf)
        case (CType.Struct, _st):
            emit_unsupported(buf)
        case (CType.Union, _u):
            emit_unsupported(buf)
        case _:
            strbuf_push_char(buf, 48)  # '0' (ints, char, bool, enum, typedef)


@compile
def _qualtype_is_pointer(qt: ptr[QualType]) -> i8:
    """Whether a QualType is a (possibly qualified) pointer type."""
    if qt == nullptr or qt.type == nullptr:
        return 0
    match qt.type[0]:
        case (CType.Ptr, _pt):
            return 1
        case _:
            return 0


@compile
def _qualtype_is_void(qt: ptr[QualType]) -> i8:
    """Whether a QualType is plain void (a function with no return value)."""
    if qt == nullptr or qt.type == nullptr:
        return 0
    match qt.type[0]:
        case CType.Void:
            return 1
        case _:
            return 0


@compile
def _qualtype_is_aggregate(qt: ptr[QualType]) -> i8:
    """Whether a QualType is an array/struct/union (has no scalar zero seed).

    PythoC zero-initializes such a slot when it is declared without an explicit
    seed (e.g. `s: static[array[i32, 5]]`), so an uninitialized aggregate global
    needs no constant initializer rather than the (impossible) scalar zero.
    """
    if qt == nullptr or qt.type == nullptr:
        return 0
    match qt.type[0]:
        case (CType.Array, _at):
            return 1
        case (CType.Struct, _st):
            return 1
        case (CType.Union, _u):
            return 1
        case _:
            return 0


@compile
def emit_global_init(buf: ptr[StringBuffer], qt: ptr[QualType], init: ptr[Expr]) -> void:
    """Emit the static seed for a global, coercing C constants to PythoC.

    A missing initializer becomes a typed zero. C's integer `0` is a null
    pointer constant, so a pointer-typed global seeded with `0` emits `nullptr`.
    """
    if init == nullptr:
        emit_zero_value(buf, qt)
        return
    if _qualtype_is_pointer(qt) != 0:
        match init.kind[0]:
            case ExprKind.IntLit:
                if init.int_val == 0:
                    strbuf_push_cstr(buf, "nullptr")
                    return
            case _:
                pass
    emit_expr(buf, init)


@compile
def emit_var_decl(buf: ptr[StringBuffer], decl: ptr[Decl]) -> void:
    """Emit a file-scope variable as a static-backed accessor function.

    `int g = 5;` lowers to
        @compile
        def _pcc_g_g() -> ptr[i32]:
            s: static[i32] = 5
            return ptr(s)
    so every reference `g` becomes `_pcc_g_g()[0]` (a stable lvalue with a fixed
    address). extern declarations define no storage in this TU and emit nothing;
    references to them keep their bare name and resolve at link time.
    """
    if decl == nullptr or decl.type == nullptr:
        return
    if decl.storage == STORAGE_EXTERN:
        return

    strbuf_push_cstr(buf, "@compile\n")
    strbuf_push_cstr(buf, "def ")
    emit_global_accessor_name(buf, decl.name)
    strbuf_push_cstr(buf, "() -> ptr[")
    emit_qualtype(buf, decl.type)
    strbuf_push_cstr(buf, "]:\n")
    strbuf_push_indent(buf, 1)
    strbuf_push_cstr(buf, "s: static[")
    emit_qualtype(buf, decl.type)
    strbuf_push_cstr(buf, "]")
    # An aggregate without an initializer is left to PythoC's implicit
    # zero-init; only emit a seed when there is one (or for scalars, which
    # always need a constant zero).
    if decl.init != nullptr or _qualtype_is_aggregate(decl.type) == 0:
        strbuf_push_cstr(buf, " = ")
        emit_global_init(buf, decl.type, decl.init)
    strbuf_push_newline(buf)
    strbuf_push_indent(buf, 1)
    strbuf_push_cstr(buf, "return ptr(s)\n\n")


@compile
def emit_decl(buf: ptr[StringBuffer], decl: ptr[Decl], lib: ptr[i8]) -> void:
    """Emit any declaration to the buffer
    
    Args:
        buf: Output buffer
        decl: Declaration to emit
        lib: Library name for @extern functions (e.g. "c", "m", or full path)
    """
    if decl == nullptr:
        return
    
    match decl.kind:
        case DeclKind.Struct:
            emit_struct_decl(buf, decl)
        case DeclKind.Union:
            emit_union_decl(buf, decl)
        case DeclKind.Enum:
            emit_enum_decl(buf, decl)
        case DeclKind.Func:
            # A function with a body becomes a @compile def; a bare prototype
            # becomes an @extern declaration linked against `lib`. inline
            # functions are header helpers (often calling compiler intrinsics)
            # and are not part of this translation unit's emitted definitions.
            if decl.storage == STORAGE_INLINE:
                pass
            elif decl.body != nullptr:
                emit_func_def(buf, decl)
            else:
                emit_func_decl(buf, decl, lib)
        case DeclKind.Typedef:
            emit_typedef_decl(buf, decl)
        case DeclKind.Var:
            emit_var_decl(buf, decl)
        case _:
            pass


# =============================================================================
# Module header emission
# =============================================================================

@compile
def emit_module_header(buf: ptr[StringBuffer]) -> void:
    """Emit standard pythoc module header with imports"""
    strbuf_push_cstr(buf, '"""Auto-generated by pcc (C -> PythoC)"""\n\n')
    strbuf_push_cstr(buf, "from pythoc import (\n")
    strbuf_push_indent(buf, 1)
    strbuf_push_cstr(buf, "compile, extern, enum, i8, i16, i32, i64,\n")
    strbuf_push_indent(buf, 1)
    strbuf_push_cstr(buf, "u8, u16, u32, u64, f32, f64, ptr, array,\n")
    strbuf_push_indent(buf, 1)
    strbuf_push_cstr(buf, "void, char, nullptr, sizeof, typeof, struct, union, func, static,\n")
    strbuf_push_indent(buf, 1)
    strbuf_push_cstr(buf, "label, goto, goto_end\n")
    strbuf_push_cstr(buf, ")\n\n")


@compile
def emit_module_footer(buf: ptr[StringBuffer]) -> void:
    """Emit a footer that turns named struct/union aliases into proper named
    types.

    Named aggregates are emitted as plain aliases (Name = struct[...]). On their
    own these are anonymous structural types: PythoC neither registers them under
    a tag name nor gives them an identified LLVM type. That breaks two things for
    real C code:
      * self-referential / mutually recursive aggregates spell their back-edges
        as ptr["Tag"], which must resolve by name; and
      * a recursive struct cannot be a literal (structural) LLVM type at all - it
        needs an identified (named) type to close the cycle.

    For every named aggregate we therefore (1) adopt the Python binding name as
    the canonical/identified name, (2) force the identified-type path, and (3)
    register the name for forward-reference resolution. This is uniform across
    all aggregates, with no per-declaration special casing."""
    strbuf_push_cstr(buf, "\n")
    strbuf_push_cstr(buf, "def _pcc_register_named_types(_ns):\n")
    strbuf_push_cstr(buf, "    from pythoc.forward_ref import mark_type_defined\n")
    strbuf_push_cstr(buf, "    for _name, _ty in list(_ns.items()):\n")
    strbuf_push_cstr(
        buf,
        "        if isinstance(_ty, type) and "
        "getattr(_ty, '_field_types', None) is not None:\n",
    )
    strbuf_push_cstr(buf, "            _ty._canonical_name = _name\n")
    strbuf_push_cstr(buf, "            _ty._force_identified = True\n")
    strbuf_push_cstr(buf, "            mark_type_defined(_name, _ty)\n")
    strbuf_push_cstr(buf, "\n\n")
    strbuf_push_cstr(buf, "_pcc_register_named_types(globals())\n")

