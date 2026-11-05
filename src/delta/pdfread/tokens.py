"""A fast reader of page content streams: bytes -> [(operands, operator)], operands as the plain
Python extract.py hands the interpreter (numbers int/float, names str with the slash, strings
bytes, arrays lists). It gives exactly what pypdf's ContentStream parser gives for the streams
Word and the usual PDF makers write, at a fraction of the cost; on anything else (an inline image,
an indirect reference, a malformed token, a stream pypdf would refuse) it returns None and the
caller parses with pypdf, so an unusual stream reads, or fails, exactly as before. The rules
below copy pypdf 6's parser, quirks included (a comma inside a number, a name's #xx escapes)."""
from __future__ import annotations

import re

# A literal string, with up to three levels of strings nested inside it: runs of plain bytes,
# escapes ("\" and any one byte, so an escaped paren never counts) and nested strings. Possessive,
# so a string left open fails at once instead of backtracking; deeper nesting is pypdf's to read.
_PLAIN = rb"[^()\\]*+"
_NEST = rb"\(" + _PLAIN + rb"(?:\\." + _PLAIN + rb")*+\)"
for _ in range(3):
    _NEST = rb"\(" + _PLAIN + rb"(?:(?:\\.|" + _NEST + rb")" + _PLAIN + rb")*+\)"
# One token, after the whitespace pypdf skips both at the top level and inside an array or a
# dictionary (NUL and vertical tab are skipped by only one of the two: those are `other` tokens).
# Every group matches at least one byte, so a findall row has exactly one non-empty field.
_TOKEN = re.compile(rb"""[\t\n\f\r ]*(?:
    ([0-9+\-.][0-9+,\-.]*)                       # a number, as pypdf reads one: + , - . and digits
  | (/[^\s()<>\[\]{}/%]*)                        # a name
  | ([A-Za-z'"][^\s()<>\[\]{}/%]*)               # an operator (or true/false/null)
  | (""" + _NEST + rb""")                         # a literal string
  | (<<|>>|\[|\]|<[^<>]*>|%[^\r\n]*[\r\n]?|[^\t\n\f\r ])   # punctuation, a hex string, a comment, other
)""", re.X | re.S)
_SPECIAL = re.compile(rb"[()\\]")
_HEX_DIGITS = re.compile(rb"[0-9A-Fa-f]*")
# An indirect reference ("1 0 R"), which pypdf cannot read in a content stream; where it might be
# read as one, pypdf decides.
_REFERENCE = re.compile(rb"\d\s+\d+\s+R[^a-zA-Z]")
_ESCAPES = {ord(k): ord(v) for k, v in (
    ("n", "\n"), ("r", "\r"), ("t", "\t"), ("b", "\b"), ("f", "\f"), ("(", "("), (")", ")"),
    ("/", "/"), ("\\", "\\"), (" ", " "), ("%", "%"), ("<", "<"), (">", ">"), ("[", "["),
    ("]", "]"), ("#", "#"), ("_", "_"), ("&", "&"), ("$", "$"))}
# pypdf reads these in chunks and gives up past a length; well short of it, the token is ours.
_MAX_OP, _MAX_NAME, _MAX_NUM = 100, 4000, 40
_MAX_DEPTH = 50                  # pypdf recurses into nested arrays; deep nesting is its call
DICT = object()                  # a dictionary operand: read past, its value never used
_DICT_OPS = {b"BDC", b"DP"}      # the operators that take one, which the interpreter ignores


class _Unsure(Exception):
    """Not a stream this reader vouches for: pypdf parses it."""


def _name(tok: bytes) -> str:
    if b"#" in tok:
        out, i = bytearray(), 0
        while i < len(tok):              # pypdf's unnumber: '#' and two hex digits is one byte
            if tok[i] == 0x23:
                try:
                    out.append(int(tok[i + 1:i + 3], 16))
                    i += 3
                    continue
                except (ValueError, IndexError):
                    pass
            out.append(tok[i])
            i += 1
        tok = bytes(out)
    for enc in ("utf-8", "gbk"):
        try:
            return tok.decode(enc)
        except UnicodeDecodeError:
            pass
    return tok.decode("latin1")


