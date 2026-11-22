"""A paragraph split in two (or two joined into one) with its words unchanged: shown and counted as
the paragraph mark that was inserted (or removed), not as the whole paragraph rewritten."""
import io
import json
import re
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import pytest
from lxml import etree

from delta.diff.compare import compare
from delta.diff.units import units
from delta.docx.ns import wq
from delta.docx.parser import parse_docx
from delta.docx.tracked import export_tracked
from delta.layout.engine import layout
from delta.layout.pieces import LayoutOptions
from delta.testing.fakefonts import FakeResolver
from delta.testing.makedocx import DOC, P, STYLES, make_docx
from delta.testing.revise import accept_docx, reject_docx
from delta.textread import compare_texts

FR = FakeResolver()
STY = STYLES('<w:rFonts w:ascii="Fake"/><w:sz w:val="20"/>')
PM = "¶"

WHOLE = "Alpha beta gamma delta."
HALVES = P("Alpha beta") + P("gamma delta.")


def _bytes(body):
    return make_docx({"word/document.xml": DOC(body), "word/styles.xml": STY})


def _doc(body):
    return parse_docx(io.BytesIO(_bytes(body)))


def _cmp(a, b, **kw):
    return compare(_doc(a), _doc(b), **kw)


def _segs(row):
    return [(s.m, s.t, s.pm) for s in row.segments]


def _row_texts(L):
    by_row = {}
    for pg in L.pages:
        for ln in pg.lines:
            if ln.row_index is not None:
                by_row.setdefault(ln.row_index, []).append("".join(r.text for r in ln.runs))
    return [re.sub(r"\s+", " ", " ".join(v)).strip() for _, v in sorted(by_row.items())]


# ---- the comparison ---------------------------------------------------------------------------

def test_a_split_paragraph_is_one_inserted_paragraph_mark():
    c = _cmp(P(WHOLE) + P("Kept."), HALVES + P("Kept."))
    assert [(r.type, r.oi, r.ni, r.join, r.head) for r in c.rows] == [
        ("changed", 0, 0, "split", True), ("equal", 0, 1, "split", False), ("equal", 1, 2, None, False)]
    assert _segs(c.rows[0]) == [("eq", "Alpha beta", False), ("ins", PM, True)]
    assert _segs(c.rows[1]) == [("eq", "gamma delta.", False)]
    s = c.summary
    assert (s["total"], s["insertions"], s["deletions"], s["splits"], s["merges"], s["content"]) == (1, 1, 0, 1, 0, 0)
    assert c.rows[0].cids == [1] and c.rows[0].segments[1].cid == 1


def test_joined_paragraphs_are_one_removed_paragraph_mark_and_count_the_same_swapped():
    c = _cmp(HALVES + P("Kept."), P(WHOLE) + P("Kept."))
    assert [(r.type, r.oi, r.ni, r.join, r.head) for r in c.rows] == [
        ("changed", 0, 0, "merge", True), ("equal", 1, 0, "merge", False), ("equal", 2, 1, None, False)]
    assert _segs(c.rows[0]) == [("eq", "Alpha beta", False), ("del", PM, True), ("eq", " gamma delta.", False)]
    assert _segs(c.rows[1]) == [("eq", "gamma delta.", False)]
    s = c.summary
    assert (s["total"], s["insertions"], s["deletions"], s["splits"], s["merges"]) == (1, 0, 1, 0, 1)


def test_a_split_into_three_is_two_marks():
    c = _cmp(P("One two. Three four. Five six."), P("One two.") + P("Three four.") + P("Five six."))
    assert [(r.join, r.head, r.type) for r in c.rows] == [
        ("split", True, "changed"), ("split", False, "changed"), ("split", False, "equal")]
    assert c.summary["total"] == 2 and c.summary["splits"] == 1


def test_an_empty_paragraph_between_the_halves_still_makes_a_split():
    c = _cmp(P(WHOLE), P("Alpha beta") + P("") + P("gamma delta."))
    assert [r.join for r in c.rows] == ["split", "split"] and c.summary["total"] == 1


