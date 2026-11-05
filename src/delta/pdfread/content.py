"""The drawing operators of one page -> its text runs and border segments. A small PDF content
interpreter: the graphics matrix stack, the text state, path painting. It knows nothing of pypdf;
`ops` holds plain Python operands (extract.py converts them)."""
from __future__ import annotations

from .pdffonts import REPLACEMENT, PdfFont
from .types import PageData, Seg, TextRun

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
THIN = 3.0            # pt: a filled rectangle no thicker than this is a drawn border, not shading
_STROKE = {b"S", b"s", b"B", b"B*", b"b", b"b*"}
_FILL = {b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}
_CLOSE = {b"s", b"b", b"b*"}
# every operator the loop below acts on; the rest (colours, clipping, marked content) are skipped
_HANDLED = frozenset({b"q", b"Q", b"cm", b"BT", b"Tf", b"Tc", b"Tw", b"Tz", b"TL", b"Ts", b"Td", b"TD",
                      b"Tm", b"T*", b"Tj", b"'", b'"', b"TJ", b"m", b"l", b"h", b"re", b"c", b"v", b"y",
                      b"n", b"Do", b"INLINE IMAGE"}) | _STROKE | _FILL


def mul(a, b):
    """The matrix that applies `a` first, then `b`."""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def apply(m, x, y):
    return (x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5])


def _x_at(t, f, rise):
    """apply(mul(t, f), 0.0, rise)[0]: the same sums in the same order, the x alone."""
    return 0.0 * (t[0] * f[0] + t[1] * f[2]) + rise * (t[2] * f[0] + t[3] * f[2]) + (t[4] * f[0] + t[5] * f[2] + f[4])


def base_matrix(box, rotation: int):
    """PDF user space (origin bottom-left, y up, the page shown turned by `rotation` degrees
    clockwise) -> page space (origin top-left of the page as shown, y down)."""
    llx, lly, urx, ury = box
    r = rotation % 360
    if r == 90:
        return (0.0, 1.0, 1.0, 0.0, -lly, -llx)
    if r == 180:
        return (-1.0, 0.0, 0.0, 1.0, urx, -lly)
    if r == 270:
        return (0.0, -1.0, -1.0, 0.0, ury, urx)
    return (1.0, 0.0, 0.0, -1.0, -llx, ury)


class _State:
    def __init__(self):
        self.ctm = IDENT
        self.font_name, self.size = None, 0.0
        self.tc = self.tw = self.rise = self.leading = 0.0
        self.th = 1.0

    def copy(self):
        s = _State()
        s.__dict__.update(self.__dict__)
        return s


