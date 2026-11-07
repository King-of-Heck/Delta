"""A paragraph's runs, tied character by character to the text the comparison saw.

The parser reads a paragraph as the text of its runs collapsed (`model.collapse_ws`). ParaMap walks
the same runs the same way, keeping for every raw character the w:r it lives in, so an offset in the
compared text can be turned back into a place in the XML: runs can be split there, and a stretch of
runs copied. A paragraph whose text this walk cannot rebuild exactly (a live PAGE / NUMPAGES field, a
LISTNUM number) has no map: `ParaMap.of` returns None and the caller marks it whole.
"""
from __future__ import annotations

import copy
import re

from ..model import WS_CHARS
from .ns import wq

_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_R, _T, _RPR = wq("r"), wq("t"), wq("rPr")
_ONE = {wq("tab"): "\t", wq("cr"): "\n", wq("noBreakHyphen"): "-", wq("sym"): "�"}
_BR = wq("br")
_TRANSPARENT = {wq(t) for t in ("hyperlink", "smartTag", "sdt", "sdtContent", "fldSimple", "ins",
                                "customXml", "dir", "bdo")}
_LIVE = ("PAGE", "NUMPAGES", "LISTNUM")
_WS = re.compile(f"[{WS_CHARS}]")


def _piece(x) -> str:
    """The raw text one child of a w:r contributes (as the parser reads it)."""
    if x.tag == _T:
        return x.text or ""
    if x.tag == _BR:
        return "" if x.get(wq("type")) == "page" else "\n"
    return _ONE.get(x.tag, "")


def run_text(r) -> str:
    return "".join(_piece(x) for x in r)


def _live(instr: str | None) -> bool:
    words = (instr or "").split()
    return bool(words) and words[0].upper() in _LIVE


def runs_of(p) -> list | None:
    """The w:r elements the parser reads in p, in order; None when p holds a live field."""
    out: list = []

    def walk(el) -> bool:
        for child in el:
            if child.tag == _R:
                for x in child:
                    if x.tag == wq("instrText") and _live(x.text):
                        return False
                out.append(child)
            elif child.tag in _TRANSPARENT:
                if child.tag == wq("fldSimple") and _live(child.get(wq("instr"))):
                    return False
                if not walk(child):
                    return False
        return True

    if not walk(p):
        return None
    # a complex field's instruction may be split over runs: join them before deciding
    instr = "".join(x.text or "" for r in out for x in r if x.tag == wq("instrText"))
    for part in re.split(r"\s{2,}|\\", instr):
        if _live(part):
            return None
    return out


def slice_run(r, s: int, e: int):
    """A copy of w:r keeping only the content at raw offsets [s, e). A zero-width child (a field
    character, a note mark) at offset k goes with the slice where s <= k < e, or the last slice
    when it sits at the run's end."""
    total = len(run_text(r))
    out = r.makeelement(r.tag, dict(r.attrib))
    pos = 0
    for x in r:
        if x.tag == _RPR:
            out.append(copy.deepcopy(x))
            continue
        text = _piece(x)
        n = len(text)
        if x.tag == _T:
            a, b = max(s, pos), min(e, pos + n)
            if a < b:
                t = copy.deepcopy(x)
                t.text = text[a - pos:b - pos]
                t.set(_XML_SPACE, "preserve")
                out.append(t)
        elif s <= pos < e or (n == 0 and pos == total == e):
            out.append(copy.deepcopy(x))
        pos += n
    return out


_FLDCHAR, _INSTR = wq("fldChar"), wq("instrText")


def _field_bits(r) -> bool:
    return any(x.tag in (_FLDCHAR, _INSTR) for x in r)


def balance_fields(runs) -> None:
    """Make the field characters in a list of runs whole: an end or separate with no begin, and a
    begin with no end (with its instruction), are removed, so what is left of such a field is its
    result as plain text. A cut field would otherwise show its instruction as text."""
    seq = [(r, x) for r in runs for x in r if x.tag in (_FLDCHAR, _INSTR)]
    stack: list[list] = []          # per open field: the (run, child) pieces belonging to its code
    drop: list = []
    for r, x in seq:
        kind = x.get(wq("fldCharType")) if x.tag == _FLDCHAR else "instr"
        if kind == "begin":
            stack.append([(r, x)])
        elif not stack:
            drop.append((r, x))
        elif kind == "end":
            stack.pop()
        else:
            stack[-1].append((r, x))
    for pieces in stack:
        drop.extend(pieces)
    for r, x in drop:
        r.remove(x)