def test_halves_that_also_changed_words_show_the_mark_and_the_word_edits():
    c = _cmp(P(WHOLE), P("Alpha beta") + P("gamma delta!"))
    assert [(r.type, r.join, r.head) for r in c.rows] == [("changed", "split", True), ("changed", "split", False)]
    assert _segs(c.rows[0]) == [("eq", "Alpha beta", False), ("ins", PM, True)]
    assert _segs(c.rows[1]) == [("eq", "gamma delta", False), ("del", ".", False), ("ins", "!", False)]
    assert c.rows[1].segments[1].at == (0, len("Alpha beta gamma delta"))
    assert c.summary["total"] == 3 and c.summary["splits"] == 1
    _rebuilds(c)
    c = _cmp(P(WHOLE), P("Alpha") + P("gamma delta."))
    assert [r.join for r in c.rows] == ["split", "split"]
    assert "".join(s.t for r in c.rows for s in r.segments if s.m == "del") .strip() == "beta"
    _rebuilds(c)


def _rebuilds(c):
    """Each split's rows give back both documents: the original from its shared and deleted text
    (with the gaps), each piece from its shared and inserted text; each deleted segment's `at`
    points at its own text."""
    for r in c.rows:
        for s in r.segments:
            if s.m == "del" and not s.pm and r.join:
                u = c.a_units[s.at[0]]
                assert u.text[s.at[1]:s.at[1] + len(s.t)] == s.t
    groups = {}
    for r in c.rows:
        if r.join == "split":
            groups.setdefault(r.oi, []).append(r)
    for oi, rows in groups.items():
        orig = "".join("".join(s.t for s in r.segments if s.m in ("eq", "del") and not s.pm) + r.gap for r in rows)
        assert orig == c.a_units[oi].text
        for r in rows:
            assert "".join(s.t for s in r.segments if s.m in ("eq", "ins") and not s.pm) == c.b_units[r.ni].text


def test_an_edit_at_the_cut_is_one_passage_both_ways():
    a, b = P("Name: Name: Title: Title:"), P("Name: Title:") + P("Title:")
    f, s = _cmp(a, b), _cmp(b, a)
    assert f.summary["total"] == s.summary["total"]


def test_a_new_paragraph_after_an_edited_one_is_not_taken_for_a_piece():
    old = P("The Contractor shall deliver the goods by the end of May at its own cost.")
    new = (P("The Contractor shall deliver the goods by the end of June at its own cost.")
           + P("The Contractor shall deliver the goods at its own cost."))
    c = _cmp(old, new)
    assert all(r.join is None for r in c.rows)


def test_of_two_like_clauses_the_pieces_go_to_the_one_they_were_cut_from():
    one = "Release of {0}. The {0} releases the {1} from all claims, demands and costs of every kind."
    a = P(one.format("Buyer", "Seller")) + P(one.format("Seller", "Buyer"))
    b = (P(one.format("Buyer", "Seller").replace("all", "any"))
         + P("Release of Seller. The Seller releases the Buyer") + P("from all claims, demands and costs of any kind."))
    c = _cmp(a, b)
    assert [(r.join, r.oi, r.ni) for r in c.rows if r.join] == [("split", 1, 1), ("split", 1, 2)]
    _rebuilds(c)
    assert c.summary["total"] == _cmp(b, a).summary["total"]


def test_a_page_number_between_two_pieces_is_stepped_over():
    whole = "The warranty runs for twelve months from delivery and covers every defect in the goods."
    c = _cmp(P(whole), P("The warranty runs for twelve months from") + P("- 2 -")
             + P("delivery and covers each defect in the goods."))
    assert [(r.type, r.ni, r.join) for r in c.rows] == [
        ("changed", 0, "split"), ("inserted", 1, None), ("changed", 2, "split")]
    _rebuilds(c)


def test_halves_that_touch_without_a_space_also_split():
    c = _cmp(P("Alphabeta"), P("Alpha") + P("beta"))
    assert [r.join for r in c.rows] == ["split", "split"] and c.summary["total"] == 1


def test_ignore_case_finds_a_split_whose_case_changed():
    c = _cmp(P(WHOLE), P("alpha beta") + P("Gamma delta."), ignore_case=True)
    assert [r.join for r in c.rows] == ["split", "split"]
    assert all(r.join is None for r in _cmp(P(WHOLE), P("alpha beta") + P("Gamma delta.")).rows)


