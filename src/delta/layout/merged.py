"""The merged document: the revised document in order, empty paragraphs kept, each deleted
paragraph interleaved where it stood -- the order every sink lays out ("final showing markup").
"""
from __future__ import annotations

from dataclasses import dataclass

from ..diff.changes import Comparison, Row
from ..diff.units import Loc, walk
from ..model import NoteRef, Paragraph


@dataclass
class Item:
    para: Paragraph
    loc: Loc | None
    side: str                    # "b" = revised document; "a" = original-only (a deleted row)
    section: int                 # index into doc.sections
    row: Row | None = None       # None for an empty revised paragraph
    row_index: int | None = None
    stream: str = "body"         # body | footnote | endnote | header | footer (see units.walk)
    note: NoteRef | None = None
    part: str | None = None


def merged_items(cmp: Comparison) -> list[Item]:
    if cmp.b_doc is None or cmp.a_doc is None:
        raise ValueError("merged_items needs a Comparison from compare(a, b) (documents attached)")
    rows = cmp.rows
    by_ni: dict[int, int] = {}
    for k, r in enumerate(rows):
        if r.ni is not None:
            by_ni.setdefault(r.ni, k)       # a merge's revised paragraph is laid out with its head row
    out: list[Item] = []
    state = {"emitted": 0, "section": 0}

    def flush_deleted(upto: int):
        # rows[:upto] precede the revised paragraph about to be emitted; the deleted ones among
        # them that have not been placed yet go here, in row order.
        while state["emitted"] < upto:
            r = rows[state["emitted"]]
            if r.ni is None or (r.join == "merge" and not r.head):
                # a deleted row, or a merged-away original piece (drawn on the original side only)
                u = cmp.a_units[r.oi]
                out.append(Item(u.para, u.loc, "a", state["section"], r, state["emitted"], u.stream, u.note, u.part))
            state["emitted"] += 1

    ni = 0
    for para, loc, where in walk(cmp.b_doc):
        if para.is_empty:
            out.append(Item(para, loc, "b", state["section"], stream=where.stream, note=where.note, part=where.part))
        else:
            k = by_ni[ni]
            ni += 1
            flush_deleted(k)
            row = rows[k]
            out.append(Item(para, loc, "b", state["section"], row, k, where.stream, where.note, where.part))
            state["emitted"] = max(state["emitted"], k + 1)
        if para.props.section_break:
            # Deletions that stood after the closing paragraph but before the next revised one
            # belong to the section being closed, so place them before the index moves on.
            flush_deleted(by_ni.get(ni, len(rows)))
            state["section"] += 1
    flush_deleted(len(rows))
    _attach_deleted_parts(out)
    return out


def _attach_deleted_parts(items: list[Item]) -> None:
    """A deleted header/footer row (side "a") carries an original part name that means nothing on
    the revised side; it is shown with the revised part it sits next to in the merged order: the
    last revised part of its stream before it, else the first one after it, else None (the stream
    has no revised part: the deleted header shows on pages that have none)."""
    first_b: dict[str, str] = {}
    for it in items:
        if it.stream in ("header", "footer") and it.side == "b" and it.part is not None:
            first_b.setdefault(it.stream, it.part)
    cur: dict[str, str] = {}
    for it in items:
        if it.stream not in ("header", "footer"):
            continue
        if it.side == "b":
            if it.part is not None:
                cur[it.stream] = it.part
        else:
            it.part = cur.get(it.stream, first_b.get(it.stream))
