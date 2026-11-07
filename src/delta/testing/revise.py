"""Accept All / Reject All over a .docx, for testing the tracked-changes export.

Accept is the production `docx.revisions.accept_revisions`; Reject is its mirror, written here only
for tests: inserted content and inserted rows go, deleted content comes back, a formatting revision
puts the recorded properties back, an inserted paragraph mark joins its paragraph to the next.
"""
from __future__ import annotations

import io
import zipfile

from lxml import etree

from ..docx.ns import wq
from ..docx.package import _ensure_w_namespace
from ..docx.revisions import accept_revisions

_P, _PPR, _RPR, _TR, _TRPR = wq("p"), wq("pPr"), wq("rPr"), wq("tr"), wq("trPr")
_KEEP_IN_PPR = {wq("rPr"), wq("sectPr"), wq("pPrChange")}


def _unwrap(el) -> None:
    parent = el.getparent()
    i = parent.index(el)
    for k, child in enumerate(list(el)):
        parent.insert(i + k, child)
    parent.remove(el)


def reject_revisions(root) -> None:
    for tr in list(root.iter(_TR)):
        trpr = tr.find(_TRPR)
        if trpr is None:
            continue
        if trpr.find(wq("ins")) is not None and tr.getparent() is not None:
            tr.getparent().remove(tr)
            continue
        for mark in trpr.findall(wq("del")):
            trpr.remove(mark)
    for rpr in list(root.iter(_RPR)):
        ch = rpr.find(wq("rPrChange"))
        if ch is None:
            continue
        old = ch.find(_RPR)
        for c in list(rpr):
            rpr.remove(c)
        for c in (list(old) if old is not None else []):
            rpr.append(c)
    for ppr in list(root.iter(_PPR)):
        ch = ppr.find(wq("pPrChange"))
        if ch is None:
            continue
        old = ch.find(_PPR)
        for c in list(ppr):
            if c.tag not in _KEEP_IN_PPR:
                ppr.remove(c)
        for k, c in enumerate(list(old) if old is not None else []):
            ppr.insert(k, c)
        ppr.remove(ch)
    for p in list(root.iter(_P)):
        rpr = p.find(f"{_PPR}/{_RPR}")
        if rpr is None:
            continue
        for mark in rpr.findall(wq("del")):
            rpr.remove(mark)
        gone = rpr.findall(wq("ins"))
        if not gone:
            continue
        for mark in gone:
            rpr.remove(mark)
        nxt = p.getnext()
        if nxt is None or nxt.tag != _P or p.getparent() is None:
            continue
        at = 1 if nxt.find(_PPR) is not None else 0
        for k, child in enumerate([c for c in p if c.tag != _PPR]):
            nxt.insert(at + k, child)
        p.getparent().remove(p)
    for el in list(root.iter(wq("ins"), wq("moveTo"))):
        if el.getparent() is not None:
            el.getparent().remove(el)
    for el in list(root.iter(wq("del"), wq("moveFrom"))):
        if el.getparent() is not None:
            _unwrap(el)
    for el in root.iter(wq("delText")):
        el.tag = wq("t")
    for el in root.iter(wq("delInstrText")):
        el.tag = wq("instrText")


def _apply(data: bytes, fn) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            body = src.read(info.filename)
            name = info.filename
            if name.startswith("word/") and name.endswith(".xml") and "/" not in name[5:] and name[5:].startswith(
                    ("document", "footnotes", "endnotes", "header", "footer")):
                root = etree.fromstring(_ensure_w_namespace(body),
                                        parser=etree.XMLParser(huge_tree=True, recover=True))
                fn(root)
                body = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            out.writestr(info, body)
    return buf.getvalue()


def accept_docx(data: bytes) -> bytes:
    return _apply(data, accept_revisions)


def reject_docx(data: bytes) -> bytes:
    return _apply(data, reject_revisions)
