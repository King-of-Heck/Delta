"""Tracked revisions still inside a document.

The reader reads every document as if its tracked changes were accepted (deleted and moved-from
text skipped, inserted text kept: parser._SKIP / _TRANSPARENT). That matches Word's own Compare,
which says so in a dialog; this module gives the number the notice shows. One integer per
document: every revision element across the body, every header and footer part the document's rels
name (an orphan part no section shows counts too), and the footnotes and endnotes. A tracked
paragraph mark (w:pPr/w:rPr/w:del) and a tracked table row (w:trPr/w:ins) use the same tags and
count without special handling. Comment text is its own stream and does not count. This is a tag
scan over each part's tree, not the paragraph parse, so an inspect request for one file stays cheap.
"""
from __future__ import annotations

from .ns import wq
from .package import Package

REVISION_TAGS = frozenset(wq(t) for t in (
    "ins", "del", "moveFrom", "moveTo",                              # content revisions
    "rPrChange", "pPrChange", "tblPrChange", "trPrChange", "tcPrChange",   # formatting revisions
    "sectPrChange", "tblGridChange", "numberingChange"))

_FIXED = ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")


def revision_parts(pkg: Package) -> list[str]:
    """The parts scanned, in a stable order, present ones only: the body, then every header and
    footer part the document's rels name, then the notes."""
    names = [_FIXED[0]]
    for target in pkg.rels("word/document.xml").values():
        if target.startswith(("header", "footer")) and target.endswith(".xml"):
            names.append("word/" + target)
    names.extend(_FIXED[1:])
    return [n for n in dict.fromkeys(names) if pkg.has(n)]


def count_revisions(pkg: Package) -> int:
    total = 0
    for name in revision_parts(pkg):
        root = pkg.xml(name)
        if root is None:
            continue
        total += sum(1 for _ in root.iter(*REVISION_TAGS))     # lxml filters by tag in C
    return total


_DROP = frozenset(wq(t) for t in (
    "del", "moveFrom", "rPrChange", "pPrChange", "tblPrChange", "trPrChange", "tcPrChange",
    "sectPrChange", "tblGridChange", "numberingChange",
    "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd"))
_UNWRAP = frozenset(wq(t) for t in ("ins", "moveTo"))
_P, _PPR, _RPR, _TR, _TRPR = wq("p"), wq("pPr"), wq("rPr"), wq("tr"), wq("trPr")


def _unwrap(el) -> None:
    """Put el's children where el is, then drop el (keeping the tail text lxml hangs on it)."""
    parent = el.getparent()
    i = parent.index(el)
    for k, child in enumerate(list(el)):
        parent.insert(i + k, child)
    parent.remove(el)


def accept_revisions(root) -> None:
    """Accept every tracked revision in one part's tree, in place: what Word's Accept All leaves.
    A paragraph whose mark is deleted joins the next paragraph (which keeps its own properties); a
    deleted table row goes; inserted content and marks simply lose their marking."""
    # rows first: a deleted row's w:del sits in trPr, an inserted one's w:ins likewise
    for tr in list(root.iter(_TR)):
        trpr = tr.find(_TRPR)
        if trpr is None:
            continue
        if trpr.find(wq("del")) is not None and tr.getparent() is not None:
            tr.getparent().remove(tr)
            continue
        for mark in trpr.findall(wq("ins")):
            trpr.remove(mark)
    # paragraph marks: inserted ones unmarked, deleted ones joined to the next paragraph
    for p in list(root.iter(_P)):
        rpr = p.find(f"{_PPR}/{_RPR}")
        if rpr is None:
            continue
        for mark in rpr.findall(wq("ins")) + rpr.findall(wq("moveTo")):
            rpr.remove(mark)
        gone = rpr.findall(wq("del")) + rpr.findall(wq("moveFrom"))
        if not gone:
            continue
        for mark in gone:
            rpr.remove(mark)
        nxt = p.getnext()
        if nxt is None or nxt.tag != _P or p.getparent() is None:
            continue            # the last paragraph of its container keeps its mark (Word's rule too)
        at = 1 if nxt.find(_PPR) is not None else 0
        for k, child in enumerate([c for c in p if c.tag != _PPR]):
            nxt.insert(at + k, child)
        p.getparent().remove(p)
    for el in list(root.iter(*_DROP)):
        if el.getparent() is not None:
            el.getparent().remove(el)
    for el in list(root.iter(*_UNWRAP)):
        if el.getparent() is not None:
            _unwrap(el)
