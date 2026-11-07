"""ParaMap: a paragraph's runs tied to the compared text."""
from lxml import etree

from delta.docx.ns import wq
from delta.docx.trackmap import ParaMap, run_text, slice_run
from delta.testing.makedocx import FLD, R, W_NS


def _p(inner: str):
    return etree.fromstring(f'<w:p xmlns:w="{W_NS}">{inner}</w:p>'.encode())


def _texts(runs) -> list[str]:
    return [run_text(r) for r in runs]


def test_text_and_offsets_over_runs_tabs_and_links():
    p = _p(R("  Hello ") + '<w:hyperlink><w:r><w:t>big</w:t><w:tab/><w:t>world</w:t></w:r></w:hyperlink>' + R("  "))
    m = ParaMap.of(p, "Hello big world")
    assert m is not None
    assert m.raw == "  Hello big\tworld  "
    assert m.raw_pos(0) == 0 and m.raw_pos(6) == 8 and m.raw_pos(len(m.text)) == len(m.raw) - 2


def test_mismatched_text_has_no_map():
    assert ParaMap.of(_p(R("one")), "two") is None


def test_live_fields_have_no_map():
    assert ParaMap.of(_p(R("Page ") + FLD("PAGE")), "Page {PAGE}") is None
    p = _p('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> NUMPAGES </w:instrText></w:r>'
           '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>3</w:t></w:r>'
           '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
    assert ParaMap.of(p, "{NUMPAGES}") is None


def test_split_keeps_formatting_and_text():
    p = _p(R("abcdef", "<w:b/>"))
    m = ParaMap.of(p, "abcdef")
    m.split([2, 4])
    runs = p.findall(wq("r"))
    assert _texts(runs) == ["ab", "cd", "ef"]
    assert all(r.find(f"{wq('rPr')}/{wq('b')}") is not None for r in runs)
    assert [r for r, _, _ in m.runs] == runs
    assert _texts(m.runs_in(2, 4)) == ["cd"]


def test_slice_puts_zero_width_marks_with_the_right_slice():
    r = etree.fromstring(f'<w:r xmlns:w="{W_NS}"><w:t>ab</w:t><w:footnoteReference w:id="1"/></w:r>'.encode())
    assert slice_run(r, 0, 2).find(wq("footnoteReference")) is not None
    assert slice_run(r, 0, 1).find(wq("footnoteReference")) is None


def test_copy_range_and_insertion_point():
    p = _p(R("one ") + R("two ", "<w:i/>") + R("three"))
    m = ParaMap.of(p, "one two three")
    copies = m.copy_range(2, 6)
    assert _texts(copies) == ["e ", "tw"]
    assert copies[1].find(f"{wq('rPr')}/{wq('i')}") is not None
    parent, i = m.point(4)
    assert parent is p and i == 1
    parent, i = m.point(99)
    assert i == 3


def _fld(kind):
    return f'<w:r><w:fldChar w:fldCharType="{kind}"/></w:r>'


def test_copy_range_keeps_whole_fields_and_drops_cut_ones():
    field = _fld("begin") + '<w:r><w:instrText> REF _Ref1 </w:instrText></w:r>' + _fld("separate") + \
        R("2.15(c)") + _fld("end")
    p = _p(R("Section ") + field + R(", Contractor"))
    m = ParaMap.of(p, "Section 2.15(c), Contractor")
    whole = m.copy_range(8, 15)                 # exactly the field's result: the whole field comes along
    kinds = [x.get(wq("fldCharType")) for r in whole for x in r if x.tag == wq("fldChar")]
    assert kinds == ["begin", "separate", "end"] and "".join(run_text(r) for r in whole) == "2.15(c)"
    part = m.copy_range(12, 20)                 # "(c), Con": the field is cut, only its text is kept
    assert not any(x.tag in (wq("fldChar"), wq("instrText")) for r in part for x in r)
    assert "".join(run_text(r) for r in part) == "(c), Con"
