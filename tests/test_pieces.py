import io

from delta.diff.compare import compare
from delta.docx.parser import parse_docx
from delta.layout.pieces import LayoutOptions, Piece, merge_pieces, row_pieces, visible
from delta.model import NoteRef
from delta.testing.makedocx import DOC, P, PR, R, make_docx


def _cmp(body_a, body_b):
    a = parse_docx(io.BytesIO(make_docx({"word/document.xml": DOC(body_a)})))
    b = parse_docx(io.BytesIO(make_docx({"word/document.xml": DOC(body_b)})))
    return compare(a, b)


def _text(pieces, modes):
    # the plain space drawn between a replaced word and its replacement is in neither text
    return "".join(p.text for p in pieces if p.mode in modes).replace("  ", " ")


def test_pieces_reassemble_both_sides_of_a_changed_row():
    c = _cmp(PR(R("The ") + R("quick", "<w:i/>") + R(" brown fox")),
             PR(R("The ") + R("slow", "<w:i/>") + R(" brown ") + R("fox", "<w:b/>")))
    (row,) = c.rows
    assert row.type == "changed"
    ps = row_pieces(c, row, LayoutOptions())
    assert _text(ps, {"del", "eq"}) == "The quick brown fox"
    assert _text(ps, {"ins", "eq"}) == "The slow brown fox"
    assert [p.italic for p in ps if p.text == "quick"] == [True]
    assert [p.italic for p in ps if p.text == "slow"] == [True]
    assert [p.bold for p in ps if p.text == "fox"] == [True]
    assert {p.cid for p in ps if p.mode == "del"} == {1}
    assert {p.cid for p in ps if p.mode == "ins"} == {2}
    assert all(p.cid is None for p in ps if p.mode == "eq")


def test_dense_rewrite_duplicates_the_shared_connective_into_both_sides():
    # coalesce() folds " and " into the struck and the inserted phrase; the two cursors must
    # both advance over it, or every later offset would be wrong.
    c = _cmp(P("Red and blue cars are parked outside."), P("Green and yellow cars are parked outside."))
    (row,) = c.rows
    ps = row_pieces(c, row, LayoutOptions())
    assert _text(ps, {"del", "eq"}) == "Red and blue cars are parked outside."
    assert _text(ps, {"ins", "eq"}) == "Green and yellow cars are parked outside."


def test_deleted_row_takes_formatting_from_the_original():
    c = _cmp(P("Keep") + P("Gone away", rpr='<w:u w:val="single"/>'), P("Keep"))
    row = c.rows[1]
    assert row.type == "deleted"
    (p,) = row_pieces(c, row, LayoutOptions())
    assert p.mode == "del" and p.underline and p.text == "Gone away"


def test_equal_row_with_a_formatting_change_flags_the_range():
    c = _cmp(P("Bold words appear here"), PR(R("Bold ") + R("words", "<w:b/>") + R(" appear here")))
    (row,) = c.rows
    assert row.fmt_changed
    ps = row_pieces(c, row, LayoutOptions())
    assert [(p.text, p.bold, p.fmt) for p in ps] == [("Bold ", False, False), ("words", True, True),
                                                     (" appear here", False, False)]
    off = row_pieces(c, row, LayoutOptions(show_formatting=False))
    assert not any(p.fmt for p in off) and [p.bold for p in off] == [False, True, False]


def test_hidden_modes_drop_their_pieces():
    c = _cmp(P("alpha beta gamma"), P("alpha delta gamma"))
    (row,) = c.rows
    no_del = row_pieces(c, row, LayoutOptions(show_deletions=False))
    assert _text(no_del, {"del"}) == "" and _text(no_del, {"ins", "eq"}) == "alpha delta gamma"
    no_ins = row_pieces(c, row, LayoutOptions(show_insertions=False))
    assert _text(no_ins, {"ins"}) == "" and _text(no_ins, {"del", "eq"}) == "alpha beta gamma"


def test_size_font_and_colour_ride_on_pieces():
    c = _cmp(P("x"), P("x") + P("Big red", rpr='<w:sz w:val="28"/><w:color w:val="FF0000"/><w:rFonts w:ascii="Arial"/>'))
    (p,) = row_pieces(c, c.rows[1], LayoutOptions())
    assert (p.mode, p.size, p.color, p.font) == ("ins", 14.0, "ff0000", "Arial")


def test_merge_pieces_joins_same_style_neighbours():
    a = Piece("ab", "eq", cid=1)
    b = Piece("cd", "eq", cid=1)
    other = Piece("ef", "ins", cid=1)
    assert [p.text for p in merge_pieces([a, b, other])] == ["abcd", "ef"]


