# -*- coding: utf-8 -*-
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 10):
    _sys.exit("Error: SL requires Python 3.10+. Running: " + _sys.version)
del _sys
"""
lexer.py  ·  Tokenizer for the symbolic language
─────────────────────────────────────────────────
Features
  · Longest-match operator table (4-char → 1-char)
  · Python-style significant indentation (INDENT / DEDENT)
  · Implicit line continuation inside  ( ) [ ] { }
  · Backtick interpolated strings  `text {expr} text`
  · Block comments  #| ... |#
  · Unicode em/en-dash aliases for  ––  and  ——
  · ARC-safe: ~  tokenised contextually (TILDE / TILDE_GT / etc.)

Usage
  tokens = Lexer(source, filename).tokenize()
  # or from the CLI:
  python lexer.py source.sl
"""


import sys
from dataclasses import dataclass
from enum import Enum, auto
from typing import Generator, List


# ── Token types ────────────────────────────────────────────────────────────────

class TT(Enum):
    # ── Literals ─────────────────────────────────────────────────────────────
    INT         = auto()   # 42
    FLOAT       = auto()   # 3.14
    STR         = auto()   # "hello"
    ISTR_START  = auto()   # ` (opening backtick)
    ISTR_TEXT   = auto()   # literal segment inside `…`
    ISTR_EXPR_S = auto()   # { inside interpolation
    ISTR_EXPR_E = auto()   # } inside interpolation
    ISTR_END    = auto()   # ` (closing backtick)
    TRUE        = auto()   # true
    FALSE       = auto()   # false

    # ── Identifiers & keywords ───────────────────────────────────────────────
    IDENT       = auto()
    FOR         = auto()
    KW_INT      = auto()   # int
    KW_FLOAT    = auto()   # float
    KW_STR      = auto()   # str
    KW_BOOL     = auto()   # bool
    KW_VOID     = auto()   # void
    KW_FN       = auto()   # fn  (function type annotation)

    # ── Assignment ───────────────────────────────────────────────────────────
    ASSIGN      = auto()   # :<
    AUG_ASSIGN  = auto()   # @:<   (var @:< + n)

    # ── Arrow / yield ────────────────────────────────────────────────────────
    YIELD_FROM  = auto()   # ->|>
    YIELD       = auto()   # ->|
    ARROW       = auto()   # ->

    # ── Colon family ─────────────────────────────────────────────────────────
    SAFE_METH   = auto()   # ::?
    DCOLON      = auto()   # ::
    SAFE_PROP   = auto()   # :?
    COLON       = auto()   # :

    # ── Dot / range / spread ─────────────────────────────────────────────────
    DOT         = auto()   # .
    SPREAD      = auto()   # ...
    RANGE_INC   = auto()   # ..=
    RANGE       = auto()   # ..

    # ── Tilde / async / null family ──────────────────────────────────────────
    RECV_CHAN   = auto()   # ~>>   (receive from channel)
    TILDE_GT    = auto()   # ~>    (import OR await — parser disambiguates)
    GATHER      = auto()   # ~|    (concurrent gather)
    FIRE        = auto()   # ~!    (fire-and-forget)
    WEAK_REF    = auto()   # ~&    (ARC weak reference)
    ASYNC_ITER  = auto()   # ~#    (async for)
    NS_OPEN     = auto()   # ~[    (namespace block open)
    CEXTERN     = auto()   # ~C    (C FFI declaration)
    TILDE       = auto()   # ~     (null value / mixin def / async fn prefix)

    # ── Pipe / generator / or / slice ────────────────────────────────────────
    PIPE        = auto()   # |>
    GEN_OPEN    = auto()   # |[   (lazy generator expression)
    SLICE       = auto()   # ||
    SET_CLOSE   = auto()   # |}
    OR          = auto()   # |

    # ── Hash ─────────────────────────────────────────────────────────────────
    HASH        = auto()   # #    (iterate / length prefix)

    # ── Comparison ───────────────────────────────────────────────────────────
    EQ          = auto()   # ==
    NEQ         = auto()   # !=
    LTE         = auto()   # <=
    GTE         = auto()   # >=
    CONTAINS    = auto()   # ?<   (membership test)
    LT          = auto()   # <
    GT          = auto()   # >

    # ── Angle / channel / generator ops ─────────────────────────────────────
    BREAK       = auto()   # <>
    SEND_CHAN   = auto()   # <<~
    SEND_GEN    = auto()   # <<|   (send into generator)
    THROW_GEN   = auto()   # <!|   (throw into generator)
    CONTINUE    = auto()   # >>

    # ── Conditional ──────────────────────────────────────────────────────────
    MATCH       = auto()   # ?|
    NULL_COAL   = auto()   # ?:
    ELIF        = auto()   # ??
    IF          = auto()   # ?
    LOOP_ELSE   = auto()   # ——  (double em-dash, loop-level else)
    ELSE        = auto()   # ––  (double en-dash or -- , if/elif else)

    # ── While / assert / not ─────────────────────────────────────────────────
    WHILE       = auto()   # !!
    NOT_NULL    = auto()   # !~
    ASSERT_KW   = auto()   # !?
    NOT         = auto()   # !

    # ── Compound bracket tokens ───────────────────────────────────────────────
    TRY         = auto()   # [?]
    FINALLY     = auto()   # [!!]
    CATCH       = auto()   # [!   (type identifier follows)
    VIS_PUB     = auto()   # [+]
    VIS_PRV     = auto()   # [-]
    VIS_PRO     = auto()   # [^]
    REQ         = auto()   # [req]
    LBRACKET    = auto()   # [
    RBRACKET    = auto()   # ]

    # ── Brace compounds ───────────────────────────────────────────────────────
    SET_OPEN    = auto()   # {|
    LBRACE      = auto()   # {
    RBRACE      = auto()   # }

    # ── Parens ────────────────────────────────────────────────────────────────
    LPAREN      = auto()
    RPAREN      = auto()

    # ── Arithmetic ────────────────────────────────────────────────────────────
    POWER       = auto()   # **
    MIXIN_ATT   = auto()   # +~   (mixin attachment)
    PLUS        = auto()   # +
    MINUS       = auto()   # -
    STAR        = auto()   # *
    SLASH       = auto()   # /
    PERCENT     = auto()   # %

    # ── Special prefix / infix ───────────────────────────────────────────────
    SELF        = auto()   # @    (self / current fn)
    PARTIAL     = auto()   # $    (partial application)
    BACKSLASH   = auto()   # \    (lambda)
    CARET       = auto()   # ^    (extends)
    AMP         = auto()   # &    (logical and)
    COMMA       = auto()
    DECORATOR   = auto()   # %    (at statement-start; parser recategorises from PERCENT)

    # ── Layout ────────────────────────────────────────────────────────────────
    NEWLINE     = auto()
    INDENT      = auto()
    DEDENT      = auto()
    EOF         = auto()


