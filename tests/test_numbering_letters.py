from delta.docx.numbering import fmt_num
from delta.reader import read_document
from delta.testing.makedocx import DOC, P, make_docx


def _listnum_docx(instr):
    numbering = ('<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 '<w:abstractNum w:abstractNumId="0"><w:name w:val="Standard"/>'
                 '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>'
                 '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="(%2)"/></w:lvl>'
                 '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')
    num = lambda lvl: f'<w:numPr><w:ilvl w:val="{lvl}"/><w:numId w:val="1"/></w:numPr>'
    field = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
             f'<w:r><w:instrText xml:space="preserve">{instr}</w:instrText></w:r>'
             '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
    body = (f'<w:p><w:pPr>{num(0)}</w:pPr>{field}<w:r><w:t xml:space="preserve"> first</w:t></w:r></w:p>'
            + P("second", num(1)))
    return make_docx({"word/document.xml": DOC(body), "word/numbering.xml": numbering})


def test_a_listnum_field_prints_its_number_and_counts():
    first, second = read_document(_listnum_docx(' LISTNUM  "Standard" \\l 2')).blocks
    assert (first.num.marker, first.text) == ("1.", "(a) first")
    assert second.num.marker == "(b)"


def _doc_with(field):
    numbering = ('<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 '<w:abstractNum w:abstractNumId="0"><w:name w:val="S"/>'
                 '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="(%1)"/></w:lvl>'
                 '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')
    body = (f'<w:p>{field}<w:r><w:t xml:space="preserve"> one</w:t></w:r></w:p>'
            f'<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> LISTNUM "S" \\l 1</w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r><w:r><w:t xml:space="preserve"> two</w:t></w:r></w:p>')
    return [p.text for p in read_document(make_docx({"word/document.xml": DOC(body), "word/numbering.xml": numbering})).blocks]


def test_a_listnums_cached_result_is_not_printed_as_well():
    field = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> LISTNUM "S" \\l 1</w:instrText></w:r>'
             '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>(z)</w:t></w:r>'
             '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
    assert _doc_with(field) == ["(a) one", "(b) two"]


def test_a_simple_listnum_field_counts():
    field = '<w:fldSimple w:instr=" LISTNUM &quot;S&quot; \\l 1"><w:r><w:t>(z)</w:t></w:r></w:fldSimple>'
    assert _doc_with(field) == ["(a) one", "(b) two"]


def test_a_listnum_start_switch_sets_the_number():
    field = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> LISTNUM "S" \\l 1 \\s 5</w:instrText></w:r>'
             '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
    assert _doc_with(field) == ["(e) one", "(f) two"]


def test_a_listnum_of_an_unknown_list_prints_nothing():
    first, second = read_document(_listnum_docx(' LISTNUM  "Other" \\l 2')).blocks
    assert first.text == "first"
    assert second.num.marker == "(a)"


def test_letters_past_z_repeat_the_letter_as_word_prints_them():
    assert [fmt_num(n, "lowerLetter") for n in (1, 26, 27, 28, 52, 53, 79)] == \
        ["a", "z", "aa", "bb", "zz", "aaa", "aaaa"]
    assert fmt_num(28, "upperLetter") == "BB"
