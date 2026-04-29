# -*- coding: utf-8 -*-
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 10):
    _sys.exit("Error: SL requires Python 3.10+. Running: " + _sys.version)
del _sys
"""
parser.py  ·  Recursive-descent + Pratt expression parser
──────────────────────────────────────────────────────────
Consumes the token list from lexer.py and produces a typed AST.

AST nodes are plain dataclasses — no inheritance hierarchy needed.
Every node carries (line, col) from the token that opened it.

Usage
  from lexer  import lex
  from parser import Parser, Program

  tree = Parser(lex(source), filename).parse()
  print(tree.pretty())

  # or from the CLI:
  python parser.py source.sl
"""


import sys
from dataclasses import dataclass, field
from typing import Any, Optional
from typing import Any
from lexer import lex, TT, Token


# ══════════════════════════════════════════════════════════════════════
# Errors
# ══════════════════════════════════════════════════════════════════════

class ParseError(Exception):
    def __init__(self, msg: str, tok: Token, filename: str = ""):
        loc = f"{filename}:{tok.line}:{tok.col}" if filename else f"line {tok.line}:{tok.col}"
        super().__init__(f"ParseError at {loc} — {msg} (got {tok.tt.name} {tok.val!r})")
        self.tok = tok


# ══════════════════════════════════════════════════════════════════════
# Type expression nodes
# ══════════════════════════════════════════════════════════════════════

@dataclass
class TyName:
    """Primitive or named type: int, str, MyClass"""
    name: str
    line: int = 0; col: int = 0

@dataclass
class TyList:
    """[T]"""
    elem: Any
    line: int = 0; col: int = 0

@dataclass
class TyDict:
    """{K~V}"""
    key: Any; val: Any
    line: int = 0; col: int = 0

@dataclass
class TyTuple:
    """(T, U, ...)"""
    elems: list
    line: int = 0; col: int = 0

@dataclass
class TyOptional:
    """T?"""
    inner: Any
    line: int = 0; col: int = 0

@dataclass
class TyUnion:
    """T~U"""
    left: Any; right: Any
    line: int = 0; col: int = 0

@dataclass
class TyGen:
    """|T  — generator / stream"""
    yield_t: Any
    line: int = 0; col: int = 0

@dataclass
class TyFn:
    """fn(T,U)->V  — function type"""
    params: list; ret: Any
    line: int = 0; col: int = 0

@dataclass
class TyParam:
    """T — unresolved type parameter (upper-case ident)"""
    name: str
    line: int = 0; col: int = 0


# ══════════════════════════════════════════════════════════════════════
# Expression nodes
# ══════════════════════════════════════════════════════════════════════

@dataclass
class IntLit:
    value: int
    line: int = 0; col: int = 0

@dataclass
class FloatLit:
    value: float
    line: int = 0; col: int = 0

@dataclass
class StrLit:
    value: str
    line: int = 0; col: int = 0

@dataclass
class BoolLit:
    value: bool
    line: int = 0; col: int = 0

@dataclass
class NullLit:
    line: int = 0; col: int = 0

@dataclass
class Ident:
    name: str
    line: int = 0; col: int = 0

@dataclass
class SelfExpr:
    """@ — current instance or recursive self-call"""
    line: int = 0; col: int = 0

@dataclass
class BinOp:
    op: str; left: Any; right: Any
    line: int = 0; col: int = 0

@dataclass
class UnaryOp:
    op: str; operand: Any
    line: int = 0; col: int = 0

@dataclass
class Assign:
    """target :< value"""
    target: Any; value: Any
    line: int = 0; col: int = 0

@dataclass
class AugAssign:
    """target @:< op value  (e.g. x @:< + 1)"""
    target: Any; op: str; value: Any
    line: int = 0; col: int = 0

@dataclass
class PropAccess:
    """obj:prop"""
    obj: Any; prop: str
    line: int = 0; col: int = 0

@dataclass
class SafePropAccess:
    """obj:?prop"""
    obj: Any; prop: str
    line: int = 0; col: int = 0

@dataclass
class MethodCall:
    """obj::method:arg,arg"""
    obj: Any; method: str; args: list
    line: int = 0; col: int = 0

@dataclass
class SafeMethodCall:
    """obj::?method:arg,arg"""
    obj: Any; method: str; args: list
    line: int = 0; col: int = 0

@dataclass
class Call:
    """fn:arg,arg  or  fn:(arg,arg)"""
    fn: Any; args: list
    line: int = 0; col: int = 0

@dataclass
class SelfCall:
    """@:method_or_fn:arg,arg  — recursive / self call"""
    name: str; args: list
    line: int = 0; col: int = 0

@dataclass
class Pipe:
    """|>"""
    left: Any; right: Any
    line: int = 0; col: int = 0

@dataclass
class NullCoalesce:
    """?:"""
    left: Any; right: Any
    line: int = 0; col: int = 0

@dataclass
class Ternary:
    """cond ? then –– else_"""
    cond: Any; then: Any; else_: Any
    line: int = 0; col: int = 0

@dataclass
class RangeExpr:
    """start .. end  or  start ..= end"""
    start: Any; end: Any; step: Any; inclusive: bool
    line: int = 0; col: int = 0

@dataclass
class YieldExpr:
    """->| value  (two-way — evaluates to what is sent in)"""
    value: Any
    line: int = 0; col: int = 0

@dataclass
class YieldFromExpr:
    """->|> value"""
    value: Any
    line: int = 0; col: int = 0

@dataclass
class AwaitExpr:
    """~> expr"""
    value: Any
    line: int = 0; col: int = 0

@dataclass
class GatherExpr:
    """~| (expr, expr, ...)"""
    exprs: list
    line: int = 0; col: int = 0

@dataclass
class FireForget:
    """~! expr"""
    value: Any
    line: int = 0; col: int = 0

@dataclass
class RecvChan:
    """~>> chan"""
    chan: Any
    line: int = 0; col: int = 0

@dataclass
class SendGen:
    """gen <<| value  — returns next yielded value"""
    gen: Any; value: Any
    line: int = 0; col: int = 0

@dataclass
class ThrowGen:
    """gen <!| .ErrType:msg"""
    gen: Any; error: Any
    line: int = 0; col: int = 0

@dataclass
class WeakRefExpr:
    """~& expr"""
    value: Any
    line: int = 0; col: int = 0

@dataclass
class PartialApp:
    """fn $ arg  or  fn$"""
    fn: Any; args: list
    line: int = 0; col: int = 0

@dataclass
class Lambda:
    r"""\param,param -> expr"""
    params: list; body: Any
    line: int = 0; col: int = 0

@dataclass
class ListLit:
    items: list
    line: int = 0; col: int = 0

@dataclass
class DictLit:
    """{ k ~ v, k ~ v }"""
    pairs: list  # list of (key, val)
    line: int = 0; col: int = 0

@dataclass
class SetLit:
    """{| a, b, c |}"""
    items: list
    line: int = 0; col: int = 0

@dataclass
class TupleLit:
    items: list
    line: int = 0; col: int = 0

@dataclass
class Comprehension:
    """[expr | var #iterable ? cond]"""
    kind: str   # 'list' | 'dict' | 'gen' | 'set'
    expr: Any   # for dict: (key_expr, val_expr)
    var: str
    iterable: Any
    cond: Any   # optional filter
    line: int = 0; col: int = 0

@dataclass
class InterpolatedStr:
    """`text {expr} text`"""
    parts: list  # alternating StrLit and Expr
    line: int = 0; col: int = 0

@dataclass
class Subscript:
    """obj[index]"""
    obj: Any; index: Any
    line: int = 0; col: int = 0