def test_visible_follows_the_show_toggles():
    c = _cmp(P("same") + P("gone") + P("old text here"), P("same") + P("new text here") + P("added"))
    eq, dele, chg, ins = c.rows
    assert (eq.type, dele.type, chg.type, ins.type) == ("equal", "deleted", "changed", "inserted")
    assert visible(eq, LayoutOptions()) and not visible(eq, LayoutOptions(show_equal=False))
    assert not visible(dele, LayoutOptions(show_deletions=False))
    assert not visible(ins, LayoutOptions(show_insertions=False))
    assert visible(chg, LayoutOptions(show_insertions=False))
    assert not visible(chg, LayoutOptions(show_insertions=False, show_deletions=False))


def test_numbering_or_formatting_change_keeps_an_equal_row_visible_under_hide_unchanged():
    c = _cmp(P("Bold words appear here"), PR(R("Bold ") + R("words", "<w:b/>") + R(" appear here")))
    (row,) = c.rows
    assert visible(row, LayoutOptions(show_equal=False))
    assert not visible(row, LayoutOptions(show_equal=False, show_formatting=False))


def test_caps_reach_the_pieces_as_one_flag():
    c = _cmp(PR(R("Engineering", "<w:caps/>") + R(" Project", "<w:smallCaps/>") + R(" plain")),
             PR(R("Engineering", "<w:caps/>") + R(" Project", "<w:smallCaps/>") + R(" plain")))
    (row,) = c.rows
    ps = row_pieces(c, row, LayoutOptions())
    assert [(p.text, p.caps) for p in ps] == [("Engineering Project", True), (" plain", False)]


def test_tabs_become_pieces_of_their_own():
    c = _cmp(PR('<w:r><w:tab/><w:t>1.1</w:t><w:tab/><w:t>Definitions</w:t><w:tab/><w:t>2</w:t></w:r>'),
             PR('<w:r><w:tab/><w:t>1.1</w:t><w:tab/><w:t>Definitions</w:t><w:tab/><w:t>2</w:t></w:r>'))
    (row,) = c.rows
    ps = row_pieces(c, row, LayoutOptions())
    assert [(p.text, p.tab) for p in ps] == [("", 1), ("1.1", 0), (" ", 1), ("Definitions", 0), (" ", 1), ("2", 0)]
    assert ps[0].mode == "eq"


def test_a_tab_inside_a_change_keeps_its_mode():
    c = _cmp(PR(R("Name")), PR('<w:r><w:t>Name</w:t><w:tab/><w:t>Value</w:t></w:r>'))
    (row,) = c.rows
    ps = row_pieces(c, row, LayoutOptions())
    assert [(p.text, p.mode, p.tab) for p in ps] == [("Name", "eq", 0), (" ", "ins", 1), ("Value", "ins", 0)]


def test_style_bold_reaches_the_piece_as_bold_and_is_not_a_formatting_change():
    from delta.testing.makedocx import STYLES, W_NS
    sty = STYLES(styles=f'<w:style w:type="paragraph" w:styleId="TOC1"><w:rPr><w:b/></w:rPr></w:style>')
    body = PR(R("Section 1") + R(" plain", '<w:b w:val="0"/>'), ppr='<w:pStyle w:val="TOC1"/>')
    a = parse_docx(io.BytesIO(make_docx({"word/document.xml": DOC(body), "word/styles.xml": sty})))
    b = parse_docx(io.BytesIO(make_docx({"word/document.xml": DOC(body)})))    # no style: not bold
    c = compare(a, a)
    (row,) = c.rows
    assert not row.fmt_changed
    ps = row_pieces(c, row, LayoutOptions())
    assert [(p.text, p.bold) for p in ps] == [("Section 1", True), (" plain", False)]
    c2 = compare(a, b)                            # only the style's bold differs: no change at all
    (row2,) = c2.rows
    assert row2.type == "equal" and not row2.fmt_changed


