from delta.docx.numbering import fmt_num


def test_letters_past_z_repeat_the_letter_as_word_prints_them():
    assert [fmt_num(n, "lowerLetter") for n in (1, 26, 27, 28, 52, 53, 79)] == \
        ["a", "z", "aa", "bb", "zz", "aaa", "aaaa"]
    assert fmt_num(28, "upperLetter") == "BB"
