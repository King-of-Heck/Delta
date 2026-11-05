"""Table rows as atomic layout blocks.

A table is a run of consecutive merged items carrying a table location, grouped into visual rows
by the revised-side table and row each item belongs to -- an original-side table or row joins the
revised one holding any paragraph the two still share, and is its own only when nothing survives,
so a deleted paragraph or row from the original sits inside the revised row it was part of. Column geometry comes from the revised-side table (the original
side only when the whole table was deleted): grid widths, scaled down when wider than the content
width, equal columns when there is no grid; a cell's x/width follow the model row's grid spans.
Row height is the tallest cell (Word's default cell margins: 5.4 pt left/right, none top/bottom),
raised to trHeight for atLeast, fixed for exact. Rows never split across pages; a row taller than
the page degrades to stacked paragraphs so line-level breaking applies (never off-page).
Equal rows stay in the table under hide-unchanged; hidden inserted/deleted rows are dropped.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..model import Table
from .blocks import collapse_spacing, Ctx, ParaBlock, para_block
from .merged import Item
from .pieces import visible

PAD_X = 5.4        # Word default cell margins: 108 twips left and right
PAD_Y = 0.0        # none top/bottom
MIN_CELL_W = 20.0


@dataclass
class CellLayout:
    x: float
    w: float
    paras: list[ParaBlock]
    v_merge_continue: bool

    @property
    def height(self) -> float:
        return sum(p.space_before + p.height + p.space_after for p in self.paras)


@dataclass
class TableRowBlock:
    cells: list[CellLayout]
    x: float
    w: float
    height: float
    section: int
    changed: bool
    cids: list[int]
    space_before: float = 0.0
    space_after: float = 0.0
    keep_next: bool = False
    keep_lines: bool = False
    page_break_before: bool = False
    line_notes: list | None = None    # one entry: the footnote keys first referenced in the row (engine)

    @property
    def line_heights(self) -> list[float]:
        return [self.height]


def table_maps(cmp) -> tuple[dict[int, int], dict[tuple[int, int], tuple[int, int]]]:
    """Original-side -> revised-side table and row correspondence, from the paired rows: an
    original table (row) maps to the revised table (row) holding any paragraph the two still
    share. Unmapped ones exist only in the original (a deleted table or a deleted row).

    Only body rows take part: a header or footer table counts its own table indices from 0 within
    its part, so its Locs would claim body coordinates."""
    tmap: dict[int, int] = {}
    rmap: dict[tuple[int, int], tuple[int, int]] = {}
    for r in cmp.rows:
        if r.oi is None or r.ni is None or cmp.unit_for(r).stream != "body":
            continue
        la, lb = cmp.a_units[r.oi].loc, cmp.b_units[r.ni].loc
        if la is None or lb is None:
            continue
        tmap.setdefault(la.ti, lb.ti)
        rmap.setdefault((la.ti, la.ri), (lb.ti, lb.ri))
    return tmap, rmap


def _table_key(it: Item, tmap) -> tuple:
    if it.side == "b":
        return ("b", it.loc.ti)
    return ("b", tmap[it.loc.ti]) if it.loc.ti in tmap else ("a", it.loc.ti)


def _row_key(it: Item, rmap) -> tuple:
    if it.side == "b":
        return ("b", it.loc.ti, it.loc.ri)
    k = (it.loc.ti, it.loc.ri)
    return ("b", *rmap[k]) if k in rmap else ("a", it.loc.ti, it.loc.ri)


def ctx_maps(ctx: Ctx) -> tuple:
    """The correspondence maps the engine built once for this layout, or fresh ones for a caller
    that did not build them."""
    return ctx.maps if ctx.maps is not None else table_maps(ctx.cmp)


def table_runs(items: list[Item], maps: tuple) -> list[tuple[int, int]]:
    """[start, end) index ranges of consecutive items forming one table (its revised-side table,
    or an original-only one). `maps` comes from table_maps(comparison)."""
    tmap, _ = maps
    out: list[tuple[int, int]] = []
    i, n = 0, len(items)
    while i < n:
        if items[i].loc is None:
            i += 1
            continue
        key = _table_key(items[i], tmap)
        j = i
        while j < n and items[j].loc is not None and _table_key(items[j], tmap) == key:
            j += 1
        out.append((i, j))
        i = j
    return out


def table_blocks(group: list[Item], ctx: Ctx) -> list:
    docs = {"a": ctx.cmp.a_doc, "b": ctx.cmp.b_doc}
    tops: dict = {}

    def table_at(s: str, i: int) -> Table:
        # table_by_ti, with each side's top-level tables listed once per group, not once per row
        got = tops.get(s)
        if got is None:
            got = tops[s] = [b for b in docs[s].blocks if isinstance(b, Table)]
        return got[i]

    tmap, rmap = ctx_maps(ctx)
    side, ti = _table_key(group[0], tmap)
    table = table_at(side, ti)
    cols = max(1, len(table.grid_pt) or max((len(r.cells) for r in table.rows), default=0))
    tx = table.ind_pt
    avail_w = max(MIN_CELL_W, ctx.content_w - tx)
    if len(table.grid_pt) == cols and sum(table.grid_pt) > 0:
        grid = list(table.grid_pt)
    else:
        grid = [avail_w / cols] * cols
    total = sum(grid)
    if total > avail_w:
        grid = [g * avail_w / total for g in grid]
    col_x = [0.0]
    for g in grid:
        col_x.append(col_x[-1] + g)

    row_groups: list[tuple[tuple, list[Item]]] = []
    for it in group:
        if it.row is not None and not (visible(it.row, ctx.opts) or it.row.type == "equal"):
            continue
        k = _row_key(it, rmap)
        if not row_groups or row_groups[-1][0] != k:
            row_groups.append((k, []))
        row_groups[-1][1].append(it)

    out: list = []
    for gi, (k, its) in enumerate(row_groups):
        mrow = table_at(k[0], k[1]).rows[k[2]]
        by_ci: dict[int, list[Item]] = {}
        for it in its:
            by_ci.setdefault(min(it.loc.ci, len(mrow.cells) - 1), []).append(it)
        cells: list[CellLayout] = []
        gc = 0
        for ci, cell in enumerate(mrow.cells):
            span = max(1, cell.grid_span)
            c0, c1 = min(gc, cols), min(gc + span, cols)
            cx, cw = col_x[c0], max(0.0, col_x[c1] - col_x[c0])
            gc += span
            cell_items = by_ci.get(ci, [])
            paras = collapse_spacing([para_block(it, cell_items[j - 1] if j else None,
                                                 cell_items[j + 1] if j + 1 < len(cell_items) else None,
                                                 ctx, avail_w=max(MIN_CELL_W, cw - 2 * PAD_X))
                                      for j, it in enumerate(cell_items)], ctx)
            cells.append(CellLayout(cx, cw, paras, cell.v_merge == "continue"))
        h = max((c.height for c in cells), default=0.0) + 2 * PAD_Y
        if mrow.height_rule == "exact" and mrow.height_pt:
            h = mrow.height_pt
        elif mrow.height_pt:
            h = max(h, mrow.height_pt)
        paras_all = [p for c in cells for p in c.paras]
        changed = any(p.changed for p in paras_all)
        cids = list(dict.fromkeys(c for p in paras_all for c in p.cids))
        pbb = gi == 0 and bool(its[0].para.props.page_break_before) and not its[0].para.props.section_page_break
        if h > ctx.avail_h - 12:
            # Page-tall: an unsplittable row cannot render; degrade to stacked paragraphs.
            for n, it in enumerate(its):
                pb = para_block(it, None, None, ctx, avail_w=avail_w)
                pb.x += tx
                pb.space_before, pb.space_after = 0.0, 2.0
                pb.page_break_before = pbb and n == 0
                out.append(pb)
            continue
        out.append(TableRowBlock(cells, tx, col_x[-1], h, its[0].section, changed, cids, page_break_before=pbb))
    return out