# Keyword map: identifiers that are reserved words
KEYWORDS: dict[str, TT] = {
    'for':   TT.FOR,
    'true':  TT.TRUE,
    'false': TT.FALSE,
    'int':   TT.KW_INT,
    'float': TT.KW_FLOAT,
    'str':   TT.KW_STR,
    'bool':  TT.KW_BOOL,
    'void':  TT.KW_VOID,
    'fn':    TT.KW_FN,
}

# Tokens that open a bracket scope (enable implicit line continuation)
_OPEN_BRACKETS  = {TT.LPAREN, TT.LBRACKET, TT.LBRACE,
                   TT.SET_OPEN, TT.GEN_OPEN, TT.NS_OPEN}
_CLOSE_BRACKETS = {TT.RPAREN, TT.RBRACKET, TT.RBRACE, TT.SET_CLOSE}


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass(slots=True)
class Token:
    tt:   TT
    val:  str
    line: int
    col:  int

    def __repr__(self) -> str:
        return f"Token({self.tt.name:<18} {self.val!r:<24} {self.line}:{self.col})"


class LexError(Exception):
    def __init__(self, msg: str, line: int, col: int, filename: str = ""):
        loc = f"{filename}:{line}:{col}" if filename else f"line {line}:{col}"
        super().__init__(f"LexError at {loc} — {msg}")
        self.line, self.col = line, col


