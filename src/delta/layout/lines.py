"""Measured runs and greedy line breaking on glyph widths.

A line's height follows Word: single spacing is the tallest run's font line height (hhea
ascender - descender + lineGap; a tab's own formatting does not count), "auto" multiplies it, "exact" fixes it, "atLeast" takes the
larger; text is bottom-aligned in the line, so the baseline sits `descent` above the bottom.
Breaking is first-fit on word/whitespace tokens; a word wider than the whole line is split at the
last character that fits (Word's behaviour for an unbreakable word); trailing whitespace never
counts toward a line's width or its justification gaps.

A tab advances to the next tab stop: the paragraph's own stops (custom positions from the left
edge of the container), then default stops every `default_tab` beyond the last custom one. A
right, centre or decimal stop aligns the text that follows it (up to the next tab) at the stop; a
stop past the line's right edge wraps the line first, and on a fresh line a tab with no reachable
stop is empty. The tab keeps its width in the line and its stop's leader for the placer to draw.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..model import WS_CHARS
from .pieces import Piece

_TOKEN = re.compile(f"[^{WS_CHARS}]+|[{WS_CHARS}]+")
_WS = re.compile(f"^[{WS_CHARS}]+$")
# WS_CHARS spelled out (its one range expanded), for the plain string tests of Run.is_space
_WS_STR = ("\t\n\v\f\r   " + "".join(map(chr, range(0x2000, 0x200b)))
           + "    　﻿")
_WS_SET = frozenset(_WS_STR)


LEADERS = {"dot": ".", "hyphen": "-", "underscore": "_", "middleDot": "\u00b7", "heavy": "_"}


MARK_SCALE = 0.65      # a reference mark's size relative to its text
MARK_RISE = 0.35       # and how far above the baseline it sits, relative to the text size


@dataclass
class Run:
    text: str
    w: float
    piece: Piece
    face: object
    size: float
    tab: bool = False            # a tab: its width is the advance to the stop, set by break_lines
    leader: str | None = None    # the stop's leader character, drawn across the tab's width
    rise: float = 0.0            # points above the baseline (a reference mark)
    image_h: float = 0.0         # an inline image: the box's height, which sizes the line

    @property
    def is_space(self) -> bool:
        # == bool(_WS.match(text)): non-empty and whitespace throughout. A word fails on its first
        # character, which is most runs, so that is tested before the whole text is.
        t = self.text
        return not self.tab and t != "" and t[0] in _WS_SET and not t.strip(_WS_STR)


def next_tab_stop(x: float, tab: float) -> float:
    return (math.floor(x / tab + 1e-9) + 1) * tab


@dataclass
class Line:
    runs: list[Run]
    width: float        # natural width, trailing whitespace excluded
    gaps: int           # whitespace runs inside the line (justification stretches these)
    height: float
    ascent: float       # baseline offset from the top of the line


@dataclass
class Spacing:
    rule: str | None = None          # None/"auto" | "exact" | "atLeast"
    multiple: float | None = None    # "auto" multiplier (None = single)
    exact_pt: float | None = None    # fixed height for exact / floor for atLeast


def measure(pieces: list[Piece], fonts, default_font, default_size: float) -> list[Run]:
    out: list[Run] = []
    for p in pieces:
        face = fonts.face(p.font or default_font, p.bold, p.italic)
        size = p.size or default_size
        if p.image_w:                # an inline image: an empty run as wide as its box, as tall as it
            out.append(Run("", p.image_w, p, face, size, image_h=p.image_h))
            continue
        if p.tab:
            out.extend(Run("\t", 0.0, p, face, size, tab=True) for _ in range(p.tab))
            continue
        if p.rise:                   # a reference mark: MARK_SCALE of the size, raised MARK_RISE of it
            out.append(Run(p.text, face.width(p.text, size * MARK_SCALE), p, face, size * MARK_SCALE,
                           rise=size * MARK_RISE))
            continue
        for tk in _TOKEN.findall(p.text.upper() if p.caps else p.text):
            out.append(Run(tk, face.width(tk, size), p, face, size))
    return out


_EPS = 1e-6


def _next_stop(pos: float, stops, default_tab: float):
    """(position, TabStop or None for a default stop) of the first stop past pos: the paragraph's
    own first, default stops only beyond the last of them; None when there is none."""
    usable = [s for s in stops if s.kind not in ("clear", "bar")]
    for s in usable:
        if s.pos_pt > pos + _EPS:
            return s.pos_pt, s
    if default_tab > 0:
        last = max((s.pos_pt for s in usable), default=-math.inf)
        return next_tab_stop(max(pos, last), default_tab), None
    return None


def _following_width(runs: list[Run], i: int, kind: str) -> float:
    """Width of the text after runs[i] up to the next tab; for a decimal stop, up to its first '.'."""
    w = 0.0
    for r in runs[i + 1:]:
        if r.tab:
            break
        if kind == "decimal" and "." in r.text:
            return w + r.face.width(r.text[:r.text.index(".")], r.size)
        w += r.w
    return w


def line_height(runs: list[Run], spacing: Spacing, default_face, default_size: float) -> tuple[float, float]:
    chars = [r for r in runs if not r.tab]     # a tab's own formatting does not size the line (Word)
    if chars:
        # both maxima in one pass; `>` keeps the first of equals, as max() does
        natural = desc = None
        for r in chars:
            lh, d = r.face.line_height(r.size), r.face.descent(r.size)
            if natural is None:
                natural, desc = lh, d
                continue
            if lh > natural:
                natural = lh
            if d > desc:
                desc = d
    else:
        natural = default_face.line_height(default_size)
        desc = default_face.descent(default_size)
    imgs = max((r.image_h for r in runs if r.image_h), default=0.0)
    natural = max(natural, imgs)
    h = natural
    if spacing.rule == "exact" and spacing.exact_pt is not None:
        h = spacing.exact_pt
    elif spacing.rule == "atLeast" and spacing.exact_pt is not None:
        h = max(natural, spacing.exact_pt)
    elif spacing.multiple:
        h = natural * spacing.multiple
    return h, h - desc


def _fit_chars(face, size: float, text: str, avail: float) -> int:
    w = 0.0
    for i, ch in enumerate(text):
        cw = face.width(ch, size)
        if w + cw > avail and i > 0:
            return i
        w += cw
    return len(text)


def break_lines(runs: list[Run], first_avail: float, avail: float, spacing: Spacing,
                default_face, default_size: float, first_x: float = 0.0, x: float = 0.0,
                stops=(), default_tab: float = 0.0) -> list[Line]:
    """`first_x` / `x` are where the first / a later line starts, measured like the tab stops
    from the container's left edge; `stops` are the paragraph's TabStops, sorted."""
    lines: list[Line] = []
    cur: list[Run] = []
    cur_w = 0.0

    def room() -> float:
        return first_avail if not lines else avail

    def start() -> float:
        return first_x if not lines else x

    def close():
        nonlocal cur, cur_w
        while cur and cur[-1].is_space:
            cur.pop()
        h, asc = line_height(cur, spacing, default_face, default_size)
        lines.append(Line(cur, sum(r.w for r in cur), sum(1 for r in cur if r.is_space), h, asc))
        cur, cur_w = [], 0.0

    for i, run in enumerate(runs):
        if run.tab:
            while True:
                pos = start() + cur_w
                hit = _next_stop(pos, stops, default_tab)
                if hit is None:                 # no stops at all: the tab is empty where it is
                    w, leader = 0.0, None
                    break
                if hit[0] > start() + room() + _EPS:
                    if cur:
                        close()                 # no stop left on this line: the tab wraps
                        continue
                    w, leader = 0.0, None
                    break
                sp, stop = hit
                kind = stop.kind if stop else "left"
                w = sp - pos
                if kind in ("right", "center", "decimal"):
                    follow = _following_width(runs, i, kind)
                    w = max(0.0, w - (follow / 2 if kind == "center" else follow))
                leader = LEADERS.get(stop.leader) if stop else None
                break
            cur.append(Run("\t", w, run.piece, run.face, run.size, tab=True, leader=leader))
            cur_w += w
            continue
        if not cur and run.is_space:
            continue
        if cur and cur_w + run.w > room():
            close()
            if run.is_space:
                continue
        if not cur and run.w > room() and len(run.text) > 1:
            rest = run.text
            while rest:
                n = _fit_chars(run.face, run.size, rest, room())
                part = Run(rest[:n], run.face.width(rest[:n], run.size), run.piece, run.face, run.size)
                cur.append(part)
                cur_w += part.w
                rest = rest[n:]
                if rest:
                    close()
            continue
        cur.append(run)
        cur_w += run.w
    if cur or not lines:
        close()
    return lines
