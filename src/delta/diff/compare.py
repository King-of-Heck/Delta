"""Document comparison: unit stream -> LCS -> pairing -> rows -> passages and summary."""
from __future__ import annotations

from ..model import Document
from .changes import Comparison, Passage, Row, empty_summary, row_passages
from .fmt import fmt_diff
from .inline import INLINE_TOKEN_CAP, PM, Seg, inline_segs, safe_inline, whole_segs
from .lcs import lcs_ops
from .text import categorize, content_tokens, norm, sim, sim_upper, tokenize, words_only
from .units import Unit, units

PAIR_THRESHOLD = 0.5

_PLURAL = {"insertion": "insertions", "deletion": "deletions", "numbering": "numbering_changes"}


def compare(a: Document, b: Document, *, ignore_case: bool = False,
            count_numbering: bool = True) -> Comparison:
    cmp = compare_units(units(a), units(b), ignore_case=ignore_case, count_numbering=count_numbering,
                        # a PDF shows how text looks, not the formatting behind it
                        ignore_format=a.source_kind != b.source_kind)
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
        nums = ou.para.num, ru.para.num
        row.num_typed = all(n is not None and n.num_id == 0 for n in nums)


def _equal_row(ou: Unit, ru: Unit, i: int, j: int, ignore_format: bool) -> Row:
    row = Row("equal", i, j, whole_segs(ru.text, ru.bold_runs, "eq"))
    ranges = None if ignore_format else fmt_diff(ou.fmt_spans, ru.fmt_spans)
    if ranges:
        row.fmt_changed = True
        row.fmt_ranges = ranges
    _mark_num(row, ou, ru)
    return row


def compare_units(orig: list[Unit], rev: list[Unit], *, ignore_case: bool = False,
                  count_numbering: bool = True, ignore_format: bool = False) -> Comparison:
    o_t =[norm(u.text, ignore_case) for u in orig]
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
        _append_rows(orig, rev, o_t, r_t, o_tok, r_tok, a0, a1, b0, b1, rows, ignore_format, ignore_case)

    summary = empty_summary()
    passages: list[Passage] = []
    carry: dict | None = None
    for k, r in enumerate(rows):
        # a split's next piece continues the passages its last piece left open at the cut
        if not (r.join == "split" and not r.head):
            carry = None
        if r.join == "split" and carry is None:
            carry = {}
        if r.num_changed:
            summary["numbering"] += 1        # every renumbered row, counted or not: the gate's key
        if r.join:
            if r.head:
                summary["splits" if r.join == "split" else "merges"] += 1
        elif r.type == "equal":
            if r.fmt_changed:
                summary["formatting"] += 1
        else:
            summary["punctuation" if r.cat == "punctuation" else "content"] += 1
        ps = row_passages(r, k, len(passages) + 1, count_numbering, carry)
        for p in ps:
            summary[_PLURAL[p.category]] += 1
        passages += ps
    summary["total"] = len(passages)
    modes, first = _note_index(rows, orig, rev)
    return Comparison(rows, summary, orig, rev, ignore_case, count_numbering, passages,
                      note_modes=modes, note_rows=first)


def _append_rows(orig: list[Unit], rev: list[Unit], o_t: list[str], r_t: list[str], o_tok, r_tok,
                 a0: int, a1: int, b0: int, b1: int, rows: list[Row], ignore_format: bool = False,
                 ignore_case: bool = False) -> None:
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
                rows.append(_equal_row(orig[i], rev[j], i, j, ignore_format))
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
        # a paragraph split (or joined) with its words unchanged: one row per piece, set aside
        # before the pairing below; each group's rows go where its first original paragraph stood
        groups = _joins(orig, rev, dels, ins, ignore_case)
        # a group's indices run from its first piece to its last (a stepped-over page number too)
        grouped_a = {i for g in groups for i in range(g[1][0], g[1][-1] + 1)}
        grouped_b = {j for g in groups for j in range(g[2][0], g[2][-1] + 1)}
        first_of = {g[1][0]: g for g in groups}
        ins =[ji for ji in ins if ji not in grouped_b]
        # both sets mirror the reference engine; kept identical on purpose
        used: set[int] = set()
        paired: list[tuple[int, int]] = []
        for di in dels:
            if di in grouped_a:
                if di in first_of:
                    paired.append((di, -2))
                continue
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
            if ji == -2:
                rows.extend(_join_rows(orig, rev, first_of[di]))
            elif ji >= 0:
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


