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
    char, refine, assume, struct, consume, linear
)
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
                i: i32 = 0
                while i < body.stmt_count:
                    emit_stmt(buf, ptr(body.stmts[i]), indent)
                    i = i + 1
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
                i: i32 = 0
                while i < s.stmt_count:
                    emit_stmt(buf, ptr(s.stmts[i]), indent)
                    i = i + 1
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
            emit_unsupported(buf)
            strbuf_push_newline(buf)
        case _:
            strbuf_push_indent(buf, indent)
            emit_unsupported(buf)
            strbuf_push_newline(buf)


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

    emit_suite(buf, decl.body, 1)
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
def emit_var_decl(buf: ptr[StringBuffer], decl: ptr[Decl]) -> void:
    """Emit a variable declaration as a typed global
    
    Generates: # var_name: Type (as comment since pythoc doesn't have global vars)
    """
    if decl == nullptr or decl.type == nullptr:
        return
    
    # Emit as comment since pythoc doesn't support global variables directly
    strbuf_push_cstr(buf, "# ")
    strbuf_push_span(buf, decl.name)
    strbuf_push_cstr(buf, ": ")
    emit_qualtype(buf, decl.type)
    strbuf_push_cstr(buf, "\n")


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
    strbuf_push_cstr(buf, "void, char, nullptr, sizeof, typeof, struct, union, func\n")
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

