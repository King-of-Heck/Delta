"""Accepting the tracked revisions already inside a part, in place (the export's first step)."""
from lxml import etree

from delta.docx.ns import wq
from delta.docx.revisions import REVISION_TAGS, accept_revisions
from delta.testing.makedocx import DOC, P, R

A = 'w:id="9" w:author="A" w:date="2026-09-17T00:00:00Z"'


def _accept(body: str):
    root = etree.fromstring(DOC(body).encode())
    accept_revisions(root)
    return root


def _texts(root) -> list[str]:
    return ["".join(t.text or "" for t in p.iter(wq("t"))) for p in root.iter(wq("p"))]


def test_inserted_runs_kept_and_deleted_runs_dropped():
    root = _accept(f'<w:p>{R("a ")}<w:ins {A}>{R("new ")}</w:ins><w:del {A}><w:r><w:delText>old </w:delText>'
                   f'</w:r></w:del>{R("z")}</w:p>')
    assert _texts(root) == ["a new z"]
    assert not any(True for _ in root.iter(*REVISION_TAGS))


def test_moves_take_the_destination():
    root = _accept(f'<w:p><w:moveFrom {A}>{R("x")}</w:moveFrom><w:moveFromRangeStart w:id="1" w:name="m"/>'
                   f'<w:moveTo {A}>{R("y")}</w:moveTo></w:p>')
    assert _texts(root) == ["y"]
    assert root.find(f".//{wq('moveFromRangeStart')}") is None


def test_formatting_changes_dropped_keeping_the_new_formatting():
    root = _accept(f'<w:p><w:pPr><w:jc w:val="center"/><w:pPrChange {A}><w:pPr/></w:pPrChange></w:pPr>'
                   f'<w:r><w:rPr><w:b/><w:rPrChange {A}><w:rPr/></w:rPrChange></w:rPr><w:t>b</w:t></w:r></w:p>')
    assert root.find(f".//{wq('b')}") is not None and root.find(f".//{wq('jc')}") is not None
    assert not any(True for _ in root.iter(*REVISION_TAGS))


def test_deleted_paragraph_mark_joins_the_next_paragraph():
    root = _accept(f'<w:p><w:pPr><w:rPr><w:del {A}/></w:rPr></w:pPr>{R("one ")}</w:p>' + P("two") + P("three"))
    assert _texts(root) == ["one two", "three"]


def test_inserted_mark_is_just_unmarked():
    root = _accept(f'<w:p><w:pPr><w:rPr><w:ins {A}/></w:rPr></w:pPr>{R("one")}</w:p>' + P("two"))
    assert _texts(root) == ["one", "two"]
    assert root.find(f".//{wq('ins')}") is None


def test_table_rows_inserted_kept_deleted_dropped():
    root = _accept(f'<w:tbl><w:tr><w:trPr><w:ins {A}/></w:trPr><w:tc>{P("in")}</w:tc></w:tr>'
                   f'<w:tr><w:trPr><w:del {A}/></w:trPr><w:tc><w:p><w:del {A}><w:r><w:delText>gone</w:delText>'
                   f'</w:r></w:del></w:p></w:tc></w:tr></w:tbl>')
    assert len(root.findall(f".//{wq('tr')}")) == 1
    assert _texts(root) == ["in"]
    assert not any(True for _ in root.iter(*REVISION_TAGS))


def test_numbering_change_dropped():
    root = _accept(f'<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/>'
                   f'<w:numberingChange {A} w:original="%1."/></w:numPr></w:pPr>{R("x")}</w:p>')
    assert root.find(f".//{wq('numberingChange')}") is None
    assert root.find(f".//{wq('numId')}") is not None
