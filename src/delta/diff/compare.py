"""Document comparison: unit stream -> LCS -> pairing -> rows -> passages and summary."""
from __future__ import annotations

from ..model import Document
from .changes import Comparison, Passage, Row, empty_summary, row_passages
from .fmt import fmt_diff
from .inline import safe_inline, whole_segs
from .lcs import lcs_ops
from .text import categorize, content_tokens, norm, sim, sim_upper
from .units import Unit, units

PAIR_THRESHOLD = 0.5

_PLURAL = {"insertion": "insertions", "deletion": "deletions", "numbering": "numbering_changes"}


def compare(a: Document, b: Document, *, ignore_case: bool = False,
            count_numbering: bool = True) -> Comparison:
    ua, ub = units(a), units(b)
    if a.source_kind != b.source_kind:       # a PDF shows how text looks, not the formatting behind it
        for u in ua + ub:
            u.fmt_spans = []
    cmp = compare_units(ua, ub, ignore_case=ignore_case, count_numbering=count_numbering)
    cmp.a_doc, cmp.b_doc = a, b
    from .comments import compare_comments
    cmp.comments = compare_comments(a, b, cmp)
    return cmp


def _stream_keys(us: list[Unit], texts: list[str]) -> list[str]:
    return [t if u.stream == "body" else u.stream + "\x00" + t for u, t in zip(us, texts)]


def _mark_num(row: Row, ou: Unit, ru: Unit) -> None:
    if (ou.marker or "") != (ru.marker or ""):
        row.num_changed = True
        row.old_marker = ou.marker or ""


def _equal_row(ou: Unit, ru: Unit, i: int, j: int) -> Row:
    row = Row("equal", i, j, whole_segs(ru.text, ru.bold_runs, "eq"))
    ranges = fmt_diff(ou.fmt_spans, ru.fmt_spans)
    if ranges:
        row.fmt_changed = True
        row.fmt_ranges = ranges
    _mark_num(row, ou, ru)
    return row


def compare_units(orig: list[Unit], rev: list[Unit], *, ignore_case: bool = False,
                  count_numbering: bool = True) -> Comparison:
    o_t = [norm(u.text, ignore_case) for u in orig]
    r_t = [norm(u.text, ignore_case) for u in rev]
    o_tok = [content_tokens(t) for t in o_t]
    r_tok = [content_tokens(t) for t in r_t]

    # units() always yields body + note paragraphs before any header/footer ones: the two are
    # diffed as separate problems (a_split/b_split are where header/footer starts on each side)
    # so a header/footer paragraph -- unmatched by construction, since it never equals or pairs
    # with a body one -- never disturbs the prefix/suffix trim an LCS run on a big document
    # falls back to (see lcs.DP_CELL_CAP): appending it must never renumber or reorder a single
    # body row.
    a_split = next((i for i, u in enumerate(orig) if u.stream in ("header", "footer")), len(orig))
    b_split = next((i for i, u in enumerate(rev) if u.stream in ("header", "footer")), len(rev))

    rows: list[Row] = []
    for a0, a1, b0, b1 in ((0, a_split, 0, b_split), (a_split, len(orig), b_split, len(rev))):
        _append_rows(orig, rev, o_t, r_t, o_tok, r_tok, a0, a1, b0, b1, rows)

    summary = empty_summary()
    passages: list[Passage] = []
    for k, r in enumerate(rows):
        if r.num_changed:
            summary["numbering"] += 1        # every renumbered row, counted or not: the gate's key
        if r.type == "equal":
            if r.fmt_changed:
                summary["formatting"] += 1
        else:
            summary["punctuation" if r.cat == "punctuation" else "content"] += 1
        ps = row_passages(r, k, len(passages) + 1, count_numbering)
        for p in ps:
            summary[_PLURAL[p.category]] += 1
        passages += ps
    summary["total"] = len(passages)
    modes, first = _note_index(rows, orig, rev)
    return Comparison(rows, summary, orig, rev, ignore_case, count_numbering, passages,
                      note_modes=modes, note_rows=first)


