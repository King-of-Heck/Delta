"""Text runs -> text lines. Runs sharing a baseline are one line, ordered left to right; a
smaller raised run stays on its line as a superscript; gaps become a space or a tab. Every
threshold is a share of the font size."""
from __future__ import annotations

from .types import Line, Span, TextRun

SAME_LINE = 0.45      # of the larger size: baselines this close are one line
SUP_SIZE = 0.85       # a superscript is smaller than this share of the line's size ...
SUP_RISE = 0.15       # ... and raised by more than this share of it
SPACE_GAP = 0.12      # a word space is 0.22 em in Calibri, 0.25 in Times, 0.28 in Arial; kerning moves far less
# DRAWN_SPACE_GAP: where the text draws its word spaces as characters, a gap must be this wide to be
# a space it left out. Word's ExportAsFixedFormat draws every letter on its own with adjustments of up
# to 0.21 em between the letters of a word (measured over a 198-page contract: 3,500 gaps at 0.11,
# a tail to 0.21), and its undrawn word gaps start at 0.36; its Save As PDF keeps them under 0.08.
DRAWN_SPACE_GAP = 0.3
TAB_GAP = 2.0
SAME_SPOT = 0.05      # of the font size: the same text this close to an earlier run is that run drawn again (fake bold)


def _half(v: float) -> float:
    return round(v * 2) / 2


def _dedupe(runs: list[TextRun]) -> list[TextRun]:
    out: list[TextRun] = []
    for r in runs:
        # A duplicate is drawn right after its original, so a short trailing window is enough to
        # catch it; this keeps the scan linear instead of comparing against every run seen so far.
        text, x0, y, near = r.text, r.x0, r.y, SAME_SPOT * r.size
        for o in out[-8:]:
            if o.text == text and abs(o.x0 - x0) < near and abs(o.y - y) < near:
                break
        else:
            out.append(r)
    return out


def _dominant(row: list[TextRun], half: list[float], ink: list[bool], keep: list[int]) -> tuple[float, float]:
    """(baseline, size) of the size that covers the most width among the runs `keep` (indices into
    the row; `half` and `ink` are each run's half-point size and whether it draws anything)."""
    width: dict[float, float] = {}
    for i in keep:
        if ink[i]:
            r, z = row[i], half[i]
            width[z] = width.get(z, 0.0) + (r.x1 - r.x0)
    size = max(width, key=lambda z: (width[z], z))
    ys = sorted(row[i].y for i in keep if half[i] == size and ink[i])
    return ys[len(ys) // 2], size


def _raised(row: list[TextRun], half: list[float], ink: list[bool]) -> set[int]:
    """Indices of the runs that are superscripts over the row's largest text: smaller and raised
    above its baseline. Measured against the largest size, not the one covering most width, so a
    footnote mark wider than the glyph it sits on (a 6.5 pt "97" on a 10 pt placeholder bullet) is
    still one."""
    inked = [i for i in range(len(row)) if ink[i]]
    if not inked:
        return set()
    top = max(half[i] for i in inked)
    ys = sorted(row[i].y for i in inked if half[i] == top)
    base = ys[len(ys) // 2]
    return {i for i in inked if half[i] < SUP_SIZE * top and row[i].y < base - SUP_RISE * top}


def _line(row: list[TextRun], page: int, cell, space_gap: float = SPACE_GAP) -> Line:
    # Each run's half-point size and whether it draws anything, worked out once: the rules below
    # read them several times per run. Runs are referred to by their index in `row`.
    half = [_half(r.size) for r in row]
    ink = [bool(r.text.strip()) for r in row]
    raised = _raised(row, half, ink)
    every = range(len(row))
    y, size = _dominant(row, half, ink, [i for i in every if i not in raised] or list(every))
    order = sorted(every, key=lambda i: row[i].x0)      # stable, so the same order as sorting the runs
    # The spans being built, as parallel lists so a run joining the last span extends its text in
    # place rather than copying a frozen Span for every run; the Spans are made once at the end.
    keys: list[tuple] = []            # (font, size, sup) of each span
    texts: list[str] = []
    prev: TextRun | None = None
    cols: list[list[float]] = []      # [x0, x1] of the ink of each piece between tabs
    tabbed = False
    for i in order:
        r = row[i]
        text = r.text
        if prev is not None:
            gap = r.x0 - prev.x1
            spaced = texts[-1][-1:].isspace() or text[:1].isspace()
            if gap > TAB_GAP * size:
                texts[-1] = texts[-1].rstrip()
                text = "\t" + text.lstrip()
                tabbed = True
            elif gap > space_gap * size and not spaced:
                text = " " + text
        if ink[i]:
            if tabbed or not cols:
                cols.append([r.x0, r.ink_end])
            else:
                cols[-1][1] = r.ink_end
            tabbed = False
        z = half[i]
        sup = i in raised or (z < SUP_SIZE * size and r.y < y - SUP_RISE * size)
        key = (r.font, z, sup)
        if keys and keys[-1] == key:
            texts[-1] += text
        else:
            keys.append(key)
            texts.append(text)
        prev = r
    texts[0] = texts[0].lstrip()
    texts[-1] = texts[-1].rstrip()
    # The right end is the ink's: Word draws the spaces after a sentence's full stop, which would
    # put a paragraph's last line past the margin and make a justified line look ragged. The pieces
    # hold it already: the first starts at the first inked run, the last ends at the last one's ink.
    ends_spaced = prev.text[-1:].isspace()     # (prev: the rightmost run) the PDF drew a space after the last word
    spans = [Span(t, f, z, s) for (f, z, s), t in zip(keys, texts) if t]
    # Only when every tab in the text is one of the gaps between pieces (a blank run can carry one).
    tabs = "".join(texts).count("\t")
    pieces = tuple((a, b) for a, b in cols) if len(cols) > 1 and tabs == len(cols) - 1 else ()
    return Line(page, cols[0][0], cols[-1][1], y, size, spans, cell, ends_spaced, pieces)


def build_lines(runs: list[TextRun], page: int = 0, cell: tuple | None = None) -> list[Line]:
    rows: list[list[TextRun]] = []
    bigs: list[TextRun] = []          # each row's largest run (the first, on a tie), kept as it grows
    for r in _dedupe(sorted(runs, key=lambda r: (r.y, r.x0))):
        size, n = r.size, len(rows)
        for k in range(n - 1, n - 4 if n > 3 else -1, -1):       # the last three rows, newest first
            big = bigs[k]
            # max(size, big.size) written out: this runs once per run per row tried
            if abs(r.y - big.y) <= SAME_LINE * (big.size if big.size > size else size):
                rows[k].append(r)
                if size > big.size:
                    bigs[k] = r
                break
        else:
            rows.append([r])
            bigs.append(r)
    # The text draws its spaces when any run of it holds one between characters or on its own.
    drawn = any(" " in r.text.strip() or (r.text and not r.text.strip()) for r in runs)
    gap = DRAWN_SPACE_GAP if drawn else SPACE_GAP
    lines = [_line(row, page, cell, gap) for row in rows if any(t.text.strip() for t in row)]
    return sorted(lines, key=lambda ln: (ln.y, ln.x0))
