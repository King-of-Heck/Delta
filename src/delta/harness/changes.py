"""Render the change model in the reference engine's HTML row shape for the parity gate (the
reference numbers rows; Delta numbers passages, so the number is not emitted).

Only the harness speaks HTML; the change model itself is plain data. The escaping and wrapper
rules here reproduce the reference's row html byte for byte, which is what lets the gate compare
whole rows instead of a lossy projection.

Notes (KNOWN_DIVERGENCES (o)): the reference has no note rows; it appends every footnote/endnote
reference to its paragraph's text as a sentinel token and diffs those with the words. The
projection here does the same: note rows are dropped, unit indices count body units only, and
the sentinels are appended to the text and html in the reference's own form.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from ..diff.changes import Comparison, Row, empty_summary
from ..diff.chars import FmtSpan
from ..diff.fmt import FmtRange, RangeCursor
from ..diff.inline import Seg
from ..diff.units import Unit


def sentinel(ref) -> str:
    """The reference's in-text anchor for a note reference: U+E000 kind:id U+E001."""
    return f"\ue000{ref.kind}:{ref.id}\ue001"


def sentinels(u: Unit | None) -> list[str]:
    return [sentinel(ref) for _, ref in u.note_refs] if u is not None else []


def body_index(units: list[Unit]) -> dict[int, int]:
    """Unit index -> the reference's paragraph index (body paragraphs only)."""
    out: dict[int, int] = {}
    for u in units:
        if u.stream == "body":
            out[u.index] = len(out)
    return out

_DEFAULT_SPAN = FmtSpan(0, 0, False, False, False, None, None, None)


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_html(segs: list[Seg]) -> str:
    html = ""
    k = 0
    while k < len(segs):
        m = segs[k].m
        inner = ""
        while k < len(segs) and segs[k].m == m:
            s = segs[k]
            inner += "<b>" + esc(s.t) + "</b>" if s.b else esc(s.t)
            k += 1
        if m == "del":
            html += "<del>" + inner + "</del>"
        elif m == "ins":
            html += "<ins>" + inner + "</ins>"
        else:
            html += inner
    return html


def fmt_wrap(text: str, spans: list[FmtSpan], ranges: list[FmtRange]) -> str:
    span_cur = RangeCursor(spans)
    diff_cur = RangeCursor(ranges)

    out = ""
    i = 0
    while i < len(text):
        sp = span_cur.at(i) or _DEFAULT_SPAN
        d = diff_cur.at(i)
        j = i + 1
        while j < len(text):
            s2 = span_cur.at(j) or sp
            d2 = diff_cur.at(j)
            if not s2.same_fmt(sp):
                break
            if (d.desc if d else None) != (d2.desc if d2 else None):
                break
            j += 1
        inner = esc(text[i:j])
        if sp.b:
            inner = "<b>" + inner + "</b>"
        if sp.i:
            inner = "<i>" + inner + "</i>"
        if sp.u:
            inner = "<u>" + inner + "</u>"
        if d:
            inner = '<span class="fmtchg" title="' + esc(d.desc).replace('"', "&quot;") + '">' + inner + "</span>"
        out += inner
        i = j
    return out


def _tail(cmp: Comparison, row: Row) -> str:
    """The sentinel tokens the reference diffs at the end of the row: shared ones plain, the
    original's only ones deleted, the revision's only ones inserted (its inline diff's order)."""
    a = sentinels(cmp.a_units[row.oi]) if row.oi is not None else []
    b = sentinels(cmp.b_units[row.ni]) if row.ni is not None else []
    if row.type == "deleted":
        return "".join(a)
    if row.type == "inserted":
        return "".join(b)
    out = ""
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out += "".join(a[i1:i2])
        else:
            if i2 > i1:
                out += "<del>" + "".join(a[i1:i2]) + "</del>"
            if j2 > j1:
                out += "<ins>" + "".join(b[j1:j2]) + "</ins>"
    return out


def row_record(cmp: Comparison, row: Row, oi_map: dict | None = None, ni_map: dict | None = None) -> dict:
    u = cmp.unit_for(row)
    tail = _tail(cmp, row)
    if row.fmt_changed:
        html = fmt_wrap(u.text, u.fmt_spans, row.fmt_ranges) + tail
    elif row.type in ("deleted", "inserted") and tail:
        segs = list(row.segments)                       # the sentinels sit inside the del/ins wrapper
        last = segs[-1]
        segs[-1] = Seg(last.m, last.t + tail, last.b, last.cid)
        html = render_html(segs)
    else:
        html = render_html(row.segments) + tail
    oi = oi_map.get(row.oi, row.oi) if oi_map is not None else row.oi
    ni = ni_map.get(row.ni, row.ni) if ni_map is not None else row.ni
    return {
        "type": row.type, "cat": row.cat, "oi": oi, "ni": ni, "html": html,
        "numChanged": row.num_changed, "oldMarker": row.old_marker,
        "fmtChanged": row.fmt_changed, "fmtDescs": [r.desc for r in row.fmt_ranges] or None,
        "tbl": u.loc.as_dict() if u.loc else None,
    }


def body_rows(cmp: Comparison) -> list[Row]:
    return [r for r in cmp.rows if cmp.unit_for(r).stream == "body"]


def records(cmp: Comparison) -> list[dict]:
    oi_map, ni_map = body_index(cmp.a_units), body_index(cmp.b_units)
    return [row_record(cmp, r, oi_map, ni_map) for r in body_rows(cmp)]


def reference_summary(cmp: Comparison) -> dict:
    """The per-row summary keys the gate compares, counted over body rows only (the reference
    never sees a note row)."""
    s = empty_summary()
    for r in body_rows(cmp):
        if r.num_changed:
            s["numbering"] += 1
        if r.join and r.head:
            s["splits" if r.join == "split" else "merges"] += 1
        if r.type == "equal":
            if r.fmt_changed:
                s["formatting"] += 1
        else:
            s["punctuation" if r.cat == "punctuation" else "content"] += 1
    return s