JOIN_CAP = 250_000          # dels x ins of one changed block past which splits are not looked for
EDITED_SHARE = 0.5          # an edited split: this share of each piece's letters unchanged, and of the whole's
EDITED_WORDS = 0.6          # a candidate piece: this share of its distinct words found in the whole paragraph
FILLER_WORDS = 3            # a paragraph this short between two pieces (a page number) is left out of the group


def _fits(target: str, pieces: list[str]) -> list[str] | None:
    """The gaps ("" or " ") between `pieces` when they spell `target` exactly, one after another
    with at most one space between two of them; else None."""
    pos, gaps = 0, []
    for k, piece in enumerate(pieces):
        if k:
            gap = " " if target.startswith(" ", pos) and not piece.startswith(" ") else ""
            gaps.append(gap)
            pos += len(gap)
        if not piece or not target.startswith(piece, pos):
            return None
        pos += len(piece)
    return gaps if pos == len(target) else None


def _same_place(us: list[Unit]) -> bool:
    """The pieces of one paragraph: one stream, one note, one header/footer part, one table cell."""
    def key(u: Unit):
        return (u.stream, u.note, u.part, None if u.loc is None else (u.loc.ti, u.loc.ri, u.loc.ci))
    return all(key(u) == key(us[0]) for u in us[1:])


def _joined(pieces: list[Unit], seps: list[str]) -> tuple[str, list, list[tuple[int, int]]]:
    """The pieces' texts one after another with `seps` between: the text, its bold runs, and
    where each piece stands in it."""
    text, runs, spans = "", [], []
    for k, u in enumerate(pieces):
        if k:
            text += seps[k - 1]
        at = len(text)
        spans.append((at, at + len(u.text)))
        runs += [(s + at, e + at) for s, e in (u.bold_runs or [])]
        text += u.text
    return text, runs, spans


def _letters(t: str) -> int:
    return sum(1 for ch in t if not ch.isspace())


def _joins(orig: list[Unit], rev: list[Unit], dels: list[int], ins: list[int], ignore_case: bool) -> list[tuple]:
    """The splits and merges of one changed block: (kind, original indices, revised indices,
    separators, segments). A split is one deleted paragraph spelled by two or more consecutive
    inserted ones; a merge is the mirror. Exact first: a space between two pieces at most, or
    nothing (segments None). Then edited: the pieces joined by a space and word-diffed against the
    whole, each piece and the whole at least half unchanged (EDITED_SHARE); the diff's segments
    are kept. A short paragraph between two edited pieces (a page number a conversion left
    behind) may be stepped over; it stays a row of its own."""
    if not dels or not ins or len(dels) * len(ins) > JOIN_CAP:
        return []

    def text(u: Unit) -> str:
        return u.text.lower() if ignore_case else u.text

    out: list[tuple] = []
    taken_a: set[int] = set()
    taken_b: set[int] = set()
    sides = (("split", dels, ins, orig, rev), ("merge", ins, dels, rev, orig))

    def taken(kind: str) -> tuple[set, set]:
        return (taken_a, taken_b) if kind == "split" else (taken_b, taken_a)

    def record(kind: str, x: int, run: list[int], seps: list[str], segs) -> None:
        taken_one, taken_many = taken(kind)
        taken_one.add(x)
        taken_many.update(run)
        out.append((kind, [x], run, seps, segs) if kind == "split" else (kind, run, [x], seps, segs))

    for kind, ones, many, us_one, us_many in sides:
        taken_one, taken_many = taken(kind)
        for x in ones:
            if x in taken_one:
                continue
            target = text(us_one[x])
            for p, y in enumerate(many):
                if y in taken_many or not target.startswith(text(us_many[y])):
                    continue
                run = [y]
                for z in many[p + 1:]:
                    if z != run[-1] + 1 or z in taken_many:
                        break
                    run.append(z)
                    if sum(len(text(us_many[i])) for i in run) > len(target):
                        break
                found = None
                for n in range(2, len(run) + 1):
                    pieces = [us_many[i] for i in run[:n]]
                    gaps = _fits(target, [text(u) for u in pieces])
                    if gaps is not None and _same_place(pieces + [us_one[x]]):
                        found = (run[:n], gaps)
                        break
                if found:
                    record(kind, x, found[0], found[1], None)
                    break

    # Edited: every candidate group first, each scored by the words it shares with the whole
    # (LCS of content tokens); a group is a candidate only when it shares more than the whole
    # paragraph's best single counterpart does -- so of two near-identical clauses (a release by
    # each party) the pieces go to the one they were cut from, not to whichever comes first.
    def toks(u: Unit) -> list[str]:
        return content_tokens(norm(u.text, ignore_case))

    tok_a = {i: toks(orig[i]) for i in dels}
    tok_b = {j: toks(rev[j]) for j in ins}
    cands: list[tuple] = []
    for kind, ones, many, us_one, us_many in sides:
        taken_one, taken_many = taken(kind)
        t_one, t_many = (tok_a, tok_b) if kind == "split" else (tok_b, tok_a)
        in_block = set(many)
        words = {y: set(words_only(us_many[y].text)) for y in many}
        for x in ones:
            if x in taken_one:
                continue
            whole = us_one[x]
            tw = set(words_only(whole.text))
            if not tw:
                continue

            def piece(y: int) -> bool:
                w = words.get(y)
                return (y in in_block and y not in taken_many and bool(w)
                        and len(w & tw) >= EDITED_WORDS * len(w) and _same_place([us_many[y], whole]))

            def filler(y: int) -> bool:
                return (y in in_block and y not in taken_many and len(us_many[y].text.strip()) <= 20
                        and len(words[y]) <= FILLER_WORDS and _same_place([us_many[y], whole]))

            single = None
            covered: set[int] = set()
            for y in many:
                if y in covered or not piece(y):
                    continue
                run, size = [y], len(us_many[y].text)
                while True:
                    z = run[-1] + 1
                    if not piece(z) and filler(z):
                        z += 1
                    if not piece(z):
                        break
                    size += len(us_many[z].text) + 1
                    if size > 2 * len(whole.text) + 40:
                        break
                    run.append(z)
                while len(run) >= 2:        # the longest run from y that fits
                    segs = _edited_fit(whole, [us_many[i] for i in run], kind)
                    if segs is not None:
                        break
                    run.pop()
                if len(run) < 2:
                    continue
                covered.update(run)
                if single is None:
                    single = _best_single(t_one[x], [t_many[z] for z in many if us_many[z].stream == whole.stream])
                score = _shared(t_one[x], [w for i in run for w in t_many[i]])
                if score > single:
                    cands.append((score, kind, x, run, segs))
    for score, kind, x, run, segs in sorted(cands, key=lambda c: -c[0]):
        taken_one, taken_many = taken(kind)
        if x in taken_one or any(y in taken_many for y in run):
            continue
        record(kind, x, run, [" "] * (len(run) - 1), segs)
    return out