# ── Lexer ──────────────────────────────────────────────────────────────────────

class Lexer:
    """
    Hand-written cursor lexer with maximal-munch operator matching.

    Operator table is pre-sorted by descending length so the first hit is
    always the longest possible match — no backtracking needed.

    Indentation rules
    -----------------
    • Only spaces count (tabs = 4 spaces, configurable via tab_width).
    • Blank lines and comment-only lines are skipped silently.
    • Bracket depth > 0 → implicit line continuation (no NEWLINE / INDENT / DEDENT).
    """

    def __init__(self, src: str, filename: str = "<stdin>", tab_width: int = 4):
        self.src       = src
        self.filename  = filename
        self.tab_width = tab_width
        self.pos       = 0
        self.line      = 1
        self.col       = 1
        self._indent   : list[int]  = [0]   # indentation stack (spaces)
        self._depth    : int        = 0     # bracket nesting depth
        self._pending  : list[Token] = []   # INDENT/DEDENT queue

    # ── Cursor primitives ───────────────────────────────────────────────────────

    def _ch(self, off: int = 0) -> str:
        i = self.pos + off
        return self.src[i] if i < len(self.src) else '\0'

    def _adv(self) -> str:
        ch = self.src[self.pos]
        self.pos += 1
        if ch == '\n':
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _match(self, s: str) -> bool:
        return self.src[self.pos: self.pos + len(s)] == s

    def _eat(self, s: str) -> bool:
        if self._match(s):
            for _ in s:
                self._adv()
            return True
        return False

    def _err(self, msg: str) -> LexError:
        return LexError(msg, self.line, self.col, self.filename)

    def _tok(self, tt: TT, val: str, line: int, col: int) -> Token:
        return Token(tt, val, line, col)

    # ── Public API ──────────────────────────────────────────────────────────────

    def tokenize(self) -> list[Token]:
        """Return all tokens as a list (filters nothing)."""
        return list(self._scan())

    def tokenize_filtered(self) -> list[Token]:
        """Return tokens, dropping NEWLINE/INDENT/DEDENT for quick testing."""
        layout = {TT.NEWLINE, TT.INDENT, TT.DEDENT}
        return [t for t in self._scan() if t.tt not in layout]

    # ── Main scanning loop ──────────────────────────────────────────────────────

    def _scan(self) -> Generator[Token, None, None]:
        while True:
            # Flush INDENT/DEDENT tokens emitted during newline processing
            while self._pending:
                yield self._pending.pop(0)

            if self.pos >= len(self.src):
                # Close any unclosed indentation levels
                while len(self._indent) > 1:
                    self._indent.pop()
                    yield Token(TT.DEDENT, '', self.line, self.col)
                yield Token(TT.EOF, '', self.line, self.col)
                return

            ch = self._ch()

            # ── Block comment  #| … |# ──────────────────────────────────────
            if self._match('#|'):
                self._adv(); self._adv()
                while self.pos < len(self.src):
                    if self._eat('|#'):
                        break
                    self._adv()
                continue

            # ── Line comment  # … EOL ───────────────────────────────────────
            if ch == '#':
                while self.pos < len(self.src) and self._ch() != '\n':
                    self._adv()
                continue

            # ── Newline + indentation ────────────────────────────────────────
            if ch == '\n':
                yield from self._handle_newline()
                continue

            # ── Inline whitespace ────────────────────────────────────────────
            if ch in ' \t\r':
                self._adv()
                continue

            line, col = self.line, self.col

            # ── Interpolated string  `…` ─────────────────────────────────────
            if ch == '`':
                yield from self._lex_interp(line, col)
                continue

            # ── Plain string  "…" ────────────────────────────────────────────
            if ch == '"':
                yield self._lex_str(line, col)
                continue

            # ── Number ───────────────────────────────────────────────────────
            if ch.isdigit():
                yield self._lex_num(line, col)
                continue

            # ── Identifier / keyword ─────────────────────────────────────────
            if ch.isalpha() or ch == '_':
                yield self._lex_ident(line, col)
                continue

            # ── Operators (maximal munch) ─────────────────────────────────────
            tok = self._lex_op(line, col)
            if tok is not None:
                if tok.tt in _OPEN_BRACKETS:
                    self._depth += 1
                elif tok.tt in _CLOSE_BRACKETS:
                    self._depth = max(0, self._depth - 1)
                yield tok
                continue

            raise self._err(f"Unexpected character: {ch!r}")

    # ── Newline / indentation handler ───────────────────────────────────────────

    def _handle_newline(self) -> Generator[Token, None, None]:
        nl_line, nl_col = self.line, self.col
        self._adv()  # consume \n

        # Measure indentation of the upcoming line
        spaces = 0
        while self.pos < len(self.src):
            c = self._ch()
            if c == ' ':
                spaces += 1
                self._adv()
            elif c == '\t':
                spaces += self.tab_width
                self._adv()
            else:
                break

        # Skip blank and comment-only lines
        if self.pos >= len(self.src):
            return
        if self._ch() in ('\n', '\r'):
            return
        if self._match('#'):
            return

        # Inside brackets: implicit continuation — suppress layout tokens
        if self._depth > 0:
            return

        yield Token(TT.NEWLINE, '\n', nl_line, nl_col)

        cur = self._indent[-1]
        if spaces > cur:
            self._indent.append(spaces)
            self._pending.append(Token(TT.INDENT, ' ' * spaces, self.line, 1))
        elif spaces < cur:
            while self._indent[-1] > spaces:
                self._indent.pop()
                self._pending.append(Token(TT.DEDENT, '', self.line, 1))
            if self._indent[-1] != spaces:
                raise self._err(
                    f"Dedent does not match any outer indent level "
                    f"(got {spaces}, expected {self._indent[-1]})"
                )

    # ── Sub-lexers ──────────────────────────────────────────────────────────────

    def _lex_num(self, line: int, col: int) -> Token:
        start = self.pos
        while self._ch().isdigit():
            self._adv()
        if self._ch() == '.' and self._ch(1).isdigit():
            self._adv()
            while self._ch().isdigit():
                self._adv()
            return Token(TT.FLOAT, self.src[start:self.pos], line, col)
        return Token(TT.INT, self.src[start:self.pos], line, col)

    def _lex_ident(self, line: int, col: int) -> Token:
        start = self.pos
        while self._ch().isalnum() or self._ch() == '_':
            self._adv()
        word = self.src[start:self.pos]
        return Token(KEYWORDS.get(word, TT.IDENT), word, line, col)

    def _lex_str(self, line: int, col: int) -> Token:
        """Lex a plain double-quoted string (no interpolation)."""
        self._adv()  # opening "
        buf: list[str] = []
        ESC = {'n': '\n', 't': '\t', 'r': '\r',
               '\\': '\\', '"': '"', '`': '`', '0': '\0'}
        while self.pos < len(self.src):
            ch = self._ch()
            if ch == '"':
                self._adv()
                break
            if ch == '\\':
                self._adv()
                e = self._adv()
                buf.append(ESC.get(e, e))
            elif ch == '\n':
                raise self._err("Unterminated string literal")
            else:
                buf.append(self._adv())
        return Token(TT.STR, ''.join(buf), line, col)

    def _lex_interp(self, line: int, col: int) -> Generator[Token, None, None]:
        """
        Lex a backtick interpolated string.
        Emits: ISTR_START  [ISTR_TEXT | (ISTR_EXPR_S <tokens> ISTR_EXPR_E)]*  ISTR_END
        """
        self._adv()  # opening `
        yield Token(TT.ISTR_START, '`', line, col)
        buf: list[str] = []

        while self.pos < len(self.src):
            ch = self._ch()

            if ch == '`':
                self._adv()
                if buf:
                    yield Token(TT.ISTR_TEXT, ''.join(buf), self.line, self.col)
                    buf.clear()
                yield Token(TT.ISTR_END, '`', self.line, self.col)
                return

            if ch == '{':
                if buf:
                    yield Token(TT.ISTR_TEXT, ''.join(buf), self.line, self.col)
                    buf.clear()
                el, ec = self.line, self.col
                self._adv()
                yield Token(TT.ISTR_EXPR_S, '{', el, ec)
                yield from self._lex_interp_expr()
            else:
                buf.append(self._adv())

        raise self._err("Unterminated interpolated string")

    def _lex_interp_expr(self) -> Generator[Token, None, None]:
        """Lex tokens inside { } of an interpolated string until matching }."""
        depth = 1
        while self.pos < len(self.src) and depth > 0:
            ch = self._ch()
            if ch in ' \t':
                self._adv()
                continue
            il, ic = self.line, self.col
            if ch == '{':
                depth += 1
                self._adv()
                yield Token(TT.LBRACE, '{', il, ic)
            elif ch == '}':
                depth -= 1
                self._adv()
                if depth == 0:
                    yield Token(TT.ISTR_EXPR_E, '}', il, ic)
                else:
                    yield Token(TT.RBRACE, '}', il, ic)
            elif ch == '"':
                yield self._lex_str(il, ic)
            elif ch == '`':
                yield from self._lex_interp(il, ic)
            elif ch.isdigit():
                yield self._lex_num(il, ic)
            elif ch.isalpha() or ch == '_':
                yield self._lex_ident(il, ic)
            else:
                tok = self._lex_op(il, ic)
                if tok:
                    yield tok
                else:
                    raise self._err(f"Unexpected char in interpolation: {ch!r}")

    # ── Operator table (maximal munch) ──────────────────────────────────────────

    # NOTE: This list is sorted by descending pattern length at class creation
    # time (see _sort_ops below) so the first match is always maximal.
    _RAW_OPS: list[tuple[str, TT]] = [
        # 5-char
        ('[req]', TT.REQ),
        # 4-char
        ('->|>', TT.YIELD_FROM),
        ('[!!]', TT.FINALLY),
        # 3-char
        ('->|',  TT.YIELD),
        ('@:<',  TT.AUG_ASSIGN),
        ('~>>',  TT.RECV_CHAN),
        ('<<~',  TT.SEND_CHAN),
        ('<<|',  TT.SEND_GEN),
        ('<!|',  TT.THROW_GEN),
        ('::?',  TT.SAFE_METH),
        ('..=',  TT.RANGE_INC),
        ('...',  TT.SPREAD),
        ('[?]',  TT.TRY),
        ('[+]',  TT.VIS_PUB),
        ('[-]',  TT.VIS_PRV),
        ('[^]',  TT.VIS_PRO),
        # 2-char (must come before their 1-char prefixes)
        (':?',   TT.SAFE_PROP),
        (':<',   TT.ASSIGN),
        ('::',   TT.DCOLON),
        ('->',   TT.ARROW),
        ('..',   TT.RANGE),
        ('~>',   TT.TILDE_GT),
        ('~|',   TT.GATHER),
        ('~!',   TT.FIRE),
        ('~&',   TT.WEAK_REF),
        ('~#',   TT.ASYNC_ITER),
        ('~[',   TT.NS_OPEN),
        ('~C',   TT.CEXTERN),
        ('|>',   TT.PIPE),
        ('|[',   TT.GEN_OPEN),
        ('||',   TT.SLICE),
        ('|}',   TT.SET_CLOSE),
        ('{|',   TT.SET_OPEN),
        ('<>',   TT.BREAK),
        ('>>',   TT.CONTINUE),
        ('**',   TT.POWER),
        ('==',   TT.EQ),
        ('!=',   TT.NEQ),
        ('<=',   TT.LTE),
        ('>=',   TT.GTE),
        ('?|',   TT.MATCH),
        ('?:',   TT.NULL_COAL),
        ('??',   TT.ELIF),
        ('?<',   TT.CONTAINS),
        ('+~',   TT.MIXIN_ATT),
        ('!!',   TT.WHILE),
        ('[!',   TT.CATCH),
        ('!~',   TT.NOT_NULL),
        ('!?',   TT.ASSERT_KW),
        # Unicode dash pairs
        ('\u2014\u2014', TT.LOOP_ELSE),   # ——  double em-dash
        ('\u2013\u2013', TT.ELSE),         # ––  double en-dash
        # ASCII fallbacks
        ('--',   TT.ELSE),
        # 1-char
        (':',    TT.COLON),
        ('.',    TT.DOT),
        ('~',    TT.TILDE),
        ('|',    TT.OR),
        ('#',    TT.HASH),
        ('<',    TT.LT),
        ('>',    TT.GT),
        ('+',    TT.PLUS),
        ('-',    TT.MINUS),
        ('*',    TT.STAR),
        ('/',    TT.SLASH),
        ('%',    TT.PERCENT),
        ('@',    TT.SELF),
        ('$',    TT.PARTIAL),
        ('\\',   TT.BACKSLASH),
        ('^',    TT.CARET),
        ('&',    TT.AMP),
        ('!',    TT.NOT),
        ('?',    TT.IF),
        (',',    TT.COMMA),
        ('(',    TT.LPAREN),
        (')',    TT.RPAREN),
        ('{',    TT.LBRACE),
        ('}',    TT.RBRACE),
        ('[',    TT.LBRACKET),
        (']',    TT.RBRACKET),
    ]

    # Sort once at class level — descending length, stable
    _OPS: list[tuple[str, TT]] = sorted(_RAW_OPS, key=lambda x: -len(x[0]))

    def _lex_op(self, line: int, col: int) -> Token | None:
        for pattern, tt in self._OPS:
            if self._match(pattern):
                for _ in pattern:
                    self._adv()
                return Token(tt, pattern, line, col)
        return None


