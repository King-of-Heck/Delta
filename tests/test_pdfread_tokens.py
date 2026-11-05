"""The fast content-stream reader against pypdf's own parser: on every stream it takes, it must give
exactly what pypdf gives (types included: 1 is not 1.0), and on the rest it must step aside."""
import random
import time

import pytest

from delta.pdfread import tokens
from delta.pdfread.extract import _plain


def pypdf_ops(data: bytes):
    from pypdf.generic import ContentStream
    cs = ContentStream(None, None)
    cs.set_data(data)
    return [([_plain(o) for o in operands], bytes(op)) for operands, op in cs.operations]


def typed(x):
    """A value with its type spelled out, so 1 and 1.0 (or -0.0 and 0.0) do not compare equal."""
    if isinstance(x, list):
        return ["list", [typed(v) for v in x]]
    if isinstance(x, float):
        return ["float", repr(x)]
    return [type(x).__name__, x]


def norm(ops):
    """A dictionary operand is never used (only BDC/DP take one), so only the operand count counts there."""
    return [(op, len(o) if op in (b"BDC", b"DP") else typed(o)) for o, op in ops]


def agrees(data: bytes) -> bool:
    """True when the fast reader took the stream (and then it matches pypdf); False when it stepped aside."""
    mine = tokens.parse(data)
    if mine is None:
        return False
    assert norm(mine) == norm(pypdf_ops(data)), data
    return True


TAKEN = [
    b"",
    b"   \n",
    b"BT /F1 12 Tf 72 700 Td (Hello) Tj ET",
    b"q 1 0 0 1 0 0 cm 0.5 g 10 20 300 40 re f Q",
    b"BT /F1 9.96 Tf 1 0 0 -1 70.8 85.2 Tm [(H)-3(e)4(llo)] TJ ET",
    b"/P <</MCID 0>> BDC BT (x) Tj ET EMC",
    b"/Span <</ActualText (a\\)b) /Lang (en-GB) /Alt <FEFF00410042>>> BDC EMC",
    b"/X <</A <</B [1 2 (c) /D]>> /E 1.5>> DP",
    b"[(a) [(b) 1] 2] TJ",
    b"[] TJ [ ] TJ",
    b"(a\\(b\\)c) Tj (a(b)c) Tj (a(b(c)d)e) Tj (a(b(c(d)e)f)g) Tj",
    b"() Tj ( ) Tj",
    b"(\\n\\r\\t\\b\\f\\/\\\\\\ \\%\\<\\>\\[\\]\\#\\_\\&\\$) Tj",
    b"(\\101\\7\\01234\\0) Tj (\\777) Tj (\\400x) Tj (\\3777) Tj",
    b"(a\\\r\nb\\\n\rc\\\r\rd\\\n\ne\\\nf\\\rg) Tj",
    b"(\\q\\Z\\\x00) Tj",
    b"(line\r\nbreak\x00and\xffhigh) Tj",
    b"<414243> Tj <41 42\n43> Tj <4> Tj <> Tj <abcdef> Tj <A b C d\x00e\tF> Tj",
    b"/F#20x 1 Tf /A#2 0 Tf /A#zz 0 Tf /A#4 0 Tf /#41#42 0 Tf",
    b"/caf\xc3\xa9 0 Tf /\xff\xfe 0 Tf /\x81\x40 0 Tf /a\x00b 0 Tf",
    b"/ 0 Tf // 0 Tf",
    b"1 -1 +1 .5 -.5 1. 1.2.3 1,5 - + . --1 +-3 007 -0 -0.0 0.0 1.50 Tm",
    b"123456789012345678901234567890 99999999999999999999.5 Tm",
    b"T* ' \" d0 BX EX Tz123 a*b",
    b"(a) '  1 2 (b) \"",
    b"% a comment\nBT % another\r(x) Tj%tail",
    b"[(a) % inside\n (b)] TJ",
    b"<</A 1 % c\n /B 2>> BDC",
    b"1 2 3",
    b"BT\t\f(a)Tj\rET",
    b"0 0 Rg 1 0 RG 2 0 Rf",
    b"/a[/b]/c<41>(d)Tj",
    b"q 1 0 0 1 0 0 cm /Im1 Do Q",
    b"BT\x00(a) Tj",                         # NUL is part of an operator, as in pypdf
    b"ID EI",
    b"BT (a) Tj ET \t\f\r\n \r\n",               # whitespace at the end
    b"(a) Tj % last comment\r\n  \n",
    b"(a) Tj %last\r \n",
    b"% only a comment \r\n\t ",
    b" \r\n\t\f ",
]


@pytest.mark.parametrize("data", TAKEN)
def test_streams_it_takes_read_exactly_as_pypdf_reads_them(data):
    assert agrees(data)