def interpret(ops, fonts: dict[str, PdfFont], xobjects: dict[str, str], base, page: PageData,
              memo: dict | None = None) -> None:
    """`memo` keeps decoded strings between calls: one dict for all the pages of a document (whose
    fonts do not change while it is read)."""
    st, stack = _State(), []
    tm = tlm = IDENT
    subpaths: list[list] = []        # each: a list of (x, y) in user space; closed ones repeat the first point
    unknown = PdfFont("Unknown")
    memo = {} if memo is None else memo
    full_ctm = full_m = None

    def full():
        nonlocal full_ctm, full_m
        if st.ctm is not full_ctm:   # the same matrix until a cm or a Q changes the ctm
            full_ctm, full_m = st.ctm, mul(st.ctm, base)
        return full_m

    def decoded(font: PdfFont, raw: bytes):
        """(text, its replacement count, the glyphs up to the last non-blank one, the glyphs after
        it), a glyph as (width / 1000, a word space?); a string is decoded once per font."""
        per = memo.get(id(font))
        if per is None:              # the font is kept with its strings, so its id is not reused
            per = memo[id(font)] = (font, {})
        pieces, adv, ink = [], [], 0
        for code, text, w in font.decode(raw):
            pieces.append(text)
            adv.append((w / 1000.0, font.nbytes == 1 and code == 32))
            if text.strip():
                ink = len(adv)
        text = "".join(pieces).replace("\x00", "")
        got = per[1][raw] = (text, text.count(REPLACEMENT), tuple(adv[:ink]), tuple(adv[ink:]))
        return got

    def show(raw: bytes):
        nonlocal tm
        if not isinstance(raw, bytes):
            return                # a malformed operand: nothing shown, nothing moved
        font = fonts.get(st.font_name, unknown)
        per = memo.get(id(font))
        got = per[1].get(raw) if per is not None else None
        text, bad, upto_ink, after_ink = got if got is not None else decoded(font, raw)
        start = tm
        if upto_ink or after_ink:
            # the text matrix after each glyph: mul((1, 0, 0, 1, tx, 0), tm) written out, the same
            # operations in the same order, so the same result to the last bit
            size, tc, tw, th = st.size, st.tc, st.tw, st.th
            t0, t1, t2, t3, t4, t5 = tm
            for wk, space in upto_ink:
                tx = (wk * size + tc + (tw if space else 0.0)) * th
                t0, t1, t2, t3, t4, t5 = (1.0 * t0 + 0.0 * t2, 1.0 * t1 + 0.0 * t3,
                                          0.0 * t0 + 1.0 * t2, 0.0 * t1 + 1.0 * t3,
                                          tx * t0 + 0.0 * t2 + t4, tx * t1 + 0.0 * t3 + t5)
            ink_tm = tm = (t0, t1, t2, t3, t4, t5) if upto_ink else tm   # after the last non-blank glyph
            for wk, space in after_ink:
                tx = (wk * size + tc + (tw if space else 0.0)) * th
                t0, t1, t2, t3, t4, t5 = (1.0 * t0 + 0.0 * t2, 1.0 * t1 + 0.0 * t3,
                                          0.0 * t0 + 1.0 * t2, 0.0 * t1 + 1.0 * t3,
                                          tx * t0 + 0.0 * t2 + t4, tx * t1 + 0.0 * t3 + t5)
            if after_ink:
                tm = (t0, t1, t2, t3, t4, t5)
        if not text:
            return
        f = full()
        f0, f1, f2, f3, f4, f5 = f
        s0, s1, s2, s3, s4, s5 = start
        # m = mul(start, f) written out
        m0, m1, m2, m3 = s0 * f0 + s1 * f2, s0 * f1 + s1 * f3, s2 * f0 + s3 * f2, s2 * f1 + s3 * f3
        if m0 > 0 and m3 < 0 and abs(m1) < 0.01 * abs(m0) and abs(m2) < 0.01 * abs(m3):
            rise = st.rise
            # apply(m, 0.0, rise) written out
            x_start = 0.0 * m0 + rise * m2 + (s4 * f0 + s5 * f2 + f4)
            y_start = 0.0 * m1 + rise * m3 + (s4 * f1 + s5 * f3 + f5)
            # apply(mul(t, f), 0.0, rise) for the end and the ink end, each worked out once
            x_end = _x_at(tm, f, rise)
            ink_end = x_end if ink_tm is tm else x_start if ink_tm is start else _x_at(ink_tm, f, rise)
            page.chars += len(text)
            page.bad_chars += bad
            if font is unknown:      # not one of the page's fonts
                page.bad_chars += len(text)
            page.runs.append(TextRun(text, x_start, x_end, y_start, st.size * abs(m3), font.name, ink_end))

    def newline(tx: float, ty: float):
        nonlocal tm, tlm
        tlm = mul((1.0, 0.0, 0.0, 1.0, tx, ty), tlm)
        tm = tlm

    def seg(p, q, m):
        (x0, y0), (x1, y1) = apply(m, *p), apply(m, *q)
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        if max(dx, dy) <= 1.0 or min(dx, dy) > 0.5:
            return                                       # a dot, or a diagonal
        if dx >= dy:
            y = (y0 + y1) / 2
            page.segs.append(Seg(min(x0, x1), y, max(x0, x1), y))
        else:
            x = (x0 + x1) / 2
            page.segs.append(Seg(x, min(y0, y1), x, max(y0, y1)))

    def paint(op: bytes):
        m = full()
        for pts in subpaths:
            if op in _CLOSE and len(pts) > 2 and pts[0] != pts[-1]:
                pts = pts + [pts[0]]
            if op in _STROKE:
                for p, q in zip(pts, pts[1:]):
                    seg(p, q, m)
            elif op in _FILL and len(pts) >= 4:
                dev = [apply(m, *p) for p in pts]
                xs, ys = [p[0] for p in dev], [p[1] for p in dev]
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                on_corner = all(min(abs(x - x0), abs(x - x1)) < 0.01 and min(abs(y - y0), abs(y - y1)) < 0.01
                                for x, y in dev)
                if not on_corner:
                    continue
                if (y1 - y0) <= THIN < (x1 - x0):
                    page.segs.append(Seg(x0, (y0 + y1) / 2, x1, (y0 + y1) / 2))
                elif (x1 - x0) <= THIN < (y1 - y0):
                    page.segs.append(Seg((x0 + x1) / 2, y0, (x0 + x1) / 2, y1))

    for operands, op in ops:
        try:
            if op not in _HANDLED:
                continue
            if op == b"q":
                stack.append(st.copy())
            elif op == b"Q":
                if stack:
                    st = stack.pop()
            elif op == b"cm":
                st.ctm = mul(tuple(float(v) for v in operands[:6]), st.ctm)
            elif op == b"BT":
                tm = tlm = IDENT
            elif op == b"Tf":
                st.font_name, st.size = operands[0], float(operands[1])
            elif op == b"Tc":
                st.tc = float(operands[0])
            elif op == b"Tw":
                st.tw = float(operands[0])
            elif op == b"Tz":
                st.th = float(operands[0]) / 100.0
            elif op == b"TL":
                st.leading = float(operands[0])
            elif op == b"Ts":
                st.rise = float(operands[0])
            elif op == b"Td":
                newline(float(operands[0]), float(operands[1]))
            elif op == b"TD":
                st.leading = -float(operands[1])
                newline(float(operands[0]), float(operands[1]))
            elif op == b"Tm":
                tm = tlm = tuple(float(v) for v in operands[:6])
            elif op == b"T*":
                newline(0.0, -st.leading)
            elif op == b"Tj":
                show(operands[0])
            elif op == b"'":
                newline(0.0, -st.leading)
                show(operands[0])
            elif op == b'"':
                st.tw, st.tc = float(operands[0]), float(operands[1])
                newline(0.0, -st.leading)
                show(operands[2])
            elif op == b"TJ":
                for item in operands[0]:
                    if isinstance(item, bytes):
                        show(item)
                    else:                # mul((1, 0, 0, 1, tx, 0), tm) written out
                        tx = -float(item) / 1000.0 * st.size * st.th
                        t0, t1, t2, t3, t4, t5 = tm
                        tm = (1.0 * t0 + 0.0 * t2, 1.0 * t1 + 0.0 * t3, 0.0 * t0 + 1.0 * t2, 0.0 * t1 + 1.0 * t3,
                              tx * t0 + 0.0 * t2 + t4, tx * t1 + 0.0 * t3 + t5)
            elif op == b"m":
                subpaths.append([(float(operands[0]), float(operands[1]))])
            elif op == b"l":
                if subpaths:
                    subpaths[-1].append((float(operands[0]), float(operands[1])))
            elif op == b"h":
                if subpaths and subpaths[-1][0] != subpaths[-1][-1]:
                    subpaths[-1].append(subpaths[-1][0])
            elif op == b"re":
                x, y, w, h = (float(v) for v in operands[:4])
                subpaths.append([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)])
            elif op in (b"c", b"v", b"y"):
                if subpaths:
                    subpaths[-1].append((float(operands[-2]), float(operands[-1])))   # a curve: keep the path going
            elif op in _STROKE or op in _FILL:
                paint(op)
                subpaths = []
            elif op == b"n":
                subpaths = []
            elif op == b"Do":
                kind = xobjects.get(operands[0])
                if kind == "image":
                    page.images += 1
                elif kind == "form":
                    page.forms += 1
            elif op == b"INLINE IMAGE":
                page.images += 1
        except (IndexError, TypeError, ValueError):
            continue                                     # a malformed operator: skip it, keep the page