def _shared(a: list[str], b: list[str]) -> int:
    return sum(1 for tag, _, _ in lcs_ops(a, b) if tag == "equal")


def _best_single(a: list[str], others: list[list[str]]) -> int:
    """The most content tokens `a` shares (in order) with any one of `others`."""
    from collections import Counter
    ca = Counter(a)
    best = 0
    bounds = sorted(((sum((ca & Counter(o)).values()), k) for k, o in enumerate(others)), reverse=True)
    for bound, k in bounds:
        if bound <= best:
            break
        best = max(best, _shared(a, others[k]))
    return best


def _edited_fit(whole: Unit, pieces: list[Unit], kind: str) -> list[Seg] | None:
    """The word diff of `whole` against `pieces` joined by spaces (the original first), when each
    piece and the whole keep at least EDITED_SHARE of their letters unchanged; else None."""
    jt, jb, spans = _joined(pieces, [" "] * (len(pieces) - 1))
    a, b, ab, bb = (whole.text, jt, whole.bold_runs, jb) if kind == "split" else (jt, whole.text, jb, whole.bold_runs)
    if len(tokenize(a)) > INLINE_TOKEN_CAP or len(tokenize(b)) > INLINE_TOKEN_CAP:
        return None
    segs = inline_segs(a, b, ab, bb)
    jm = "ins" if kind == "split" else "del"
    kept = [0] * len(pieces)
    total = pos = k = 0
    for s in segs:
        if s.m not in ("eq", jm):
            continue
        if s.m == "eq":
            for c, ch in enumerate(s.t):
                if ch.isspace():
                    continue
                total += 1
                at = pos + c
                while k < len(spans) - 1 and at >= spans[k][1]:
                    k += 1
                if spans[k][0] <= at < spans[k][1]:
                    kept[k] += 1
        pos += len(s.t)
    if total < EDITED_SHARE * _letters(whole.text):
        return None
    if any(kept[k] < EDITED_SHARE * _letters(u.text) for k, u in enumerate(pieces)):
        return None
    return segs


