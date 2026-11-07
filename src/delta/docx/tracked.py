"""The comparison of two Word documents as a Word document with tracked changes.

Built on the Modified: a copy of its package whose body, notes, headers and footers carry the
differences as Word revisions, so Accept All gives the Modified and Reject All the Original.

- Revisions already in either source are accepted first (the comparison reads them that way).
- Both sources are read with their XML kept (`parse_package(keep_xml=True)`) and compared.
- Row by row: inserted text is wrapped in w:ins; the Original's deleted text goes back in as w:del
  runs copied from the Original (its formatting kept); a whole inserted / deleted paragraph has its
  mark marked too. A table row only one side has is a row revision (w:trPr/w:ins|w:del); a deleted
  row or table is a copy of the Original's. Formatting changes Delta reports are w:rPrChange holding
  the Original run's properties; a paragraph whose list or level changed gets w:pPrChange.
- Deleted content is placed through `twin`: every Original element (paragraph, cell, row, table,
  note, part root) mapped to its counterpart in the Modified (from the ancestors of each matched
  paragraph pair) or to the copy put in for it, so a deleted paragraph lands after the counterpart
  of the nearest Original sibling before it.
- A paragraph whose XML text the comparison's text cannot be traced through (`trackmap.ParaMap`)
  is marked whole: the Original paragraph deleted, the Modified one inserted.

Known limits: empty Original paragraphs, a note or header/footer part only the Original has, and
images inside deleted text are not put back; comments only the Original has are not brought back.
"""
from __future__ import annotations

import bisect
import copy
import io
import zipfile
from datetime import datetime

from lxml import etree

from ..diff.compare import compare
from ..reader import kind_of
from .ns import W14, wq
from .package import Package
from .parser import parse_package
from .revisions import accept_revisions, revision_parts
from .trackmap import _TRANSPARENT, ParaMap

_P, _R, _T, _PPR, _RPR = wq("p"), wq("r"), wq("t"), wq("pPr"), wq("rPr")
_TC, _TR, _TBL, _TRPR, _BODY, _SECTPR = wq("tc"), wq("tr"), wq("tbl"), wq("trPr"), wq("body"), wq("sectPr")
_ID = wq("id")
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_CLIMB = {_TR, _TBL, wq("sdt"), wq("sdtContent"), wq("customXml")}
# what a copy of Original content may not keep: parts of the Original's package (images, links,
# objects), marks tied to its notes and comments, and ids that would clash with the Modified's
_STRIP = {wq(t) for t in ("drawing", "pict", "object", "commentReference", "commentRangeStart", "commentRangeEnd", "bookmarkStart",
                          "bookmarkEnd", "permStart", "permEnd", "altChunk", "sectPr")}
_UNWRAP = {wq("hyperlink")}
_FLDSIMPLE = wq("fldSimple")
_REFS = {wq("footnoteReference"): "footnote", wq("endnoteReference"): "endnote"}
_W14_IDS =(f"{{{W14}}}paraId", f"{{{W14}}}textId")
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class NotWord(ValueError):
    """The tracked-changes export needs two Word documents."""


def para_runs(p) -> list:
    """The w:r elements of a paragraph the comparison reads (through links, fields, controls)."""
    out: list = []

    def walk(el):
        for child in el:
            if child.tag == _R:
                out.append(child)
            elif child.tag in _TRANSPARENT:
                walk(child)
    walk(p)
    return out


def _ref_run(p, ref):
    """The run of paragraph p holding the reference mark to note `ref` (a NoteRef)."""
    tag = wq(ref.kind + "Reference")
    return next((r for r in para_runs(p) if any(x.tag == tag and x.get(_ID) == str(ref.id) for x in r)), None)


def _wrap(el, tag: str, attrs: dict):
    w = el.makeelement(wq(tag), attrs)
    el.addprevious(w)
    w.append(el)
    return w


def _ppr(p):
    ppr = p.find(_PPR)
    if ppr is None:
        ppr = p.makeelement(_PPR, {})
        p.insert(0, ppr)
    return ppr


