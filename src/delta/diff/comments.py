"""Comments compared by identity, not position. A comment is matched to its counterpart across the
two documents (durable id, then anchor overlap + author, then text similarity + author), classified
added / removed / edited / unchanged, and its text diffed with the shared inline machinery. Comments
never enter the body LCS: they hang off Comparison.comments, so the counts and the parity gate are
untouched.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..model import Document
from .changes import Comparison
from .inline import safe_inline, whole_segs
from .text import norm, sim

MATCH_THRESHOLD = 0.5


@dataclass
class Anchor:
    side: str
    para: object
    offset: int
    end_para: object | None
    end_offset: int | None
    on_deleted: bool
    on_inserted: bool


@dataclass
class CommentChange:
    state: str
    author: str
    initials: str
    date: str
    done: bool
    depth: int
    segments: list
    anchor: Anchor
    old_id: str | None
    new_id: str | None
    cid: int | None = None


def _text(comment) -> str:
    return "\n".join(p.text for p in comment.paras)


def _seg_overlap(row, offset: int, end_offset: int | None, mode: str) -> bool:
    """Does the anchor's [offset, end_offset) fall inside a `mode` ("del"/"ins") segment of a
    "changed" row, walking that side's own text (its eq + mode segments only)?"""
    pos = 0
    for s in row.segments:
        if s.pm or s.m not in ("eq", mode):          # a paragraph mark is in neither text
            continue
        seg_start, seg_end = pos, pos + len(s.t)
        if s.m == mode:
            if end_offset is None:
                if seg_start <= offset < seg_end:
                    return True
            elif offset < seg_end and end_offset > seg_start:
                return True
        pos = seg_end
    return False


def _para_state(cmp: Comparison, para, offset: int, end_offset: int | None) -> tuple[bool, bool]:
    """(on_deleted, on_inserted) for the body paragraph an anchor sits on: is the anchored span a
    deletion / insertion in the redline -- the whole row when it was dropped/added outright, or
    just the anchored slice when it sits inside an inline "changed" row? Matched by identity
    through the units' .para."""
    for u in cmp.a_units:
        if u.para is para:
            r = next((r for r in cmp.rows if r.oi == u.index), None)
            if r is None:
                return (False, False)
            if r.type == "deleted":
                return (True, False)
            if r.join:                      # a split / merge: each deleted segment says where it stands
                end = offset + 1 if end_offset is None else end_offset
                return (any(s.m == "del" and s.at is not None and s.at[0] == u.index
                            and offset < s.at[1] + len(s.t) and end > s.at[1]
                            for x in cmp.rows if x.join for s in x.segments), False)
            if r.type == "changed":
                return (_seg_overlap(r, offset, end_offset, "del"), False)
            return (False, False)
    for u in cmp.b_units:
        if u.para is para:
            r = next((r for r in cmp.rows if r.ni == u.index), None)
            if r is None:
                return (False, False)
            if r.type == "inserted":
                return (False, True)
            if r.type == "changed":
                return (False, _seg_overlap(r, offset, end_offset, "ins"))
            return (False, False)
    return (False, False)


def _anchor(doc: Document, cmp: Comparison, cid: str, side: str) -> Anchor | None:
    a = doc.comment_anchors.get(cid)
    if a is None:
        return None
    start = a.start or a.ref
    if start is None:
        return None
    para, off = start
    end_para, end_off = (a.end if a.end else (None, None))
    span_end = end_off if end_para is para else None
    on_del, on_ins = _para_state(cmp, para, off, span_end)
    return Anchor(side, para, off, end_para, end_off, on_del, on_ins)


def _body_order(cmp: Comparison) -> dict:
    """Paragraph identity -> its position in reading order (revised body first, then deleted rows in
    row order), so comments sort where their anchor sits. An original paragraph that pairs with a
    revised one (the row has both `oi` and `ni`) shares that revised paragraph's reading position --
    the common case for a removed comment on an "equal"/"changed" row -- and a purely deleted
    paragraph sorts after the revised body, in row order."""
    order: dict = {}
    for k, u in enumerate(cmp.b_units):
        order.setdefault(id(u.para), k)
    base = len(cmp.b_units)
    for r in cmp.rows:
        if r.oi is None:
            continue
        a_para = cmp.a_units[r.oi].para
        if r.ni is not None:
            pos = order.get(id(cmp.b_units[r.ni].para), base + r.oi)
        else:
            pos = base + r.oi
        order.setdefault(id(a_para), pos)
    return order


