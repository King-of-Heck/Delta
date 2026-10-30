"""numbering.xml and list-marker resolution with Word semantics."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..model import NumInfo
from .ns import wq, wval, twips_to_pt
from .styles import read_ppr

BULLETS = ["\u2022", "\u25e6", "\u25aa", "\u2023", "\u00b7"]  # SorkWhare's BULLETS glyphs, verbatim
_ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"),
          (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
_PH = re.compile(r"%(\d+)")
_LISTNUM = re.compile(r'\s*LISTNUM\s+"?([^"\\]+?)"?\s*(?:\\l\s*(\d+))?\s*$')


def _roman(n: int) -> str:
    out = ""
    for v, s in _ROMAN:
        while n >= v:
            out += s
            n -= v
    return out or str(n)


def _letters(n: int, upper: bool) -> str:
    # Word: a..z, then aa..zz (the same letter repeated), so 28 -> "bb"
    return chr((65 if upper else 97) + (n - 1) % 26) * ((n - 1) // 26 + 1)


def fmt_num(n: int, fmt: str) -> str:
    if fmt == "lowerLetter":
        return _letters(n, False)
    if fmt == "upperLetter":
        return _letters(n, True)
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    if fmt == "decimalZero":
        return f"0{n}" if n < 10 else str(n)
    return str(n)


@dataclass
class Level:
    fmt: str = "decimal"
    text: str = "%1."
    start: int = 1
    p_style: str | None = None
    ind_left_pt: float | None = None
    ind_hanging_pt: float | None = None
    align: str | None = None
    suff: str = "tab"
    # The level's own <w:pPr> (spacing group etc.) -- the rung the reference inserts between
    # a paragraph's own properties and the style chain in its spacing fallback. Indents are
    # already captured above as their own fields; kept for the existing callers of those.
    ppr: dict = field(default_factory=dict)


class Numbering:
    def __init__(self):
        self.num_to_abs: dict[int, int] = {}
        self.abstract: dict[int, dict[int, Level]] = {}
        self.overrides: dict[int, dict[int, int]] = {}
        self.style_to_num: dict[str, tuple[int, int]] = {}
        self.list_names: dict[str, int] = {}      # w:abstractNum's w:name -> its id (for LISTNUM fields)

    @classmethod
    def parse(cls, root) -> "Numbering":
        n = cls()
        if root is None:
            return n
        for ab in root.iter(wq("abstractNum")):
            aid = ab.get(wq("abstractNumId"))
            if aid is None:
                continue
            levels: dict[int, Level] = {}
            for lv in ab.iter(wq("lvl")):
                il = lv.get(wq("ilvl"))
                if il is None:
                    continue
                ind = lv.find(f"{wq('pPr')}/{wq('ind')}")
                start = wval(lv.find(wq("start")), "1")
                levels[int(il)] = Level(
                    fmt=wval(lv.find(wq("numFmt")), "decimal"),
                    text=wval(lv.find(wq("lvlText")), "%1."),
                    start=int(start) if start.isdigit() else 1,
                    p_style=wval(lv.find(wq("pStyle"))),
                    ind_left_pt=twips_to_pt(ind.get(wq("left"))) if ind is not None else None,
                    ind_hanging_pt=twips_to_pt(ind.get(wq("hanging"))) if ind is not None else None,
                    align=wval(lv.find(f"{wq('pPr')}/{wq('jc')}")) or wval(lv.find(wq("lvlJc"))),
                    ppr=read_ppr(lv.find(wq("pPr"))),
                    suff=wval(lv.find(wq("suff")), "tab") or "tab",
                )
            n.abstract[int(aid)] = levels
            name = wval(ab.find(wq("name")))
            if name:
                n.list_names.setdefault(name, int(aid))
        for num in root.iter(wq("num")):
            nid, aid = num.get(wq("numId")), wval(num.find(wq("abstractNumId")))
            if nid is None or aid is None:
                continue
            n.num_to_abs[int(nid)] = int(aid)
            for ov in num.iter(wq("lvlOverride")):
                il, so = ov.get(wq("ilvl")), wval(ov.find(wq("startOverride")))
                if il is not None and so is not None and so.isdigit():
                    n.overrides.setdefault(int(nid), {})[int(il)] = int(so)
        for nid, aid in n.num_to_abs.items():
            for il, lv in n.abstract.get(aid, {}).items():
                if lv.p_style and lv.p_style not in n.style_to_num:
                    n.style_to_num[lv.p_style] = (nid, il)
        return n

    def level(self, num_id: int, ilvl: int):
        aid = self.num_to_abs.get(num_id)
        if aid is None:
            return None
        return self.abstract.get(aid, {}).get(ilvl)


class NumberingCounter:
    """Per-abstract-list cascading counters (Word semantics)."""

    def __init__(self, numbering: Numbering):
        self.n = numbering
        self._counts: dict[int, dict[int, int]] = {}   # abstractId -> ilvl -> current value
        self._override_used: set[tuple[int, int]] = set()

    def listnum(self, instr: str) -> str | None:
        """What a LISTNUM field prints: the next number of the named list, which it also uses up.
        None for another field, or a list this document does not name."""
        m = _LISTNUM.match(instr)
        if m is None or m[1] not in self.n.list_names:
            return None
        aid = self.n.list_names[m[1]]
        num_id = next((nid for nid, a in self.n.num_to_abs.items() if a == aid), None)
        info = self.next(num_id, int(m[2] or 1) - 1) if num_id is not None else None
        return info.marker if info is not None and not info.is_bullet else None

    def next(self, num_id: int, ilvl: int) -> NumInfo | None:
        lv = self.n.level(num_id, ilvl)
        if lv is None:
            return None
        if lv.fmt == "bullet":
            return NumInfo(num_id, ilvl, BULLETS[min(ilvl, len(BULLETS) - 1)], True, lv.suff, lv.align or "left")
        aid = self.n.num_to_abs[num_id]
        counts = self._counts.setdefault(aid, {})
        so = self.n.overrides.get(num_id, {}).get(ilvl)
        if so is not None and (num_id, ilvl) not in self._override_used:
            self._override_used.add((num_id, ilvl))
            counts[ilvl] = so - 1
        counts[ilvl] = counts.get(ilvl, lv.start - 1) + 1
        for deeper in [k for k in counts if k > ilvl]:
            del counts[deeper]
        levels = self.n.abstract[aid]

        def sub(m):
            li = int(m.group(1)) - 1
            l2 = levels.get(li, Level())
            return fmt_num(counts.get(li, l2.start), l2.fmt)

        return NumInfo(num_id, ilvl, _PH.sub(sub, lv.text), False, lv.suff, lv.align or "left")
