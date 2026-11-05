"""The page model every sink draws: pages of positioned lines of glyph runs, plus table boxes.
Points, origin top-left, y downward. Colours, strikes and underlines are the sink's business
(render sets); a run carries its diff mode, formatting-change flag and change number instead.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .pieces import LayoutOptions


@dataclass(slots=True)
class GlyphRun:
    text: str
    x: float
    w: float
    face: str            # font face key, resolved through Layout.fonts
    size: float
    bold: bool
    italic: bool
    underline: bool      # the document's own underline (not the redline decoration)
    color: str | None    # the document's own colour, 6-hex lower-case or None
    mode: str            # eq | del | ins
    fmt: bool            # inside a formatting-change range
    cid: int | None
    rise: float = 0.0    # points above the line's baseline (a note reference mark)
    field: str | None = None    # "PAGE" | "NUMPAGES": this run is that field


@dataclass(frozen=True)
class Rule:
    """A paragraph border segment in page points: horizontal (y1 == y2) for a top or bottom
    border, vertical for a left or right one; `width` is the line's thickness, centred on it."""
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    color: str            # 6-hex lower-case


@dataclass
class PlacedLine:
    x: float
    top: float
    height: float
    baseline: float
    runs: list[GlyphRun]
    marker: list[GlyphRun]
    changed: bool
    cid_starts: list[int]     # passage numbers whose first glyph run is on this line (gutter numbers)
    row_index: int | None
    rules: list[Rule] = field(default_factory=list)   # the paragraph's border segments on this line
    stream: str = "body"      # body | footnote | endnote


@dataclass
class CellBox:
    x: float
    y: float
    w: float
    h: float
    v_merge_continue: bool


@dataclass
class TableRowBox:
    x: float
    y: float
    w: float
    h: float
    cells: list[CellBox]
    changed: bool
    cids: list[int]           # passage numbers of the row's cells (gutter numbers)


@dataclass
class Bubble:
    cid: int | None
    state: str                 # added | removed | edited | unchanged
    header: str                # "AL - 2026-09-16" (initials - date), a leading check if done
    lines: list                # PlacedLine of the comment text, x/y within the column
    depth: int
    height: float


@dataclass
class PlacedComment:
    x: float                   # column left
    y: float                   # bubble top (after packing)
    w: float                   # column width
    bubble: Bubble
    anchor_x: float            # body x of the anchor (connector start)
    anchor_y: float            # body baseline of the anchor line


@dataclass
class Page:
    number: int
    w: float
    h: float
    margin_left: float
    margin_top: float
    margin_right: float
    margin_bottom: float
    section: int
    lines: list[PlacedLine] = field(default_factory=list)
    table_rows: list[TableRowBox] = field(default_factory=list)
    comments: list = field(default_factory=list)   # PlacedComment, the bubbles in the right column
    label: str = ""           # the page's Word page number, formatted (what its PAGE fields show);
                              # not serialised by Layout.to_dict: it is chrome, not page content


@dataclass
class FontRef:
    path: str
    font_number: int
    family: str
    bold: bool
    italic: bool
    synthetic: bool
    symbol: bool = False      # a symbol-encoded face (Wingdings, Symbol): the viewer draws its outlines


@dataclass
class Layout:
    pages: list[Page]
    fonts: dict[str, FontRef]
    options: LayoutOptions

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def to_dict(self) -> dict:
        r = lambda v: round(v, 2)  # noqa: E731

        def run(g: GlyphRun) -> dict:
            d = {"t": g.text, "x": r(g.x), "w": r(g.w), "face": g.face, "size": r(g.size), "b": g.bold,
                 "i": g.italic, "u": g.underline, "clr": g.color, "m": g.mode, "fmt": g.fmt, "cid": g.cid}
            if g.rise:
                d["rise"] = r(g.rise)
            return d

        def line(ln: PlacedLine) -> dict:
            d = {"x": r(ln.x), "top": r(ln.top), "h": r(ln.height), "baseline": r(ln.baseline),
                 "changed": ln.changed, "cids": list(ln.cid_starts), "row": ln.row_index,
                 "runs": [run(g) for g in ln.runs], "marker": [run(g) for g in ln.marker]}
            if ln.rules:              # only when present, so a border-free page model reads as before
                d["rules"] = [{"x1": r(q.x1), "y1": r(q.y1), "x2": r(q.x2), "y2": r(q.y2), "w": r(q.width),
                               "clr": q.color} for q in ln.rules]
            if ln.stream != "body":
                d["stream"] = ln.stream
            return d

        def trow(t: TableRowBox) -> dict:
            return {"x": r(t.x), "y": r(t.y), "w": r(t.w), "h": r(t.h), "changed": t.changed,
                    "cids": list(t.cids),
                    "cells": [{"x": r(c.x), "y": r(c.y), "w": r(c.w), "h": r(c.h), "vm": c.v_merge_continue}
                              for c in t.cells]}

        return {
            "page_count": self.page_count,
            "options": {k: getattr(self.options, k) for k in
                        ("show_equal", "show_insertions", "show_deletions", "show_formatting", "side")},
            "fonts": {k: asdict(f) for k, f in self.fonts.items()},
            "pages": [{"number": p.number, "w": r(p.w), "h": r(p.h),
                       "margins": [r(p.margin_left), r(p.margin_top), r(p.margin_right), r(p.margin_bottom)],
                       "section": p.section, "lines": [line(ln) for ln in p.lines],
                       "table_rows": [trow(t) for t in p.table_rows],
                       **({"comments": [{"x": r(pc.x), "y": r(pc.y), "w": r(pc.w),
                                         "anchor": [r(pc.anchor_x), r(pc.anchor_y)],
                                         "cid": pc.bubble.cid, "state": pc.bubble.state,
                                         "header": pc.bubble.header, "depth": pc.bubble.depth,
                                         "lines": [line(ln) for ln in pc.bubble.lines]}
                                        for pc in p.comments]} if p.comments else {})}
                      for p in self.pages],
        }