def _mark_rpr(p):
    """The paragraph mark's w:rPr (made where the schema wants it: before sectPr / pPrChange)."""
    ppr = _ppr(p)
    rpr = ppr.find(_RPR)
    if rpr is None:
        rpr = ppr.makeelement(_RPR, {})
        after = [c for c in ppr if c.tag in (_SECTPR, wq("pPrChange"))]
        if after:
            after[0].addprevious(rpr)
        else:
            ppr.append(rpr)
    return rpr


def expand_fields(el) -> None:
    """Every w:fldSimple under el rewritten as the same field in runs (begin, instruction,
    separate, the cached result, end): a run revision can hold those, not a w:fldSimple."""
    for f in list(el.iter(_FLDSIMPLE)):
        parent = f.getparent()
        if parent is None:
            continue
        result = list(f)
        first = next((x for x in result if x.tag == _R), None)
        rpr = first.find(_RPR) if first is not None else None

        def run(*children):
            r = f.makeelement(_R, {})
            if rpr is not None:
                r.append(copy.deepcopy(rpr))
            for c in children:
                r.append(c)
            return r
        instr = f.makeelement(wq("instrText"), {_XML_SPACE: "preserve"})
        instr.text = f.get(wq("instr")) or ""
        new = [run(f.makeelement(wq("fldChar"), {wq("fldCharType"): "begin"})), run(instr),
               run(f.makeelement(wq("fldChar"), {wq("fldCharType"): "separate"})), *result,
               run(f.makeelement(wq("fldChar"), {wq("fldCharType"): "end"}))]
        i = parent.index(f)
        parent.remove(f)
        for k, x in enumerate(new):
            parent.insert(i + k, x)


def _clean(el) -> None:
    """Make a copy of Original content safe inside the Modified (see _STRIP / _UNWRAP); note
    references are the editor's (`_Editor.clean`)."""
    expand_fields(el)
    for x in list(el.iter(*_STRIP)):
        if x.getparent() is not None:
            x.getparent().remove(x)
    for x in list(el.iter(*_UNWRAP)):
        parent = x.getparent()
        if parent is None:
            continue
        i = parent.index(x)
        for k, child in enumerate(list(x)):
            parent.insert(i + k, child)
        parent.remove(x)
    for x in el.iter():
        for a in _W14_IDS:
            if a in x.attrib:
                del x.attrib[a]


def _as_deleted(el) -> None:
    for x in el.iter(_T):
        x.tag = wq("delText")
    for x in el.iter(wq("instrText")):
        x.tag = wq("delInstrText")


def _same(a, b) -> bool:
    def key(el):
        if el is None:
            return b""
        return b"".join(etree.tostring(c) for c in el if c.tag != wq("rPrChange"))
    return key(a) == key(b)


def _num_key(ppr) -> tuple:
    if ppr is None:
        return (None, None, None)
    style = ppr.find(wq("pStyle"))
    num = ppr.find(wq("numPr"))
    nid = num.find(wq("numId")) if num is not None else None
    lvl = num.find(wq("ilvl")) if num is not None else None
    val = wq("val")
    return (style.get(val) if style is not None else None, nid.get(val) if nid is not None else None,
            lvl.get(val) if lvl is not None else None)


