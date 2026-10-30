from delta.mixed import level
from delta.model import Document, NoteRef, NumInfo, Paragraph, ParaProps, Run, RunProps
from delta.reader import documents, read_source
from delta.testing.makedocx import DOC, make_docx


def _para(text, num=None, style=None, **props):
    return Paragraph([Run(text, RunProps(**props))], ParaProps(style_id=style), num)


def _word(*blocks):
    return Document(blocks=list(blocks), sections=[])


def _pdf(*blocks):
    return Document(blocks=list(blocks), sections=[], source_kind="pdf")


def _num(marker):
    return NumInfo(1, 0, marker, False)


def test_a_pdf_paragraphs_leading_number_becomes_its_numbering():
    _, b = level(_word(_para("Intent", _num("2.1"))), _pdf(_para("2.1 Intent")))
    (p,) = b.blocks
    assert (p.num.marker, p.text) == ("2.1", "Intent")


def test_a_number_typed_in_word_is_read_by_the_same_rule():
    a, _ = level(_word(_para("(a) Typed")), _pdf(_para("(a) Typed")))
    assert (a.blocks[0].num.marker, a.blocks[0].text) == ("(a)", "Typed")


def test_a_lifted_number_takes_words_spelling():
    _, b = level(_word(_para("GENERAL", _num("Section 3"))), _pdf(_para("SECTION 3 GENERAL")))
    assert (b.blocks[0].num.marker, b.blocks[0].text) == ("Section 3", "GENERAL")


def test_a_worded_number_new_to_the_pdf_is_still_a_number():
    _, b = level(_word(_para("GENERAL", _num("Section 3"))), _pdf(_para("SECTION 17 NEW")))
    assert b.blocks[0].num.marker == "SECTION 17"


def test_ordinary_opening_words_are_not_numbers():
    _, b = level(_word(_para("x")), _pdf(_para("A contractor shall"), _para("2023 was a year")))
    assert [p.num for p in b.blocks] == [None, None]


def test_a_footnote_mark_after_the_number_survives():
    ref = NoteRef("footnote", 1)
    p = Paragraph([Run("2.1 ", RunProps()), Run("", RunProps(note=ref)), Run("Intent", RunProps())], ParaProps())
    _, b = level(_word(_para("x")), _pdf(p))
    assert b.blocks[0].text == "Intent"
    assert any(r.props.note == ref for r in b.blocks[0].runs)


def test_word_capitals_formatting_compares_as_printed():
    a, _ = level(_word(_para("Interpretation", caps=True)), _pdf(_para("INTERPRETATION")))
    assert a.blocks[0].text == "INTERPRETATION"


def test_a_word_contents_lines_page_number_is_the_pdfs_token():
    a, _ = level(_word(_para("Intent\t24", style="TOC2")), _pdf(_para("x")))
    assert a.blocks[0].text.endswith("{PAGEREF}")
    assert a.blocks[0].runs[-1].props.field_text == "24"


def test_bullet_forms_are_one_character():
    a, b = level(_word(_para("� PROJECT")), _pdf(_para(" PROJECT")))
    assert a.blocks[0].text == b.blocks[0].text == "• PROJECT"


def test_headers_and_comments_are_left_out_and_the_source_is_untouched():
    w = _word(_para("(a) x"))
    w.parts = {"header1.xml": []}
    w.comments = {"1": object()}
    a, _ = level(w, _pdf(_para("x")))
    assert a.parts == {} and a.comments == {} and a.comment_anchors == {}
    assert w.parts and w.comments and w.blocks[0].num is None


def test_either_order_gives_the_same_result():
    x, y = _word(_para("Intent", _num("2.1"))), _pdf(_para("2.1 Intent"))
    b, a = level(y, x)
    assert b.blocks[0].num.marker == "2.1" and a.source_kind == "docx"


def test_a_word_cell_is_split_at_its_line_breaks():
    cell = "<w:p><w:r><w:t>Name:</w:t><w:br/><w:t>Title:</w:t></w:r></w:p>"
    body = f"<w:tbl><w:tr><w:tc>{cell}</w:tc></w:tr></w:tbl>"
    (w,) = documents(read_source(make_docx({"word/document.xml": DOC(body)})))
    a, _ = level(w, _pdf(_para("x")))
    assert [p.text for p in a.blocks[0].rows[0].cells[0].blocks] == ["Name:", "Title:"]
