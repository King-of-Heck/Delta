"""The faster forms of hot helpers give exactly what the plain forms they replaced gave. Each test
keeps the plain form as its reference and checks the fast one against it on small built inputs,
including the odd cases a real document rarely has."""
import itertools
import random
import re
from xml.sax.saxutils import escape, quoteattr

from lxml import etree

from delta.diff.chars import FmtSpan, char_fmt
from delta.diff.fmt import FmtRange, RangeCursor, fmt_desc, fmt_diff
from delta.docx.ns import W
from delta.docx.package import Package
from delta.docx.styles import read_ppr, read_rpr
from delta.layout.lines import Run as LRun
from delta.model import WS_CHARS, Paragraph, ParaProps, Run, RunProps
from delta.pdf.fpdf_sink import ITALIC_DEGREES, STROKE_FRACTION
from delta.testing.fakefonts import FakeFace
from delta.testing.makedocx import DOC, P, make_docx
from delta.viewer.svg import SvgPainter


# -- fmt_diff: walked a segment at a time ------------------------------------------------------

def _fmt_diff_by_position(a, b):
    """fmt_diff as it was: one decision per character position."""
    end = max(a[-1].e if a else 0, b[-1].e if b else 0)
    out, run = [], None
    a_cur, b_cur = RangeCursor(a), RangeCursor(b)
    for pos in range(end):
        av, bv = a_cur.at(pos), b_cur.at(pos)
        if av is None or bv is None or av.same_fmt(bv):
            if run:
                out.append(run)
                run = None
            continue
        desc = fmt_desc(av, bv)
        if run and run.desc == desc and run.e == pos:
            run.e = pos + 1
        else:
            if run:
                out.append(run)
            run = FmtRange(pos, pos + 1, desc)
    if run:
        out.append(run)
    return out


def _random_spans(rng, sorted_=True):
    spans, pos = [], rng.randint(0, 2)
    for _ in range(rng.randint(0, 6)):
        n = rng.randint(1, 5)
        spans.append(FmtSpan(pos, pos + n, rng.random() < 0.5, rng.random() < 0.3, False,
                             rng.choice(["Arial", "Calibri"]), rng.choice([10.0, 11.0]), None))
        pos += n + rng.choice([0, 0, 0, 1, 2])
    if not sorted_:
        rng.shuffle(spans)
    return spans


def test_fmt_diff_matches_the_position_walk():
    rng = random.Random(7)
    for i in range(3000):
        a, b = _random_spans(rng, i % 5 != 0), _random_spans(rng, i % 7 != 0)
        assert fmt_diff(a, b) == _fmt_diff_by_position(a, b), (a, b)


def test_fmt_diff_odd_spans():
    sp = lambda s, e, **k: FmtSpan(s, e, k.get("b", False), False, False, "A", 11.0, None)
    for a, b in [([sp(-3, 2)], [sp(-3, 2, b=True)]),          # starts before 0
                 ([sp(2, 1), sp(0, 4)], [sp(0, 4, b=True)]),   # an inverted span
                 ([sp(0, 3), sp(1, 5)], [sp(0, 5, b=True)]),   # overlapping spans
                 ([], [sp(0, 3)]), ([sp(0, 0)], [sp(0, 0, b=True)])]:
        assert fmt_diff(a, b) == _fmt_diff_by_position(a, b)


# -- read_rpr / read_ppr: one pass over the children -------------------------------------------

def _x(inner, tag="rPr"):
    return etree.fromstring(f'<w:{tag} xmlns:w="{W}">{inner}</w:{tag}>')


def test_read_rpr_takes_the_first_of_a_repeated_property_and_skips_comments():
    el = _x('<!-- c --><w:b w:val="0"/><w:b/><?pi x?><w:sz w:val="24"/><w:sz w:val="30"/><w:other/>'
            '<w:color w:val="AUTO"/><w:rFonts w:ascii="X"/><w:rFonts w:ascii="Y"/>')
    assert read_rpr(el) == {"bold": False, "font": "X", "size_pt": 12.0, "color": None}
    assert read_rpr(_x("")) == {} and read_rpr(_x("<w:x/>")) == {} and read_rpr(None) == {}
    assert read_rpr(_x('<w:u w:val="none"/><w:caps/><w:smallCaps w:val="off"/><w:i/>')) == {
        "italic": True, "underline": False, "caps": True, "small_caps": False}


def test_read_rpr_keeps_the_key_order_of_the_find_walk():
    el = _x('<w:color w:val="FF0000"/><w:sz w:val="20"/><w:i/><w:b/>')
    assert list(read_rpr(el)) == ["bold", "italic", "size_pt", "color"]


def test_read_ppr_takes_the_first_of_a_repeated_property():
    el = _x('<!-- c --><w:jc w:val="center"/><w:jc w:val="right"/><w:ind w:left="200"/><w:ind w:left="400"/>'
            '<w:keepNext/><w:keepNext w:val="0"/><w:spacing w:after="40"/>', "pPr")
    assert read_ppr(el) == {"align": "center", "ind_left_pt": 10.0, "space_after_pt": 2.0, "keep_next": True}
    assert read_ppr(_x("", "pPr")) == {} and read_ppr(None) == {}


# -- Package.xml: parsed once ------------------------------------------------------------------