class _Editor:
    def __init__(self, cmp, author: str, date: str, first_id: int, a_notes: dict, b_notes: dict):
        self.cmp = cmp
        self.a_notes = a_notes            # (kind, id) -> the Original's w:footnote / w:endnote
        self.b_notes = b_notes            # kind -> the Modified's notes root (w:footnotes / w:endnotes)
        self.refs_done: set = set()       # Original notes whose deleted reference is in place
        self.b_targets: set = set()       # Modified elements with an Original counterpart (after seed)
        self.author, self.date = author, date
        self.next_id = first_id
        self.twin: dict = {}
        self.inserted: list = []          # Modified paragraphs inserted whole
        self.skipped = 0                  # deleted paragraphs with nowhere to go (known limits)

    def attrs(self) -> dict:
        a = {_ID: str(self.next_id), wq("author"): self.author, wq("date"): self.date}
        self.next_id += 1
        return a

    # ---- twins -----------------------------------------------------------------------------
    def seed(self, extra_roots) -> None:
        for r in self.cmp.rows:
            if r.oi is None or r.ni is None:
                continue
            x, y = self.cmp.a_units[r.oi].para.xml, self.cmp.b_units[r.ni].para.xml
            while x is not None and y is not None and x.tag == y.tag:
                self.twin.setdefault(x, y)
                x, y = x.getparent(), y.getparent()
        for x, y in extra_roots:
            self.twin.setdefault(x, y)

    def cell(self, tc):
        tr = tc.getparent()
        mtr = self.twin.get(tr)
        if mtr is None:
            return None
        cells = mtr.findall(_TC)
        if not cells:
            return None
        got = cells[min(tr.findall(_TC).index(tc), len(cells) - 1)]
        self.twin[tc] = got
        return got

    def place(self, x, hints=(None, None)) -> None:
        """Put a deleted copy of Original element x into the Modified (once). hints: the Modified
        paragraphs of the rows before and after it, for where nothing structural says."""
        if x in self.twin:
            return
        parent = x.getparent()
        if parent is None:
            self.skipped += 1
            return
        m = self.twin.get(parent)
        if m is None and parent.tag == _TC:
            m = self.cell(parent)
            if m is None:
                self.place(parent.getparent(), hints)          # the whole row goes back
                return
        if m is None:
            if parent.tag in _CLIMB:
                self.place(parent, hints)
            elif parent.tag in (wq("footnote"), wq("endnote")):
                self.note_copy(parent)
            else:
                self.skipped += 1
            return
        dup = self.copy(x)
        self.mark_deleted(dup)
        self.insert(x, dup, m, hints)

    def copy(self, x):
        """A cleaned deep copy of Original content, its paragraphs, cells, rows and tables twinned."""
        dup = copy.deepcopy(x)
        for a, b in zip(x.iter(_P, _TC, _TR, _TBL), dup.iter(_P, _TC, _TR, _TBL)):
            self.twin[a] = b
        self.clean(dup)
        return dup

    def clean(self, el) -> None:
        """_clean, and every note reference in it pointed at the Modified's copy of that note
        (made here, deleted, when the Modified has none); a reference to a note the Original
        does not hold goes."""
        _clean(el)
        for ref in list(el.iter(*_REFS)):
            note = self.a_notes.get((_REFS[ref.tag], ref.get(_ID)))
            target = self.note_copy(note) if note is not None else None
            if target is None:
                ref.getparent().remove(ref)
                continue
            ref.set(_ID, target.get(_ID))
            self.refs_done.add(note)

    def note_copy(self, note):
        """The Modified's note for an Original note: its twin, else a deleted copy added with a new id."""
        if note in self.twin:
            return self.twin[note]             # None while the copy is being made
        kind = "footnote" if note.tag == wq("footnote") else "endnote"
        root = self.b_notes.get(kind)
        if root is None:
            self.skipped += 1
            return None
        self.twin[note] = None
        dup = self.copy(note)
        self.mark_deleted(dup)
        ids = [int(v) for el in root for v in [el.get(_ID)] if v is not None and v.lstrip("-").isdigit()]
        dup.set(_ID, str(max(ids + [0]) + 1))
        root.append(dup)
        self.twin[note] = dup
        return dup

    def insert(self, x, dup, m, hints=(None, None)) -> None:
        sib = x.getprevious()
        while sib is not None:
            t = self.twin.get(sib)
            if t is not None and t.getparent() is m:
                t.addnext(dup)
                return
            sib = sib.getprevious()
        sib = x.getnext()
        while sib is not None:
            t = self.twin.get(sib)
            if t is not None and t.getparent() is m:
                t.addprevious(dup)
                return
            sib = sib.getnext()
        prev, nxt = hints
        for h, after in ((prev, True), (nxt, False)):
            while h is not None and h.getparent() is not m:
                h = h.getparent()
            if h is not None:
                (h.addnext if after else h.addprevious)(dup)
                return
        tail = m.find(_SECTPR) if m.tag == _BODY else None
        if tail is not None:
            tail.addprevious(dup)
        else:
            m.append(dup)

    # ---- marks -----------------------------------------------------------------------------
    def mark_deleted(self, el) -> None:
        """Everything in a cleaned copy deleted: runs, paragraph marks, table rows."""
        paras = [el] if el.tag == _P else list(el.iter(_P))
        for p in paras:
            for r in para_runs(p):
                _as_deleted(r)
                _wrap(r, "del", self.attrs())
            _mark_rpr(p).insert(0, p.makeelement(wq("del"), self.attrs()))
        rows = [el] if el.tag == _TR else list(el.iter(_TR))
        for tr in rows:
            self.row_mark(tr, "del")

    def row_mark(self, tr, tag: str) -> None:
        trpr = tr.find(_TRPR)
        if trpr is None:
            trpr = tr.makeelement(_TRPR, {})
            ex = tr.find(wq("tblPrEx"))
            if ex is not None:
                ex.addnext(trpr)
            else:
                tr.insert(0, trpr)
        if trpr.find(wq(tag)) is not None:
            return
        change = trpr.find(wq("trPrChange"))
        mark = tr.makeelement(wq(tag), self.attrs())
        if change is not None:
            change.addprevious(mark)
        else:
            trpr.append(mark)

    def mark_inserted(self, p) -> None:
        expand_fields(p)
        for r in para_runs(p):
            _wrap(r, "ins", self.attrs())
        _mark_rpr(p).insert(0, p.makeelement(wq("ins"), self.attrs()))
        self.inserted.append(p)

    # ---- rows ------------------------------------------------------------------------------
    def hints(self, k: int) -> tuple:
        """The Modified paragraphs of the nearest rows before and after row k in the same stream."""
        rows, bu = self.cmp.rows, self.cmp.b_units
        stream = self.cmp.a_units[rows[k].oi].stream
        out = []
        for rng in (range(k - 1, -1, -1), range(k + 1, len(rows))):
            out.append(next((bu[rows[j].ni].para.xml for j in rng
                             if rows[j].ni is not None and bu[rows[j].ni].stream == stream), None))
        return tuple(out)

    def run(self) -> None:
        self.b_targets = set(self.twin.values())
        for k, row in enumerate(self.cmp.rows):
            ou = self.cmp.a_units[row.oi] if row.oi is not None else None
            ru = self.cmp.b_units[row.ni] if row.ni is not None else None
            if row.type == "inserted":
                self.mark_inserted(ru.para.xml)
            elif row.type == "deleted":
                self.place(ou.para.xml, self.hints(k))
            elif row.type == "equal":
                if row.fmt_ranges:
                    self.formatting(row, ou, ru)
                self.numbering(row, ou, ru)
                self.note_marks(ou, ru, [("eq", 0, len(ou.text), 0, len(ru.text))])
            elif not self.changed(row, ou, ru):
                pa, pb = ou.para.xml, ru.para.xml
                dup = self.copy(pa)
                self.mark_deleted(dup)
                pb.addprevious(dup)
                self.mark_inserted(pb)
            else:
                self.numbering(row, ou, ru)
        trs = set(self.twin.values())
        for p in self.inserted:
            tr = next((a for a in p.iterancestors(_TR)), None)
            if tr is not None and tr not in trs:
                self.row_mark(tr, "ins")

    def changed(self, row, ou, ru) -> bool:
        """The word-level edit in place; False when the paragraph cannot be traced (marked whole)."""
        ma = ParaMap.of(ou.para.xml, ou.text)
        mb = ParaMap.of(ru.para.xml, ru.text)
        if ma is None or mb is None:
            return False
        plan, a, b = [], 0, 0
        for s in row.segments:
            n = len(s.t)
            if s.m == "eq":
                plan.append(("eq", a, a + n, b, b + n))
                a, b = a + n, b + n
            elif s.m == "del":
                plan.append(("del", a, a + n, b, b))
                a += n
            else:
                plan.append(("ins", a, a, b, b + n))
                b += n
        if a != len(ou.text) or b != len(ru.text):
            return False
        mb.split([mb.raw_pos(k) for _, _, _, b0, b1 in plan for k in (b0, b1)])
        for m, a0, a1, b0, _ in plan:
            if m != "del":
                continue
            runs = ma.copy_range(ma.raw_pos(a0), ma.raw_pos(a1))
            if not runs:
                continue
            # after the marks (a note reference, a field character) that stood before it in the Original
            at = mb.point(mb.raw_pos(b0), skip=ma.marks_at(ma.raw_pos(a0)))
            d = ru.para.xml.makeelement(wq("del"), self.attrs())
            for r in runs:
                self.clean(r)
                _as_deleted(r)
                d.append(r)
            if at is None:
                ru.para.xml.append(d)
            else:
                at[0].insert(at[1], d)
        self.note_marks(ou, ru, plan, mb)
        for m, _, _, b0, b1 in plan:
            if m != "ins":
                continue
            for r in mb.marked_runs(mb.raw_pos(b0), mb.raw_pos(b1)):
                if r.getparent().tag != wq("ins"):
                    _wrap(r, "ins", self.attrs())
        return True

    def note_marks(self, ou, ru, plan, mb=None) -> None:
        """Note references the text diff cannot see. An Original reference whose note the Modified
        lacks (and that no deleted text brought back) goes in as a deleted reference to a deleted
        copy of the note; a Modified reference to a note new in the Modified is marked inserted."""
        for c, ref in ou.note_refs:
            note = self.a_notes.get((ref.kind, str(ref.id)))
            if note is None or note in self.refs_done or self.twin.get(note) is not None:
                continue
            run = _ref_run(ou.para.xml, ref)
            if run is None:
                continue
            b = next((b0 + c - a0 for m, a0, a1, b0, _ in plan if m == "eq" and a0 <= c <= a1),
                     next((b0 for m, a0, a1, b0, _ in plan if m == "del" and a0 <= c <= a1), len(ru.text)))
            dup = copy.deepcopy(run)
            for x in list(dup):
                if x.tag != _RPR and x.tag not in _REFS:
                    dup.remove(x)
            self.clean(dup)
            if dup.find(f"./{wq(ref.kind + 'Reference')}") is None:
                continue
            d = dup.makeelement(wq("del"), self.attrs())
            d.append(dup)
            if mb is None:
                mb = ParaMap.of(ru.para.xml, ru.text)
            k = mb.raw_pos(b) if mb is not None else 0
            if mb is not None:
                mb.split([k])
            at = mb.point(k, skip=mb.marks_at(k)) if mb is not None else None
            if at is None:
                ru.para.xml.append(d)
            else:
                at[0].insert(at[1], d)
        for _, ref in ru.note_refs:
            note = self.b_note(ref)
            if note is None or note in self.b_targets:
                continue
            run = _ref_run(ru.para.xml, ref)
            if run is not None and run.getparent().tag != wq("ins"):
                _wrap(run, "ins", self.attrs())

    def b_note(self, ref):
        root = self.b_notes.get(ref.kind)
        if root is None:
            return None
        return next((el for el in root.findall(wq(ref.kind)) if el.get(_ID) == str(ref.id)), None)

    def formatting(self, row, ou, ru) -> None:
        ma = ParaMap.of(ou.para.xml, ou.text)
        mb = ParaMap.of(ru.para.xml, ru.text)
        if ma is None or mb is None:
            return
        mb.split([mb.raw_pos(k) for f in row.fmt_ranges for k in (f.s, f.e)])
        for f in row.fmt_ranges:
            for r in mb.runs_in(mb.raw_pos(f.s), mb.raw_pos(f.e)):
                start = next(rs for x, rs, _ in mb.runs if x is r)
                k = ma.raw_pos(min(bisect.bisect_left(mb.cmap, start), len(ou.text)))
                old = next((x for x, rs, n in ma.runs if rs <= k < rs + n), None)
                old_rpr = old.find(_RPR) if old is not None else None
                new_rpr = r.find(_RPR)
                if _same(old_rpr, new_rpr):
                    continue
                if new_rpr is None:
                    new_rpr = r.makeelement(_RPR, {})
                    r.insert(0, new_rpr)
                for c in new_rpr.findall(wq("rPrChange")):
                    new_rpr.remove(c)
                ch = new_rpr.makeelement(wq("rPrChange"), self.attrs())
                inner = ch.makeelement(_RPR, {})
                for c in (old_rpr if old_rpr is not None else []):
                    if c.tag != wq("rPrChange"):
                        inner.append(copy.deepcopy(c))
                ch.append(inner)
                new_rpr.append(ch)

    def numbering(self, row, ou, ru) -> None:
        if not row.num_changed:
            return
        pa, pb = ou.para.xml, ru.para.xml
        old = pa.find(_PPR)
        if _num_key(old) == _num_key(pb.find(_PPR)):
            return                                   # renumbered by counting: Word renumbers live
        ppr = _ppr(pb)
        for c in ppr.findall(wq("pPrChange")):
            ppr.remove(c)
        ch = ppr.makeelement(wq("pPrChange"), self.attrs())
        inner = ch.makeelement(_PPR, {})
        for c in (old if old is not None else []):
            if c.tag not in (_RPR, _SECTPR, wq("pPrChange")):
                inner.append(copy.deepcopy(c))
        ch.append(inner)
        ppr.append(ch)


