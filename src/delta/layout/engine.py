"""The layout pipeline: merged items -> blocks per section -> planned breaks -> placed pages."""
from __future__ import annotations

from collections import Counter

from ..diff.changes import Comparison
from ..diff.compare import compare
from ..docx.hf import VARIANTS, SectionHf, displayed, page_number, resolve
from ..model import Document, Section
from .blocks import Ctx, collapse_spacing, note_height, para_block, sep_block
from .chrome import STREAMS, Chrome, hf_groups
from .comments import COLUMN_W, build_bubbles, place_bubbles, reserved_width
from .fonts import default_resolver
from .lines import Run
from .merged import Item, merged_items
from .pages import CellBox, FontRef, Layout, Page, TableRowBox
from .pieces import LayoutOptions, visible
from .placeline import place_line
from .planner import NOTE_SEP_PT, BlockSpec, Plan, plan_breaks
from .sides import side_items, side_options
from .tables import (MIN_CELL_W, PAD_X, PAD_Y, TableRowBlock, ctx_maps, table_blocks, table_maps,
                     table_runs)


def build_blocks(items: list[Item], ctx: Ctx) -> list:
    """The blocks of one section group in placement order: the body (paragraphs and table rows),
    then, under a separator, the endnotes. Footnotes are built here too but kept aside in
    ctx.notes by note key; the placer floats them to the foot of the page that references them.
    Every block's line_notes says which footnotes each of its lines brings to the page."""
    body = [it for it in items if it.stream == "body"]
    tail: list = []
    for stream, key, its in _note_groups(items):
        blocks = [para_block(it, its[j - 1] if j else None, its[j + 1] if j + 1 < len(its) else None, ctx)
                  for j, it in enumerate(its) if (ctx.opts.show_equal if it.row is None else visible(it.row, ctx.opts))]
        if stream == "footnote":
            ctx.notes[key] = collapse_spacing(blocks, ctx)
        else:
            tail.extend(blocks)
    out = _body_blocks(body, ctx)
    if tail:
        out.append(sep_block(tail[0].section, "endnote"))
        out.extend(tail)
    collapse_spacing(out, ctx)
    _annotate_notes(out, ctx)
    return out


def _body_blocks(items: list[Item], ctx: Ctx) -> list:
    out: list = []
    ranges = table_runs(items, ctx_maps(ctx))
    ri, i, n = 0, 0, len(items)
    while i < n:
        if ri < len(ranges) and ranges[ri][0] == i:
            s, e = ranges[ri]
            ri += 1
            out.extend(table_blocks(items[s:e], ctx))
            i = e
            continue
        it = items[i]
        show = ctx.opts.show_equal if it.row is None else visible(it.row, ctx.opts)
        if show:
            out.append(para_block(it, items[i - 1] if i else None, items[i + 1] if i + 1 < n else None, ctx))
        i += 1
    return out


def _note_groups(items: list[Item]) -> list[tuple[str, int, list[Item]]]:
    """(stream, key, items) per note: consecutive items of one note; the key is the index of the
    note's first comparison row (what its reference marks carry). A note of empty paragraphs
    only has no row and is dropped."""
    out: list[tuple[str, int, list[Item]]] = []
    run: list[Item] = []

    def flush():
        if run:
            key = next((it.row_index for it in run if it.row_index is not None), None)
            if key is not None:
                out.append((run[0].stream, key, list(run)))
            run.clear()

    for it in items:
        if it.stream == "body":
            flush()
            continue
        if run and (run[-1].stream, run[-1].note, run[-1].side) != (it.stream, it.note, it.side):
            flush()
        run.append(it)
    flush()
    return out


def _line_keys(runs: list[Run], seen: set, ctx: Ctx) -> list[int]:
    keys: list[int] = []
    for r in runs:
        k = r.piece.note
        if k is not None and k in ctx.notes and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


def _annotate_notes(blocks: list, ctx: Ctx) -> None:
    """Fill line_notes: the footnote keys each line (each table row) brings to its page, first
    reference only."""
    seen: set = set()
    for blk in blocks:
        if isinstance(blk, TableRowBlock):
            keys: list[int] = []
            for c in blk.cells:
                for pb in c.paras:
                    for line in pb.lines:
                        keys += _line_keys(line.runs, seen, ctx)
            blk.line_notes = [keys]
        else:
            blk.line_notes = [_line_keys(line.runs, seen, ctx) for line in blk.lines]


def _section_groups(items: list[Item], sections: list[Section]) -> list[tuple[int, list[Item]]]:
    """Consecutive items by section. A section whose predecessor closed with a continuous break
    flows on in the previous group (same page, same geometry)."""
    groups: list[tuple[int, list[Item]]] = []
    for it in items:
        if groups and groups[-1][1][-1].section == it.section:
            groups[-1][1].append(it)
            continue
        prev = sections[min(it.section - 1, len(sections) - 1)] if it.section > 0 else None
        if groups and prev is not None and prev.type in ("continuous", "nextColumn"):
            groups[-1][1].append(it)
            continue
        groups.append((it.section, [it]))
    return groups


