"""Inline word diff of a paired paragraph: (mode, text, bold) segments.

Tokens align by lcs.word_ops (weighted by _weight, the same result whichever side is the original); a token is bold when a majority of its non-space characters are bold. Dense
alternations (>= 2 struck runs AND >= 2 inserted runs separated only by whitespace or a lone
connective word) are rebuilt as one struck phrase followed by one inserted phrase, the shared
whitespace/connective duplicated into both -- the presentation readers of legal redlines expect.
A multi-word shared run is never folded: preserved clauses stay anchors.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..model import WS_CHARS
from .lcs import word_ops
from .text import tokenize

INLINE_TOKEN_CAP = 4000
CONN = frozenset("a an the and or nor of to in on at by for as if is".split())
_LONE_WORD = re.compile(f"^[{WS_CHARS}]*[A-Za-z0-9_]+[{WS_CHARS}]*$")
_WS_ONLY = re.compile(f"^[{WS_CHARS}]+$")
_WS_CHAR = re.compile(f"[{WS_CHARS}]")


@dataclass
class Seg:
    m: str
    t: str
    b: bool = False
    cid: int | None = None      # the passage number of a marked segment (spec 14.2); None on equal text
    pm: bool = False            # a paragraph mark inserted / removed (t is the drawn PM): in neither text


PM = "¶"                   # how an inserted or removed paragraph mark is drawn


def _in_runs(runs, i: int) -> bool:
    # runs must be sorted and non-overlapping (as bold_runs produces) for the early break below.
    for s, e in runs or []:
        if s <= i < e:
            return True
        if s > i:
            break
    return False


def tok_bold(tokens: list[str], runs) -> list[bool]:
    flags = [False] * len(tokens)
    if not runs:
        return flags
    off = 0
    for k, tk in enumerate(tokens):
        cnt = bc = 0
        for c, ch in enumerate(tk):
            if _WS_CHAR.match(ch):
                continue
            cnt += 1
            if _in_runs(runs, off + c):
                bc += 1
        flags[k] = cnt > 0 and (bc / cnt) >= 0.5
        off += len(tk)
    return flags


def whole_segs(text: str, runs, mode: str) -> list[Seg]:
    if not text:
        return []
    if not runs:
        return [Seg(mode, text)]
    out: list[Seg] = []
    pos = 0
    for s, e in runs:
        if s > pos:
            out.append(Seg(mode, text[pos:s]))
        out.append(Seg(mode, text[s:e], True))
        pos = e
    if pos < len(text):
        out.append(Seg(mode, text[pos:]))
    return out


def _is_ws_eq(s: Seg) -> bool:
    return s.m == "eq" and bool(_WS_ONLY.match(s.t))


def _is_conn_eq(s: Seg) -> bool:
    return s.m == "eq" and bool(_LONE_WORD.match(s.t)) and s.t.strip().lower() in CONN


def _is_transparent(s: Seg) -> bool:
    return _is_ws_eq(s) or _is_conn_eq(s)


def coalesce(segs: list[Seg]) -> list[Seg]:
    out: list[Seg] = []
    i = 0
    while i < len(segs):
        if segs[i].m == "eq" and not _is_ws_eq(segs[i]):
            out.append(segs[i])
            i += 1
            continue
        j = i
        while j < len(segs) and (segs[j].m != "eq" or _is_transparent(segs[j])):
            j += 1
        span = segs[i:j]
        while span and _is_transparent(span[0]):
            out.append(span.pop(0))
        tail: list[Seg] = []
        while span and _is_transparent(span[-1]):
            tail.insert(0, span.pop())
        # Count MODE RUNS (maximal same-mode groups), not segments: a bold flip splits a phrase
        # into several segments of one mode, and that must not trip the collapse.
        del_runs = ins_runs = 0
        prev = None
        for x in span:
            if _is_transparent(x):
                prev = None
                continue
            if x.m != prev:
                if x.m == "del":
                    del_runs += 1
                elif x.m == "ins":
                    ins_runs += 1
            prev = x.m
        if del_runs >= 2 and ins_runs >= 2:
            olds: list[Seg] = []
            news: list[Seg] = []
            for x in span:
                if x.m == "del":
                    olds.append(Seg("del", x.t, x.b))
                elif x.m == "ins":
                    news.append(Seg("ins", x.t, x.b))
                else:
                    olds.append(Seg("del", x.t, x.b))
                    news.append(Seg("ins", x.t, x.b))
            out.extend(olds)
            out.extend(news)
        else:
            out.extend(span)
        out.extend(tail)
        i = j
    return out


def _weight(tok: str) -> int:
    """A matched word counts by its length, punctuation as one letter, whitespace least of all --
    but still something, so a shared space is kept rather than struck to save a change run."""
    if _WS_ONLY.match(tok):
        return 1
    return 3 * len(tok) if tok[0].isalnum() or tok[0] == "_" else 3


def _end_on_sentences(ops, a, b):
    """Slide a purely inserted (or purely deleted) stretch that could sit a few tokens either way
    so it ends on a sentence's full stop: "end.[+ New sentence.+]", not "end[+. New sentence+].\""""
    out = list(ops)
    p = 0
    while p < len(out):
        if out[p][0] == "equal":
            p += 1
            continue
        q = p
        while q < len(out) and out[q][0] == out[p][0]:
            q += 1
        if q < len(out) and out[q][0] != "equal":       # deletes then inserts: a replacement
            while q < len(out) and out[q][0] != "equal":
                q += 1
            p = q
            continue
        ins = out[p][0] == "insert"
        seq, k_of = (b, 2) if ins else (a, 1)
        s, e = out[p][k_of], out[q - 1][k_of] + 1
        left = 0
        while p - left > 0 and out[p - left - 1][0] == "equal" and seq[s - left - 1] == seq[e - left - 1]:
            left += 1
        right = 0
        while q + right < len(out) and out[q + right][0] == "equal" and seq[s + right] == seq[e + right]:
            right += 1
        ends = [k for k in range(-left, right + 1) if seq[e + k - 1] in (".", "!", "?")]
        k = min(ends, key=lambda k: (abs(k), k)) if ends else 0
        if k:
            g = e - s
            o0 = out[p - left][3 - k_of]                # the other side's index of the first equal
            region: list = []
            for t in range(left + g + right):
                if left + k <= t < left + k + g:
                    pos = s - left + t
                    region.append(("insert", o0 + left + k, pos) if ins else ("delete", pos, o0 + left + k))
                else:
                    here = s - left + t
                    other = o0 + (t if t < left + k else t - g)
                    region.append(("equal", other, here) if ins else ("equal", here, other))
            out[p - left:q + right] = region
        p = q + right
    return out


def inline_segs(o: str, n: str, ob, nb) -> list[Seg]:
    a, b = tokenize(o), tokenize(n)
    ops = _end_on_sentences(word_ops(a, b, _weight), a, b)
    af, bf = tok_bold(a, ob), tok_bold(b, nb)
    segs: list[Seg] = []
    for tag, i, j in ops:
        is_ins = tag == "insert"
        piece = b[j] if (is_ins or tag == "equal") else a[i]
        m = "eq" if tag == "equal" else ("del" if tag == "delete" else "ins")
        bb = bf[j] if is_ins else af[i]
        if segs and segs[-1].m == m and segs[-1].b == bb:
            segs[-1].t += piece
        else:
            segs.append(Seg(m, piece, bb))
    return coalesce(segs)


def safe_inline(o: str, n: str, ob, nb) -> list[Seg]:
    if len(tokenize(o)) > INLINE_TOKEN_CAP or len(tokenize(n)) > INLINE_TOKEN_CAP:
        return whole_segs(o, ob, "del") + whole_segs(n, nb, "ins")
    return inline_segs(o, n, ob, nb)
