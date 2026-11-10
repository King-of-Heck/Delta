from delta.diff.compare import compare
from delta.reader import read_document
from delta.testing.makedocx import DOC, P, make_docx
from delta.textread import TEXT_MAX_BYTES, compare_texts, read_text


def _texts(doc):
    return [p.text for p in doc.paragraphs()]


def test_each_non_blank_line_is_one_paragraph():
    doc = read_text("  First line \r\n\r\nSecond\rThird\n\n\n")
    assert _texts(doc) == ["First line", "Second", "Third"]
    assert doc.source_kind == "text" and len(doc.sections) == 1


def test_empty_text_is_an_empty_document():
    assert _texts(read_text("")) == [] and _texts(read_text(" \n\t\n")) == []


def test_a_typed_number_stays_text():
    (p,) = read_text("1. The term").paragraphs()
    assert p.num is None and p.text == "1. The term"


def test_compare_texts_gives_rows_and_counts():
    d = compare_texts("The fee is 10 days.\nKeep this.", "The fee is 30 days.\nKeep this.\nNew line.")
    assert d["summary"]["total"] == 3 and d["summary"]["insertions"] == 2 and d["summary"]["deletions"] == 1
    assert d["rows"] == [
        {"type": "changed", "segments": [{"m": "eq", "t": "The fee is "}, {"m": "del", "t": "10"},
                                         {"m": "eq", "t": " "}, {"m": "ins", "t": "30"},
                                         {"m": "eq", "t": " days."}]},
        {"type": "equal", "segments": [{"m": "eq", "t": "Keep this."}]},
        {"type": "inserted", "segments": [{"m": "ins", "t": "New line."}]},
    ]


def test_swapped_sides_count_the_same():
    a, b = "Alpha beta gamma.\nOne.", "Alpha delta gamma, epsilon.\nTwo."
    assert compare_texts(a, b)["summary"]["total"] == compare_texts(b, a)["summary"]["total"]


def test_ignore_case():
    assert compare_texts("Hello World", "hello world")["summary"]["total"] > 0
    assert compare_texts("Hello World", "hello world", ignore_case=True)["summary"]["total"] == 0


def test_one_side_empty_is_all_inserted():
    d = compare_texts("", "Only here.")
    assert d["summary"]["total"] == 1 and d["rows"] == [{"type": "inserted", "segments": [{"m": "ins", "t": "Only here."}]}]


def test_pasted_text_counts_as_the_same_word_document_does():
    a_lines = ["The Supplier shall deliver within 10 days.", "Payment is due monthly.", "Old clause."]
    b_lines = ["The Supplier shall deliver within 30 business days.", "Payment is due monthly.", "A new clause."]
    a_doc = read_document(make_docx({"word/document.xml": DOC("".join(P(t) for t in a_lines))}))
    b_doc = read_document(make_docx({"word/document.xml": DOC("".join(P(t) for t in b_lines))}))
    want = compare(a_doc, b_doc, count_numbering=False).to_dict()
    got = compare_texts("\n".join(a_lines), "\n".join(b_lines))
    assert got["summary"] == want["summary"]
    def side(rows, drop):              # same words each side; only the shown separating space differs
        return [" ".join("".join(s["t"] for s in r["segments"] if s["m"] != drop).split()) for r in rows]
    for drop in ("ins", "del"):
        assert side(got["rows"], drop) == side(want["changes"], drop)


def test_the_cap_is_200_kb():
    assert TEXT_MAX_BYTES == 200 * 1024


def test_redline_html_escapes_and_marks():
    from delta.textread import redline_html
    rows = [{"segments": [{"m": "eq", "t": "a<b "}, {"m": "del", "t": "x"}, {"m": "ins", "t": "y"}]}]
    assert redline_html(rows) == '<p>a&lt;b <s style="color:#ff0000">x</s><u style="color:#0000ff">y</u></p>'


def _segs(a, b):
    from delta.textread import compare_texts
    return [(s["m"], s["t"]) for r in compare_texts(a, b)["rows"] for s in r["segments"]]


def test_a_replaced_word_is_kept_apart_by_a_plain_space():
    assert _segs("this is not interesting", "this is very interesting") == [
        ("eq", "this is "), ("del", "not"), ("eq", " "), ("ins", "very"), ("eq", " interesting")]
    assert _segs("the fee is 10 days", "the fee is 30 business days") == [
        ("eq", "the fee is "), ("del", "10"), ("eq", " "), ("ins", "30 business"), ("eq", " days")]
    assert _segs("this is not.", "this is very.") == [
        ("eq", "this is "), ("del", "not"), ("eq", " "), ("ins", "very"), ("eq", ".")]


def test_no_space_is_added_where_the_words_touch_punctuation_on_both_sides():
    assert _segs("(not)", "(very)") == [("eq", "("), ("del", "not"), ("ins", "very"), ("eq", ")")]


def test_lone_insertions_and_deletions_are_unchanged():
    assert _segs("this is not interesting", "this is interesting") == [
        ("eq", "this is "), ("del", "not "), ("eq", "interesting")]
    assert _segs("this is interesting", "this is very interesting") == [
        ("eq", "this is "), ("ins", "very "), ("eq", "interesting")]