def _spec(b, ctx: Ctx) -> BlockSpec:
    notes = None
    if b.line_notes and any(b.line_notes):
        notes = [sum(note_height(ctx.notes[k]) for k in keys) for keys in b.line_notes]
    return BlockSpec(b.line_heights, b.space_before, b.space_after, b.keep_next, b.keep_lines, b.page_break_before,
                     notes)


NO_HF = SectionHf({v: None for v in VARIANTS}, {v: None for v in VARIANTS})   # a document with no sections


def _new_page(pages: list[Page], sec: Section, section_idx: int, strip_w: float = 0.0) -> Page:
    # `strip_w` widens the sheet to the right for the comment column, so the body (laid out at the
    # section's own width) is never narrowed and a document paginates the same with or without
    # comments. The added strip sits outside the right margin; headers/footers stay on the body.
    pg = Page(len(pages) + 1, sec.page_w_pt + strip_w, sec.page_h_pt, sec.margin_left_pt, sec.margin_top_pt,
              sec.margin_right_pt, sec.margin_bottom_pt, section_idx)
    pages.append(pg)
    return pg


def _place_row(blk: TableRowBlock, page: Page, x: float, y: float, ctx: Ctx):
    boxes: list[CellBox] = []
    for c in blk.cells:
        boxes.append(CellBox(x + c.x, y, c.w, blk.height, c.v_merge_continue))
        cy = y + PAD_Y
        for pb in c.paras:
            cy += pb.space_before
            for li, line in enumerate(pb.lines):
                page.lines.append(place_line(pb, li, line, x + c.x + PAD_X, cy, max(MIN_CELL_W, c.w - 2 * PAD_X), ctx))
                cy += line.height
            cy += pb.space_after
    page.table_rows.append(TableRowBox(x, y, blk.w, blk.height, boxes, blk.changed, list(blk.cids)))


def _place(blocks: list, plan: Plan, sec: Section, section_idx: int, pages: list[Page], ctx: Ctx,
           chrome: Chrome):
    brk = Counter((b.block, b.line) for b in plan.breaks)
    ml = sec.margin_left_pt
    # Pages are created lazily: a planned break before the very first block (a page-tall space
    # before, say) must not leave a blank leading page behind. st["pi"] counts every page the
    # planner started (blank ones are materialised by new_pages), so it is the index the planner's
    # avail_of saw and the one the chrome answers for.
    st = {"page": None, "y": chrome.top(0), "top": True, "pi": 0}

    def page() -> Page:
        if st["page"] is None:
            st["page"] = _new_page(pages, sec, section_idx, reserved_width(ctx.cmp))
            chrome.draw(st["page"], st["pi"])
        return st["page"]

    def bottom() -> float:
        return chrome.bottom(st["pi"])

    pending: list[int] = []          # footnote keys to set at the foot of the current page

    def reserve() -> float:
        return NOTE_SEP_PT + sum(note_height(ctx.notes[k]) for k in pending) if pending else 0.0

    def brings(keys: list[int]) -> float:
        """The foot-of-page height a line adds by referencing `keys` (the separator when the page
        has no notes yet)."""
        if not keys:
            return 0.0
        return sum(note_height(ctx.notes[k]) for k in keys) + (0.0 if pending else NOTE_SEP_PT)

    def close_page():
        """Set the page's footnotes bottom-up: the separator, then the notes in reference order."""
        if not pending:
            return
        pg = page()
        y = bottom() - reserve()
        sep = sep_block(section_idx, "footnote")
        pg.lines.append(place_line(sep, 0, sep.lines[0], ml, y, ctx.content_w, ctx))
        y += NOTE_SEP_PT
        for k in pending:
            for pb in ctx.notes[k]:
                y += pb.space_before
                for li, line in enumerate(pb.lines):
                    pg.lines.append(place_line(pb, li, line, ml, y, ctx.content_w, ctx))
                    y += line.height
                y += pb.space_after
        pending.clear()

    def new_page():
        close_page()
        st["pi"] += 1
        st["page"], st["y"], st["top"] = None, chrome.top(st["pi"]), True

    def new_pages(n: int):
        # The planner can break more than once at the same point; each break starts its own page,
        # so every break after the first leaves a blank page behind (materialised here, since a
        # page nothing is placed on is otherwise never created).
        for k in range(n):
            if k:
                page()
            new_page()

    for bi, blk in enumerate(blocks):
        new_pages(brk[(bi, 0)])
        if plan.before[bi]:
            st["y"] += blk.space_before
        if isinstance(blk, TableRowBlock):
            keys = blk.line_notes[0] if blk.line_notes else []
            if st["y"] + blk.height + brings(keys) > bottom() - reserve() + 1e-6 and not st["top"]:
                new_page()
            _place_row(blk, page(), ml + blk.x, st["y"], ctx)
            pending.extend(keys)
            st["y"] += blk.height
            st["top"] = False
        else:
            for li, line in enumerate(blk.lines):
                if li > 0:
                    new_pages(brk[(bi, li)])
                keys = blk.line_notes[li] if blk.line_notes else []
                if st["y"] + line.height + brings(keys) > bottom() - reserve() + 1e-6 and not st["top"]:
                    new_page()          # safety net; the planner should have broken earlier
                page().lines.append(place_line(blk, li, line, ml, st["y"], ctx.content_w, ctx))
                pending.extend(keys)
                st["y"] += line.height
                st["top"] = False
        st["y"] += blk.space_after
    close_page()


