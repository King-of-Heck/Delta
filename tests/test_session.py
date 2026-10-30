import json
import os
from datetime import datetime

import pytest

from delta import __version__
from delta.layout.fonts import default_dirs
from delta.server.session import (OPTION_KEYS, BadDocument, Options, Session, change_marks, check_render,
                                      parse_options)
from delta.server import session as session_module
from delta.testing.fakefonts import FakeResolver
from delta.testing.makedocx import COMMENTS, CRANGE, CRELS, DOC, P, PR, R, STYLES, TBL, make_docx
from delta.testing.makepdf import make_pdf

FR = FakeResolver()
STY = STYLES('<w:rFonts w:ascii="Fake"/><w:sz w:val="20"/>')
WHEN = datetime(2026, 9, 9, 14, 5)


def _docx(body):
    return make_docx({"word/document.xml": DOC(body), "word/styles.xml": STY})


def _session(a=P("aaaa"), b=P("aaaa bbbb"), **kw):
    s = Session(fonts=FR, clock=lambda: WHEN)
    s.load("a.docx", _docx(a), "b.docx", _docx(b), **kw)
    return s


def test_options_defaults_and_layout_options():
    o = Options()
    assert (o.ignore_case, o.count_numbering, o.show_equal, o.show_insertions, o.show_deletions,
            o.show_formatting) == (False, True, True, True, True, True)
    lo = Options(show_equal=False).layout_options(FR)
    assert (lo.show_equal, lo.show_insertions, lo.fonts) == (False, True, FR)
    assert OPTION_KEYS == ("ignore_case", "count_numbering", "show_equal", "show_insertions",
                           "show_deletions", "show_formatting")


def test_parse_options_merges_over_a_base_and_rejects_bad_input():
    assert parse_options({}) == Options()
    assert parse_options({"ignore_case": True}, Options(show_equal=False)) == Options(ignore_case=True, show_equal=False)
    with pytest.raises(ValueError, match="unknown options: zoom"):
        parse_options({"zoom": 2})
    with pytest.raises(ValueError, match="ignore_case must be true or false"):
        parse_options({"ignore_case": "yes"})
    with pytest.raises(ValueError, match="must be an object"):
        parse_options([])


def test_check_render():
    check_render("Standard")
    check_render("Black and White", "first")
    with pytest.raises(ValueError, match="Sepia"):
        check_render("Sepia")
    with pytest.raises(ValueError, match="report"):
        check_render("Standard", "middle")


def test_state_before_and_after_load():
    s = Session(fonts=FR, clock=lambda: WHEN)
    assert s.state() == {"version": __version__, "loaded": False, "names": None, "tracked": None,
                         "source": None}
    assert not s.loaded
    s.load("a.docx", _docx(P("x")), "b.docx", _docx(P("x")))
    assert s.state() == {"version": __version__, "loaded": True,
                         "names": {"original": "a.docx", "modified": "b.docx"},
                         "tracked": {"original": 0, "modified": 0},
                         "source": {"kind": "docx", "skipped": {"original": [], "modified": []}}}


def test_methods_need_a_loaded_comparison():
    s = Session(fonts=FR)
    for call in (lambda: s.relayout(Options()), lambda: s.pages(), lambda: s.payload(), lambda: s.pdf()):
        with pytest.raises(LookupError, match="no comparison loaded"):
            call()


def test_bad_document_names_the_file():
    s = Session(fonts=FR)
    with pytest.raises(BadDocument, match="b.docx: not a Word document"):
        s.load("a.docx", _docx(P("x")), "b.docx", b"not a zip")
    assert not s.loaded


def test_a_failed_second_load_leaves_the_first_pair_intact():
    s = _session()
    with pytest.raises(BadDocument, match="d.docx: not a Word document"):
        s.load("c.docx", _docx(P("cccc")), "d.docx", b"not a zip")
    assert s.state()["names"] == {"original": "a.docx", "modified": "b.docx"}
    assert s.payload()["summary"]["total"] == 1 and "bbbb" in s.payload()["pages"][0]