def _max_id(roots) -> int:
    top = 0
    for root in roots:
        for el in root.iter():
            v = el.get(_ID)
            if v is not None and v.lstrip("-").isdigit():
                top = max(top, int(v))
    return top


def export_tracked(a: bytes, b: bytes, *, author: str = "Delta", when: datetime | None = None,
                   ignore_case: bool = False) -> bytes:
    """The .docx bytes: the Modified (b) with its differences from the Original (a) tracked."""
    if kind_of(a) != "docx" or kind_of(b) != "docx":
        raise NotWord("the Word export needs two Word documents")
    when = when or datetime.now()
    with Package.open(a) as pa, Package.open(b) as pb:
        names_a, names_b = revision_parts(pa), revision_parts(pb)
        for pkg, names in ((pa, names_a), (pb, names_b)):
            for n in names:
                root = pkg.xml(n)
                if root is not None:
                    accept_revisions(root)
        da = parse_package(pa, keep_xml=True)
        db = parse_package(pb, keep_xml=True)
        cmp = compare(da, db, ignore_case=ignore_case)
        roots = [(pa.xml(n), pb.xml(n)) for n in names_a if n in names_b]
        body = [(ra.find(_BODY), rb.find(_BODY)) for ra, rb in roots[:1] if ra is not None and rb is not None]
        # notes pair up only through their matched paragraphs (ids are renumbered when one goes)
        a_notes, b_notes = {}, {}
        for n, kind in (("word/footnotes.xml", "footnote"), ("word/endnotes.xml", "endnote")):
            if (ra := pa.xml(n)) is not None:
                a_notes.update({(kind, el.get(_ID)): el for el in ra.findall(wq(kind))})
            if (rb := pb.xml(n)) is not None:
                b_notes[kind] = rb
        ed = _Editor(cmp, (author or "Delta").strip() or "Delta", when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     _max_id(rb for n in names_b if (rb := pb.xml(n)) is not None) + 1, a_notes, b_notes)
        ed.seed(body + roots)
        ed.run()
        edited = {n: etree.tostring(pb.xml(n), xml_declaration=True, encoding="UTF-8", standalone=True)
                  for n in names_b if pb.xml(n) is not None}
        src = zipfile.ZipFile(io.BytesIO(b))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
            for info in src.infolist():
                data = edited.get(info.filename)
                if data is None:
                    out.writestr(info, src.read(info.filename))
                else:
                    out.writestr(info.filename, data, compress_type=zipfile.ZIP_DEFLATED)
        return buf.getvalue()