def _literal(data: bytes, i: int) -> tuple[bytes, int]:
    """A string with escapes or nested parens, from just after its "("; -> (bytes, end)."""
    out, depth, n = bytearray(), 1, len(data)
    while True:
        m = _SPECIAL.search(data, i)
        if m is None:
            raise _Unsure                # unterminated: pypdf raises
        k = m.start()
        out += data[i:k]
        c = data[k]
        if c == 0x28:
            depth += 1
            out.append(c)
            i = k + 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), k + 1
            out.append(c)
            i = k + 1
        else:
            if k + 1 >= n:
                raise _Unsure
            e = data[k + 1]
            if e in _ESCAPES:
                out.append(_ESCAPES[e])
                i = k + 2
            elif 0x30 <= e <= 0x37:          # up to three octal digits
                j = k + 2
                while j < k + 4 and j < n and 0x30 <= data[j] <= 0x37:
                    j += 1
                v = int(data[k + 1:j], 8)
                if v > 255:                  # pypdf keeps the backslash and rereads the digits
                    out.append(0x5C)
                    i = k + 1
                else:
                    out.append(v)
                    i = j
            elif e in (0x0A, 0x0D):          # an escaped line break: nothing, CRLF/LFCR/LFLF as one
                i = k + 2
                if i < n and data[i] in (0x0A, 0x0D):
                    i += 1
            else:                            # an unknown escape keeps its backslash
                out.append(0x5C)
                out.append(e)
                i = k + 2


def _hex(tok: bytes) -> bytes:
    """A hex string token "<...>" -> its bytes."""
    digits = tok[1:-1].translate(None, b"\x00\t\n\f\r ")
    if not _HEX_DIGITS.fullmatch(digits):
        raise _Unsure                    # pypdf raises on a stray character
    if len(digits) % 2:
        digits += b"0"
    return bytes.fromhex(digits.decode("ascii"))


def parse(data: bytes) -> list | None:
    """The operations of a content stream, or None when pypdf should parse it."""
    if _REFERENCE.search(data):
        return None
    try:
        return _parse(data)
    except _Unsure:
        return None
    except Exception:                    # never a fault of our own in place of pypdf's answer
        return None


def _parse(data: bytes) -> list:
    ops: list = []
    operands: list = []
    stack: list = []                 # the open arrays and dictionaries: (outer target, outer in_dict)
    target = operands                # where the next value goes (None in a dictionary)
    in_dict = False
    want_key = False                 # in a dictionary: the next token is a key
    has_dict = False                 # the pending operands include a dictionary
    for num, name, op, lit, punct in _TOKEN.findall(data):
        if num:
            if len(num) >= _MAX_NUM:
                raise _Unsure
            try:
                value = float(num) if b"." in num else int(num)
            except ValueError:
                value = 0.0 if b"." in num else 0      # pypdf reads a malformed number as zero
        elif op:
            if stack or len(op) >= _MAX_OP or op == b"BI" or op.startswith(b"stream"):
                raise _Unsure            # true/false/null in an array, an inline image, a stream object
            if has_dict and op not in _DICT_OPS:
                raise _Unsure
            ops.append((operands, op))
            operands = target = []
            has_dict = False
            continue
        elif name:
            if len(name) >= _MAX_NAME:
                raise _Unsure
            if in_dict:
                want_key = not want_key
                continue                 # a dictionary's keys and values are never used
            value = _name(name)
        elif lit:
            if b"\\" in lit or lit.count(b"(") > 1:
                value, end = _literal(lit, 1)
                if end != len(lit):
                    raise _Unsure
            else:
                value = lit[1:-1]
        else:
            c = punct[0]
            if c == 0x5B:            # "["
                if in_dict and want_key or len(stack) >= _MAX_DEPTH:
                    raise _Unsure
                stack.append((target, in_dict))
                target, in_dict = [], False
                continue
            elif c == 0x5D:          # "]"
                if not stack or in_dict:
                    raise _Unsure
                value = target
                target, in_dict = stack.pop()
                if in_dict:
                    want_key = True      # the array was a dictionary's value
                    continue
            elif punct == b"<<":
                if in_dict and want_key or len(stack) >= _MAX_DEPTH:
                    raise _Unsure
                stack.append((target, in_dict))
                target, in_dict, want_key = None, True, True
                has_dict = True
                continue
            elif punct == b">>":
                if not in_dict or not want_key:
                    raise _Unsure
                target, in_dict = stack.pop()
                if in_dict:
                    want_key = True
                    continue
                value = DICT
            elif c == 0x3C:          # "<" hex string ">"
                if len(punct) < 2:
                    raise _Unsure        # a "<" with no ">" after it
                value = _hex(punct)
            elif c == 0x25:          # "%": a comment, through the end of its line
                if stack and punct[-1] not in (0x0A, 0x0D):
                    raise _Unsure        # one left open at the end: pypdf raises inside an array
                continue
            else:
                raise _Unsure
        if in_dict:
            if want_key:
                raise _Unsure            # a dictionary key that is not a name
            want_key = True
            continue
        target.append(value)
    if stack:
        raise _Unsure                    # an array or dictionary left open
    return ops