@dataclass
class MatchExpr:
    """expr ?| arm: val, arm: val"""
    subject: Any; arms: list  # (pattern, expr)
    line: int = 0; col: int = 0

@dataclass
class NotNullExpr:
    """expr !~  — assert non-null and unwrap"""
    value: Any
    line: int = 0; col: int = 0

@dataclass
class LenExpr:
    """#| expr  — length"""
    value: Any
    line: int = 0; col: int = 0

@dataclass
class IterExpr:
    """# expr  — iterate"""
    value: Any
    line: int = 0; col: int = 0

@dataclass
class ClassRef:
    """.ClassName  — class name in expression position"""
    name: str
    line: int = 0; col: int = 0


# ══════════════════════════════════════════════════════════════════════
# Statement / declaration nodes
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Param:
    name: str
    type_: Any = None   # TyXxx node or None
    default: Any = None # Expr or None

@dataclass
class Block:
    stmts: list
    line: int = 0; col: int = 0

@dataclass
class FnDef:
    name: str
    params: list      # list[Param]
    return_type: Any  # TyXxx or None
    body: Block
    is_async: bool = False
    is_gen: bool = False
    receiver: str = 'static'  # 'instance'(@) | 'class'(.) | 'static' | 'ext'
    line: int = 0; col: int = 0

@dataclass
class ClassDef:
    name: str
    parent: str | None
    mixins: list        # list[str]
    interfaces: list    # list[str]
    fields: list        # list[Param]
    body: Block
    line: int = 0; col: int = 0

@dataclass
class MixinDef:
    name: str
    body: Block
    line: int = 0; col: int = 0

@dataclass
class IfaceDef:
    name: str
    body: Block
    line: int = 0; col: int = 0

@dataclass
class ReturnStmt:
    value: Any  # Expr or None
    line: int = 0; col: int = 0

@dataclass
class YieldStmt:
    value: Any
    line: int = 0; col: int = 0

@dataclass
class YieldFromStmt:
    value: Any
    line: int = 0; col: int = 0

@dataclass
class IfStmt:
    cond: Any
    then: Block
    elifs: list     # list[(cond, Block)]
    else_: Any      # Block or None
    line: int = 0; col: int = 0

@dataclass
class WhileStmt:
    cond: Any
    body: Block
    else_: Any      # Block (loop-else ––) or None
    loop_else: Any  # Block (loop-else ——) or None
    line: int = 0; col: int = 0

@dataclass
class ForStmt:
    var: str
    iterable: Any
    body: Block
    else_: Any      # Block or None
    loop_else: Any  # Block or None
    line: int = 0; col: int = 0

@dataclass
class BreakStmt:
    label: str | None = None
    line: int = 0; col: int = 0

@dataclass
class ContinueStmt:
    line: int = 0; col: int = 0

@dataclass
class ExprStmt:
    expr: Any
    line: int = 0; col: int = 0

@dataclass
class ImportStmt:
    """~> module:name  or  ~> module"""
    module: str
    names: list   # list[str] — specific imports, or [] for whole module
    alias: str | None = None
    line: int = 0; col: int = 0

@dataclass
class TryCatch:
    body: Block
    handlers: list  # list[(type_name, var_name, Block)]
    finally_: Any   # Block or None
    line: int = 0; col: int = 0

@dataclass
class AssertStmt:
    cond: Any
    msg: Any  # Expr or None
    line: int = 0; col: int = 0

@dataclass
class MixinAttach:
    """+~ MixinName  — attach mixin to enclosing class"""
    mixin: str
    line: int = 0; col: int = 0

@dataclass
@dataclass
class ExternC:
    """~C :c_name:params[ret]   —   declare a C function callable from SL."""
    c_name:      str        # actual C symbol name
    params:      list       # [Param]
    return_type: Any        # TyName etc.
    line: int; col: int

@dataclass
class Namespace:
    """~[ name ] ... block"""
    name: str
    body: Any
    line: int = 0; col: int = 0

@dataclass
class Program:
    stmts: list
    filename: str = ""
    line: int = 0; col: int = 0


# ══════════════════════════════════════════════════════════════════════
# Pratt binding powers
# ══════════════════════════════════════════════════════════════════════
#
# lbp = left-binding power  (how tightly an infix operator binds to its LEFT)
# For right-associative operators, rbp = lbp - 1 (so a right operand
# with the same lbp is accepted and forms a right-assoc chain).
#
# Level  Operators              Assoc
#   1    :<  @:<                right
#   2    |>                     left
#   3    ?:                     left
#   4    ? ––  (ternary)        right
#   5    |                      left
#   6    &                      left
#   7    == != < > <= >= ?<     non-assoc
#   8    .. ..=                 non-assoc
#   9    + -                    left
#  10    * / %                  left
#  11    **                     right
#  12    (prefix unary)
#  13    postfix / access

_BP_ASSIGN   = 10
_BP_PIPE     = 20
_BP_COAL     = 30
_BP_TERNARY  = 40
_BP_OR       = 50
_BP_AND      = 60
_BP_CMP      = 70
_BP_RANGE    = 80
_BP_ADD      = 90
_BP_MUL      = 100
_BP_POW      = 110
_BP_UNARY    = 120
_BP_POSTFIX  = 130


# ══════════════════════════════════════════════════════════════════════
# Parser
# ══════════════════════════════════════════════════════════════════════

