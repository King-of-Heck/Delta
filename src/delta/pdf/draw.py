"""Walks a Layout and emits Painter calls: runs and markers with their decorations, the table
grid, paragraph borders, change bars, gutter change numbers, and the pages themselves.

Three painters take these calls: fpdf_sink.FpdfPainter (the PDF), viewer.svg.SvgPainter (the
on-screen pages) and testing.recpaint.RecordingPainter (tests). run_style, cid_label,
bar_intervals, content_bottom and report.report_lines are the shared decisions, so a change to
any of them shows in every surface."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from ..layout.pages import FontRef, GlyphRun, Layout, Page, PlacedLine, TableRowBox
from .decor import decorations, run_effects
from .rendersets import RenderSet
from .report import GAP, ReportInfo, draw_report, report_height

BLACK = "000000"

_NO_EFFECTS = frozenset()
TINT = 0.15           # how much of the category colour shows in a side pane's tint box


def tint(color: str) -> str:
    """The category colour mixed TINT into white, 6-hex: the fill behind a side pane's changed text."""
    return "".join(f"{round(255 - (255 - int(color[i:i + 2], 16)) * TINT):02x}" for i in (0, 2, 4))


@dataclass
class PdfOptions:
    render_set: str = "Standard"
    change_bars: bool = True
    report: str = "last"            # first | last | none
    changed_only: bool = False      # emit page 1 plus the pages with a change mark (V31)
    fonts: object | None = None     # a FontResolver (or the test FakeResolver); None = system fonts
    # the PDF creation date; the report's own time stamp is ReportInfo.when; None = now
    now: datetime | None = None
    marks: bool = False             # tint the changed runs of a side layout (spec §12.3); no effect on the blackline


def cid_label(cids: list[int]) -> str:
    """Gutter label: contiguous numbers hyphenated, the rest comma-joined ("1-3, 5, 7-8")."""
    nums = sorted(set(cids))
    parts: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(parts)


def run_style(g: GlyphRun, face: FontRef, rs: RenderSet, plain: bool = False) -> tuple[str, frozenset, bool, bool]:
    """How one run is painted: (colour, effects, fake_bold, fake_italic). The colour is the
    category's, else the run's own, else black; a synthetic face is faked in the direction the
    resolver could not supply, and the bold / italic effects fake it too. `plain` (a side
    layout) ignores the category: the run's own colour and underline only."""
    style = None if plain else rs.category(g.mode, g.fmt)
    color = style.color if style else (g.color or BLACK)
    # most runs are plain text: no category and no underline have no effects (run_effects((), False))
    eff = run_effects(style.effects if style else (), g.underline) if style or g.underline else _NO_EFFECTS
    return (color, eff, (face.synthetic and face.bold) or "bold" in eff,
            (face.synthetic and face.italic) or "italic" in eff)


def draw_runs(line: PlacedLine, runs: list[GlyphRun], fonts: dict[str, FontRef], rs: RenderSet, painter,
              plain: bool = False, marks_mode: str | None = None) -> None:
    """Text and decorations of one list of glyph runs on one line (the marker or the body runs).
    With `marks_mode` ("del" on the original side, "ins" on the modified) every run of that mode
    gets a tint box behind it first."""
    for g in runs:
        face = fonts[g.face]
        color, eff, fake_bold, fake_italic = run_style(g, face, rs, plain)
        if marks_mode is not None and g.mode == marks_mode:
            painter.box(g.x, line.top, g.w, line.height, tint(rs.category(g.mode, False).color))
        baseline = line.baseline - g.rise           # a reference mark sits above the baseline
        if g.text.strip():
            painter.text(g.x, baseline, g.text, face, g.size, color, fake_bold, fake_italic, width=g.w)
        if eff:                                     # no effects draw no rules
            for x1, x2, y, t, dotted in decorations(eff, g.x, g.x + g.w, baseline, g.size):
                painter.rule(x1, x2, y, t, color, dotted)


BAR_WIDTH = 1.5
BAR_GAP = 6.0          # bar x = margin_left - BAR_GAP
NUMBER_SIZE = 7.0
NUMBER_GAP = 10.0      # number right edge = margin_left - NUMBER_GAP
GRID_WIDTH = 0.5
BAR_MERGE_TOL = 0.5    # spans this close vertically are one bar (a changed row and the line under it)
_EPS = 1e-6


