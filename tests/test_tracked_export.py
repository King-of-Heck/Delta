"""The Word export with tracked changes: Accept All gives the Modified, Reject All the Original."""
import io
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from lxml import etree

from delta.diff.units import units
from delta.docx.ns import wq
from delta.docx.parser import parse_docx
from delta.docx.tracked import NotWord, export_tracked
from delta.testing.makedocx import (DOC, ENDNOTES, FLD, FNREF, FOOTNOTES, HDR, NUMBERING, P, PR, R, RELS, SECT,
                                    TBL, make_docx)
from delta.testing.revise import accept_docx, reject_docx

WHEN = datetime(2026, 11, 7, 12, 30, 0)
CORPUS = Path(__file__).parent / "corpus"


def _streams(data: bytes) -> list[tuple[str, str]]:
    return [(u.stream, u.text) for u in units(parse_docx(data))]


def _export(a: bytes, b: bytes, author: str = "Delta") -> bytes:
    return export_tracked(a, b, author=author, when=WHEN)


def _round_trip(a: bytes, b: bytes) -> bytes:
    out = _export(a, b)
    assert _streams(accept_docx(out)) == _streams(accept_docx(b))
    assert _streams(reject_docx(out)) == _streams(accept_docx(a))
    return out


def _doc(body: str, **parts) -> bytes:
    return make_docx({"word/document.xml": DOC(body), **parts})


def _part(data: bytes, name: str = "word/document.xml"):
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read(name))


def _revisions(root) -> list:
    return list(root.iter(wq("ins"), wq("del"), wq("rPrChange"), wq("pPrChange")))


def test_words_changed_inside_a_paragraph():
    out = _round_trip(_doc(P("The quick brown fox.") + P("Kept.")),
                      _doc(P("The slow brown fox jumps.") + P("Kept.")))
    root = _part(out)
    assert [t.text for t in root.iter(wq("delText"))] == ["quick"]
    inserted = "".join(t.text for i in root.iter(wq("ins")) for t in i.iter(wq("t")))
    assert "slow" in inserted and "jumps" in inserted and "brown" not in inserted


def test_every_revision_has_author_date_and_a_unique_id():
    out = _export(_doc(P("one two three") + P("gone")), _doc(P("one four three") + P("new")), author="Sandy H")
    revs = _revisions(_part(out))
    assert revs
    assert all(r.get(wq("author")) == "Sandy H" and r.get(wq("date")) == "2026-11-07T12:30:00Z" for r in revs)
    ids = [r.get(wq("id")) for r in revs]
    assert len(ids) == len(set(ids))


def test_paragraphs_inserted_and_deleted():
    _round_trip(_doc(P("First.") + P("Old middle.") + P("Last.")),
                _doc(P("First.") + P("Last.") + P("Brand new ending.")))


def test_first_and_only_paragraphs():
    _round_trip(_doc(P("Only old.")), _doc(P("Entirely different text here.")))
    _round_trip(_doc(P("Lead.") + P("Body.")), _doc(P("Body.")))


def test_formatting_and_runs_survive_a_change():
    a = _doc(PR(R("Alpha ") + R("beta", "<w:b/>") + R(" gamma delta.")))
    b = _doc(PR(R("Alpha ") + R("beta", "<w:b/>") + R(" gamma epsilon.")))
    out = _round_trip(a, b)
    assert any(r.find(f"{wq('rPr')}/{wq('b')}") is not None for r in _part(out).iter(wq("r")))


def test_table_rows_inserted_deleted_and_changed():
    a = _doc(P("Before.") + TBL([["a1", "b1"], ["a2", "b2"], ["a3", "b3"]]) + P("After."))
    b = _doc(P("Before.") + TBL([["a1", "b1 changed"], ["a3", "b3"], ["a4", "b4"]]) + P("After."))
    out = _round_trip(a, b)
    root = _part(out)
    assert any(tr.find(f"{wq('trPr')}/{wq('ins')}") is not None for tr in root.iter(wq("tr")))
    assert any(tr.find(f"{wq('trPr')}/{wq('del')}") is not None for tr in root.iter(wq("tr")))


def test_whole_table_deleted_and_inserted():
    _round_trip(_doc(P("Intro.") + TBL([["x", "y"], ["z", "w"]]) + P("Outro.")), _doc(P("Intro.") + P("Outro.")))
    _round_trip(_doc(P("Intro.") + P("Outro.")), _doc(P("Intro.") + TBL([["x", "y"]]) + P("Outro.")))


def test_footnote_edited():
    a = _doc(PR(R("Text") + FNREF(1)), **{"word/footnotes.xml": FOOTNOTES({1: "Old note words.\nSecond line."})})
    b = _doc(PR(R("Text") + FNREF(1)), **{"word/footnotes.xml": FOOTNOTES({1: "New note words."})})
    out = _round_trip(a, b)
    assert _revisions(_part(out, "word/footnotes.xml"))