def test_the_change_list_json_marks_the_paragraph_mark_and_the_group():
    d = _cmp(P(WHOLE), HALVES).to_dict()
    r0, r1 = d["changes"]
    assert [(s["m"], s["t"], s.get("pm", False)) for s in r0["segments"]] == [("eq", "Alpha beta", False),
                                                                               ("ins", PM, True)]
    assert (r0["join"], r0["head"], r1["join"], r1["head"]) == ("split", True, "split", False)
    assert r0["a"] == WHOLE
    m = _cmp(HALVES, P(WHOLE)).to_dict()["changes"]
    assert [s["t"] for s in m[1]["segments"]] == ["gamma delta."] and m[1]["a"] == "gamma delta."


# ---- the three layouts ------------------------------------------------------------------------

def test_split_layouts_original_once_modified_twice_blackline_with_the_mark():
    c = _cmp(P(WHOLE), HALVES)
    assert _row_texts(layout(c, LayoutOptions(fonts=FR, side="original"))) == [WHOLE]
    assert _row_texts(layout(c, LayoutOptions(fonts=FR, side="modified"))) == ["Alpha beta", "gamma delta."]
    black = layout(c, LayoutOptions(fonts=FR))
    assert _row_texts(black) == ["Alpha beta" + PM, "gamma delta."]
    runs = [r for pg in black.pages for ln in pg.lines for r in ln.runs]
    assert [(r.mode, r.cid) for r in runs if r.text == PM] == [("ins", 1)]
    first = [ln for pg in black.pages for ln in pg.lines if ln.row_index == 0]
    assert all(ln.changed for ln in first)


def test_merge_layouts_original_twice_modified_once_blackline_with_the_struck_mark():
    c = _cmp(HALVES, P(WHOLE))
    assert _row_texts(layout(c, LayoutOptions(fonts=FR, side="original"))) == ["Alpha beta", "gamma delta."]
    assert _row_texts(layout(c, LayoutOptions(fonts=FR, side="modified"))) == [WHOLE]
    black = layout(c, LayoutOptions(fonts=FR))
    assert _row_texts(black) == ["Alpha beta" + PM + " gamma delta."]
    runs = [r for pg in black.pages for ln in pg.lines for r in ln.runs]
    assert [(r.mode, r.cid) for r in runs if r.text == PM] == [("del", 1)]


def test_edited_split_layouts_show_the_word_edits_on_the_blackline_only():
    a, b = P("One two three four. Five six seven eight."), P("One two three four.") + P("Five six nine eight.")
    c = _cmp(a, b)
    assert _row_texts(layout(c, LayoutOptions(fonts=FR, side="original"))) == ["One two three four. Five six seven eight."]
    assert _row_texts(layout(c, LayoutOptions(fonts=FR, side="modified"))) == ["One two three four.", "Five six nine eight."]
    black = layout(c, LayoutOptions(fonts=FR))
    runs = [(r.text, r.mode) for pg in black.pages for ln in pg.lines for r in ln.runs if r.mode != "eq"]
    assert runs == [(PM, "ins"), ("seven", "del"), ("nine", "ins")]
    m = _cmp(b, a)                                  # the mirror merge: its struck words from each piece
    black = layout(m, LayoutOptions(fonts=FR))
    runs = [(r.text, r.mode) for pg in black.pages for ln in pg.lines for r in ln.runs if r.mode != "eq"]
    assert runs == [(PM, "del"), ("nine", "del"), ("seven", "ins")]
    assert m.summary["total"] == c.summary["total"] == 3


def test_hiding_unchanged_text_keeps_the_row_with_the_mark():
    c = _cmp(P(WHOLE) + P("Kept."), HALVES + P("Kept."))
    L = layout(c, LayoutOptions(fonts=FR, show_equal=False))
    assert _row_texts(L)[0] == "Alpha beta" + PM


# ---- the Word export --------------------------------------------------------------------------

def _streams(data):
    return [(u.stream, u.text) for u in units(parse_docx(data))]


@pytest.mark.parametrize("a, b", [(P(WHOLE), HALVES), (P(WHOLE), P("Alpha beta") + P("") + P("gamma delta.")),
                                  (P("Alphabeta"), P("Alpha") + P("beta")), (HALVES, P(WHOLE))])
def test_word_export_accepts_to_the_modified_and_rejects_to_the_original(a, b):
    out = export_tracked(_bytes(a), _bytes(b), author="Delta")
    assert _streams(accept_docx(out)) == _streams(_bytes(b))
    assert _streams(reject_docx(out)) == _streams(_bytes(a))


