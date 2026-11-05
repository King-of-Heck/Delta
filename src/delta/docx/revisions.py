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
