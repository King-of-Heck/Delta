from delta.pdfread.lines import build_lines
from delta.pdfread.types import TextRun


def R(text, x0, x1, y=100.0, size=12.0, font="Arial"):
    return TextRun(text, x0, x1, y, size, font)


def test_runs_on_one_baseline_make_one_line_left_to_right():
    (ln,) = build_lines([R("world", 110, 140), R("Hello ", 72, 110)], page=3, cell=(0, 2))
    assert (ln.text, ln.x0, ln.x1, ln.y, ln.size, ln.page, ln.cell) == ("Hello world", 72, 140, 100, 12, 3, (0, 2))
    assert len(ln.spans) == 1


def test_two_baselines_are_two_lines_in_reading_order():
    a, b = build_lines([R("second", 72, 110, y=114), R("first", 72, 100, y=100)])
    assert (a.text, b.text) == ("first", "second")


def test_a_format_change_starts_a_new_span_and_sizes_round_to_half_points():
    (ln,) = build_lines([R("plain ", 72, 110, size=11.04), R("bold", 110, 140, size=11.04, font="Arial,Bold")])
    assert [(s.text, s.font, s.size) for s in ln.spans] == [("plain ", "Arial", 11.0), ("bold", "Arial,Bold", 11.0)]


def test_a_raised_smaller_run_is_a_superscript_on_its_line():
    (ln,) = build_lines([R("1", 130, 134, y=95.5, size=8), R("the Supplier", 72, 130), R(" shall", 134, 160)])
    assert [(s.text, s.sup) for s in ln.spans] == [("the Supplier", False), ("1", True), (" shall", False)]
    assert (ln.y, ln.size) == (100, 12)


def test_word_positioned_runs_without_space_characters_get_spaces():
    (ln,) = build_lines([R("The", 72, 90), R("quick", 94, 120), R("fox", 124, 140)])
    assert ln.text == "The quick fox"


def test_no_second_space_where_one_is_already_there_and_kerning_gaps_add_none():
    (ln,) = build_lines([R("The ", 72, 94), R("quick", 94.5, 120), R("ly", 120.4, 130)])
    assert ln.text == "The quickly"


def test_a_wide_gap_is_a_tab_and_replaces_trailing_spaces():
    (ln,) = build_lines([R("12.3 ", 72, 96), R("Payment", 126, 170)])
    assert ln.text == "12.3\tPayment"
    (ln,) = build_lines([R("Name:", 72, 100), R("Title:", 300, 330)])
    assert ln.text == "Name:\tTitle:"


def test_text_drawn_twice_for_fake_bold_is_read_once():
    (ln,) = build_lines([R("Heading", 72, 120), R("Heading", 72.3, 120.3)])
    assert ln.text == "Heading"


def test_a_redrawn_run_is_measured_against_its_own_font_size():
    (ln,) = build_lines([R("Heading", 72, 120, size=24), R("Heading", 73.0, 121.0, size=24)])
    assert ln.text == "Heading"
    (ln,) = build_lines([R("x", 72, 75, size=6), R("x", 72.5, 75.5, size=6)])
    assert ln.text.count("x") == 2


def test_blank_lines_are_dropped_and_edges_ignore_outer_whitespace():
    lines = build_lines([R("   ", 72, 80, y=50), R(" padded ", 72, 120)])
    assert [ln.text for ln in lines] == ["padded"]


def test_a_line_ends_where_its_ink_ends_not_after_trailing_spaces():
    (ln,) = build_lines([R("ends here.", 72, 120), TextRun("  More ", 120, 160, 100.0, 12.0, "Arial", ink_x1=154)])
    assert (ln.x0, ln.x1) == (72, 154)
    (ln,) = build_lines([R("no ink end given", 72, 150)])
    assert ln.x1 == 150


def test_a_raised_number_after_a_larger_glyph_is_a_superscript_even_when_it_is_wider():
    """A placeholder bullet with a footnote mark on it: the 6.5 pt digits cover more width than the
    10 pt bullet, but they are the mark, raised over the bullet's baseline, not the line's text."""
    (ln,) = build_lines([R("", 113.7, 118.2, y=164.4, size=10, font="Symbol"),
                         R("97", 118.2, 125.4, y=160.9, size=6.5)])
    assert [(s.text, s.sup) for s in ln.spans] == [("", False), ("97", True)]
    assert (ln.y, ln.size) == (164.4, 10)


def test_letters_drawn_one_by_one_with_their_spaces_drawn_get_no_spaces_from_small_gaps():
    """Word's export can draw every letter on its own with small adjustments between letters (up
    to about 0.21 em); it then draws each word space as a character, so a gap that small is not a
    space. 'Indem' 'nifying' was read with a space where 'm' ends 0.13 em before 'n'."""
    letters = [("t", 72.0, 74.7), ("h", 74.7, 80.1), ("e", 80.1, 85.5), (" ", 85.5, 88.2), ("I", 89.9, 92.6),
               ("n", 92.6, 98.0), ("d", 98.0, 103.4), ("e", 103.4, 108.8), ("m", 108.8, 116.9), ("n", 118.2, 123.6),
               ("i", 123.6, 125.8), ("f", 125.8, 128.5), ("y", 129.5, 134.4), ("i", 134.4, 136.6), ("n", 136.6, 142.0),
               ("g", 142.0, 147.4)]
    (ln,) = build_lines([R(t, x0, x1, size=9.75) for t, x0, x1 in letters])
    assert ln.text == "the Indemnifying"
    (gap,) = build_lines([R("the", 72, 85.5, size=9.75), R(" ", 85.5, 88.2, size=9.75), R("Goods", 89, 115, size=9.75),
                          R("Buyer", 119, 145, size=9.75)])
    assert gap.text == "the Goods Buyer"              # a word gap with no space drawn is still a space


def test_a_line_knows_whether_its_last_drawn_text_was_a_space():
    (ln,) = build_lines([R("at least A-", 72, 120), R(" ", 120, 123)])
    assert ln.text == "at least A-" and ln.ends_spaced
    (ln,) = build_lines([R("a non-", 72, 100)])
    assert not ln.ends_spaced


def test_a_run_joins_a_row_by_its_largest_run_even_when_that_run_came_later():
    # "c" sits 7.5 pt under "B" (within 0.45 of 20 pt) but 8.5 pt under "a" (beyond 0.45 of 10 pt)
    (ln,) = build_lines([R("a", 72, 80, y=100, size=10), R("B", 80, 100, y=101, size=20),
                         R("c", 100, 108, y=108.5, size=10)])
    assert ln.text == "aBc"


def test_runs_at_the_same_left_edge_keep_their_drawing_order():
    (ln,) = build_lines([R("a", 72, 80, y=100.0), R("b", 72, 80, y=100.1)])
    assert ln.text == "ab"
    (ln,) = build_lines([R("b", 72, 80, y=100.1), R("a", 72, 80, y=100.0)])
    assert ln.text == "ab"


def test_a_line_spans_its_inked_runs_only():
    (ln,) = build_lines([R(" ", 60, 72), R("word", 72, 100), R(" ", 100, 104)])
    assert (ln.text, ln.x0, ln.x1, ln.ends_spaced) == ("word", 72, 100, True)
