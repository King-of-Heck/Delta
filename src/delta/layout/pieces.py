"""Styled text pieces of one comparison row, ready for measurement.

A row's segments concatenate back to the paragraph texts they came from: the del-mode segments
plus the shared (eq) segments spell the original paragraph, the ins-mode segments plus the shared
ones spell the revised paragraph (the dense-rewrite coalescing duplicates a shared connective into
both, and both cursors advance over it). Walking the segments with one cursor per side therefore
recovers where every segment sits in its source text, and the unit's per-character formatting
spans are sliced at those offsets. Bold is per character here (the diff's per-token majority bold
on Seg.b is a presentation choice of the reference row shape and is not used), and a piece is bold
when the run or its paragraph style says so: the style's bold is drawn, never compared.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..diff.changes import Comparison, Row
from ..diff.units import Unit


@dataclass
class LayoutOptions:
    show_equal: bool = True
    show_insertions: bool = True
    show_deletions: bool = True
    show_formatting: bool = True
    fonts: object | None = None    # a FontResolver (or the test FakeResolver); None = system fonts
    side: str = "blackline"        # blackline | original | modified (spec §12.1)


@dataclass
class Piece:
    text: str
    mode: str                      # eq | del | ins
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font: str | None = None
    size: float | None = None
    color: str | None = None
    fmt: bool = False              # inside a formatting-change range (only when formatting is shown)
    cid: int | None = None            # the passage number (spec 14.4)
    caps: bool = False             # drawn in capitals (w:caps or w:smallCaps)
    tab: int = 0                   # this piece is a tab: the number of stops to advance (its text is
                                   # the collapsed space it became, or "" before the first word)
    rise: bool = False             # a footnote/endnote reference mark: drawn small, above the baseline
    note: int | None = None        # a reference mark: the index of the note's first comparison row
    field: str | None = None       # "PAGE" | "NUMPAGES": this piece is a field run; its text is the token
    field_text: str | None = None  # that field's cached result text: drawn where there is no live value
    image_w: float = 0.0           # an inline image: this piece has no text and is this wide a box
    image_h: float = 0.0

    def style_key(self):
        return (self.mode, self.bold, self.italic, self.underline, self.font, self.size, self.color,
                self.fmt, self.cid, self.caps, self.tab, self.rise, self.field, self.field_text,
                self.image_w, self.image_h)


def visible(row: Row, opts: LayoutOptions) -> bool:
    if row.type == "equal":
        return opts.show_equal or (row.fmt_changed and opts.show_formatting) or row.num_changed
    if row.type == "inserted":
        return opts.show_insertions
    if row.type == "deleted":
        return opts.show_deletions
    return opts.show_insertions or opts.show_deletions


def merge_pieces(pieces: list[Piece]) -> list[Piece]:
    out: list[Piece] = []
    for p in pieces:
        if out and out[-1].style_key() == p.style_key() and not p.tab and not p.rise and not p.image_w:
            out[-1].text += p.text
        elif p.text or p.tab or p.image_w:
            out.append(Piece(p.text, p.mode, p.bold, p.italic, p.underline, p.font, p.size, p.color,
                             p.fmt, p.cid, p.caps, p.tab, p.rise, p.note, p.field, p.field_text,
                             p.image_w, p.image_h))
    return out


def _mark(unit: Unit, ref, at: int, mode: str, side: str, cmp: Comparison) -> Piece:
    """The reference mark of `ref` at offset `at`: the note's number in its own document (`side`),
    drawn raised in the formatting of the text it sits in, in the mode of the note itself
    (inserted note, inserted mark) when the surrounding text is shared."""
    sp = next((x for x in unit.fmt_spans if x.s <= at < x.e), unit.fmt_spans[-1] if unit.fmt_spans else None)
    m = cmp.note_modes.get((side, ref), mode) if mode == "eq" else mode
    doc = cmp.a_doc if side == "a" else cmp.b_doc
    text = str(doc.note_numbers.get(ref, "?")) if doc is not None else "?"
    key = cmp.note_rows.get((side, ref))
    if sp is None:
        return Piece(text, m, rise=True, note=key)
    return Piece(text, m, sp.b or sp.style_bold, sp.i, sp.u, sp.f, sp.z, sp.clr, rise=True, note=key)


def _slice(unit: Unit, s: int, e: int, mode: str, ranges, cid, refs=(), cmp: Comparison | None = None) -> list[Piece]:
    """The pieces of unit.text[s:e] in `mode`, cut at every formatting or formatting-change edge
    and at every tab; `refs` = (offset, NoteRef, side) reference marks to splice in at their
    offsets (a mark at the end of the slice only when the text ends there)."""
    spans = unit.fmt_spans
    cuts = {s, e}
    marks: dict[int, list] = {}             # offset -> the (ref, side) marks that sit there
    for off, ref, side in refs:
        if s <= off < e or (off == e == len(unit.text)):
            marks.setdefault(off, []).append((ref, side))
            if s < off < e:
                cuts.add(off)
    for sp in spans:
        if s < sp.s < e:
            cuts.add(sp.s)
        if s < sp.e < e:
            cuts.add(sp.e)
    for r in ranges:
        if s < r.s < e:
            cuts.add(r.s)
        if s < r.e < e:
            cuts.add(r.e)
    for t in unit.tabs:                     # a collapsed space that held a tab is a piece of its own
        if s <= t < e:
            cuts.add(t)
            cuts.add(t + 1)
    pts = sorted(cuts)
    out: list[Piece] = []
    for a, b in zip(pts, pts[1:]):
        for ref, side in marks.pop(a, ()):
            out.append(_mark(unit, ref, a, mode, side, cmp))
        sp = next((x for x in spans if x.s <= a < x.e), None)
        fmt = any(r.s <= a < r.e for r in ranges)
        text = unit.text[a:b]
        tab = unit.tabs.get(a, 0)
        if sp is None:
            out.append(Piece(text, mode, fmt=fmt, cid=cid, tab=tab))
        else:
            out.append(Piece(text, mode, sp.b or sp.style_bold, sp.i, sp.u, sp.f, sp.z, sp.clr, fmt, cid,
                             sp.caps or sp.small_caps, tab, field=sp.field, field_text=sp.field_text))
    for off in sorted(marks):                       # marks at the very end of the text
        for ref, side in marks[off]:
            out.append(_mark(unit, ref, off, mode, side, cmp))
    return out


def _needs_gap(segs, k: int, opts: LayoutOptions) -> bool:
    """A word replaced by another comes as a deletion and an insertion side by side ("notvery").
    When both are drawn and the words around them are spaced, a plain space goes between them,
    as between a renumbered list's old and new number: "~~not~~ very". It is in neither document
    (each keeps its one space), so it is drawn only: unmarked and with no change number."""
    if opts.side != "blackline" or not (opts.show_deletions and opts.show_insertions) or k + 1 >= len(segs):
        return False
    a, b = segs[k], segs[k + 1]
    if {a.m, b.m} != {"del", "ins"} or not a.t or not b.t or a.t[-1].isspace() or b.t[0].isspace():
        return False
    before = segs[k - 1].t if k > 0 and segs[k - 1].m == "eq" else ""
    after = segs[k + 2].t if k + 2 < len(segs) and segs[k + 2].m == "eq" else ""
    return before[-1:].isspace() or after[:1].isspace()


def row_pieces(cmp: Comparison, row: Row, opts: LayoutOptions) -> list[Piece]:
    ou = cmp.a_units[row.oi] if row.oi is not None else None
    ru = cmp.b_units[row.ni] if row.ni is not None else None
    ranges = row.fmt_ranges if (row.fmt_changed and opts.show_formatting) else []
    oa = ob = 0
    out: list[Piece] = []
    segs = row.segments
    for k, seg in enumerate(segs):
        n = len(seg.t)
        if seg.m == "del":
            unit, start, oa, side = ou, oa, oa + n, "a"
            refs = [(o, ref, "a") for o, ref in ou.note_refs]
        elif seg.m == "ins":
            unit, start, ob, side = ru, ob, ob + n, "b"
            refs = [(o, ref, "b") for o, ref in ru.note_refs]
        else:
            unit, start, side = (ou, oa, "a") if opts.side == "original" else (ru, ob, "b")
            refs = [(o, ref, side) for o, ref in unit.note_refs]
            if opts.side == "blackline" and ou is not None and ru is not None:
                # shared text shows the revised marks; a note the revision dropped keeps its
                # (deleted) mark where it stood, at the same offset of the shared text
                refs += [(ob + (o - oa), ref, "a") for o, ref in ou.note_refs
                         if (oa <= o < oa + n or o == oa + n == len(ou.text))
                         and cmp.note_modes.get(("a", ref)) == "del"]
            oa += n
            ob += n
        if seg.m == "del" and not opts.show_deletions:
            continue
        if seg.m == "ins" and not opts.show_insertions:
            continue
        out.extend(_slice(unit, start, start + n, seg.m, ranges if seg.m == "eq" else [], seg.cid, refs, cmp))
        if out and _needs_gap(segs, k, opts):
            p = out[-1]
            out.append(Piece(" ", "eq", p.bold, p.italic, False, p.font, p.size, p.color, caps=p.caps))
    lead_unit = ou if (opts.side == "original" and ou is not None) else (ru or ou)
    if lead_unit is not None and lead_unit.lead_tabs and out:
        out.insert(0, Piece("", out[0].mode, tab=lead_unit.lead_tabs))
    return merge_pieces(out)