def field_spans(runs) -> dict:
    """run -> (begin run, end run) of the complex field each field-character run belongs to."""
    out: dict = {}
    stack: list[list] = []
    for r in runs:
        for x in r:
            if x.tag not in (_FLDCHAR, _INSTR):
                continue
            kind = x.get(wq("fldCharType")) if x.tag == _FLDCHAR else "instr"
            if kind == "begin":
                stack.append([r])
            elif stack:
                stack[-1].append(r)
                if kind == "end":
                    members = stack.pop()
                    for m in members:
                        out[m] = (members[0], r)
    return out


class ParaMap:
    """runs: [w:r, raw start, raw length] in order; raw: the raw text; cmap[c]: the raw offset of
    compared character c."""

    def __init__(self, runs: list, text: str):
        self.runs = []
        pos = 0
        for r in runs:
            n = len(run_text(r))
            self.runs.append([r, pos, n])
            pos += n
        self.raw = "".join(run_text(r) for r, _, _ in self.runs)
        self.cmap: list[int] = []
        out: list[str] = []
        i, n = 0, len(self.raw)
        while i < n:
            if _WS.match(self.raw[i]):
                j = i
                while j < n and _WS.match(self.raw[j]):
                    j += 1
                if out and j < n:              # inner whitespace becomes one space; ends are trimmed
                    out.append(" ")
                    self.cmap.append(i)
                i = j
                continue
            out.append(self.raw[i])
            self.cmap.append(i)
            i += 1
        self.text = "".join(out)

    @classmethod
    def of(cls, p, text: str) -> "ParaMap | None":
        runs = runs_of(p)
        if runs is None:
            return None
        m = cls(runs, text)
        return m if m.text == text else None

    def raw_pos(self, c: int) -> int:
        """The raw offset where compared offset c starts; for c == len(text), just after the last
        compared character (trailing spaces the comparison trimmed stay after anything put there)."""
        if c <= 0:
            return 0
        return self.cmap[c] if c < len(self.cmap) else self.cmap[-1] + 1

    def split(self, cuts) -> None:
        """Split runs so every raw offset in cuts falls between two runs (the tree is edited)."""
        for k in sorted(set(cuts), reverse=True):
            for i, (r, s, n) in enumerate(self.runs):
                if s < k < s + n:
                    left, right = slice_run(r, 0, k - s), slice_run(r, k - s, n)
                    parent = r.getparent()
                    at = parent.index(r)
                    parent.remove(r)
                    parent.insert(at, right)
                    parent.insert(at, left)
                    self.runs[i:i + 1] = [[left, s, k - s], [right, k, s + n - k]]
                    break

    def runs_in(self, s: int, e: int, last: bool = False) -> list:
        """The (already split) runs inside raw [s, e); a zero-width run at e too when `last`."""
        return [r for r, rs, n in self.runs
                if (n > 0 and s <= rs and rs + n <= e) or (n == 0 and (s <= rs < e or (last and rs == e)))]

    def marked_runs(self, s: int, e: int) -> list:
        """The runs a revision over raw [s, e) covers: the runs with text inside it; a zero-width
        run strictly inside it (not a note mark at its edge); a field character only when its
        whole field (begin to end) lies within [s, e], so a revision never cuts a field."""
        spans = field_spans([r for r, _, _ in self.runs])
        start = {id(r): rs for r, rs, _ in self.runs}
        out = []
        for r, rs, n in self.runs:
            if n > 0:
                if s <= rs and rs + n <= e:
                    out.append(r)
                continue
            if r in spans:
                b, f = spans[r]
                if s <= start[id(b)] and start[id(f)] <= e:
                    out.append(r)
            elif s < rs < e:
                out.append(r)
        return out

    def copy_range(self, s: int, e: int) -> list:
        """Copies of the runs' content in raw [s, e), one per run touched, in order; a zero-width
        run (a note mark, a field character) strictly inside the range too."""
        out = []
        for r, rs, n in self.runs:
            a, b = max(s, rs), min(e, rs + n)
            if a < b:
                out.append(slice_run(r, a - rs, b - rs))
            elif n == 0 and (s < rs < e or (s <= rs <= e and _field_bits(r))):
                out.append(copy.deepcopy(r))
        balance_fields(out)
        return [r for r in out if any(x.tag != _RPR for x in r)]

    def marks_at(self, k: int) -> int:
        """How many zero-width runs (note marks, field characters) sit at raw offset k."""
        return sum(1 for _, s, n in self.runs if n == 0 and s == k)

    def point(self, k: int, skip: int = 0):
        """(parent, index) where content placed at raw offset k goes: before the first run that
        starts at or after k (passing over `skip` zero-width runs at k), else after the last run,
        else at the paragraph's end."""
        for r, s, n in self.runs:
            if s == k and n == 0 and skip > 0:
                skip -= 1
                continue
            if s >= k:
                return r.getparent(), r.getparent().index(r)
        if self.runs:
            r = self.runs[-1][0]
            return r.getparent(), r.getparent().index(r) + 1
        return None