def _ctx(cmp: Comparison, run_opts, fonts, sec: Section, doc: Document, faces: dict, maps, hf_items: dict) -> Ctx:
    full_w = sec.page_w_pt - sec.margin_left_pt - sec.margin_right_pt
    return Ctx(cmp, run_opts, fonts,
               full_w,        # the body keeps its full width; the comment column is added to the sheet (see _new_page), not carved out of the body
               sec.page_h_pt - sec.margin_top_pt - sec.margin_bottom_pt,
               doc.default_font, doc.default_size_pt, doc.default_tab_pt, faces, maps,
               html_spacing=doc.html_spacing, hf_items=hf_items, chrome_w=full_w)


def layout(cmp: Comparison, opts: LayoutOptions | None = None) -> Layout:
    lay, flags = _layout(cmp, opts, None)
    if "NUMPAGES" in flags:
        # The total is only known once every page exists, and a header or footer shows it: lay the
        # document out again with it, so the number is in place before anything is measured. The
        # second pass can in principle reach a different page count (the wider total breaking a
        # header line differently); we take that count rather than chase it with a third pass.
        lay, _ = _layout(cmp, opts, lay.page_count)
    return lay


def _layout(cmp: Comparison, opts: LayoutOptions | None, total: int | None) -> tuple[Layout, set]:
    """One layout pass. `total` is the page count NUMPAGES shows, unknown (None) in the first pass.
    Returns the layout and the flags the pass raised ("NUMPAGES" when a part carries that field)."""
    opts = opts or LayoutOptions()
    if cmp.b_doc is None or cmp.a_doc is None:
        raise ValueError("layout needs a Comparison from compare(a, b) (documents attached)")
    run_opts = side_options(opts)               # raises on an unknown side
    fonts = opts.fonts or default_resolver()
    doc = cmp.b_doc
    sections = doc.sections or [Section()]
    all_items = side_items(merged_items(cmp), cmp, opts.side)
    items = [it for it in all_items if it.stream not in STREAMS]
    hf_items = hf_groups(all_items)
    sec_hf = resolve(doc)
    aliases = {k: displayed(doc, k)[1] for k in STREAMS}
    seen: set = set()                           # (stream, part) already marked, across the whole layout
    flags: set = set()                          # shared with every Chrome of the pass
    pages_total = str(total) if total is not None else None
    number = 0                                  # Word page number of the last page placed
    pages: list[Page] = []
    faces: dict = {}
    maps = table_maps(cmp)      # one correspondence build for the whole layout
    for leader, group in _section_groups(items, sections):
        sec = sections[min(leader, len(sections) - 1)]
        ctx = _ctx(cmp, run_opts, fonts, sec, doc, faces, maps, hf_items)
        blocks = build_blocks(group, ctx)
        if not blocks:
            continue
        chrome = Chrome(sec, sec_hf[min(leader, len(sec_hf) - 1)] if sec_hf else NO_HF, doc, aliases,
                        ctx, page_number(sec, 0, number), seen, pages_total, flags)
        before = len(pages)
        plan = plan_breaks([_spec(b, ctx) for b in blocks], lambda p, c=chrome: c.bottom(p) - c.top(p))
        _place(blocks, plan, sec, leader, pages, ctx, chrome)
        if len(pages) > before:
            number = chrome.number(len(pages) - before - 1)
    if not pages:
        # Nothing was placed: the one empty page takes the body (last) section's geometry, which is
        # the page a reader of an empty document sees in Word, and carries that section's chrome.
        sec = sections[-1]
        ctx = _ctx(cmp, run_opts, fonts, sec, doc, faces, maps, hf_items)
        chrome = Chrome(sec, sec_hf[-1] if sec_hf else NO_HF, doc, aliases, ctx,
                        page_number(sec, 0, 0), seen, pages_total, flags)
        chrome.draw(_new_page(pages, sec, len(sections) - 1, reserved_width(cmp)), 0)
    # The comment bubbles: built at the column width with the document-wide fonts/defaults (any
    # section ctx carries them) and registered in `faces` before the refs are frozen below, then
    # resolved to the page of their anchor once every body line is placed.
    bubbles = build_bubbles(cmp, ctx, COLUMN_W) if cmp.comments and pages else None
    refs = {k: FontRef(f.path, f.font_number, f.family, f.bold, f.italic, f.synthetic, getattr(f, "symbol", False))
            for k, f in faces.items()}
    # the options as requested (opts.side names the side); the narrowed run options are not stored
    lay = Layout(pages, refs, opts)
    if bubbles is not None:
        place_bubbles(lay, cmp, bubbles, faces)
    return lay, flags


def layout_document(doc: Document, opts: LayoutOptions | None = None) -> Layout:
    return layout(compare(doc, doc), opts)