def _in_row(line: PlacedLine, rows: list[TableRowBox]) -> bool:
    return any(r.y - _EPS <= line.top and line.top + line.height <= r.y + r.h + _EPS
               and r.x - _EPS <= line.x <= r.x + r.w + _EPS for r in rows)


def bar_intervals(page: Page) -> list[tuple[float, float]]:
    """Vertical (top, bottom) spans of the change bars: changed rows as a whole, changed lines
    outside rows, merged when they touch or overlap."""
    spans = [(r.y, r.y + r.h) for r in page.table_rows if r.changed]
    spans += [(ln.top, ln.top + ln.height) for ln in page.lines
              if ln.changed and not _in_row(ln, page.table_rows)]
    spans.sort()
    merged: list[tuple[float, float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + BAR_MERGE_TOL:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def changed_pages(layout: Layout) -> list[int]:
    """The ONLY definition of a changed page: it carries a change mark - a line that is changed,
    starts a change number or carries one in its runs, or a changed table row."""
    out = []
    for page in layout.pages:
        lines = any(ln.changed or ln.cid_starts or any(g.cid is not None for g in ln.runs + ln.marker)
                    for ln in page.lines)
        rows = any(r.changed or r.cids for r in page.table_rows)
        if lines or rows:
            out.append(page.number)
    return out


def draw_grid(page: Page, painter) -> None:
    """The uniform table grid (KNOWN_DIVERGENCES (m)): four rules per cell, no top rule on a
    vertically merged continuation."""
    for row in page.table_rows:
        for c in row.cells:
            x2, y2 = c.x + c.w, c.y + c.h
            if not c.v_merge_continue:
                painter.line(c.x, c.y, x2, c.y, GRID_WIDTH, BLACK)
            painter.line(x2, c.y, x2, y2, GRID_WIDTH, BLACK)
            painter.line(c.x, y2, x2, y2, GRID_WIDTH, BLACK)
            painter.line(c.x, c.y, c.x, y2, GRID_WIDTH, BLACK)


MARKS_MODE = {"original": "del", "modified": "ins"}

COMMENT_STATE_CAT = {"added": "ins", "removed": "del", "edited": None, "unchanged": None}
BUBBLE_BORDER = 0.5
CONNECTOR_W = 0.5
BRACKET_W = 0.75
BRACKET_LEN = 4.0
NEUTRAL = "888888"


def draw_comments(page: Page, fonts: dict[str, FontRef], rs: RenderSet, painter) -> None:
    """The comment column for one page: each PlacedComment's bubble box, border, connector back
    to its anchor, an anchor bracket, and the header/text lines. `bubble.lines` were built by
    layout.comments.build_bubbles/_stack at the bubble's OWN origin (top-left 0,0, padding baked
    in) before packing knew the final position, so every line is translated by (pc.x, pc.y)
    before it is drawn -- that is the only adaptation packing needs at draw time."""
    for pc in page.comments:
        b = pc.bubble
        cat = COMMENT_STATE_CAT.get(b.state)
        fill = tint(rs.category(cat, False).color) if cat else "f4f4f4"
        edge = rs.category(cat, False).color if cat else NEUTRAL
        painter.box(pc.x, pc.y, pc.w, b.height, fill)
        # border: four thin lines
        x2, y2 = pc.x + pc.w, pc.y + b.height
        for (x1, y1, x3, y3) in ((pc.x, pc.y, x2, pc.y), (pc.x, y2, x2, y2),
                                  (pc.x, pc.y, pc.x, y2), (x2, pc.y, x2, y2)):
            painter.line(x1, y1, x3, y3, BUBBLE_BORDER, edge)
        # connector from the anchor to the bubble's left edge
        painter.line(pc.anchor_x, pc.anchor_y, pc.x, pc.y + b.height / 2, CONNECTOR_W, edge)
        # anchor bracket at the anchored body range (the first cut brackets the anchor point only)
        painter.rule(pc.anchor_x, pc.anchor_x + BRACKET_LEN, pc.anchor_y + 1, BRACKET_W, edge)
        # header + text lines: translate from the bubble's own origin to its packed position
        for ln in b.lines:
            moved = replace(ln, top=pc.y + ln.top, baseline=pc.y + ln.baseline,
                             runs=[replace(g, x=pc.x + g.x) for g in ln.runs])
            draw_runs(moved, moved.runs, fonts, rs, painter)


def draw_page(page: Page, fonts: dict[str, FontRef], rs: RenderSet, opts: PdfOptions, painter, number_face,
              side: str = "blackline") -> None:
    painter.page(page.w, page.h)
    draw_grid(page, painter)
    for ln in page.lines:                 # paragraph borders, under the text like the grid
        for q in ln.rules:
            painter.line(q.x1, q.y1, q.x2, q.y2, q.width, q.color)
    plain = side != "blackline"
    marks_mode = MARKS_MODE.get(side) if (plain and opts.marks) else None
    for ln in page.lines:
        draw_runs(ln, ln.marker, fonts, rs, painter, plain, marks_mode)
        draw_runs(ln, ln.runs, fonts, rs, painter, plain, marks_mode)
    if not plain:
        draw_comments(page, fonts, rs, painter)
    if plain:
        return                       # a side shows no gutter numbers, no change bars, no comments
    # One label per baseline: the cells of a table row are separate lines on one baseline, and each
    # may start a change of its own, so their numbers merge ("7-9") instead of printing on top of each other.
    starts: dict[float, list[int]] = {}
    for ln in page.lines:
        if ln.cid_starts:
            starts.setdefault(round(ln.baseline, 2), []).extend(ln.cid_starts)
    for baseline, cids in starts.items():
        label = cid_label(cids)
        w = number_face.width(label, NUMBER_SIZE)
        painter.text(page.margin_left - NUMBER_GAP - w, baseline, label, number_face, NUMBER_SIZE, BLACK,
                     width=w, role="gutter")
    if opts.change_bars:
        x = page.margin_left - BAR_GAP
        for y1, y2 in bar_intervals(page):
            painter.line(x, y1, x, y2, BAR_WIDTH, BLACK)


def content_bottom(page: Page) -> float:
    """The lowest edge of anything on the page (the top margin on an empty page)."""
    ys = [page.margin_top]
    ys += [ln.top + ln.height for ln in page.lines]
    ys += [r.y + r.h for r in page.table_rows]
    ys += [pc.y + pc.bubble.height for pc in page.comments]
    return max(ys)


@dataclass
class DrawResult:
    pages: int                  # pages emitted, report page included
    report_page: int | None     # 1-based page the report block is on, None when omitted


def _report_faces(layout: Layout, resolver):
    fam = next((f.family for f in layout.fonts.values()), None)
    return resolver.face(fam), resolver.face(fam, bold=True)


def draw_layout(layout: Layout, rs: RenderSet, opts: PdfOptions, painter, resolver,
                report: ReportInfo | None) -> DrawResult:
    regular, bold = _report_faces(layout, resolver)      # the document's own face; also the gutter numbers
    where = opts.report if report is not None else "none"
    pages = layout.pages
    if opts.changed_only:
        wanted = set(changed_pages(layout))
        pages = [p for p in layout.pages if p.number == 1 or p.number in wanted]
        if report is not None:
            report = replace(report, changed_only=(len(pages), layout.page_count))
    n, report_page = 0, None
    if not pages:                                        # a hand-built empty layout: nothing to draw, no report
        where = "none"
    if where == "first":
        g = pages[0]
        painter.page(g.w, g.h)
        draw_report(report, g.margin_left, g.margin_top, g.w - g.margin_left - g.margin_right, regular, bold, painter)
        n, report_page = 1, 1
    for page in pages:
        draw_page(page, layout.fonts, rs, opts, painter, regular, layout.options.side)
        n += 1
    if where == "last":
        last = pages[-1]
        w = last.w - last.margin_left - last.margin_right
        y = content_bottom(last) + GAP
        if y + report_height(report, regular, bold) > last.h - last.margin_bottom + _EPS:
            painter.page(last.w, last.h)
            n, y = n + 1, last.margin_top
        draw_report(report, last.margin_left, y, w, regular, bold, painter)
        report_page = n
    return DrawResult(n, report_page)