def test_a_load_that_fails_after_parsing_leaves_the_first_pair_intact(monkeypatch):
    """Both files parse before anything is committed, so the not-a-zip case above never got as far
    as the fields; a failure in the comparison or the layout did, and left the session describing
    the new pair while still holding the old one's drawings."""
    import delta.server.session as sessmod

    s = _session()
    monkeypatch.setattr(sessmod, "layout", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        s.load("c.docx", _docx(P("cccc")), "d.docx", _docx(P("cccc dddd")))
    monkeypatch.undo()
    assert s.state()["names"] == {"original": "a.docx", "modified": "b.docx"}
    d = s.payload()
    assert d["summary"]["total"] == 1 and "bbbb" in d["pages"][0] and "dddd" not in d["pages"][0]


def test_payload_of_a_one_line_insertion():
    s = _session()
    d = s.payload()
    assert d["names"] == {"original": "a.docx", "modified": "b.docx"}
    assert d["options"] == {"ignore_case": False, "count_numbering": True, "show_equal": True,
                            "show_insertions": True, "show_deletions": True, "show_formatting": True}
    assert d["summary"]["total"] == 1 and d["summary"]["insertions"] == 1 and "amendments" not in d["summary"]
    assert len(d["changes"]) == 1 and d["changes"][0]["cids"] == [1]
    assert d["passages"] == [{"cid": 1, "category": "insertion", "row": 0}]
    assert d["page_count"] == 1 and len(d["pages"]) == 1 and "bbbb" in d["pages"][0]
    assert d["anchors"] == {1: {"page": 1, "top": 72.0}}
    assert d["marks"] == [[1, 72.0, 12.0, [1]]]
    assert d["render_sets"] == ["Standard", "Black and White"]
    assert d["render_set_styles"]["Standard"]["insert"]["color"] == "0000ff"
    assert d["render_set_styles"]["Black and White"]["delete"]["color"] == "000000"
    assert (d["render_set"], d["change_bars"]) == ("Standard", True)
    assert d["report_lines"][0] == "Original: a.docx" and d["report_lines"][2].startswith("Compared: 2026-09-09 14:05 with Delta ")
    assert d["report_lines"][3] == "Rendering set: Standard"
    assert d["changed_pages"] == [1]
    json.dumps(d)


def test_payload_carries_comments_and_a_click_to_scroll_anchor_for_each():
    """The viewer's Comments filter renders data.comments and jumps to data.comment_anchors[cid] --
    both must reach the payload, and the anchor must place the bubble on the page it landed on."""
    a_bytes = make_docx({"word/document.xml": DOC(P("The fox jumps.")), "word/styles.xml": STY})
    b_bytes = make_docx({
        "word/document.xml": DOC(PR(R("The ") + CRANGE("0", "fox") + R(" jumps."))),
        "word/styles.xml": STY,
        "word/_rels/document.xml.rels": CRELS(comments=True),
        "word/comments.xml": COMMENTS([{"id": "0", "author": "Ada", "initials": "AL",
                                        "date": "2026-09-16T00:00:00Z", "paras": [("p1", "Which fox?")]}]),
    })
    s = Session(fonts=FR, clock=lambda: WHEN)
    s.load("a.docx", a_bytes, "b.docx", b_bytes)
    d = s.payload()
    assert len(d["comments"]) == 1
    c = d["comments"][0]
    assert (c["cid"], c["state"], c["author"]) == (1, "added", "Ada")
    a = d["comment_anchors"][c["cid"]]
    assert a["page"] == 1
    assert a["top"] >= 0 and a["left"] > 0 and a["width"] > 0 and a["height"] > 0
    json.dumps(d)


def test_pages_follow_the_render_set_and_the_bars_toggle():
    s = _session()
    d = s.pages("Black and White", change_bars=False)
    assert set(d) == {"render_set", "change_bars", "side_marks", "pages", "sides", "report_lines"}
    assert d["render_set"] == "Black and White" and d["change_bars"] is False
    assert "#0000ff" not in d["pages"][0] and 'stroke-width="1.50"' not in d["pages"][0]
    assert d["report_lines"][3] == "Rendering set: Black and White"
    with pytest.raises(ValueError):
        s.pages("Sepia")


def test_relayout_reruns_the_comparison_with_the_new_options():
    s = _session(P("Alpha") + P("Beta gamma"), P("Alpha") + P("beta gamma"))
    assert s.payload()["summary"]["total"] == 2
    s.relayout(Options(ignore_case=True))
    d = s.payload()
    assert d["summary"]["total"] == 0 and d["options"]["ignore_case"] is True and d["anchors"] == {}
    assert s.options == Options(ignore_case=True)


def test_a_show_option_change_keeps_the_comparison_and_a_compare_option_redoes_it(monkeypatch):
    s = _session(P("Alpha") + P("Beta gamma"), P("Alpha") + P("beta gamma"))
    first = s.cmp
    calls = []
    real = session_module.compare
    monkeypatch.setattr(session_module, "compare", lambda *a, **k: calls.append(1) or real(*a, **k))
    s.relayout(Options(show_equal=False, show_formatting=False))
    assert s.cmp is first and calls == []
    fresh = Session(fonts=FR, clock=lambda: WHEN)
    fresh.load("a.docx", _docx(P("Alpha") + P("Beta gamma")), "b.docx", _docx(P("Alpha") + P("beta gamma")),
               options=Options(show_equal=False, show_formatting=False))
    assert s.payload() == fresh.payload()                  # the same pages as a comparison made afresh
    calls.clear()
    s.relayout(Options(show_equal=False, count_numbering=False))
    assert calls == [1] and s.cmp is not first
    s.relayout(Options(ignore_case=True))
    assert calls == [1, 1] and s.payload()["summary"]["total"] == 0


def test_hidden_insertions_leave_the_page_and_load_keeps_the_current_options():
    s = _session(options=Options(show_insertions=False))
    assert "bbbb" not in s.payload()["pages"][0] and s.options.show_insertions is False
    s.load("a.docx", _docx(P("cccc")), "b.docx", _docx(P("cccc dddd")))
    assert "dddd" not in s.payload()["pages"][0]          # the options survive a new pair
    s.load("a.docx", _docx(P("cccc")), "b.docx", _docx(P("cccc dddd")), options=Options())
    assert "dddd" in s.payload()["pages"][0]


def test_change_marks_include_changed_table_rows_and_anchor_every_number():
    s = _session(P("aaaa") + TBL([["x", "y"]], [4680, 4680]), P("aaaa bbbb") + TBL([["x", "z"]], [4680, 4680]))
    anchors, marks = change_marks(s.layout)
    cids = {c for r in s.cmp.to_dict()["changes"] for c in r["cids"]}
    assert set(anchors) == cids == {p["cid"] for p in s.cmp.to_dict()["passages"]} and 1 in cids and len(cids) >= 2
    assert all(len(m) == 4 and m[0] == 1 for m in marks)
    row_marks = [m for m in marks if set(m[3]) & (cids - {1})]
    assert row_marks and any(m[2] > 12.0 for m in row_marks)      # a row box is taller than a line
    assert all(cid in anchors for m in marks for cid in m[3])


def test_anchors_follow_a_passage_to_the_line_it_starts_on():
    words = " ".join(["aaaa"] * 30)
    s = _session(P(words + " bbbb cccc"), P(words + " bbbb dddd cccc"))
    anchors, marks = change_marks(s.layout)
    ins_line = next(ln for pg in s.layout.pages for ln in pg.lines if any(g.mode == "ins" for g in ln.runs))
    assert anchors == {1: {"page": 1, "top": round(ins_line.top, 2)}} and ins_line.top > 72
    assert [m[3] for m in marks] == [[1]]                     # only the line holding the passage


def test_a_hidden_passage_is_anchored_to_its_row():
    s = _session(P("The old value here"), P("The new value here"), options=Options(show_insertions=False))
    anchors, marks = change_marks(s.layout, s.cmp.passages)
    assert set(anchors) == {1, 2}
    assert anchors[2] == anchors[1]
    assert {c for m in marks for c in m[3]} == {1}


def test_touch_updates_last_seen():
    s = Session(fonts=FR)
    before = s.last_seen
    s.touch()
    assert s.last_seen >= before


@pytest.mark.skipif(not any(os.path.isdir(d) for d in default_dirs()), reason="no system font directory")
def test_pdf_bytes_and_file_name():
    s = Session(clock=lambda: WHEN)
    s.load("Draft v1.docx", make_docx({"word/document.xml": DOC(P("aaaa"))}),
           "Draft v2.docx", make_docx({"word/document.xml": DOC(P("aaaa bbbb"))}))
    data, name = s.pdf()
    assert data.startswith(b"%PDF") and name == "Draft v1 vs Draft v2 redline.pdf"
    data2, _ = s.pdf("Black and White", change_bars=False, report="none")
    assert data2.startswith(b"%PDF") and data2 != data
    with pytest.raises(ValueError):
        s.pdf(report="middle")


@pytest.mark.skipif(not any(os.path.isdir(d) for d in default_dirs()), reason="no system font directory")
def test_pdf_changed_only_changes_the_name_and_the_report():
    s = Session(clock=lambda: WHEN)
    s.load("a.docx", make_docx({"word/document.xml": DOC(P("aaaa"))}),
           "b.docx", make_docx({"word/document.xml": DOC(P("aaaa bbbb"))}))
    data, name = s.pdf(changed_only=True)
    assert name == "a vs b redline (changed pages).pdf" and data.startswith(b"%PDF")
    _, plain = s.pdf()
    assert plain == "a vs b redline.pdf"


# v2.4.0: three sides per comparison (spec §12.2).

from delta.server.session import row_map


def test_row_map_names_the_first_line_of_every_row():
    s = _session(P("aaaa") + P("bbbb"), P("aaaa") + P("bbbb") + P("cccc"))
    assert row_map(s.layout) == {0: {"page": 1, "top": 72.0, "height": 12.0},
                                 1: {"page": 1, "top": 84.0, "height": 12.0},
                                 2: {"page": 1, "top": 96.0, "height": 12.0}}
    assert row_map(s.side_layout("original")) == {0: {"page": 1, "top": 72.0, "height": 12.0},
                                                  1: {"page": 1, "top": 84.0, "height": 12.0}}


def test_the_session_lays_out_three_sides_and_ships_them():
    s = _session(P("aaaa") + P("old old"), P("aaaa") + P("new new"))
    assert set(s.layouts) == {"blackline"} and s.layouts["blackline"] is s.layout   # v2.4.3: the sides wait
    d = s.payload()
    assert set(s.layouts) == {"blackline", "original", "modified"}
    assert d["side_marks"] is False and set(d["sides"]) == {"blackline", "original", "modified"}
    for side in ("blackline", "original", "modified"):
        blk = d["sides"][side]
        assert set(blk) == {"pages", "page_count", "changed_pages", "rows"} and blk["page_count"] == len(blk["pages"]) == 1
    assert d["sides"]["blackline"]["pages"] == d["pages"] and d["sides"]["blackline"]["changed_pages"] == d["changed_pages"]
    assert "old" in d["sides"]["original"]["pages"][0] and "new" not in d["sides"]["original"]["pages"][0]
    assert "new" in d["sides"]["modified"]["pages"][0] and "old" not in d["sides"]["modified"]["pages"][0]
    assert d["sides"]["original"]["rows"] == {0: {"page": 1, "top": 72.0, "height": 12.0},
                                              1: {"page": 1, "top": 84.0, "height": 12.0}}
    assert d["sides"]["original"]["changed_pages"] == [1]


def test_marks_tint_the_side_pages_and_a_relayout_rebuilds_every_side():
    s = _session(P("aaaa") + P("old old"), P("aaaa") + P("new new"))
    plain, marked = s.pages(), s.pages(marks=True)
    assert "<rect" not in plain["sides"]["original"]["pages"][0]
    assert "<rect" in marked["sides"]["original"]["pages"][0] and marked["side_marks"] is True
    assert marked["pages"] == plain["pages"]                     # the blackline never changes with marks
    s.relayout(Options(show_equal=False))
    assert "aaaa" not in s.payload()["sides"]["modified"]["pages"][0]


# v2.4.3: the sides are laid out and drawn on request and kept until the next commit.

from delta.server.session import BadRequest, parse_sides


def test_parse_sides_takes_a_list_or_a_query_string_in_side_order():
    assert parse_sides(None) == parse_sides("") == parse_sides([]) == ["blackline", "original", "modified"]
    assert parse_sides("modified,original") == ["original", "modified"]
    assert parse_sides(["blackline", "blackline"]) == ["blackline"]
    with pytest.raises(BadRequest, match="unknown sides: left"):
        parse_sides("left,blackline")
    with pytest.raises(BadRequest, match="list of side names"):
        parse_sides([1])
    with pytest.raises(BadRequest, match="unknown side"):
        _session().side_layout("left")


def test_pages_lays_out_only_the_sides_asked_for():
    s = _session(P("aaaa") + P("old"), P("aaaa") + P("new"))
    d = s.pages(sides=["blackline"])
    assert set(d["sides"]) == {"blackline"} and set(s.layouts) == {"blackline"} and d["pages"] == d["sides"]["blackline"]["pages"]
    d = s.pages(sides=["original"])
    assert set(d["sides"]) == {"original"} and set(s.layouts) == {"blackline", "original"}
    assert d["pages"] == [] and "old" in d["sides"]["original"]["pages"][0]
    assert set(s.payload(sides=["modified", "blackline"])["sides"]) == {"blackline", "modified"}


def test_side_layouts_and_drawings_are_kept_until_the_next_commit(monkeypatch):
    import delta.server.session as mod
    s = _session(P("aaaa") + P("old"), P("aaaa") + P("new"))
    calls = []
    real_layout, real_render = mod.layout, mod.render_pages
    monkeypatch.setattr(mod, "layout", lambda *a, **k: (calls.append("layout"), real_layout(*a, **k))[1])
    monkeypatch.setattr(mod, "render_pages", lambda *a, **k: (calls.append("render"), real_render(*a, **k))[1])
    first = s.pages(sides=["original"])
    assert calls == ["layout", "render"]
    again = s.pages(sides=["original"])
    assert calls == ["layout", "render"] and again == first                 # nothing recomputed
    s.pages(marks=True, sides=["original"])
    assert calls == ["layout", "render", "render"]                        # a new style draws again, no relayout
    s.pages(marks=True, sides=["original"])
    assert calls == ["layout", "render", "render"]
    s.relayout(Options(show_equal=False))                                 # a commit drops every side and drawing
    assert set(s.layouts) == {"blackline"} and calls[-1] == "layout"
    n = len(calls)
    s.pages(sides=["original"])
    assert calls[n:] == ["layout", "render"]


def test_the_same_pair_loaded_again_keeps_its_comparison(monkeypatch):
    import delta.server.session as mod
    a, b = _docx(P("aaaa") + P("old")), _docx(P("aaaa") + P("new"))
    s = Session(fonts=FR, clock=lambda: WHEN)
    s.load("a.docx", a, "b.docx", b)
    before = s.payload()
    cmp = s.cmp
    calls = []
    for name in ("compare", "layout"):
        real = getattr(mod, name)
        monkeypatch.setattr(mod, name, lambda *x, _r=real, _n=name, **k: (calls.append(_n), _r(*x, **k))[1])
    s.load("c.docx", a, "d.docx", b)                       # the same files again (parsed once, so the same documents)
    after = s.payload()
    assert s.cmp is cmp and "compare" not in calls and "layout" in calls
    assert after["names"] == {"original": "c.docx", "modified": "d.docx"}
    named = ("names", "report_lines")                      # the two that name the files
    assert {k: v for k, v in after.items() if k not in named} == {k: v for k, v in before.items() if k not in named}
    s.load("c.docx", b, "d.docx", a)                      # Swap compares again
    assert s.cmp is not cmp and calls.count("compare") == 1


def test_a_side_that_fails_to_lay_out_leaves_the_comparison_shown(monkeypatch):
    import delta.server.session as mod
    s = _session(P("aaaa") + P("old"), P("aaaa") + P("new"))
    before = s.pages(sides=["blackline"])

    def boom(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(mod, "layout", boom)
    with pytest.raises(RuntimeError):
        s.pages(sides=["original"])
    assert s.loaded and set(s.layouts) == {"blackline"} and s.pages(sides=["blackline"]) == before


def test_the_timing_line_names_the_stages_of_the_call():
    lines = []
    s = Session(fonts=FR, clock=lambda: WHEN, log=lines.append)
    s.begin()
    s.load("a.docx", _docx(P("aaaa")), "b.docx", _docx(P("aaaa bbbb")))
    s.payload(sides=["blackline"])
    line = s.log_timings("compare")
    assert lines == [line] and line.isascii()
    words = line.split()
    assert words[:3] == ["timing", "compare", "pages=1"]
    assert [w.split("=")[0] for w in words[3:]] == ["parse", "compare", "layout.blackline", "render.blackline", "total"]
    assert all(float(w.split("=")[1]) >= 0 for w in words[3:])
    assert s.timings == []
    s.begin()
    s.pages(sides=["original", "modified"])
    words = s.log_timings("pages").split()
    assert [w.split("=")[0] for w in words[3:]] == ["layout.original", "render.original", "layout.modified",
                                                    "render.modified", "total"]
    s.begin()
    s.pages(sides=["original"])
    assert s.log_timings("pages").split()[3:] == ["total=0.000"]           # everything was kept
    assert Session(fonts=FR).timing_line("pages") == "timing pages pages=0 total=0.000"   # no sink, nothing loaded


INS_RUN = '<w:ins w:id="1" w:author="A"><w:r><w:t>new</w:t></w:r></w:ins>'


def test_payload_and_state_carry_the_tracked_counts():
    s = _session(a="<w:p>" + R("kept ") + INS_RUN + "</w:p>", b=P("kept new"))
    assert s.tracked() == {"original": 1, "modified": 0}
    assert s.payload()["tracked"] == {"original": 1, "modified": 0}
    assert s.state()["tracked"] == {"original": 1, "modified": 0}


# Task 11: PDFs read through the same seam, a same-kind check, a parse cache and read progress.


def _pdf(*lines):
    return make_pdf([[("text", 72, 100 + 40 * i, 12, t) for i, t in enumerate(lines)]])


def test_two_pdfs_compare_and_say_so():
    s = Session(fonts=FR, clock=lambda: WHEN)
    s.load("a.pdf", _pdf("Alpha beta", "Gamma"), "b.pdf", _pdf("Alpha beta", "Gamma delta"))
    assert s.cmp.summary["total"] == 1
    assert s.source() == {"kind": "pdf", "skipped": {"original": [], "modified": []}}
    assert s.payload()["source"]["kind"] == "pdf" and s.state()["source"]["kind"] == "pdf"
    assert s.progress is None


def test_a_word_pair_reports_its_kind_too():
    assert _session().source() == {"kind": "docx", "skipped": {"original": [], "modified": []}}
    assert Session(fonts=FR).source() is None


def test_a_word_document_against_a_pdf_compares():
    s = _session()
    s.load("a.docx", _docx(P("x")), "b.pdf", _pdf("x"))
    assert s.a_name == "a.docx" and s.b_name == "b.pdf"
    assert s.source()["kind"] == "mixed"


def test_a_mixed_compare_leaves_the_cached_word_reading_alone():
    s = _session()
    word = _docx(P("(a) x"))
    s.load("a.docx", word, "b.pdf", _pdf("(a) x"))
    s.load("a.docx", word, "c.docx", word)
    assert s.a_doc.blocks[0].num is None


def test_a_refused_pdf_names_the_file_and_gives_the_reason():
    s = Session(fonts=FR)
    with pytest.raises(BadDocument) as e:
        s.load("scan.pdf", make_pdf([[("image", 0, 0, 612, 792)]]), "b.pdf", _pdf("x"))
    assert str(e.value).startswith("scan.pdf: This PDF has no text")
    with pytest.raises(BadDocument, match="notes.txt: not a Word document or a PDF"):
        s.load("notes.txt", b"hello", "b.pdf", _pdf("x"))


def test_a_file_is_parsed_once_however_often_it_is_loaded(monkeypatch):
    calls = []
    real = session_module.read_source
    monkeypatch.setattr(session_module, "read_source", lambda data, progress=None: calls.append(1) or real(data, progress))
    s = Session(fonts=FR)
    a, b = _pdf("Alpha"), _pdf("Alpha beta")
    s.load("a.pdf", a, "b.pdf", b)
    s.load("b.pdf", b, "a.pdf", a)                      # Swap
    assert len(calls) == 2
    for i in range(5):
        s.load("a.pdf", a, f"n{i}.pdf", _pdf(f"Alpha {i}"))
    s.load("a.pdf", a, "b.pdf", b)                      # b fell out of the cache of four; a did not
    assert len(calls) == 2 + 5 + 1


def test_progress_is_visible_while_a_pdf_is_read(monkeypatch):
    s = Session(fonts=FR)
    seen = []

    def slow(data, progress=None):
        progress(1, 2)
        seen.append(dict(s.progress))
        return real(data)
    real = session_module.read_source
    monkeypatch.setattr(session_module, "read_source", slow)
    s.load("a.pdf", _pdf("x"), "b.pdf", _pdf("y"))
    assert seen[0] == {"name": "a.pdf", "page": 1, "pages": 2} and s.progress is None