def test_reference_marks_are_raised_pieces_with_the_note_number_and_mode():
    from delta.testing.makedocx import FNREF, FOOTNOTES, PR, R
    fa = {"word/footnotes.xml": FOOTNOTES({1: "Old note.", 2: "Kept."})}
    fb = {"word/footnotes.xml": FOOTNOTES({2: "Kept.", 3: "New note."})}
    a = parse_docx(io.BytesIO(make_docx({"word/document.xml": DOC(PR(R("Text") + FNREF(1) + R(" more") + FNREF(2))), **fa})))
    b = parse_docx(io.BytesIO(make_docx({"word/document.xml": DOC(PR(R("Text") + FNREF(2) + R(" more") + FNREF(3))), **fb})))
    c = compare(a, b)
    row = c.rows[0]
    assert row.type == "equal"
    ps = row_pieces(c, row, LayoutOptions())
    # the revised document's marks: note 2 is its first footnote (shared, eq), note 3 its second
    # (inserted); the original's dropped note 1 keeps its deleted mark where it stood
    assert [(p.text, p.rise, p.mode) for p in ps] == [("Text", False, "eq"), ("1", True, "eq"), ("1", True, "del"),
                                                     (" more", False, "eq"), ("2", True, "ins")]
    ps_o = row_pieces(c, row, LayoutOptions(side="original"))
    assert [(p.text, p.rise, p.mode) for p in ps_o] == [("Text", False, "eq"), ("1", True, "del"), (" more", False, "eq"),
                                                       ("2", True, "eq")]
    assert c.note_modes == {("a", NoteRef("footnote", 1)): "del", ("a", NoteRef("footnote", 2)): "eq",
                            ("b", NoteRef("footnote", 2)): "eq", ("b", NoteRef("footnote", 3)): "ins"}


def test_a_mark_inside_a_deleted_segment_comes_from_the_original():
    from delta.testing.makedocx import FNREF, FOOTNOTES, PR, R
    fa = {"word/footnotes.xml": FOOTNOTES({1: "Gone."})}
    a = parse_docx(io.BytesIO(make_docx({"word/document.xml": DOC(PR(R("Keep all of this shared text") + FNREF(1)
                                                                       + R(" and drop that"))), **fa})))
    b = parse_docx(io.BytesIO(make_docx({"word/document.xml": DOC(PR(R("Keep all of this shared text and add this")))})))
    c = compare(a, b)
    row, note_row = c.rows                    # the body row, then the deleted note's own row
    assert row.type == "changed" and note_row.type == "deleted"
    ps = row_pieces(c, row, LayoutOptions())
    marks = [(p.text, p.mode) for p in ps if p.rise]
    assert marks == [("1", "del")]


def test_field_run_is_its_own_piece_carrying_the_field():
    from delta.testing.makedocx import FLD, PR, R
    import io
    from delta.docx.parser import parse_docx
    from delta.testing.makedocx import DOC, make_docx
    from delta.diff.compare import compare
    from delta.layout.pieces import LayoutOptions, row_pieces
    d = parse_docx(io.BytesIO(make_docx({"word/document.xml": DOC(PR(R("Page ") + FLD("PAGE", "7") + R(" end")))})))
    c = compare(d, d)
    ps = row_pieces(c, c.rows[0], LayoutOptions())
    assert [(p.text, p.field) for p in ps] == [("Page ", None), ("{PAGE}", "PAGE"), (" end", None)]


def _drawn(pieces):
    return [(p.mode, p.text) for p in pieces]


def test_a_replaced_word_is_kept_apart_from_its_replacement_by_a_plain_space():
    c = _cmp(P("this is not interesting"), P("this is very interesting"))
    (row,) = c.rows
    assert _drawn(row_pieces(c, row, LayoutOptions())) == [
        ("eq", "this is "), ("del", "not"), ("eq", " "), ("ins", "very"), ("eq", " interesting")]
    c = _cmp(P("this is not."), P("this is very."))
    (row,) = c.rows
    assert _drawn(row_pieces(c, row, LayoutOptions())) == [
        ("eq", "this is "), ("del", "not"), ("eq", " "), ("ins", "very"), ("eq", ".")]


def test_the_plain_space_takes_the_formatting_of_the_word_before_it_and_no_change_number():
    c = _cmp(PR(R("this is ") + R("not", "<w:b/>") + R(" interesting")),
             PR(R("this is ") + R("very", "<w:b/>") + R(" interesting")))
    (row,) = c.rows
    gap = row_pieces(c, row, LayoutOptions())[2]
    assert (gap.mode, gap.text, gap.bold, gap.cid) == ("eq", " ", True, None)


def test_no_plain_space_where_one_side_is_hidden_or_the_words_touch_punctuation():
    c = _cmp(P("this is not interesting"), P("this is very interesting"))
    (row,) = c.rows
    assert _drawn(row_pieces(c, row, LayoutOptions(show_deletions=False))) == [
        ("eq", "this is "), ("ins", "very"), ("eq", " interesting")]
    assert _drawn(row_pieces(c, row, LayoutOptions(side="modified", show_deletions=False))) == [
        ("eq", "this is "), ("ins", "very"), ("eq", " interesting")]
    c = _cmp(P("(not)"), P("(very)"))
    (row,) = c.rows
    assert ("eq", " ") not in _drawn(row_pieces(c, row, LayoutOptions()))