class Parser:
    def __init__(self, tokens: list[Token], filename: str = "<stdin>"):
        # Strip INDENT/DEDENT/NEWLINE for expression parsing
        # but keep them for statement-level block detection
        self.tokens   = tokens
        self.filename = filename
        self.pos      = 0

    # ── Token stream primitives ──────────────────────────────────────

    def _cur(self) -> Token:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else Token(TT.EOF, '', 0, 0)

    def _peek(self, off: int = 1) -> Token:
        i = self.pos + off
        return self.tokens[i] if i < len(self.tokens) else Token(TT.EOF, '', 0, 0)

    def _adv(self) -> Token:
        tok = self._cur()
        self.pos += 1
        return tok

    def _eat(self, tt: TT, msg: str = "") -> Token:
        tok = self._cur()
        if tok.tt != tt:
            raise ParseError(
                msg or f"expected {tt.name}", tok, self.filename)
        return self._adv()

    def _skip_newlines(self) -> None:
        while self._cur().tt in (TT.NEWLINE,):
            self._adv()

    def _at(self, *tts: TT) -> bool:
        return self._cur().tt in tts

    def _err(self, msg: str) -> ParseError:
        return ParseError(msg, self._cur(), self.filename)

    # ── Top-level entry point ────────────────────────────────────────

    def parse(self) -> Program:
        tok = self._cur()
        stmts = self._parse_stmts_until(TT.EOF)
        return Program(stmts=stmts, filename=self.filename,
                       line=tok.line, col=tok.col)

    # ── Block / statement list ───────────────────────────────────────

    def _parse_block(self) -> Block:
        """Parse an indented block: NEWLINE INDENT stmts DEDENT"""
        tok = self._cur()
        self._eat(TT.NEWLINE, "expected newline before block")
        self._eat(TT.INDENT, "expected indent")
        stmts = self._parse_stmts_until(TT.DEDENT)
        if self._at(TT.DEDENT):
            self._adv()
        return Block(stmts=stmts, line=tok.line, col=tok.col)

    def _parse_stmts_until(self, stop: TT) -> list:
        stmts = []
        while not self._at(stop, TT.EOF):
            self._skip_newlines()
            if self._at(stop, TT.EOF):
                break
            stmt = self._parse_stmt()
            if stmt is not None:
                stmts.append(stmt)
        return stmts

    # ── Statement dispatch ───────────────────────────────────────────

    def _parse_stmt(self):
        tok = self._cur()
        tt  = tok.tt

        # ── Function definition  :name:params[ret]  ────────────────
        if tt == TT.COLON and self._peek().tt == TT.IDENT:
            return self._parse_fn_def(is_async=False, is_gen=False)

        # ── Generator function  |:name:...  ───────────────────────
        if tt == TT.OR and self._peek().tt == TT.COLON:
            return self._parse_fn_def(is_async=False, is_gen=True)

        # ── Async function  ~:name:...  ───────────────────────────
        if tt == TT.TILDE and self._peek().tt == TT.COLON:
            return self._parse_fn_def(is_async=True, is_gen=False)

        # ── Class definition  .ClassName  ─────────────────────────
        if tt == TT.DOT and self._peek().tt == TT.IDENT:
            return self._parse_class_def()

        # ── Mixin definition  ~.MixinName  ───────────────────────
        if tt == TT.TILDE and self._peek().tt == TT.DOT:
            return self._parse_mixin_def()

        # ── Interface definition  >InterfaceName  ────────────────
        if tt == TT.GT and self._peek().tt == TT.IDENT:
            return self._parse_iface_def()

        # ── Return  -> expr  ─────────────────────────────────────
        if tt == TT.ARROW:
            return self._parse_return()

        # ── Yield  ->| expr  ─────────────────────────────────────
        if tt == TT.YIELD:
            return self._parse_yield_stmt()

        # ── Yield-from  ->|> expr  ───────────────────────────────
        if tt == TT.YIELD_FROM:
            tok2 = self._adv()
            val  = self._parse_expr()
            self._eat_newline()
            return YieldFromStmt(value=val, line=tok2.line, col=tok2.col)

        # ── While  !! cond  ───────────────────────────────────────
        if tt == TT.WHILE:
            return self._parse_while()

        # ── For  for var #iterable  ───────────────────────────────
        if tt == TT.FOR:
            return self._parse_for()

        # ── If  ? cond  ───────────────────────────────────────────
        if tt == TT.IF:
            return self._parse_if()

        # ── Break  <>  ────────────────────────────────────────────
        if tt == TT.BREAK:
            self._adv()
            label = None
            if self._at(TT.COLON) and self._peek().tt == TT.IDENT:
                self._adv()
                label = self._adv().val
            self._eat_newline()
            return BreakStmt(label=label, line=tok.line, col=tok.col)

        # ── Continue  >>  ─────────────────────────────────────────
        if tt == TT.CONTINUE:
            self._adv()
            self._eat_newline()
            return ContinueStmt(line=tok.line, col=tok.col)

        # ── Try  [?]  ─────────────────────────────────────────────
        if tt == TT.TRY:
            return self._parse_try()

        # ── Assert  !? cond  ──────────────────────────────────────
        if tt == TT.ASSERT_KW:
            self._adv()
            cond = self._parse_expr()
            msg  = None
            if self._at(TT.COMMA):
                self._adv()
                msg = self._parse_expr()
            self._eat_newline()
            return AssertStmt(cond=cond, msg=msg, line=tok.line, col=tok.col)

        # ── Import  ~> module:name  ───────────────────────────────
        if tt == TT.TILDE_GT:
            return self._parse_import()

        # ── Mixin attach  +~ MixinName  ──────────────────────────
        if tt == TT.MIXIN_ATT:
            self._adv()
            name = self._eat(TT.IDENT).val
            self._eat_newline()
            return MixinAttach(mixin=name, line=tok.line, col=tok.col)

        # ── Namespace  ~[ name ]  ─────────────────────────────────
        if tt == TT.NS_OPEN:
            return self._parse_namespace()

        # ── C FFI declaration  ~C :name:params[ret]  ──────────────────
        if tt == TT.CEXTERN:
            return self._parse_extern_c()

        # ── Everything else: expression statement ─────────────────
        return self._parse_expr_stmt()

    # ── Function definition ──────────────────────────────────────────

    def _parse_fn_def(self, is_async: bool, is_gen: bool) -> FnDef:
        tok = self._cur()

        # Consume the opening sigil(s)
        if is_gen:    self._adv()   # |
        elif is_async: self._adv()  # ~

        self._adv()  # :  (the COLON that precedes the fn name)
        name_tok = self._eat(TT.IDENT, "expected function name")
        name = name_tok.val

        # Receiver (first param determines method kind)
        receiver = 'static'
        params   = []

        if self._at(TT.COLON):
            self._adv()  # :
            params, receiver = self._parse_params()

        # Return type  [type]
        ret_type = None
        if self._at(TT.LBRACKET):
            ret_type = self._parse_return_type_brackets()

        # Inline body:  :fn:params[ret] -> expr   (no newline required)
        if self._at(TT.ARROW):
            arrow_tok = self._adv()  # ->
            expr      = self._parse_expr()
            self._eat_newline()
            ret_stmt  = ReturnStmt(value=expr, line=arrow_tok.line, col=arrow_tok.col)
            body      = Block(stmts=[ret_stmt], line=arrow_tok.line, col=arrow_tok.col)
        else:
            body = self._parse_block()
        return FnDef(name=name, params=params, return_type=ret_type,
                     body=body, is_async=is_async, is_gen=is_gen,
                     receiver=receiver, line=tok.line, col=tok.col)

    def _parse_params(self) -> tuple[list, str]:
        """Parse comma-separated params; return (params, receiver_kind)."""
        params   = []
        receiver = 'static'

        while not self._at(TT.LBRACKET, TT.NEWLINE, TT.EOF, TT.INDENT):
            tok = self._cur()

            # Instance method marker  @
            if tok.tt == TT.SELF:
                receiver = 'instance'
                self._adv()
                params.append(Param(name='self', type_=None))
                if self._at(TT.COMMA): self._adv()
                continue

            # Class method / extension receiver  .ClassName  or  .
            if tok.tt == TT.DOT:
                self._adv()
                if self._at(TT.IDENT):
                    cname = self._adv().val
                    if receiver == 'static':
                        # First time seeing a type-prefixed param → extension receiver
                        receiver = 'ext'
                        params.append(Param(name='self', type_=TyName(cname)))
                    else:
                        # Already have @ or ext receiver → this is a typed "other" parameter.
                        # Auto-name: Vec2 → vec2, Point → point
                        auto_name = cname[0].lower() + cname[1:]
                        params.append(Param(name=auto_name, type_=TyName(cname)))
                else:
                    if receiver == 'static':
                        receiver = 'class'
                    params.append(Param(name='cls', type_=None))
                if self._at(TT.COMMA): self._adv()
                continue

            # Regular param: ident (with optional _type suffix)
            if tok.tt == TT.IDENT:
                name, ty = self._split_param_name(tok.val, tok)
                self._adv()

                # Inline type after name: _[ T ]  or  _{ K~V }  etc.
                if ty is None and self._at(TT.LBRACKET):
                    ty = self._parse_type_brackets()

                # Default value
                default = None
                if self._at(TT.ASSIGN):
                    self._adv()
                    default = self._parse_expr(_BP_ASSIGN)

                params.append(Param(name=name, type_=ty, default=default))
                if self._at(TT.COMMA): self._adv()
                continue

            break

        return params, receiver

    def _split_param_name(self, val: str, tok: Token) -> tuple[str, Any]:
        """
        Split 'n_int' → ('n', TyName('int')).
        Handles primitive suffixes: _int _float _str _bool _void.
        Trailing underscore with no recognised type → name kept as-is, ty=None.
        """
        primitives = {'int': TyName, 'float': TyName, 'str': TyName,
                      'bool': TyName, 'void': TyName,
                      'arr': TyName, 'obj': TyName}
        if '_' in val:
            idx  = val.rfind('_')
            name = val[:idx]
            suf  = val[idx+1:]
            if suf in primitives:
                return name, primitives[suf](suf, tok.line, tok.col)
            if suf == '':
                return name, None  # trailing _ → type follows as next token
        return val, None

    # ── Type expressions ─────────────────────────────────────────────

    def _parse_return_type_brackets(self) -> Any:
        """
        Parse  [type]  as a RETURN TYPE ANNOTATION.
        The outer brackets are delimiters only, not a list constructor.
          [int]    → TyName('int')
          [|int]   → TyGen(TyName('int'))   generator return
          [[int]]  → TyList(TyName('int'))  returns a list
        """
        self._eat(TT.LBRACKET)
        tok = self._cur()
        if tok.tt == TT.OR:              # [|T]  generator yield type
            self._adv()
            inner = self._parse_type_expr()
            self._eat(TT.RBRACKET)
            return TyGen(inner, tok.line, tok.col)
        if tok.tt == TT.RBRACKET:        # []  empty / unit
            self._adv()
            return TyName('void', tok.line, tok.col)
        inner = self._parse_type_expr()  # the actual return type
        self._eat(TT.RBRACKET)
        return inner                     # ← return directly, no TyList wrapper

    def _parse_type_brackets(self) -> Any:
        """
        Parse  [type]  as a LIST TYPE (used after _ in param names).
          [T]  → TyList(TyName('T'))
        """
        self._eat(TT.LBRACKET)
        tok = self._cur()
        if tok.tt == TT.RBRACKET:
            self._adv()
            return TyList(TyParam('_'), tok.line, tok.col)
        inner = self._parse_type_expr()
        self._eat(TT.RBRACKET)
        return TyList(inner, tok.line, tok.col)

    def _parse_type_expr(self) -> Any:
        """Parse a type expression (not surrounded by brackets)."""
        tok = self._cur()
        ty  = self._parse_type_atom()

        # T?  — optional
        if self._at(TT.IF):   # '?' token
            self._adv()
            ty = TyOptional(ty, tok.line, tok.col)

        # T~U  — union
        if self._at(TT.TILDE):
            self._adv()
            right = self._parse_type_atom()
            ty = TyUnion(ty, right, tok.line, tok.col)

        return ty

    def _parse_type_atom(self) -> Any:
        tok = self._cur()
        tt  = tok.tt

        if tt in (TT.KW_INT, TT.KW_FLOAT, TT.KW_STR, TT.KW_BOOL, TT.KW_VOID):
            self._adv()
            return TyName(tok.val, tok.line, tok.col)

        if tt == TT.IDENT:
            self._adv()
            return TyName(tok.val, tok.line, tok.col)

        if tt == TT.LBRACKET:    # [T] — list
            return self._parse_type_brackets()

        if tt == TT.LBRACE:      # {K~V} — dict
            self._adv()
            key = self._parse_type_expr()
            self._eat(TT.TILDE)
            val = self._parse_type_expr()
            self._eat(TT.RBRACE)
            return TyDict(key, val, tok.line, tok.col)

        if tt == TT.LPAREN:      # (T, U) — tuple
            self._adv()
            elems = []
            while not self._at(TT.RPAREN, TT.EOF):
                elems.append(self._parse_type_expr())
                if self._at(TT.COMMA): self._adv()
            self._eat(TT.RPAREN)
            return TyTuple(elems, tok.line, tok.col)

        if tt == TT.OR:          # |T — generator/stream
            self._adv()
            inner = self._parse_type_atom()
            return TyGen(inner, tok.line, tok.col)

        if tt == TT.KW_FN:       # fn(T,U)->V
            self._adv()
            self._eat(TT.LPAREN)
            ptypes = []
            while not self._at(TT.RPAREN, TT.EOF):
                ptypes.append(self._parse_type_expr())
                if self._at(TT.COMMA): self._adv()
            self._eat(TT.RPAREN)
            self._eat(TT.ARROW)
            ret = self._parse_type_expr()
            return TyFn(ptypes, ret, tok.line, tok.col)

        raise self._err(f"expected type expression, got {tt.name}")

    # ── Class / Mixin / Interface definitions ────────────────────────

    def _parse_class_def(self) -> ClassDef:
        tok = self._cur()
        self._adv()  # .
        name = self._eat(TT.IDENT).val

        # Inheritance  ^.Parent
        parent = None
        mixins = []
        interfaces = []

        while self._at(TT.CARET):
            self._adv()  # ^
            self._eat(TT.DOT)
            parent = self._eat(TT.IDENT).val

        # Mixin attachment  +~ MixinName
        while self._at(TT.MIXIN_ATT):
            self._adv()
            mixins.append(self._eat(TT.IDENT).val)

        # Interface  >InterfaceName
        while self._at(TT.GT) and self._peek().tt == TT.IDENT:
            self._adv()
            interfaces.append(self._eat(TT.IDENT).val)

        # Field list  :field_type,field_type
        fields = []
        if self._at(TT.COLON):
            self._adv()
            raw, _ = self._parse_params()
            fields = raw

        body = self._parse_block()
        return ClassDef(name=name, parent=parent, mixins=mixins,
                        interfaces=interfaces, fields=fields,
                        body=body, line=tok.line, col=tok.col)

    def _parse_mixin_def(self) -> MixinDef:
        tok = self._cur()
        self._adv()  # ~
        self._adv()  # .
        name = self._eat(TT.IDENT).val
        body = self._parse_block()
        return MixinDef(name=name, body=body, line=tok.line, col=tok.col)

    def _parse_iface_def(self) -> IfaceDef:
        tok = self._cur()
        self._adv()  # >
        name = self._eat(TT.IDENT).val
        body = self._parse_block()
        return IfaceDef(name=name, body=body, line=tok.line, col=tok.col)

    # ── Control flow ─────────────────────────────────────────────────

    def _parse_return(self) -> ReturnStmt:
        tok = self._adv()  # ->
        val = None
        if not self._at(TT.NEWLINE, TT.EOF, TT.DEDENT):
            val = self._parse_expr()
        self._eat_newline()
        return ReturnStmt(value=val, line=tok.line, col=tok.col)

    def _parse_yield_stmt(self) -> YieldStmt:
        tok = self._adv()  # ->|
        val = None
        if not self._at(TT.NEWLINE, TT.EOF, TT.DEDENT):
            val = self._parse_expr()
        self._eat_newline()
        return YieldStmt(value=val, line=tok.line, col=tok.col)

    def _parse_if(self) -> IfStmt:
        tok = self._adv()  # ?
        cond = self._parse_expr()
        then = self._parse_block()

        elifs = []
        else_ = None

        while True:
            self._skip_newlines()
            if self._at(TT.ELIF):   # ??
                self._adv()
                ec = self._parse_expr()
                eb = self._parse_block()
                elifs.append((ec, eb))
            elif self._at(TT.ELSE): # ––
                self._adv()
                else_ = self._parse_block()
                break
            else:
                break

        return IfStmt(cond=cond, then=then, elifs=elifs, else_=else_,
                      line=tok.line, col=tok.col)

    def _parse_while(self) -> WhileStmt:
        tok = self._adv()  # !!
        cond = self._parse_expr()
        body = self._parse_block()

        else_      = None
        loop_else_ = None

        self._skip_newlines()
        if self._at(TT.ELSE):       # ––
            self._adv()
            else_ = self._parse_block()
        elif self._at(TT.LOOP_ELSE): # ——
            self._adv()
            loop_else_ = self._parse_block()

        return WhileStmt(cond=cond, body=body, else_=else_,
                         loop_else=loop_else_, line=tok.line, col=tok.col)

    def _parse_for(self) -> ForStmt:
        tok = self._adv()  # for
        var = self._eat(TT.IDENT).val
        self._eat(TT.HASH)
        iterable = self._parse_expr()
        body = self._parse_block()

        else_      = None
        loop_else_ = None

        self._skip_newlines()
        if self._at(TT.ELSE):
            self._adv()
            else_ = self._parse_block()
        elif self._at(TT.LOOP_ELSE):
            self._adv()
            loop_else_ = self._parse_block()

        return ForStmt(var=var, iterable=iterable, body=body,
                       else_=else_, loop_else=loop_else_,
                       line=tok.line, col=tok.col)

    def _parse_try(self) -> TryCatch:
        tok = self._adv()  # [?]
        body = self._parse_block()

        handlers = []
        self._skip_newlines()
        while self._at(TT.CATCH):   # [!
            self._adv()
            type_name = self._eat(TT.IDENT).val
            var_name  = None
            if self._at(TT.IDENT):
                var_name = self._adv().val
            self._eat(TT.RBRACKET)
            hbody = self._parse_block()
            handlers.append((type_name, var_name, hbody))
            self._skip_newlines()

        finally_ = None
        if self._at(TT.FINALLY):    # [!!]
            self._adv()
            finally_ = self._parse_block()

        return TryCatch(body=body, handlers=handlers, finally_=finally_,
                        line=tok.line, col=tok.col)

    def _parse_import(self) -> ImportStmt:
        tok = self._adv()  # ~>
        module = self._eat(TT.IDENT).val

        # ~> module:name,name  — specific imports
        names = []
        if self._at(TT.COLON):
            self._adv()
            names.append(self._eat(TT.IDENT).val)
            while self._at(TT.COMMA):
                self._adv()
                names.append(self._eat(TT.IDENT).val)

        # alias
        alias = None
        if self._at(TT.IDENT) and self._cur().val == 'as':
            self._adv()
            alias = self._eat(TT.IDENT).val

        self._eat_newline()
        return ImportStmt(module=module, names=names, alias=alias,
                          line=tok.line, col=tok.col)

    def _parse_extern_c(self) -> ExternC:
        tok = self._adv()  # ~C
        # Syntax:  ~C :c_name:params[ret]
        self._eat(TT.COLON)           # leading :
        c_name = self._eat(TT.IDENT).val
        params = []
        if self._at(TT.COLON):        # optional :params
            self._adv()
            params, _ = self._parse_params()
        ret = self._parse_return_type_brackets() if self._at(TT.LBRACKET) else None
        self._eat_newline()
        return ExternC(c_name=c_name, params=params, return_type=ret,
                       line=tok.line, col=tok.col)

    def _parse_namespace(self) -> Namespace:
        tok = self._adv()  # ~[
        name = self._eat(TT.IDENT).val
        self._eat(TT.RBRACKET)
        body = self._parse_block()
        return Namespace(name=name, body=body, line=tok.line, col=tok.col)

    def _parse_expr_stmt(self) -> ExprStmt | None:
        tok = self._cur()
        if self._at(TT.NEWLINE):
            self._adv()
            return None
        expr = self._parse_expr()
        self._eat_newline()
        return ExprStmt(expr=expr, line=tok.line, col=tok.col)

    def _eat_newline(self):
        if self._at(TT.NEWLINE):
            self._adv()

    # ══════════════════════════════════════════════════════════════════
    # Pratt expression parser
    # ══════════════════════════════════════════════════════════════════

    def _parse_expr(self, min_bp: int = 0, call_arg: bool = False) -> Any:
        """
        Pratt parser: parse an expression with left-binding power > min_bp.
        If call_arg=True, stops at +/- when followed by a new call (IDENT COLON),
        so that  fib:n - 1 + fib:n - 2  parses as  fib(n-1) + fib(n-2).
        """
        left = self._parse_prefix()

        while True:
            tok = self._cur()
            tt  = tok.tt

            # ── Infix: assignment  :<  (right-assoc, bp=10) ─────────
            if tt == TT.ASSIGN:
                if _BP_ASSIGN <= min_bp: break
                self._adv()
                right = self._parse_expr(_BP_ASSIGN - 1)   # right-assoc
                left  = Assign(target=left, value=right,
                               line=tok.line, col=tok.col)
                continue

            # ── Infix: augmented assign  @:<  (right-assoc) ─────────
            if tt == TT.AUG_ASSIGN:
                if _BP_ASSIGN <= min_bp: break
                self._adv()
                # Next token is the operator (+, -, *, /, etc.)
                op_tok = self._adv()
                value  = self._parse_expr(_BP_ASSIGN - 1)
                left   = AugAssign(target=left, op=op_tok.val, value=value,
                                   line=tok.line, col=tok.col)
                continue

            # ── Infix: pipe  |>  ─────────────────────────────────────
            if tt == TT.PIPE:
                if _BP_PIPE <= min_bp: break
                self._adv()
                right = self._parse_expr(_BP_PIPE)
                left  = Pipe(left=left, right=right, line=tok.line, col=tok.col)
                continue

            # ── Infix: null coalesce  ?:  ────────────────────────────
            if tt == TT.NULL_COAL:
                if _BP_COAL <= min_bp: break
                self._adv()
                right = self._parse_expr(_BP_COAL)
                left  = NullCoalesce(left=left, right=right,
                                     line=tok.line, col=tok.col)
                continue

            # ── Infix: ternary  ? then –– else  (right-assoc) ───────
            if tt == TT.IF:
                if _BP_TERNARY <= min_bp: break
                self._adv()
                then  = self._parse_expr(0)
                self._eat(TT.ELSE, "expected –– in ternary")
                else_ = self._parse_expr(_BP_TERNARY - 1)
                left  = Ternary(cond=left, then=then, else_=else_,
                                line=tok.line, col=tok.col)
                continue

            # ── Infix: logical or  |  ────────────────────────────────
            if tt == TT.OR:
                if _BP_OR <= min_bp: break
                self._adv()
                right = self._parse_expr(_BP_OR)
                left  = BinOp(op='|', left=left, right=right,
                              line=tok.line, col=tok.col)
                continue

            # ── Infix: logical and  &  ───────────────────────────────
            if tt == TT.AMP:
                if _BP_AND <= min_bp: break
                self._adv()
                right = self._parse_expr(_BP_AND)
                left  = BinOp(op='&', left=left, right=right,
                              line=tok.line, col=tok.col)
                continue

            # ── Infix: comparison  == != < > <= >= ?<  ──────────────
            if tt in (TT.EQ, TT.NEQ, TT.LT, TT.GT, TT.LTE, TT.GTE):
                if _BP_CMP <= min_bp: break
                # In call_arg mode comparisons always terminate the argument:
                #   fn:x == 0  →  fn(x) == 0,  not  fn(x == 0)
                if call_arg: break
                op = self._adv().val
                right = self._parse_expr(_BP_CMP)  # non-assoc
                left  = BinOp(op=op, left=left, right=right,
                              line=tok.line, col=tok.col)
                continue

            if tt == TT.CONTAINS:   # ?<
                if _BP_CMP <= min_bp: break
                self._adv()
                right = self._parse_expr(_BP_CMP)
                left  = BinOp(op='?<', left=left, right=right,
                              line=tok.line, col=tok.col)
                continue

            # ── Infix: range  ..  ..=  ───────────────────────────────
            if tt in (TT.RANGE, TT.RANGE_INC):
                if _BP_RANGE <= min_bp: break
                inclusive = (tt == TT.RANGE_INC)
                self._adv()
                end  = self._parse_expr(_BP_RANGE)
                # optional step  :step
                step = None
                if self._at(TT.COLON) and not self._at(TT.DCOLON):
                    self._adv()
                    step = self._parse_expr(_BP_RANGE + 1)
                left = RangeExpr(start=left, end=end, step=step,
                                 inclusive=inclusive,
                                 line=tok.line, col=tok.col)
                continue

            # ── Infix: addition  + -  ────────────────────────────────
            if tt in (TT.PLUS, TT.MINUS):
                if _BP_ADD <= min_bp: break
                # Call-arg boundary: stop if +/- is immediately followed by
                # a new bare call  (IDENT COLON)  so that
                #   fib:n - 1 + fib:n - 2  →  fib(n-1) + fib(n-2)
                # but  factorial:n - 1  →  factorial(n-1)  still works because
                # the token after - is an INT/IDENT, not IDENT COLON.
                if call_arg:
                    nxt1 = self._peek(1)   # token after +/-
                    nxt2 = self._peek(2)   # token after that
                    # Stop at new function call:   + name:
                    if nxt1.tt == TT.IDENT and nxt2.tt == TT.COLON:
                        break
                    # Stop at self-field access:   + @:field
                    if nxt1.tt == TT.SELF:
                        break
                op    = self._adv().val
                right = self._parse_expr(_BP_ADD, call_arg=call_arg)
                left  = BinOp(op=op, left=left, right=right,
                              line=tok.line, col=tok.col)
                continue

            # ── Infix: multiplication  * / %  ───────────────────────
            if tt in (TT.STAR, TT.SLASH, TT.PERCENT):
                if _BP_MUL <= min_bp: break
                op    = self._adv().val
                right = self._parse_expr(_BP_MUL)
                left  = BinOp(op=op, left=left, right=right,
                              line=tok.line, col=tok.col)
                continue

            # ── Infix: power  **  (right-assoc) ─────────────────────
            if tt == TT.POWER:
                if _BP_POW <= min_bp: break
                self._adv()
                right = self._parse_expr(_BP_POW - 1)   # right-assoc
                left  = BinOp(op='**', left=left, right=right,
                              line=tok.line, col=tok.col)
                continue

            # ── Infix: send-to-generator  <<|  ───────────────────────
            if tt == TT.SEND_GEN:
                if _BP_ADD <= min_bp: break
                self._adv()
                right = self._parse_expr(_BP_ADD)
                left  = SendGen(gen=left, value=right,
                                line=tok.line, col=tok.col)
                continue

            # ── Infix: throw-into-generator  <!|  ────────────────────
            if tt == TT.THROW_GEN:
                if _BP_ADD <= min_bp: break
                self._adv()
                right = self._parse_expr(_BP_ADD)
                left  = ThrowGen(gen=left, error=right,
                                 line=tok.line, col=tok.col)
                continue

            # ── Postfix: property access  :prop  ─────────────────────
            if tt == TT.COLON and self._peek().tt == TT.IDENT:
                if _BP_POSTFIX <= min_bp: break
                self._adv()
                prop = self._adv().val
                left = PropAccess(obj=left, prop=prop,
                                  line=tok.line, col=tok.col)
                continue

            # ── Postfix: safe property access  :?prop  ───────────────
            if tt == TT.SAFE_PROP and self._peek().tt == TT.IDENT:
                if _BP_POSTFIX <= min_bp: break
                self._adv()
                prop = self._adv().val
                left = SafePropAccess(obj=left, prop=prop,
                                      line=tok.line, col=tok.col)
                continue

            # ── Postfix: method call  ::method:args  ─────────────────
            if tt == TT.DCOLON and self._peek().tt == TT.IDENT:
                if _BP_POSTFIX <= min_bp: break
                self._adv()
                method = self._adv().val
                args   = self._parse_call_args()
                left   = MethodCall(obj=left, method=method, args=args,
                                    line=tok.line, col=tok.col)
                continue

            # ── Postfix: safe method call  ::?method:args  ───────────
            if tt == TT.SAFE_METH and self._peek().tt == TT.IDENT:
                if _BP_POSTFIX <= min_bp: break
                self._adv()
                method = self._adv().val
                args   = self._parse_call_args()
                left   = SafeMethodCall(obj=left, method=method, args=args,
                                        line=tok.line, col=tok.col)
                continue

            # ── Postfix: subscript  [index]  ─────────────────────────
            if tt == TT.LBRACKET:
                if _BP_POSTFIX <= min_bp: break
                self._adv()
                idx  = self._parse_expr()
                self._eat(TT.RBRACKET)
                left = Subscript(obj=left, index=idx,
                                 line=tok.line, col=tok.col)
                continue

            # ── Postfix: partial application  $  ─────────────────────
            if tt == TT.PARTIAL:
                if _BP_POSTFIX <= min_bp: break
                self._adv()
                args = []
                if self._at(TT.COLON):
                    args = self._parse_call_args()
                left = PartialApp(fn=left, args=args,
                                  line=tok.line, col=tok.col)
                continue

            # ── Postfix: not-null assertion  !~  ─────────────────────
            if tt == TT.NOT_NULL:
                if _BP_POSTFIX <= min_bp: break
                self._adv()
                left = NotNullExpr(value=left, line=tok.line, col=tok.col)
                continue

            # ── Postfix: length  ||  ─────────────────────────────────
            if tt == TT.SLICE:   # reused as || for length
                if _BP_POSTFIX <= min_bp: break
                self._adv()
                left = LenExpr(value=left, line=tok.line, col=tok.col)
                continue

            # No more infix/postfix operators
            break

        return left

    # ── Prefix (nud) parsers ─────────────────────────────────────────

    def _parse_prefix(self) -> Any:
        tok = self._cur()
        tt  = tok.tt

        # ── Integer literal ──────────────────────────────────────────
        if tt == TT.INT:
            self._adv()
            return IntLit(value=int(tok.val), line=tok.line, col=tok.col)

        # ── Float literal ────────────────────────────────────────────
        if tt == TT.FLOAT:
            self._adv()
            return FloatLit(value=float(tok.val), line=tok.line, col=tok.col)

        # ── String literal ───────────────────────────────────────────
        if tt == TT.STR:
            self._adv()
            return StrLit(value=tok.val, line=tok.line, col=tok.col)

        # ── Interpolated string  `...`  ──────────────────────────────
        if tt == TT.ISTR_START:
            return self._parse_interp_str()

        # ── Bool literals ────────────────────────────────────────────
        if tt == TT.TRUE:
            self._adv()
            return BoolLit(value=True, line=tok.line, col=tok.col)

        if tt == TT.FALSE:
            self._adv()
            return BoolLit(value=False, line=tok.line, col=tok.col)

        # ── Null  ~  (standalone tilde) ──────────────────────────────
        if tt == TT.TILDE:
            self._adv()
            return NullLit(line=tok.line, col=tok.col)

        # ── Identifier ───────────────────────────────────────────────
        if tt == TT.IDENT:
            self._adv()
            name = tok.val

            # bare call via colon  name:arg,arg
            if self._at(TT.COLON) and not self._peek().tt == TT.COLON:
                if self._peek().tt not in (TT.NEWLINE, TT.EOF,
                                           TT.INDENT, TT.DEDENT):
                    self._adv()  # :
                    args = self._parse_arg_list()
                    return Call(fn=Ident(name, tok.line, tok.col), args=args,
                                line=tok.line, col=tok.col)

            return Ident(name=name, line=tok.line, col=tok.col)

        # ── Self  @  ─────────────────────────────────────────────────
        if tt == TT.SELF:
            self._adv()
            # @:name:args  — self-call or property access
            if self._at(TT.COLON):
                self._adv()
                if self._at(TT.IDENT):
                    name = self._adv().val
                    args = self._parse_call_args()
                    return SelfCall(name=name, args=args,
                                    line=tok.line, col=tok.col)
            return SelfExpr(line=tok.line, col=tok.col)

        # ── Class reference  .ClassName  ─────────────────────────────
        if tt == TT.DOT and self._peek().tt == TT.IDENT:
            self._adv()
            name = self._adv().val
            # .ClassName:arg,arg  — constructor call
            if self._at(TT.COLON):
                self._adv()
                args = self._parse_arg_list()
                return Call(fn=ClassRef(name, tok.line, tok.col), args=args,
                            line=tok.line, col=tok.col)
            return ClassRef(name=name, line=tok.line, col=tok.col)

        # ── Unary  !  ────────────────────────────────────────────────
        if tt == TT.NOT:
            self._adv()
            operand = self._parse_expr(_BP_UNARY)
            return UnaryOp(op='!', operand=operand, line=tok.line, col=tok.col)

        # ── Unary  -  ────────────────────────────────────────────────
        if tt == TT.MINUS:
            self._adv()
            operand = self._parse_expr(_BP_UNARY)
            return UnaryOp(op='-', operand=operand, line=tok.line, col=tok.col)

        # ── Yield expression  ->|  ───────────────────────────────────
        if tt == TT.YIELD:
            self._adv()
            val = None
            if not self._at(TT.NEWLINE, TT.EOF, TT.DEDENT, TT.RBRACKET,
                             TT.RPAREN, TT.RBRACE, TT.COMMA):
                val = self._parse_expr(_BP_ASSIGN)
            return YieldExpr(value=val, line=tok.line, col=tok.col)

        # ── Yield-from  ->|>  ────────────────────────────────────────
        if tt == TT.YIELD_FROM:
            self._adv()
            val = self._parse_expr(_BP_ASSIGN)
            return YieldFromExpr(value=val, line=tok.line, col=tok.col)

        # ── Await  ~>  ───────────────────────────────────────────────
        if tt == TT.TILDE_GT:
            self._adv()
            val = self._parse_expr(_BP_UNARY)
            return AwaitExpr(value=val, line=tok.line, col=tok.col)

        # ── Gather  ~| (expr, ...)  ──────────────────────────────────
        if tt == TT.GATHER:
            self._adv()
            self._eat(TT.LPAREN)
            exprs = self._parse_expr_list(TT.RPAREN)
            self._eat(TT.RPAREN)
            return GatherExpr(exprs=exprs, line=tok.line, col=tok.col)

        # ── Fire-and-forget  ~!  ─────────────────────────────────────
        if tt == TT.FIRE:
            self._adv()
            val = self._parse_expr(_BP_UNARY)
            return FireForget(value=val, line=tok.line, col=tok.col)

        # ── Receive channel  ~>>  ────────────────────────────────────
        if tt == TT.RECV_CHAN:
            self._adv()
            chan = self._parse_expr(_BP_UNARY)
            return RecvChan(chan=chan, line=tok.line, col=tok.col)

        # ── Weak ref  ~&  ────────────────────────────────────────────
        if tt == TT.WEAK_REF:
            self._adv()
            val = self._parse_expr(_BP_UNARY)
            return WeakRefExpr(value=val, line=tok.line, col=tok.col)

        # ── Hash / iterate / length  #  ──────────────────────────────
        if tt == TT.HASH:
            self._adv()
            val = self._parse_expr(_BP_UNARY)
            return IterExpr(value=val, line=tok.line, col=tok.col)

        # ── List / comprehension  [...]  ─────────────────────────────
        if tt == TT.LBRACKET:
            return self._parse_list_or_comp()

        # ── Dict / set literal  {...}  ───────────────────────────────
        if tt == TT.LBRACE:
            return self._parse_dict()

        # ── Set literal  {| ... |}  ──────────────────────────────────
        if tt == TT.SET_OPEN:
            return self._parse_set()

        # ── Generator expression  |[ expr | var #iter ]  ─────────────
        if tt == TT.GEN_OPEN:
            return self._parse_gen_expr()

        # ── Tuple / parenthesised  (...)  ────────────────────────────
        if tt == TT.LPAREN:
            return self._parse_paren_or_tuple()

        # ── Lambda  \params -> expr  ─────────────────────────────────
        if tt == TT.BACKSLASH:
            return self._parse_lambda()

        # ── Bare method/stdlib call  ::name:args  ────────────────────
        if tt == TT.DCOLON:
            self._adv()
            name = self._eat(TT.IDENT).val
            args = self._parse_call_args()
            return Call(fn=Ident(name, tok.line, tok.col), args=args,
                        line=tok.line, col=tok.col)

        # ── Generator instantiation  |::fn:args  ─────────────────────
        # |::counter:5  →  Call(Ident('counter'), [5])
        # (counter_init was registered under 'counter' by _emit_gen_fn)
        if tt == TT.OR and self._peek().tt == TT.DCOLON:
            self._adv()   # |
            self._adv()   # ::
            fn_name = self._eat(TT.IDENT).val
            args    = self._parse_call_args()
            return Call(fn=Ident(fn_name, tok.line, tok.col), args=args,
                        line=tok.line, col=tok.col)

        raise self._err(f"unexpected token in expression: {tt.name} {tok.val!r}")

    # ── Helper: parse call arguments after the initial colon ────────

    def _parse_call_args(self) -> list:
        """Parse arguments after ::method or :fn — returns list of exprs."""
        if self._at(TT.COLON):
            self._adv()
            return self._parse_arg_list()
        return []

    def _parse_arg_list(self) -> list:
        """Comma-separated expression list (no surrounding brackets)."""
        args = []
        stop = {TT.NEWLINE, TT.EOF, TT.DEDENT, TT.RBRACKET,
                TT.RPAREN, TT.RBRACE, TT.SET_CLOSE}
        if self._cur().tt in stop:
            return args
        args.append(self._parse_expr(_BP_ASSIGN, call_arg=True))
        while self._at(TT.COMMA):
            self._adv()
            if self._cur().tt in stop:
                break
            args.append(self._parse_expr(_BP_ASSIGN, call_arg=True))
        return args

    def _parse_expr_list(self, stop_tt: TT) -> list:
        exprs = []
        while not self._at(stop_tt, TT.EOF):
            exprs.append(self._parse_expr(_BP_ASSIGN))
            if self._at(TT.COMMA):
                self._adv()
        return exprs

    # ── Collection literals ──────────────────────────────────────────

    def _parse_list_or_comp(self) -> Any:
        """[ items ]  or  [ expr | var #iter ? cond ]"""
        tok = self._adv()  # [
        if self._at(TT.RBRACKET):
            self._adv()
            return ListLit(items=[], line=tok.line, col=tok.col)

        first = self._parse_expr()

        # Comprehension:  [ expr | var #iter ]
        if self._at(TT.OR):
            self._adv()
            var      = self._eat(TT.IDENT).val
            self._eat(TT.HASH)
            iterable = self._parse_expr()
            cond     = None
            if self._at(TT.IF):
                self._adv()
                cond = self._parse_expr()
            self._eat(TT.RBRACKET)
            return Comprehension(kind='list', expr=first, var=var,
                                 iterable=iterable, cond=cond,
                                 line=tok.line, col=tok.col)

        # Plain list
        items = [first]
        while self._at(TT.COMMA):
            self._adv()
            if self._at(TT.RBRACKET):
                break
            items.append(self._parse_expr())
        self._eat(TT.RBRACKET)
        return ListLit(items=items, line=tok.line, col=tok.col)

    def _parse_dict(self) -> Any:
        """{ k ~ v, k ~ v }"""
        tok = self._adv()  # {
        pairs = []
        while not self._at(TT.RBRACE, TT.EOF):
            k = self._parse_expr()
            self._eat(TT.TILDE)
            v = self._parse_expr()
            pairs.append((k, v))
            if self._at(TT.COMMA):
                self._adv()
        self._eat(TT.RBRACE)
        return DictLit(pairs=pairs, line=tok.line, col=tok.col)

    def _parse_set(self) -> Any:
        """{| a, b |}"""
        tok = self._adv()  # {|
        items = []
        while not self._at(TT.SET_CLOSE, TT.EOF):
            items.append(self._parse_expr())
            if self._at(TT.COMMA):
                self._adv()
        self._eat(TT.SET_CLOSE)
        return SetLit(items=items, line=tok.line, col=tok.col)

    def _parse_gen_expr(self) -> Any:
        """|[ expr | var #iter ]"""
        tok = self._adv()  # |[
        expr = self._parse_expr()
        self._eat(TT.OR)
        var  = self._eat(TT.IDENT).val
        self._eat(TT.HASH)
        iterable = self._parse_expr()
        cond = None
        if self._at(TT.IF):
            self._adv()
            cond = self._parse_expr()
        self._eat(TT.RBRACKET)
        return Comprehension(kind='gen', expr=expr, var=var,
                             iterable=iterable, cond=cond,
                             line=tok.line, col=tok.col)

    def _parse_paren_or_tuple(self) -> Any:
        tok = self._adv()  # (
        if self._at(TT.RPAREN):
            self._adv()
            return TupleLit(items=[], line=tok.line, col=tok.col)
        first = self._parse_expr()
        if self._at(TT.COMMA):
            items = [first]
            while self._at(TT.COMMA):
                self._adv()
                if self._at(TT.RPAREN):
                    break
                items.append(self._parse_expr())
            self._eat(TT.RPAREN)
            return TupleLit(items=items, line=tok.line, col=tok.col)
        self._eat(TT.RPAREN)
        return first   # just parentheses for grouping

    def _parse_lambda(self) -> Lambda:
        r"""\ param, param -> expr"""
        tok = self._adv()  # \
        params = []
        while not self._at(TT.ARROW, TT.EOF):
            p_tok = self._eat(TT.IDENT)
            name, ty = self._split_param_name(p_tok.val, p_tok)
            params.append(Param(name=name, type_=ty))
            if self._at(TT.COMMA):
                self._adv()
        self._eat(TT.ARROW)
        body = self._parse_expr()
        return Lambda(params=params, body=body, line=tok.line, col=tok.col)

    def _parse_interp_str(self) -> InterpolatedStr:
        tok = self._adv()  # ISTR_START
        parts = []
        while not self._at(TT.ISTR_END, TT.EOF):
            if self._at(TT.ISTR_TEXT):
                parts.append(StrLit(value=self._adv().val))
            elif self._at(TT.ISTR_EXPR_S):
                self._adv()  # {
                parts.append(self._parse_expr())
                self._eat(TT.ISTR_EXPR_E)
        if self._at(TT.ISTR_END):
            self._adv()
        return InterpolatedStr(parts=parts, line=tok.line, col=tok.col)