def test_endnote_paragraph_added():
    a = _doc(PR(R("Text") + '<w:r><w:endnoteReference w:id="1"/></w:r>'), **{"word/endnotes.xml": ENDNOTES({1: "One."})})
    b = _doc(PR(R("Text") + '<w:r><w:endnoteReference w:id="1"/></w:r>'),
             **{"word/endnotes.xml": ENDNOTES({1: "One.\nTwo added."})})
    _round_trip(a, b)


def _with_header(body: str, header: str) -> bytes:
    return make_docx({"word/document.xml": DOC(body + SECT(hdr={"default": "rId1"})),
                      "word/_rels/document.xml.rels": RELS({"rId1": "header1.xml"}),
                      "word/header1.xml": HDR(header)})


def test_header_edited():
    out = _round_trip(_with_header(P("Body."), P("Draft one") + P("Confidential")),
                      _with_header(P("Body."), P("Draft two") + P("Extra header line")))
    assert _revisions(_part(out, "word/header1.xml"))


def test_live_field_paragraph_marked_whole():
    a = _with_header(P("Body."), PR(R("Page ") + FLD("PAGE") + R(" draft")))
    b = _with_header(P("Body."), PR(R("Page ") + FLD("PAGE") + R(" final")))
    out = _round_trip(a, b)
    assert _revisions(_part(out, "word/header1.xml"))


def test_formatting_change_is_a_formatting_revision():
    a = _doc(PR(R("Plain ") + R("bold", "<w:b/>") + R(" end.")))
    b = _doc(PR(R("Plain ") + R("bold") + R(" end.")))
    out = _export(a, b)
    assert _part(out).find(f".//{wq('rPrChange')}") is not None
    runs = [r for p in parse_docx(reject_docx(out)).paragraphs() for r in p.runs]
    assert any(r.props.bold and "bold" in r.text for r in runs)
    runs = [r for p in parse_docx(accept_docx(out)).paragraphs() for r in p.runs]
    assert not any(r.props.bold for r in runs)


def test_numbering_removed_is_a_paragraph_revision():
    num = '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
    parts = {"word/numbering.xml": NUMBERING([("decimal", "%1.", 720, 360, None)])}
    a = _doc(P("Item one.", num) + P("Item two.", num), **parts)
    b = _doc(P("Item one.", num) + P("Item two."), **parts)
    out = _export(a, b)
    assert _part(out).find(f".//{wq('pPrChange')}") is not None
    restored = list(parse_docx(reject_docx(out)).paragraphs())
    assert restored[1].num is not None
    assert list(parse_docx(accept_docx(out)).paragraphs())[1].num is None


def test_sources_with_tracked_changes_are_read_as_accepted():
    ins = '<w:ins w:id="1" w:author="X" w:date="2026-01-01T00:00:00Z"><w:r><w:t>added </w:t></w:r></w:ins>'
    dele = '<w:del w:id="2" w:author="X" w:date="2026-01-01T00:00:00Z"><w:r><w:delText>dropped </w:delText></w:r></w:del>'
    a = _doc(PR(R("Start ") + dele + R("end.")))
    b = _doc(PR(R("Start ") + ins + R("end changed.")))
    out = _round_trip(a, b)
    assert all(r.get(wq("author")) == "Delta" for r in _revisions(_part(out)))


def test_pdf_or_text_refused():
    with pytest.raises(NotWord):
        export_tracked(b"%PDF-1.7 nothing", _doc(P("x")), author="Delta", when=WHEN)


def _pairs():
    if not CORPUS.is_dir():
        return []
    out = []
    for a in sorted(CORPUS.glob("*A.docx")):
        b = a.with_name(a.name[:-6] + "B.docx")
        if b.exists():
            out.append((a, b))
    return out


# Word cannot track a section's header/footer references (w:sectPrChange holds no references), so
# a header the Original shows and the Modified's section no longer names cannot come back on Reject:
# its text is still shown as deleted, in the part where it was.
HEADER_REFS_DROPPED = {("gen-hf-B.docx", "gen-hf-A.docx")}


def _no_hf(streams):
    return [s for s in streams if s[0] not in ("header", "footer")]


@pytest.mark.parametrize("pa,pb", _pairs(), ids=lambda p: p.name)
def test_corpus_round_trips_both_ways(pa, pb):
    a, b = pa.read_bytes(), pb.read_bytes()
    for x, y, names in ((a, b, (pa.name, pb.name)), (b, a, (pb.name, pa.name))):
        if names not in HEADER_REFS_DROPPED:
            _round_trip(x, y)
            continue
        out = _export(x, y)
        assert _streams(accept_docx(out)) == _streams(accept_docx(y))
        assert _no_hf(_streams(reject_docx(out))) == _no_hf(_streams(accept_docx(x)))
