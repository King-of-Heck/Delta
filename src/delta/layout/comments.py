"""The comment column: bubbles built at the column width, resolved to the page of their anchor, and
packed top-to-bottom so they do not overlap. A document with no comments reserves no column, so its
body geometry is unchanged. Overflow past the page bottom spills to the next page's column (a known
limit); a thread taller than a page is truncated (Task 5 draws the truncation)."""
from __future__ import annotations

from .blocks import Ctx
from .lines import Line, Spacing, break_lines, measure
from .pages import Bubble, CommentMark, GlyphRun, PlacedComment, PlacedLine
from .pieces import Piece

COLUMN_W = 150.0
COLUMN_GAP = 12.0
BUBBLE_PAD = 4.0
BUBBLE_MARGIN = 6.0        # vertical gap between packed bubbles


def reserved_width(cmp) -> float:
    return COLUMN_W + COLUMN_GAP if cmp.comments else 0.0


def _header(c) -> str:
    who = c.initials or c.author
    date = (c.date or "")[:10]        # ISO date, day precision
    head = f"{who} - {date}" if date else who
    return ("[resolved] " + head) if c.done else head


def _face(ctx: Ctx):
    return ctx.fonts.face(ctx.default_font, False, False)


def _lay_pieces(pieces: list[Piece], ctx: Ctx, inner: float) -> list[Line]:
    runs = measure(pieces, ctx.fonts, ctx.default_font, ctx.default_size)
    return break_lines(runs, inner, inner, Spacing(), _face(ctx), ctx.default_size)


def _placed_line(line: Line, ctx: Ctx, x: float, top: float) -> PlacedLine:
    runs: list[GlyphRun] = []
    cx = x
    for r in line.runs:
        ctx.faces.setdefault(r.face.key, r.face)
        p = r.piece
        runs.append(GlyphRun(r.text, cx, r.w, r.face.key, r.size, p.bold, p.italic, p.underline,
                             p.color, p.mode, p.fmt, p.cid, r.rise, p.field))
        cx += r.w
    return PlacedLine(x, top, line.height, top + line.ascent, runs, [], False, [], None, [], "comment")


def _stack(lines: list[Line], ctx: Ctx, start_y: float) -> list[PlacedLine]:
    """Lay the bubble's lines top-to-bottom from `start_y`, x at the bubble's inner left."""
    out: list[PlacedLine] = []
    y = start_y
    for line in lines:
        out.append(_placed_line(line, ctx, BUBBLE_PAD, y))
        y += line.height
    return out


def build_bubbles(cmp, ctx: Ctx, col_w: float) -> list:
    """One bubble per comment change: a bold header line, then the change's text laid out at the
    column's inner width (the same line breaking the body uses). Reuses ctx's document-wide fonts
    and defaults; the lines are display-only (stream "comment", never a diff stream)."""
    inner = col_w - 2 * BUBBLE_PAD
    out: list = []
    for c in cmp.comments:
        header = _header(c)
        lines = _lay_pieces([Piece(header, "eq", bold=True)], ctx, inner)
        seg_pieces = [Piece(s.t, s.m) for s in c.segments if s.t] or [Piece("", "eq")]
        lines += _lay_pieces(seg_pieces, ctx, inner)
        placed = _stack(lines, ctx, BUBBLE_PAD)
        height = 2 * BUBBLE_PAD + sum(line.height for line in lines)
        out.append((c, Bubble(c.cid, c.state, header, placed, c.depth, height)))
    return out


def _anchor_positions(layout, cmp) -> dict:
    """Anchor paragraph identity -> (page, baseline y, right end x) of the first body line that
    lays that paragraph out. Keyed by BOTH the a-side and b-side paragraph of each row (mirroring
    diff/comments.py::_body_order), so a removed comment -- whose anchor.para is the ORIGINAL-side
    Paragraph -- still resolves when that row survives as equal/changed (the common case)."""
    out: dict = {}
    for page in layout.pages:
        for ln in page.lines:
            if ln.stream != "body" or ln.row_index is None:
                continue
            if not 0 <= ln.row_index < len(cmp.rows):
                continue
            row = cmp.rows[ln.row_index]
            keys = []
            if row.ni is not None:
                keys.append(id(cmp.b_units[row.ni].para))
            if row.oi is not None:
                keys.append(id(cmp.a_units[row.oi].para))
            if not keys:
                continue
            pos = None
            for key in keys:
                if key not in out:
                    if pos is None:
                        anchor_x = max((g.x + g.w for g in ln.runs), default=ln.x)
                        pos = (page, ln.baseline, anchor_x)
                    out[key] = pos
    return out


def _solid(text: str, end: int) -> int:
    """How many non-space characters `text[:end]` holds. Word's range offsets count the paragraph's
    raw run text; the laid-out runs drop or stretch spaces (collapsed, broken lines, tabs), so the
    range is carried over to the glyphs by counting only the characters that are not white space."""
    return sum(1 for ch in text[:end] if not ch.isspace())


def _spans(anchor, cmp) -> list:
    """(paragraph, first, end) solid-character spans the comment covers; `end` None runs to the
    paragraph's end. A range over several paragraphs walks the anchor side's paragraphs between its
    two ends. A point anchor (no range, or an empty one) is one span with first == end."""
    para, off = anchor.para, anchor.offset
    raw = "".join(r.text for r in para.runs)
    first = _solid(raw, off)
    end_para, end_off = anchor.end_para, anchor.end_offset
    if end_para is None or end_off is None:
        return [(para, first, first)]
    if end_para is para:
        return [(para, first, max(first, _solid(raw, end_off)))]
    units = cmp.b_units if anchor.side == "b" else cmp.a_units
    order = [u.para for u in units if u.stream == "body"]
    ids = [id(p) for p in order]
    if id(para) not in ids or id(end_para) not in ids:
        return [(para, first, None)]
    i, j = ids.index(id(para)), ids.index(id(end_para))
    if j < i:
        return [(para, first, first)]
    end_raw = "".join(r.text for r in end_para.runs)
    return ([(para, first, None)] + [(p, 0, None) for p in order[i + 1:j]]
            + [(end_para, 0, _solid(end_raw, end_off))])