# ══════════════════════════════════════════════════════════════════════
# Pretty printer — walks the AST and produces indented text
# ══════════════════════════════════════════════════════════════════════

def _pretty(node: Any, indent: int = 0) -> str:
    pad = "  " * indent
    name = type(node).__name__

    if node is None:
        return f"{pad}None"

    if isinstance(node, (int, float, str, bool)):
        return f"{pad}{node!r}"

    if isinstance(node, list):
        if not node:
            return f"{pad}[]"
        lines = [f"{pad}["]
        for item in node:
            lines.append(_pretty(item, indent + 1))
        lines.append(f"{pad}]")
        return "\n".join(lines)

    if not hasattr(node, '__dataclass_fields__'):
        return f"{pad}{node!r}"

    fields = node.__dataclass_fields__
    # Filter out line/col for cleanliness
    display = {k: getattr(node, k) for k in fields
               if k not in ('line', 'col')}

    if not display:
        return f"{pad}{name}()"

    # Single-field scalars inline
    if len(display) == 1:
        k, v = next(iter(display.items()))
        if isinstance(v, (int, float, str, bool, type(None))):
            return f"{pad}{name}({k}={v!r})"

    lines = [f"{pad}{name}"]
    for k, v in display.items():
        child = _pretty(v, indent + 1)
        child_stripped = child.lstrip()
        if '\n' not in child_stripped:
            lines.append(f"{pad}  {k}: {child_stripped}")
        else:
            lines.append(f"{pad}  {k}:")
            lines.append(child)
    return "\n".join(lines)