LEFT_TO_PYPDF = [
    b"1 0 R Tf",                                 # an indirect reference
    b"BI /W 1 /H 1 /BPC 8 /CS /G ID \x00 EI Q",  # an inline image
    b"(open Tj",
    b"(a\\",
    b"[(a) (b) TJ",
    b"[true] TJ",
    b"[null] TJ",
    b"<<>> Tj",                                  # a dictionary where one is not expected
    b"[<<>>] TJ",
    b"<</A 1>> stream\nxx\nendstream",
    b"<<1 2>> BDC",
    b"<</A>> BDC",
    b"<</A 1 > BDC",
    b"<</A true>> BDC",
    b"<</A 1",
    b"[% open comment",
    b"BT \x00 (a) Tj",
    b"BT \x0b (a) Tj",
    b"stream1 x",
    b"{ } Tj",
    b") Tj",
    b"] Tj",
    b">> Tj",
    b"<41 4G> Tj",
    b"<41",
    b"(a(b(c(d(e)d)c)b)a) Tj",                   # nested deeper than the fast reader goes
    b"[" * 60 + b"]" * 60 + b" TJ",
    b"1" * 50 + b" Tc",
    b"T" + b"x" * 200,
    b"/" + b"n" * 5000 + b" 1 Tf",
    b", Tc",
    b"[(a) % comment\r\n  ",                     # an array left open, a comment and whitespace last
    b"(a) Tj (open \\( (b) Tj (c) Tj",           # a string left open, whole strings after it
    b"<41 (a) Tj <42> Tj",                       # a "<" with no ">" before the next "<"
    b"(a) Tj @ (b) Tj",                          # a stray byte, then good tokens
]


@pytest.mark.parametrize("data", LEFT_TO_PYPDF)
def test_streams_it_is_unsure_of_are_left_to_pypdf(data):
    assert tokens.parse(data) is None


# Streams the reader once took time over that grew with the square of their length (many seconds
# each); read in one pass, each takes a few milliseconds. -> (stream, taken by the fast reader)
LONG_RUNS = {
    "a string left open again and again": (b"BT (Hi) Tj ET\n" + b"\\(" * 32000, False),
    "an inline image of escaped parens": (b"BI /W 128 /H 128 /BPC 8 /CS /G ID " + b"\\(" * 8192
                                          + b" EI\n" + b"BT (Line of text) Tj ET\n" * 2000, False),
    "trailing spaces": (b"BT (a) Tj ET" + b" " * 30000, True),
    "trailing line ends": (b"BT (a) Tj ET" + b"\r\n" * 15000, True),
}


@pytest.mark.parametrize("data, taken", LONG_RUNS.values(), ids=LONG_RUNS.keys())
def test_long_runs_are_read_in_one_pass(data, taken):
    start = time.perf_counter()
    ops = tokens.parse(data)
    assert time.perf_counter() - start < 1.0
    assert (ops is not None) == taken
    if taken:
        assert agrees(data)


ALPHABET = [
    b" ", b"  ", b"\n", b"\r\n", b"\t", b"\f", b"\x00", b"\x0b",
    b"0", b"1", b"12", b"-3", b"+4", b".5", b"6.", b"7.25", b"1,2", b"-", b".", b"--1",
    b"/F1", b"/a#20b", b"/x#4", b"/\xc3\xa9", b"/", b"/\xff",
    b"Tj", b"TJ", b"Tf", b"Td", b"BT", b"ET", b"q", b"Q", b"cm", b"re", b"BDC", b"EMC", b"'", b'"',
    b"true", b"false", b"null", b"R", b"BI", b"ID", b"EI", b"stream",
    b"(a)", b"(b c)", b"()", b"(", b")", b"\\", b"\\(", b"\\)", b"\\n", b"\\101", b"\\777", b"\\\r\n",
    b"(x(y)z)", b"<41>", b"<4 1>", b"<>", b"<", b">", b"<<", b">>", b"[", b"]", b"{", b"}",
    b"%c\n", b"%", b"\xe9", b"0 0 R ",
]


def test_random_streams_agree_with_pypdf_whenever_they_are_taken():
    rng = random.Random(20261104)
    taken = 0
    for _ in range(4000):
        data = b"".join(rng.choice(ALPHABET) for _ in range(rng.randint(1, 30)))
        taken += agrees(data)
    assert taken > 500                       # the comparison means something: many streams taken


def test_random_well_formed_streams_are_all_taken():
    rng = random.Random(7)
    pieces = [b"(a)", b"(b\\)c)", b"(d(e)f)", b"<4142>", b"1", b"-2.5", b"/F1", b"[(x) -3 (y)]", b"<</MCID 3>>"]
    for _ in range(500):
        ops = []
        for _ in range(rng.randint(1, 8)):
            operands = [rng.choice(pieces[:-1]) for _ in range(rng.randint(0, 4))]
            ops.append(b" ".join(operands + [rng.choice([b"Tj", b"TJ", b"Td", b"Tf", b"re", b"q"])]))
        ops.append(b"/P " + pieces[-1] + b" BDC")
        assert agrees(b"\n".join(ops))