def _x_in_run(g, k: int, faces) -> float:
    """x of the gap before character k of glyph run g, measured with its face and scaled to the run's
    laid-out width (a justified line stretches it)."""
    face = faces.get(g.face) if faces else None
    if face is None or not g.text:
        return g.x + (g.w * k / len(g.text) if g.text else 0.0)
    full = face.width(g.text, g.size)
    part = face.width(g.text[:k], g.size)
    return g.x + (part * g.w / full if full else 0.0)


def _locate(spans, para_rows: dict, row_lines: dict, faces):
    """Carry the spans onto the laid-out lines. Returns (marks, point): marks are (page, line, x1, x2)
    pieces of tinted text, point (page, line, x) is where the commented text begins -- None when
    none of it could be found on the page."""
    marks: list = []
    point = None
    for para, first, end in spans:
        hit = para_rows.get(id(para))
        if hit is None:
            continue
        ri, para_side = hit
        modes = ("eq", "del") if para_side == "a" else ("eq", "ins")
        lines = row_lines.get(ri, ())
        pos = 0
        for page, ln in lines:
            x1 = x2 = None
            for g in ln.runs:
                if g.mode not in modes:
                    continue
                for k, ch in enumerate(g.text):
                    if ch.isspace():
                        continue
                    if point is None:
                        if first == end and first and pos == first - 1:   # a point: right after the letter before it
                            point = (page, ln, _x_in_run(g, k + 1, faces))
                        elif pos >= first and not (first == end and first):
                            point = (page, ln, _x_in_run(g, k, faces))
                    if pos >= first and (end is None or pos < end):
                        if x1 is None:
                            x1 = _x_in_run(g, k, faces)
                        x2 = _x_in_run(g, k + 1, faces)
                    pos += 1
            if x1 is not None and x2 > x1:
                marks.append((page, ln, x1, x2))
            if end is not None and pos >= end and point is not None:
                break
        if point is None and lines and pos <= first:   # the point sits after the paragraph's last letter
            page, ln = lines[-1]
            runs = [g for g in ln.runs if g.mode in modes]
            if runs:
                point = (page, ln, runs[-1].x + runs[-1].w)
    return marks, point


def _row_maps(layout, cmp):
    """(paragraph identity -> (row index, side), row index -> [(page, line)] in reading order)."""
    para_rows: dict = {}
    for ri, row in enumerate(cmp.rows):
        if row.ni is not None:
            para_rows.setdefault(id(cmp.b_units[row.ni].para), (ri, "b"))
        if row.oi is not None:
            para_rows.setdefault(id(cmp.a_units[row.oi].para), (ri, "a"))
    row_lines: dict = {}
    for page in layout.pages:
        for ln in page.lines:
            if ln.stream == "body" and ln.row_index is not None:
                row_lines.setdefault(ln.row_index, []).append((page, ln))
    return para_rows, row_lines


def place_bubbles(layout, cmp, bubbles, faces=None) -> None:
    """Resolve each bubble to the page + baseline of its anchor paragraph, then pack the bubbles of
    a page top-to-bottom (monotonic y, no overlap) and append the PlacedComments to page.comments.
    A comment with an unresolved anchor (no anchor paragraph, e.g. a threaded reply, or an anchor
    that isn't a body paragraph at all) inherits the most recently resolved position instead of
    falling back to page 0 -- comments arrive in reading order with replies right after their
    parent, so that position is the parent's."""
    if not layout.pages:
        return
    anchor_pos = _anchor_positions(layout, cmp)
    para_rows, row_lines = _row_maps(layout, cmp)
    default = (layout.pages[0], layout.pages[0].margin_top, 0.0, None)
    by_page: dict = {}
    last_resolved = None
    for c, bubble in bubbles:
        para = c.anchor.para if c.anchor is not None else None
        resolved = None
        if para is not None:
            marks, point = _locate(_spans(c.anchor, cmp), para_rows, row_lines, faces)
            for mpage, ln, x1, x2 in marks:
                mpage.comment_marks.append(CommentMark(x1, ln.top, x2 - x1, ln.height, c.state))
            if point is not None:
                ppage, ln, x = point
                if marks and marks[0][1] is ln:     # the line leaves from the end of the tint, not under its words
                    x = marks[0][3]
                resolved = (ppage, ln.baseline, x, ln.top + ln.height)
            elif (pos := anchor_pos.get(id(para))) is not None:
                resolved = (*pos, None)
        if resolved is not None:
            page, base_y, ax, lead_y = resolved
            last_resolved = resolved
        elif last_resolved is not None:
            page, base_y, ax, lead_y = last_resolved
        else:
            page, base_y, ax, lead_y = default
        by_page.setdefault(page.number, []).append((bubble, base_y, ax, lead_y, page))
    for _num, items in by_page.items():
        items.sort(key=lambda t: t[1])              # by anchor baseline
        page = items[0][4]
        col_x = page.w - page.margin_right - COLUMN_W
        y = page.margin_top
        for bubble, base_y, ax, lead_y, _pg in items:
            y = max(y, base_y - bubble.height / 2)      # start near the anchor
            page.comments.append(PlacedComment(col_x, y, COLUMN_W, bubble, ax, base_y, lead_y))
            y += bubble.height + BUBBLE_MARGIN