def _roots_and_replies(comments: dict) -> list:
    """Comment ids in thread order: each root followed by its replies (parent_id chains), roots in
    id order. depth 0 for a root, 1+ down the chain."""
    children: dict = {}
    para_to_id: dict = {}
    for c in comments.values():
        for pid in c.para_ids:
            para_to_id[pid] = c.id
    for c in comments.values():
        parent_cid = para_to_id.get(c.parent_id) if c.parent_id else None
        children.setdefault(parent_cid, []).append(c.id)
    out: list = []

    def walk(cid: str, depth: int):
        out.append((cid, depth))
        for kid in children.get(cid, []):
            walk(kid, depth + 1)

    for cid in children.get(None, []):
        walk(cid, 0)
    return out


def compare_comments(a: Document, b: Document, cmp: Comparison) -> list[CommentChange]:
    # Document order (dict insertion order), not a set: two comments that tie on every sort key
    # below (same author, same anchored paragraph, both roots) must still land in a reproducible
    # order -- a set's iteration order depends on Python's per-process string hash seed.
    a_ids = list(a.comments)
    b_ids = list(b.comments)
    # 1. match: greedy by (author, anchor overlap, text similarity).
    a_anchor = {cid: _anchor(a, cmp, cid, "a") for cid in a_ids}
    b_anchor = {cid: _anchor(b, cmp, cid, "b") for cid in b_ids}
    # row-correspondence maps: an anchor paragraph's identity -> the comparison row it sits in, so
    # the original and revised anchors can be compared across the two separate document parses.
    a_para_row = {id(cmp.a_units[r.oi].para): k for k, r in enumerate(cmp.rows) if r.oi is not None}
    b_para_row = {id(cmp.b_units[r.ni].para): k for k, r in enumerate(cmp.rows) if r.ni is not None}
    matched: dict = {}          # b_id -> a_id
    used_a: set = set()
    for bid in sorted(b_ids, key=lambda x: (b.comments[x].author, _text(b.comments[x]), x)):
        bc = b.comments[bid]
        best, bs = None, MATCH_THRESHOLD - 1e-9
        for aid in (x for x in a_ids if x not in used_a):
            ac = a.comments[aid]
            if ac.author != bc.author:
                continue
            score = sim(norm(_text(ac), False), norm(_text(bc), False))
            ba, bb_ = a_anchor.get(aid), b_anchor.get(bid)
            ra = a_para_row.get(id(ba.para)) if ba is not None else None
            rb = b_para_row.get(id(bb_.para)) if bb_ is not None else None
            if ra is not None and ra == rb:
                score += 0.5        # same anchored body row: a strong signal
            if score > bs:
                best, bs = aid, score
        if best is not None:
            matched[bid] = best
            used_a.add(best)
    # 2. build changes in reading order.
    order = _body_order(cmp)
    threads = _roots_and_replies(b.comments)
    depth_of = {cid: d for cid, d in threads}

    def sort_key(entry):
        cid, side = entry
        anc = (b_anchor if side == "b" else a_anchor).get(cid)
        pos = order.get(id(anc.para), len(order)) if anc else len(order)
        return (pos, depth_of.get(cid, 0))

    entries = [(bid, "b") for bid in b_ids] + [(aid, "a") for aid in a_ids if aid not in used_a]
    changes: list[CommentChange] = []
    for cid, side in sorted(entries, key=sort_key):
        if side == "b":
            bc = b.comments[cid]
            anc = b_anchor.get(cid) or _fallback_anchor(anc_side="b")
            depth = depth_of.get(cid, 0)
            if cid in matched:
                ac = a.comments[matched[cid]]
                at, bt = _text(ac), _text(bc)
                if norm(at, False) == norm(bt, False):
                    changes.append(CommentChange("unchanged", bc.author, bc.initials, bc.date, bc.done,
                                                 depth, whole_segs(bt, [], "eq"), anc, ac.id, bc.id))
                else:
                    changes.append(CommentChange("edited", bc.author, bc.initials, bc.date, bc.done,
                                                 depth, safe_inline(at, bt, [], []), anc, ac.id, bc.id))
            else:
                changes.append(CommentChange("added", bc.author, bc.initials, bc.date, bc.done,
                                             depth, whole_segs(_text(bc), [], "ins"), anc, None, bc.id))
        else:
            ac = a.comments[cid]
            anc = a_anchor.get(cid) or _fallback_anchor(anc_side="a")
            changes.append(CommentChange("removed", ac.author, ac.initials, ac.date, ac.done,
                                         0, whole_segs(_text(ac), [], "del"), anc, ac.id, None))
    # 3. number the flagged ones.
    n = 0
    for c in changes:
        if c.state != "unchanged":
            n += 1
            c.cid = n
    return changes


def _fallback_anchor(anc_side: str) -> Anchor:
    """A comment whose anchor markers are missing (malformed docx): a null anchor at the document
    end so it still lists, just without a bubble position."""
    return Anchor(anc_side, None, 0, None, None, False, False)