# ── Convenience helpers ────────────────────────────────────────────────────────

def lex(src: str, filename: str = "<stdin>") -> list[Token]:
    """Tokenize a source string and return all tokens."""
    return Lexer(src, filename).tokenize()


def lex_file(path: str) -> list[Token]:
    """Tokenize a source file and return all tokens."""
    with open(path, encoding='utf-8') as f:
        return lex(f.read(), path)


# ── CLI driver ─────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print("usage: lexer.py <source.sl>  [--no-layout]", file=sys.stderr)
        sys.exit(1)

    no_layout = '--no-layout' in sys.argv
    layout    = {TT.NEWLINE, TT.INDENT, TT.DEDENT}
    tokens    = lex_file(sys.argv[1])

    for tok in tokens:
        if no_layout and tok.tt in layout:
            continue
        print(tok)


# ── Quick smoke test ───────────────────────────────────────────────────────────

def _smoke_test() -> None:
    sample = r'''
:fibonacci_recursive:n_int[int]
    ? n <= 1 -> n
    -> @:n-1 + @:n-2

.Point:x_int,y_int
    :dist:@,.Point[float]
        dx :< @:x - .Point:x
        dy :< @:y - .Point:y
        -> (dx**2 + dy**2)**0.5

|:counter:n_int[|int]
    i :< 0
    !! i < n
        received :< ->| i
        i @:< + 1

msg :< `hello {name}, score={score*2}`
cfg:?db:?host ?: "localhost"
~> math:sqrt
'''
    tokens = lex(sample, "<smoke_test>")
    for tok in tokens:
        print(tok)
    print(f"\n{len(tokens)} tokens total")


if __name__ == '__main__':
    if len(sys.argv) == 1:
        _smoke_test()
    else:
        main()