EDITED = [(P(WHOLE), P("Alpha beta") + P("gamma delta!")),
          (P(WHOLE), P("Alpha") + P("gamma delta.")),
          (P("One two three four. Five six seven eight."), P("One two three four.") + P("Five six nine eight.")),
          (P("One two three four. Five six seven eight."), P("One new two three four.") + P("") + P("Five six seven eight.")),
          (P("The warranty runs for twelve months from delivery and covers every defect."),
           P("The warranty runs for twelve months from") + P("- 2 -") + P("delivery and covers each defect.")),
          (P("Alpha beta") + P("gamma delta!"), P(WHOLE))]


@pytest.mark.parametrize("a, b", EDITED)
def test_word_export_of_an_edited_split_or_merge_accepts_and_rejects_cleanly(a, b):
    out = export_tracked(_bytes(a), _bytes(b), author="Delta")
    assert _streams(accept_docx(out)) == _streams(_bytes(b))
    assert _streams(reject_docx(out)) == _streams(_bytes(a))


def test_word_export_of_an_edited_split_marks_the_words_not_the_paragraphs():
    out = export_tracked(_bytes(P("One two three four. Five six seven eight.")),
                         _bytes(P("One two three four.") + P("Five six nine eight.")), author="Delta")
    root = etree.fromstring(__import__("zipfile").ZipFile(io.BytesIO(out)).read("word/document.xml"))
    inserted = "".join(t.text or "" for i in root.iter(wq("ins")) for t in i.iter(wq("t")))
    assert inserted == "nine"
    assert sorted(t.text for t in root.iter(wq("delText"))) == [" ", "seven"]


def test_word_export_of_a_split_marks_only_the_paragraph_mark():
    out = export_tracked(_bytes(P(WHOLE)), _bytes(HALVES), author="Delta")
    root = etree.fromstring(__import__("zipfile").ZipFile(io.BytesIO(out)).read("word/document.xml"))
    ps = root.findall(f".//{wq('p')}")
    assert ps[0].find(f"{wq('pPr')}/{wq('rPr')}/{wq('ins')}") is not None
    assert ps[1].find(f"{wq('pPr')}/{wq('rPr')}/{wq('ins')}") is None
    inserted = "".join(t.text or "" for i in root.iter(wq("ins")) for t in i.iter(wq("t")))
    assert inserted == ""                                    # no words inserted
    assert [t.text for t in root.iter(wq("delText"))] == [" "]   # only the space the break replaced


# ---- pasted text ------------------------------------------------------------------------------

def test_pasted_text_split_and_join():
    d = compare_texts(WHOLE, "Alpha beta\ngamma delta.")
    assert [[(s["m"], s["t"]) for s in r["segments"]] for r in d["rows"]] == [
        [("eq", "Alpha beta"), ("ins", PM)], [("eq", "gamma delta.")]]
    assert d["summary"]["total"] == 1
    d = compare_texts("Alpha beta\ngamma delta.", WHOLE)
    assert [[(s["m"], s["t"]) for s in r["segments"]] for r in d["rows"]] == [
        [("eq", "Alpha beta"), ("del", PM), ("eq", " gamma delta.")]]
    assert d["summary"]["total"] == 1
    d = compare_texts("One two three four. Five six seven eight.", "One two three four.\nFive six nine eight.")
    assert [[(s["m"], s["t"]) for s in r["segments"]] for r in d["rows"]] == [
        [("eq", "One two three four."), ("ins", PM)], [("eq", "Five six "), ("del", "seven"), ("eq", " "), ("ins", "nine"), ("eq", " eight.")]]


# ---- Copy (viewer) ----------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
@pytest.mark.parametrize("a, b", [(P(WHOLE), HALVES), (HALVES, P(WHOLE))] + EDITED)
def test_copy_gives_each_documents_own_paragraphs(a, b):
    rows = _cmp(a, b).to_dict()["changes"]
    uri = Path(str(resources.files("delta.viewer").joinpath("copy.js"))).as_uri()
    code = (f'import * as m from "{uri}";\nconst rows = {json.dumps(rows)};\n'
            'console.log(JSON.stringify([m.linesOf(rows, "original"), m.linesOf(rows, "modified")]));')
    r = subprocess.run([shutil.which("node"), "--input-type=module", "-e", code],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    orig, mod = json.loads(r.stdout)
    texts = lambda body: [u.text for u in units(_doc(body))]
    assert orig == texts(a) and mod == texts(b)
