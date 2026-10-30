"""The summary report block (V28): names, time, rendering set, options, counts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .. import __version__
from ..diff.changes import Comparison

TITLE = "Comparison summary"
TITLE_SIZE = 11.0
SIZE = 9.0
GAP = 24.0           # space between the last content and the block
RULE_GAP = 6.0       # space between the top rule and the title
LINE_GAP = 2.0       # space between the title and the first line
RULE_T = 0.5
BLACK = "000000"
ELLIPSIS = "..."


@dataclass
class ReportInfo:
    original: str
    modified: str
    when: datetime
    render_set: str
    summary: dict
    ignore_case: bool
    count_numbering: bool
    changed_only: tuple[int, int] | None = None   # (pages emitted, pages in the layout) for a changed-pages-only PDF
    comments: tuple | None = None   # (added, removed, edited) counts, None when no comment changed
    tracked: tuple[int, int] | None = None    # (original, modified) tracked changes read as accepted; None when both are zero
    source: tuple | None = None     # (original, modified) skipped page numbers when a source is a PDF; None for Word
    mixed: bool = False             # a Word document against a PDF: wording and numbering only


def report_info(cmp: Comparison, original: str, modified: str, render_set: str,
                when: datetime | None = None) -> ReportInfo:
    flagged = [c for c in cmp.comments if c.state != "unchanged"]
    comments = None
    if flagged:
        comments = (sum(c.state == "added" for c in flagged),
                    sum(c.state == "removed" for c in flagged),
                    sum(c.state == "edited" for c in flagged))
    counts = tuple(d.tracked_changes if d is not None else 0 for d in (cmp.a_doc, cmp.b_doc))
    tracked = counts if any(counts) else None
    docs = (cmp.a_doc, cmp.b_doc)
    source = None
    mixed = all(d is not None for d in docs) and docs[0].source_kind != docs[1].source_kind
    if mixed or all(d is not None and d.source_kind == "pdf" for d in docs):
        source = tuple(tuple(d.skipped_pages) for d in docs)
    return ReportInfo(original, modified, when or datetime.now(), render_set, dict(cmp.summary),
                      cmp.ignore_case, cmp.count_numbering, comments=comments, tracked=tracked, source=source,
                      mixed=mixed)


def _onoff(b: bool) -> str:
    return "on" if b else "off"


def _page_list(pages) -> str:
    """(41, 42, 44) -> '41-42, 44'; () -> 'none'."""
    if not pages:
        return "none"
    out, start, prev = [], pages[0], pages[0]
    for p in list(pages[1:]) + [None]:
        if p is not None and p == prev + 1:
            prev = p
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = p
    return ", ".join(out)


def report_lines(info: ReportInfo) -> list[str]:
    s = info.summary
    lines = [
        f"Original: {info.original}",
        f"Modified: {info.modified}",
        f"Compared: {info.when:%Y-%m-%d %H:%M} with Delta {__version__}",
        f"Rendering set: {info.render_set}",
        f"Options: ignore case {_onoff(info.ignore_case)}; count numbering changes {_onoff(info.count_numbering)}",
        f"Changes: {s['total']} (insertions {s['insertions']}, deletions {s['deletions']}, "
        f"numbering {s['numbering_changes']}); formatting {s['formatting']} (not counted)",
    ]
    if info.tracked and any(info.tracked):
        a, b = info.tracked
        lines.insert(5, f"Tracked changes in sources: original {a}, modified {b} (compared as accepted)")
    if info.source is not None:
        extra = [("Word compared with PDF: wording and numbering only. Formatting, headers, footers and comments "
                  "were not compared.") if info.mixed
                 else "Sources: PDFs, text and tables re-read from the pages (layout approximate, images not shown)"]
        if any(info.source):
            a, b = info.source
            extra.append(f"Pages with no text, not compared: original {_page_list(a)}, modified {_page_list(b)}")
        lines[5:5] = extra
    if info.comments:
        add, rem, edt = info.comments
        lines.append(f"Comments: {add} added, {rem} removed, {edt} edited (not counted)")
    if info.changed_only:
        lines.append(f"Changed pages only: {info.changed_only[0]} of {info.changed_only[1]} pages")
    return lines


def report_height(info: ReportInfo, regular, bold) -> float:
    return RULE_GAP + bold.line_height(TITLE_SIZE) + LINE_GAP + len(report_lines(info)) * regular.line_height(SIZE)


def _fit(text: str, face, size: float, w: float) -> str:
    if face.width(text, size) <= w:
        return text
    while text and face.width(text + ELLIPSIS, size) > w:
        text = text[:-1]
    return text + ELLIPSIS


def draw_report(info: ReportInfo, x: float, y: float, w: float, regular, bold, painter) -> float:
    """Draws the block with its top rule at y; returns the y below it."""
    painter.rule(x, x + w, y, RULE_T, BLACK)
    y += RULE_GAP
    painter.text(x, y + bold.ascent(TITLE_SIZE), TITLE, bold, TITLE_SIZE, BLACK,
                 fake_bold=bool(bold.synthetic and bold.bold))
    y += bold.line_height(TITLE_SIZE) + LINE_GAP
    for t in report_lines(info):
        painter.text(x, y + regular.ascent(SIZE), _fit(t, regular, SIZE, w), regular, SIZE, BLACK)
        y += regular.line_height(SIZE)
    return y