def _tidy(segs: list[Seg]) -> list[Seg]:
    """Neighbouring segments of one kind joined again after the cuts at the pieces' edges."""
    out: list[Seg] = []
    for s in segs:
        if not s.t:
            continue
        p = out[-1] if out else None
        if (p is not None and not p.pm and not s.pm and p.m == s.m and p.b == s.b
                and (p.at is None) == (s.at is None)
                and (p.at is None or (p.at[0] == s.at[0] and p.at[1] + len(p.t) == s.at[1]))):
            p.t += s.t
        else:
            out.append(Seg(s.m, s.t, s.b, pm=s.pm, at=s.at))
    return out


def _join_rows(orig: list[Unit], rev: list[Unit], group: tuple) -> list[Row]:
    """The rows of a split (one per revised piece) or a merge (the head holding the whole revised
    paragraph, then one row per further original piece), cut from the diff's segments where each
    piece ends. The separator between two pieces of the joined text is in neither piece: shared,
    it is the whole paragraph's own text there (a split's `gap`; a merge's shared space). A
    deleted segment says where its text stands in the original (`Seg.at`)."""
    kind, ois, nis, seps, segs = group
    pieces = [rev[j] for j in nis] if kind == "split" else [orig[i] for i in ois]
    jt, jb, spans = _joined(pieces, seps)
    if segs is None:                    # exact: every word shared
        ru = rev[nis[0]]
        segs = whole_segs(jt, jb, "eq") if kind == "split" else whole_segs(ru.text, ru.bold_runs, "eq")
    last = len(pieces) - 1
    jm = "ins" if kind == "split" else "del"
    parts: list[list[Seg]] = [[] for _ in pieces]
    gaps = [""] * last
    head: list[Seg] = []
    marked: set[int] = set()
    k = jpos = opos = 0
    for s in segs:
        if s.m not in ("eq", jm):       # the whole paragraph's own text, in no piece
            if kind == "split":
                parts[k].append(Seg("del", s.t, s.b, at=(ois[0], opos)))
                opos += len(s.t)
            else:
                head.append(Seg("ins", s.t, s.b))
            continue
        t = s.t
        while t:
            if k < last and jpos >= spans[k][1]:
                if kind == "merge" and k not in marked:
                    marked.add(k)
                    head.append(Seg("del", PM, pm=True))
                take = min(len(t), spans[k + 1][0] - jpos)
                if take and s.m == "eq":
                    if kind == "split":
                        gaps[k] += t[:take]
                        opos += take
                    else:
                        head.append(Seg("eq", t[:take], s.b))
                jpos += take
                t = t[take:]
                if jpos >= spans[k + 1][0]:
                    k += 1
                continue
            take = min(len(t), spans[k][1] - jpos) if k < last else len(t)
            chunk, t = t[:take], t[take:]
            if kind == "split":
                parts[k].append(Seg(s.m, chunk, s.b))
                if s.m == "eq":
                    opos += take
            elif s.m == "del":
                head.append(Seg("del", chunk, s.b, at=(ois[k], jpos - spans[k][0])))
            else:
                head.append(Seg("eq", chunk, s.b))
            jpos += take
    rows: list[Row] = []

    def between(us: list[Unit], ix: list[int], k: int, mode: str, side: str) -> list[Row]:
        # a short paragraph stepped over between two pieces: a row of its own, where it stands
        if k >= last:
            return []
        out = []
        for f in range(ix[k] + 1, ix[k + 1]):
            u = us[f]
            out.append(Row("inserted", None, f, whole_segs(u.text, u.bold_runs, mode)) if side == "b"
                       else Row("deleted", f, None, whole_segs(u.text, u.bold_runs, mode)))
        return out

    if kind == "split":
        for k, j in enumerate(nis):
            segs_k = _tidy(parts[k])
            edited = any(s.m != "eq" for s in segs_k)
            if k < last:
                segs_k.append(Seg("ins", PM, pm=True))
            changed = edited or k < last
            row = Row("changed" if changed else "equal", ois[0], j, segs_k, cat="content" if changed else None,
                      join="split", head=k == 0, gap=gaps[k] if k < last else "")
            if k == 0:
                _mark_num(row, orig[ois[0]], rev[j])
            rows.append(row)
            rows += between(rev, nis, k, "ins", "b")
        return rows
    ru = rev[nis[0]]
    row = Row("changed", ois[0], nis[0], _tidy(head), cat="content", join="merge", head=True)
    _mark_num(row, orig[ois[0]], ru)
    rows.append(row)
    rows += between(orig, ois, 0, "del", "a")
    for k, i in enumerate(ois[1:], 1):
        ou = orig[i]
        rows.append(Row("equal", i, nis[0], whole_segs(ou.text, ou.bold_runs, "eq"), join="merge"))
        rows += between(orig, ois, k, "del", "a")
    return rows


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