def test_a_part_is_parsed_once_and_a_missing_one_stays_none():
    pkg = Package.open(make_docx({"word/document.xml": DOC(P("hi"))}))
    assert pkg.xml("word/document.xml") is pkg.xml("word/document.xml")
    assert pkg.xml("word/styles.xml") is None and pkg.xml("word/styles.xml") is None


# -- chars._spans: the shortcut for the words of one run ----------------------------------------

_WS_RUN = re.compile(f"[{WS_CHARS}]+")


def _spans_as_was(p):
    out, n, last_space = [], 0, False

    def put(length, fmt):
        nonlocal n
        last = out[-1] if out else None
        if last is not None and tuple(last[2:]) == fmt:
            last[1] += length
        else:
            out.append([n, n + length, *fmt])
        n += length

    for run in p.runs:
        pr = run.props
        fmt = (pr.bold, pr.italic, pr.underline, pr.font, pr.size_pt, pr.color, pr.caps, pr.small_caps,
               pr.style_bold, pr.field, pr.field_text)
        for k, part in enumerate(_WS_RUN.split(run.text)):
            if k:
                if n == 0:
                    pass
                elif last_space:
                    last = out[-1]
                    if pr.bold and not last[2]:
                        if last[1] - last[0] == 1:
                            last[2] = True
                            if len(out) > 1 and tuple(out[-2][2:]) == tuple(last[2:]):
                                out[-2][1] = last[1]
                                out.pop()
                        else:
                            last[1] -= 1
                            out.append([n - 1, n, True, *last[3:]])
                else:
                    put(1, fmt)
                    last_space = True
            if part:
                put(len(part), fmt)
                last_space = False
    if last_space:
        last = out[-1]
        last[1] -= 1
        if last[1] == last[0]:
            out.pop()
    return [FmtSpan(*sp) for sp in out]


def test_char_fmt_matches_the_plain_span_walk():
    rng = random.Random(11)
    words = ["a", "bb", " ", "  ", "\t", " c ", "d e", "", " ", "f g"]
    for _ in range(4000):
        runs = [Run("".join(rng.choice(words) for _ in range(rng.randint(0, 4))),
                    RunProps(bold=rng.random() < 0.4, italic=rng.random() < 0.2, font=rng.choice(["A", "B"])))
                for _ in range(rng.randint(0, 6))]
        p = Paragraph(runs, ParaProps())
        assert char_fmt(p)[1] == _spans_as_was(p), [(r.text, r.props.bold) for r in runs]


# -- layout Run.is_space: string tests in place of the regex ----------------------------------

def test_is_space_matches_the_regex_for_every_whitespace_character_and_mixes():
    ws = re.compile(f"^[{WS_CHARS}]+$")
    singles = [chr(c) for c in range(0x3100)] + ["﻿", "\U0001f600"]
    cases = singles + ["", "  ", " \n", "\n", " a", "a ", "  \t", " \n\n", "​", "\x85", "᠎"]
    for t in cases:
        r = LRun(t, 0.0, None, None, 10.0)
        assert r.is_space == bool(ws.match(t)), repr(t)
    assert LRun(" ", 0.0, None, None, 10.0, tab=True).is_space is False


# -- SvgPainter.text: one string built in place ------------------------------------------------

def _n(v):
    return f"{v:.2f}"


def _svg_text_as_was(x, baseline, text, face, size, color, fake_bold=False, fake_italic=False, width=None,
                     role=None):
    attrs = [f'x="{_n(x)}"', f'y="{_n(baseline)}"', f"font-family={quoteattr(face.family)}",
             f'font-size="{_n(size)}"', f'fill="#{color}"']
    if role == "gutter":
        attrs[0] = f'x="{_n(x + (width or 0))}"'
        attrs += ['class="gutter"', 'text-anchor="end"']
        width = None
    if face.bold and not face.synthetic:
        attrs.append('font-weight="bold"')
    if face.italic and not face.synthetic:
        attrs.append('font-style="italic"')
    if fake_bold:
        attrs += [f'stroke="#{color}"', f'stroke-width="{_n(size * STROKE_FRACTION)}"']
    if fake_italic:
        attrs.append(f'transform="translate({_n(x)} {_n(baseline)}) skewX(-{ITALIC_DEGREES}) '
                     f'translate(-{_n(x)} -{_n(baseline)})"')
    if width:
        attrs += [f'textLength="{_n(width)}"', 'lengthAdjust="spacing"']
    attrs.append('xml:space="preserve"')
    return f"<text {' '.join(attrs)}>{escape(text)}</text>"


def test_svg_text_is_the_same_string_as_before():
    faces = []
    for bold, italic, synthetic in itertools.product((False, True), repeat=3):
        f = FakeFace('Fa"m & <ily>', bold, italic, 0.5, 1.2, 0.8)
        f.synthetic = synthetic
        faces.append(f)
    for face, fb, fi, width, role, text in itertools.product(
            faces, (False, True), (False, True), (None, 0, 0.0, 12.345), (None, "gutter"),
            ("word", "a<b & c>d", "&amp;", " ")):
        painter = SvgPainter()
        painter.page(100, 100)
        painter.text(1.005, -2.5, text, face, 10.25, "00ff00", fb, fi, width=width, role=role)
        assert painter._pages[-1][-1] == _svg_text_as_was(1.005, -2.5, text, face, 10.25, "00ff00", fb, fi,
                                                           width, role)
