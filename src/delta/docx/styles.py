"""styles.xml: document defaults and the paragraph-style chain."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..model import Border, TabStop, merge_tabs
from .ns import wq, wval, wbool, half_pt, twips_to_pt, _num

_TAB_KINDS = {"start": "left", "end": "right"}
# Clark names built once: these are looked up for every run and paragraph of a document.
_B, _I, _U, _CAPS, _SMALL_CAPS, _RFONTS, _SZ, _COLOR, _ASCII = (
    wq(t) for t in ("b", "i", "u", "caps", "smallCaps", "rFonts", "sz", "color", "ascii"))
_RPR_TAGS = frozenset((_B, _I, _U, _CAPS, _SMALL_CAPS, _RFONTS, _SZ, _COLOR))
_PPR_TAGS = frozenset(wq(t) for t in ("jc", "ind", "spacing", "keepNext", "keepLines", "contextualSpacing",
                                      "pageBreakBefore", "outlineLvl", "tabs", "pBdr", "numPr"))


def _first_children(el, tags: frozenset) -> dict:
    """tag -> the first direct child with that tag, for the tags asked for: one pass over the
    children instead of one find() per property (the same element find() returns)."""
    out: dict = {}
    for ch in el:
        t = ch.tag
        if t in tags and t not in out:
            out[t] = ch
    return out


def read_rpr(rpr) -> dict:
    """Run properties from a <w:rPr>; only keys that are set are returned."""
    out: dict = {}
    if rpr is None:
        return out
    kids = _first_children(rpr, _RPR_TAGS)
    if not kids:
        return out
    get = kids.get
    b = get(_B)
    if b is not None:
        out["bold"] = wbool(b)
    i = get(_I)
    if i is not None:
        out["italic"] = wbool(i)
    u = get(_U)
    if u is not None:
        out["underline"] = wval(u, "single") != "none"
    for tag, key in ((_CAPS, "caps"), (_SMALL_CAPS, "small_caps")):
        el = get(tag)
        if el is not None:
            out[key] = wbool(el)
    fonts = get(_RFONTS)
    if fonts is not None and fonts.get(_ASCII):
        out["font"] = fonts.get(_ASCII)
    sz = get(_SZ)
    if sz is not None and half_pt(wval(sz)) is not None:
        out["size_pt"] = half_pt(wval(sz))
    color = get(_COLOR)
    if color is not None:
        v = wval(color)
        out["color"] = None if v is None or v.lower() == "auto" else v.lower()
    return out


def read_ppr(ppr) -> dict:
    """Paragraph properties from a <w:pPr>; only keys that are set are returned."""
    out: dict = {}
    if ppr is None:
        return out
    kids = _first_children(ppr, _PPR_TAGS)
    if not kids:
        return out
    jc = kids.get(wq("jc"))
    if jc is not None:
        v = wval(jc, "left")
        out["align"] = {"both": "justify", "start": "left", "end": "right"}.get(v, v)
    ind = kids.get(wq("ind"))
    if ind is not None:
        for attr, key in (("left", "ind_left_pt"), ("start", "ind_left_pt"),
                          ("right", "ind_right_pt"), ("end", "ind_right_pt"),
                          ("hanging", "ind_hanging_pt"), ("firstLine", "ind_first_line_pt")):
            v = twips_to_pt(ind.get(wq(attr)))
            if v is not None:
                out[key] = v
    sp = kids.get(wq("spacing"))
    if sp is not None:
        before, after = twips_to_pt(sp.get(wq("before"))), twips_to_pt(sp.get(wq("after")))
        if before is not None:
            out["space_before_pt"] = before
        if after is not None:
            out["space_after_pt"] = after
        line = sp.get(wq("line"))
        rule = sp.get(wq("lineRule")) or "auto"
        if line is not None:
            try:
                n = float(line)
            except ValueError:
                n = None
            if n is not None:
                out["line_rule"] = rule
                # "auto" is a multiplier (240 twips == a single line); "exact"/"atLeast" is a
                # fixed line height in twips. These are different units for different concepts,
                # so they are kept in separate fields rather than one conflated line_spacing.
                if rule == "auto":
                    out["line_spacing"] = n / 240.0
                else:
                    out["line_exact_pt"] = n / 20.0
    for tag, key in (("keepNext", "keep_next"), ("keepLines", "keep_lines"),
                     ("contextualSpacing", "contextual_spacing"), ("pageBreakBefore", "page_break_before")):
        el = kids.get(wq(tag))
        if el is not None:
            out[key] = wbool(el)
    ol = kids.get(wq("outlineLvl"))
    if ol is not None and wval(ol) is not None:
        out["outline_level"] = int(wval(ol))
    tabs = kids.get(wq("tabs"))
    if tabs is not None:
        stops = []
        for t in tabs.findall(wq("tab")):
            pos = twips_to_pt(t.get(wq("pos")))
            if pos is None:
                continue
            kind = wval(t, "left") or "left"
            stops.append(TabStop(pos, _TAB_KINDS.get(kind, kind), t.get(wq("leader")) or "none"))
        out["tabs"] = tuple(stops)
    bdr = kids.get(wq("pBdr"))
    if bdr is not None:
        for side in ("top", "bottom", "left", "right"):
            el = bdr.find(wq(side))
            if el is None:
                continue
            val = wval(el, "single")
            sz = _num(el.get(wq("sz")))
            if val in ("none", "nil") or not sz:
                out[f"border_{side}"] = None          # set to None on purpose: cancels an inherited side
                continue
            color = (el.get(wq("color")) or "auto").lower()
            out[f"border_{side}"] = Border(sz / 8.0, _num(el.get(wq("space"))) or 0.0,
                                           None if color == "auto" else color)
    numpr = kids.get(wq("numPr"))
    if numpr is not None:
        nid, il = numpr.find(wq("numId")), numpr.find(wq("ilvl"))
        if nid is not None and wval(nid) is not None:
            out["num_id"] = int(wval(nid))
        if il is not None and wval(il) is not None:
            out["ilvl"] = int(wval(il))
    return out


@dataclass
class Style:
    id: str
    name: str = ""
    type: str = "paragraph"
    based_on: str | None = None
    rpr: dict = field(default_factory=dict)
    ppr: dict = field(default_factory=dict)


class Styles:
    def __init__(self):
        self._map: dict[str, Style] = {}
        self.defaults = {"font": None, "size_pt": 11.0, "space_before_pt": None, "space_after_pt": None,
                          "line_spacing": None, "line_rule": None, "line_exact_pt": None}
        self.default_paragraph_style_id: str | None = None

    @classmethod
    def parse(cls, root) -> "Styles":
        s = cls()
        if root is None:
            return s
        dd = root.find(wq("docDefaults"))
        if dd is not None:
            r = read_rpr(dd.find(f"{wq('rPrDefault')}/{wq('rPr')}"))
            p = read_ppr(dd.find(f"{wq('pPrDefault')}/{wq('pPr')}"))
            s.defaults = {"font": r.get("font"), "size_pt": r.get("size_pt", 11.0),
                          "space_before_pt": p.get("space_before_pt"), "space_after_pt": p.get("space_after_pt"),
                          "line_spacing": p.get("line_spacing"), "line_rule": p.get("line_rule"),
                          "line_exact_pt": p.get("line_exact_pt")}
        for el in root.iter(wq("style")):
            sid = el.get(wq("styleId"))
            if not sid:
                continue
            styp = el.get(wq("type")) or "paragraph"
            st = Style(id=sid, name=wval(el.find(wq("name")), ""), type=styp,
                       based_on=wval(el.find(wq("basedOn"))),
                       rpr=read_rpr(el.find(wq("rPr"))),      # direct child only: pPr/rPr is the paragraph mark
                       ppr=read_ppr(el.find(wq("pPr"))))
            s._map[sid] = st
            if styp == "paragraph" and (el.get(wq("default")) or "").strip().lower() in ("1", "true", "on"):
                s.default_paragraph_style_id = sid
        return s

    def get(self, sid):
        return self._map.get(sid) if sid else None

    def _chain(self, sid):
        # A paragraph that names no pStyle uses Word's default paragraph style (the
        # w:type="paragraph" style with w:default="1"), not an empty chain.
        if sid is None:
            sid = self.default_paragraph_style_id
        seen, out = set(), []
        st = self.get(sid)
        while st is not None and st.id not in seen:
            seen.add(st.id)
            out.append(st)
            st = self.get(st.based_on)
        return out  # child first

    def resolved_rpr(self, sid) -> dict:
        out: dict = {}
        for st in reversed(self._chain(sid)):
            out.update(st.rpr)
        return out

    def resolved_ppr(self, sid) -> dict:
        out: dict = {}
        tabs: tuple = ()
        for st in reversed(self._chain(sid)):
            out.update(st.ppr)
            tabs = merge_tabs(tabs, st.ppr.get("tabs", ()))   # stops accumulate down the chain
        if tabs or "tabs" in out:
            out["tabs"] = tabs
        return out