def pretty(node: Any) -> str:
    return _pretty(node)


# ══════════════════════════════════════════════════════════════════════
# Visitor base class  (for codegen / analysis passes)
# ══════════════════════════════════════════════════════════════════════

class NodeVisitor:
    """
    Walk the AST by calling visit(node).
    Subclass and implement visit_<NodeTypeName> methods.
    Falls back to generic_visit which recurses into all fields.
    """

    def visit(self, node: Any) -> Any:
        if node is None:
            return None
        name = type(node).__name__
        method = getattr(self, f"visit_{name}", self.generic_visit)
        return method(node)

    def generic_visit(self, node: Any) -> None:
        if not hasattr(node, '__dataclass_fields__'):
            return
        for field_name in node.__dataclass_fields__:
            val = getattr(node, field_name)
            if field_name in ('line', 'col'):
                continue
            if isinstance(val, list):
                for item in val:
                    self.visit(item)
            elif hasattr(val, '__dataclass_fields__'):
                self.visit(val)
            elif isinstance(val, tuple):
                for item in val:
                    if hasattr(item, '__dataclass_fields__'):
                        self.visit(item)


# ══════════════════════════════════════════════════════════════════════
# CLI driver + smoke test
# ══════════════════════════════════════════════════════════════════════

_SMOKE = """\
~> math:sqrt

.Point:x_int,y_int
    :dist:@,.Point[float]
        dx :< @:x - .Point:x
        dy :< @:y - .Point:y
        -> (dx**2 + dy**2)**0.5

:fibonacci:n_int[int]
    ? n <= 1
        -> n
    -> @:n-1 + @:n-2

|:counter:n_int[|int]
    i :< 0
    !! i < n
        received :< ->| i
        i @:< + 1

~:fetch:url_str[str]
    data :< ~> ::get:url
    -> data

msg :< `hello {name}, score={score*2}`
cfg:?db:?host ?: "localhost"
"""

def main():
    if len(sys.argv) < 2:
        print("── Smoke test ──")
        tokens = lex(_SMOKE, "<smoke>")
        tree   = Parser(tokens, "<smoke>").parse()
        print(pretty(tree))
        return

    filename = sys.argv[1]
    with open(filename, encoding='utf-8') as f:
        src = f.read()

    tokens = lex(src, filename)
    tree   = Parser(tokens, filename).parse()
    print(pretty(tree))


if __name__ == '__main__':
    main()