def _append_rows(orig: list[Unit], rev: list[Unit], o_t: list[str], r_t: list[str], o_tok, r_tok,
                 a0: int, a1: int, b0: int, b1: int, rows: list[Row]) -> None:
    """The rows of orig[a0:a1] vs rev[b0:b1], appended to `rows` with indices into the full
    (unsliced) orig/rev -- one independent LCS problem, so nothing outside the range can affect
    it, or be affected by it."""
    segs: list[tuple[str, list[tuple[int, int]]]] = []
    # a note paragraph never equals or pairs with a body paragraph: the LCS keys carry the stream
    o_keys = _stream_keys(orig[a0:a1], o_t[a0:a1])
    r_keys = _stream_keys(rev[b0:b1], r_t[b0:b1])
    for tag, i, j in lcs_ops(o_keys, r_keys):
        i, j = i + a0, j + b0
        if segs and segs[-1][0] == tag:
            segs[-1][1].append((i, j))
        else:
            segs.append((tag, [(i, j)]))

    s = 0
    while s < len(segs):
        tag, items = segs[s]
        if tag == "equal":
            for i, j in items:
                rows.append(_equal_row(orig[i], rev[j], i, j))
            s += 1
            continue
        dels: list[int] = []
        ins: list[int] = []
        if tag == "delete":
            dels = [i for i, _ in items]
            if s + 1 < len(segs) and segs[s + 1][0] == "insert":
                ins = [j for _, j in segs[s + 1][1]]
                s += 1
        else:
            ins = [j for _, j in items]
            if s + 1 < len(segs) and segs[s + 1][0] == "delete":
                dels = [i for i, _ in segs[s + 1][1]]
                s += 1
        # both sets mirror the reference engine; kept identical on purpose
        used: set[int] = set()
        paired: list[tuple[int, int]] = []
        for di in dels:
            best, bs = -1, 0.0
            for ji in ins:
                if ji in used or rev[ji].stream != orig[di].stream:
                    continue
                if sim_upper(o_tok[di], r_tok[ji]) <= max(bs, PAIR_THRESHOLD - 1e-9):
                    continue
                v = sim(o_t[di], r_t[ji])
                if v > bs:
                    bs, best = v, ji
            if best >= 0 and bs >= PAIR_THRESHOLD:
                used.add(best)
                paired.append((di, best))
            else:
                paired.append((di, -1))
        done: set[int] = set()
        for di, ji in paired:
            if ji >= 0:
                ou, ru = orig[di], rev[ji]
                row = Row("changed", di, ji, safe_inline(ou.text, ru.text, ou.bold_runs, ru.bold_runs),
                          cat=categorize(o_t[di], r_t[ji]))
                _mark_num(row, ou, ru)
                rows.append(row)
                done.add(ji)
            else:
                ou = orig[di]
                rows.append(Row("deleted", di, None, whole_segs(ou.text, ou.bold_runs, "del")))
        for ji in ins:
            if ji not in done:
                ru = rev[ji]
                rows.append(Row("inserted", None, ji, whole_segs(ru.text, ru.bold_runs, "ins")))
        s += 1


def _note_index(rows: list[Row], orig: list[Unit], rev: list[Unit]) -> tuple[dict, dict]:
    """Per (side, NoteRef): the mode of the note's reference mark and the index of the note's
    first row, both from the row of the note's first paragraph."""
    modes: dict = {}
    first: dict = {}
    for k, r in enumerate(rows):
        if r.ni is not None:
            u = rev[r.ni]
            if u.note is not None and (r.ni == 0 or rev[r.ni - 1].note != u.note):
                modes[("b", u.note)] = "ins" if r.type == "inserted" else "eq"
                first[("b", u.note)] = k
        if r.oi is not None:
            u = orig[r.oi]
            if u.note is not None and (r.oi == 0 or orig[r.oi - 1].note != u.note):
                modes[("a", u.note)] = "del" if r.type == "deleted" else "eq"
                first[("a", u.note)] = k
    return modes, first
