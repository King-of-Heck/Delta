import io
import json
import os
import sys
import time
from datetime import datetime

import pytest
from pypdf import PdfReader

from delta.__main__ import _open_log, main
from delta.layout.fonts import default_dirs
from delta.testing.makedocx import make_docx, DOC, P

_needs_fonts = pytest.mark.skipif(not any(os.path.isdir(d) for d in default_dirs()),
                                  reason="no system font directory on this machine")


def test_dump(tmp_path, capsys):
    f = tmp_path / "x.docx"
    f.write_bytes(make_docx({"word/document.xml": DOC(P("Héllo"))}))
    assert main(["dump", str(f)]) == 0
    out = capsys.readouterr().out
    assert out.isascii()
    assert json.loads(out)[0]["text"] == "Héllo"


def test_usage(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out.lower()


def _write(tmp_path, name, body):
    p = tmp_path / name
    p.write_bytes(make_docx({"word/document.xml": DOC(body)}))
    return str(p)


def test_compare_prints_change_model_json(tmp_path, capsys):
    # "Beta gamma" / "beta gamma" pair at similarity 0.5 (a lone "Beta"/"beta" would not pair).
    a = _write(tmp_path, "a.docx", P("Alpha") + P("Beta gamma"))
    b = _write(tmp_path, "b.docx", P("Alpha") + P("beta gamma"))
    assert main(["compare", a, b]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["total"] == 2 and out["changes"][1]["cat"] == "punctuation"
    assert out["options"] == {"ignore_case": False, "count_numbering": True}
    assert main(["compare", a, b, "--ignore-case", "--no-count-numbering"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["total"] == 0 and out["options"] == {"ignore_case": True, "count_numbering": False}


def test_compare_usage_errors(tmp_path, capsys):
    a = _write(tmp_path, "a.docx", P("x"))
    assert main(["compare", a]) == 2
    assert main(["compare", a, a, "--bogus"]) == 2
    assert "usage" in capsys.readouterr().out


def test_compare_flags_accepted_in_any_position(tmp_path, capsys):
    a = _write(tmp_path, "a.docx", P("x"))
    b = _write(tmp_path, "b.docx", P("x"))
    assert main(["compare", "--ignore-case", b]) == 2
    assert "usage" in capsys.readouterr().out.lower()
    assert main(["compare", a, "--ignore-case"]) == 2
    assert "usage" in capsys.readouterr().out.lower()
    assert main(["compare", "--ignore-case", a, b]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["options"]["ignore_case"] is True



@_needs_fonts
def test_layout_pages_only(tmp_path, capsys):
    a = _write(tmp_path, "a.docx", P("Alpha"))
    b = _write(tmp_path, "b.docx", P("Alpha") + P("Beta"))
    assert main(["layout", a, b, "--pages"]) == 0
    assert json.loads(capsys.readouterr().out) == {"pages": 1}


@_needs_fonts
def test_layout_model_and_hide_flag(tmp_path, capsys):
    a = _write(tmp_path, "a.docx", P("Alpha"))
    b = _write(tmp_path, "b.docx", P("Alpha") + P("Beta"))
    assert main(["layout", "--hide-unchanged", a, b]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["page_count"] == 1 and d["options"]["show_equal"] is False
    assert [r["t"] for r in d["pages"][0]["lines"][0]["runs"]] == ["Beta"]
    assert d["pages"][0]["lines"][0]["runs"][0]["m"] == "ins"


def test_layout_rejects_unknown_flags_and_wrong_arity(tmp_path, capsys):
    a = _write(tmp_path, "a.docx", P("Alpha"))
    assert main(["layout", a, a, "--bogus"]) == 2
    assert main(["layout", a]) == 2
    assert main(["compare", a, a, "--pages"]) == 2      # a layout-only flag is not a compare flag


@_needs_fonts
def test_pdf_command_writes_the_file_and_reports_pages(tmp_path, capsys):
    a = _write(tmp_path, "a.docx", P("Alpha"))
    b = _write(tmp_path, "b.docx", P("Alpha") + P("Beta"))
    out = str(tmp_path / "red.pdf")
    assert main(["pdf", a, b, out]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d == {"pages": 1, "out": out}
    r = PdfReader(out)
    text = r.pages[0].extract_text()
    assert "Beta" in text and "Comparison summary" in text and "Original: a.docx" in text
    assert "Rendering set: Standard" in text


@_needs_fonts
def test_pdf_command_options(tmp_path, capsys):
    a = _write(tmp_path, "a.docx", P("Alpha"))
    b = _write(tmp_path, "b.docx", P("Alpha") + P("Beta"))
    out = str(tmp_path / "red.pdf")
    assert main(["pdf", "--render-set=Black and White", "--report=first", "--no-change-bars",
                 "--hide-unchanged", a, b, out]) == 0
    assert json.loads(capsys.readouterr().out)["pages"] == 2
    r = PdfReader(out)
    assert "Rendering set: Black and White" in r.pages[0].extract_text()
    assert "Alpha" not in r.pages[1].extract_text() and "Beta" in r.pages[1].extract_text()
    assert main(["pdf", "--report=none", a, b, out]) == 0
    assert json.loads(capsys.readouterr().out)["pages"] == 1
    assert "Comparison summary" not in PdfReader(out).pages[0].extract_text()
    assert main(["pdf", "--changed-only", a, b, out]) == 0
    assert "Changed pages only:" in PdfReader(out).pages[-1].extract_text()


@_needs_fonts
def test_pdf_command_stamps_the_report_and_the_metadata_from_one_clock(tmp_path, capsys, monkeypatch):
    import delta.__main__ as cli

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 9, 14, 5)

    monkeypatch.setattr(cli, "datetime", _Clock)
    a = _write(tmp_path, "a.docx", P("Alpha"))
    b = _write(tmp_path, "b.docx", P("Alpha") + P("Beta"))
    out = str(tmp_path / "red.pdf")
    assert main(["pdf", a, b, out]) == 0
    capsys.readouterr()
    r = PdfReader(out)
    assert "Compared: 2026-09-09 14:05" in r.pages[0].extract_text()
    assert str(r.metadata["/CreationDate"]).startswith("D:20260909140500")


def test_pdf_command_usage_errors(tmp_path, capsys):
    a = _write(tmp_path, "a.docx", P("x"))
    out = str(tmp_path / "o.pdf")
    assert main(["pdf", a, a]) == 2                              # three positionals needed
    assert main(["pdf", a, a, out, "--pages"]) == 2              # a layout-only flag
    assert main(["pdf", a, a, out, "--report=middle"]) == 2
    assert main(["pdf", a, a, out, "--render-set"]) == 2         # needs =NAME
    assert "usage" in capsys.readouterr().out.lower()
    assert main(["pdf", a, a, out, "--render-set=Sepia"]) == 2
    err = capsys.readouterr().out
    assert "Sepia" in err and "Standard" in err and "Black and White" in err
    assert not os.path.exists(out)
    assert main(["layout", a, a, "--changed-only"]) == 2   # a pdf-only flag


def test_serve_usage_errors(capsys):
    assert main(["serve", "--bogus"]) == 2
    assert main(["serve", "extra"]) == 2
    assert main(["serve", "--port=abc"]) == 2
    assert main(["serve", "--port=70000"]) == 2
    assert main(["serve", "--idle=-1"]) == 2
    assert main(["serve", "--idle=soon"]) == 2
    assert main(["serve", "--idle=nan"]) == 2                # float() takes these; a watchdog cannot
    assert main(["serve", "--idle=inf"]) == 2
    assert "serve" in capsys.readouterr().out


def test_serve_prints_the_url_and_stops_when_idle(capsys, monkeypatch):
    opened = []
    monkeypatch.setattr("delta.server.app.open_viewer", lambda url: opened.append(url))
    t0 = time.perf_counter()
    assert main(["serve", "--no-browser", "--port=0", "--idle=0.4", "--grace=0.4"]) == 0
    assert time.perf_counter() - t0 < 30
    line = capsys.readouterr().out.splitlines()[0]
    url = json.loads(line)["url"]
    assert url.startswith("http://127.0.0.1:") and url.endswith("/") and opened == []


def test_serve_opens_the_browser_by_default(monkeypatch):
    opened = []
    monkeypatch.setattr("delta.server.app.open_viewer", lambda url: opened.append(url))
    assert main(["serve", "--idle=0.4", "--grace=0.4"]) == 0
    assert len(opened) == 1 and opened[0].startswith("http://127.0.0.1:")


def test_version_subcommand(capsys):
    from delta import __version__
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"delta {__version__}"


def test_version_subcommand_takes_no_arguments(capsys):
    assert main(["version", "extra"]) == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_serve_writes_the_launchers_shortcut_and_logs_it(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr("delta.server.app.open_viewer", lambda url: None)
    cmd = tmp_path / "Delta.cmd"
    cmd.write_text("@echo off\r\n")
    (tmp_path / "Delta.ico").write_bytes(b"\0\0\1\0")
    assert main(["serve", "--no-browser", "--port=0", "--idle=0.4", "--grace=0.4", f"--shortcut={cmd}"]) == 0
    out = capsys.readouterr().out
    assert "shortcut written" in out and (tmp_path / "Delta.lnk").stat().st_size > 0x4C
    assert main(["serve", "--no-browser", "--port=0", "--idle=0.4", "--grace=0.4", f"--shortcut={cmd}"]) == 0
    assert "shortcut unchanged" in capsys.readouterr().out


def test_serve_puts_the_shortcut_on_the_desktop_once(capsys, monkeypatch, tmp_path, _no_real_desktop):
    monkeypatch.setattr("delta.server.app.open_viewer", lambda url: None)
    cmd = tmp_path / "Delta.cmd"
    cmd.write_text("@echo off\r\n")
    (tmp_path / "Delta.ico").write_bytes(b"\0\0\1\0")
    args = ["serve", "--no-browser", "--port=0", "--idle=0.4", "--grace=0.4", f"--shortcut={cmd}"]
    assert main(args) == 0
    assert "desktop shortcut written" in capsys.readouterr().out
    lnk = _no_real_desktop / "Desktop" / "Delta.lnk"
    assert lnk.stat().st_size > 0x4C
    lnk.unlink()
    assert main(args) == 0
    assert "desktop shortcut removed by the user" in capsys.readouterr().out and not lnk.exists()


def test_a_failed_desktop_shortcut_is_one_line_and_the_app_still_serves(capsys, monkeypatch):
    monkeypatch.setattr("delta.server.app.open_viewer", lambda url: None)
    monkeypatch.setattr("delta.__main__.write_shortcut", lambda p: "written")
    monkeypatch.setattr("delta.__main__.desktop_shortcut",
                        lambda *a: (_ for _ in ()).throw(PermissionError("read only")))
    assert main(["serve", "--no-browser", "--port=0", "--idle=0.4", "--grace=0.4", "--shortcut=x.cmd"]) == 0
    out = capsys.readouterr().out
    assert "desktop shortcut failed 'read only'" in out and '"url"' in out


def test_a_failed_shortcut_is_one_line_and_the_app_still_serves(capsys, monkeypatch):
    monkeypatch.setattr("delta.server.app.open_viewer", lambda url: None)
    monkeypatch.setattr("delta.__main__.write_shortcut", lambda p: (_ for _ in ()).throw(PermissionError("read only")))
    assert main(["serve", "--no-browser", "--port=0", "--idle=0.4", "--grace=0.4", "--shortcut=x.cmd"]) == 0
    out = capsys.readouterr().out
    assert "shortcut failed 'read only'" in out and '"url"' in out


def test_serve_grace_usage_errors(capsys):
    assert main(["serve", "--grace=-1"]) == 2
    assert main(["serve", "--grace=soon"]) == 2
    assert main(["serve", "--grace=inf"]) == 2
    assert "--grace=SECONDS" in capsys.readouterr().out


def test_serve_writes_the_url_line_to_the_log_file(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("delta.server.app.open_viewer", lambda url: None)
    log = tmp_path / "sub" / "delta.log"
    assert main(["serve", "--no-browser", "--idle=0.4", "--grace=0.4", f"--log={log}"]) == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("--- ") and "delta" in lines[0]
    assert json.loads(lines[1])["url"].startswith("http://127.0.0.1:")
    assert json.loads(capsys.readouterr().out.splitlines()[0])["url"] == json.loads(lines[1])["url"]


def test_serve_hands_the_timing_sink_to_the_server_and_it_writes_console_and_log(tmp_path, capsys, monkeypatch):
    seen = {}

    def fake_serve(**kw):
        seen.update(kw)
        kw["ready"]("http://127.0.0.1:1/")
        kw["log"]("timing compare pages=1 total=0.010")
    monkeypatch.setattr("delta.__main__.serve", fake_serve)
    log = tmp_path / "delta.log"
    assert main(["serve", "--no-browser", f"--log={log}"]) == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[-1] == "timing compare pages=1 total=0.010" and json.loads(lines[-2])["url"] == "http://127.0.0.1:1/"
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "timing compare pages=1 total=0.010"
    assert main(["serve", "--no-browser"]) == 0                  # no log file: the console alone
    assert capsys.readouterr().out.splitlines()[-1] == "timing compare pages=1 total=0.010"


def test_open_log_binds_missing_streams_and_rotates(tmp_path, monkeypatch):
    log = tmp_path / "c.log"
    log.write_bytes(b"x" * 1_000_001)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    f = _open_log(str(log))
    try:
        assert sys.stdout is f and sys.stderr is f
        print("hello")
        f.flush()
    finally:
        f.close()
    text = log.read_text(encoding="utf-8")
    assert not text.startswith("x") and text.splitlines()[0].startswith("--- ") and "hello" in text


def test_open_log_appends_when_a_console_exists(tmp_path):
    log = tmp_path / "c.log"
    log.write_text("old\n", encoding="utf-8")
    f = _open_log(str(log))
    f.close()
    assert sys.stdout is not f
    assert log.read_text(encoding="utf-8").startswith("old\n--- ")


def test_serve_carries_on_when_the_log_cannot_be_opened(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("delta.server.app.open_viewer", lambda url: None)
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert main(["serve", "--no-browser", "--idle=0.4", "--grace=0.4", f"--log={blocker / 'delta.log'}"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out.splitlines()[0])["url"].startswith("http://127.0.0.1:")
    assert "cannot open the log file" in err


def test_open_log_rotates_the_old_log_to_dot_one(tmp_path):
    log = tmp_path / "c.log"
    log.write_bytes(b"x" * 1_000_001)
    (tmp_path / "c.log.1").write_text("older\n", encoding="utf-8")
    f = _open_log(str(log))
    f.close()
    assert (tmp_path / "c.log.1").read_bytes() == b"x" * 1_000_001      # kept, the older one replaced
    assert log.read_text(encoding="utf-8").startswith("--- ")


def test_verbose_is_dropped_when_there_is_no_stderr_to_write_to(monkeypatch):
    calls = []
    monkeypatch.setattr("delta.__main__.serve", lambda **kw: calls.append(kw))
    monkeypatch.setattr(sys, "stderr", None)                      # pythonw.exe without --log
    assert main(["serve", "--no-browser", "--verbose"]) == 0
    assert calls[0]["verbose"] is False
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert main(["serve", "--no-browser", "--verbose"]) == 0
    assert calls[1]["verbose"] is True


# Task 11: compare and dump take PDFs too, through the same reader seam as the server.

def test_compare_and_dump_take_pdfs(tmp_path, capsys):
    from delta.testing.makepdf import make_pdf
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(make_pdf([[("text", 72, 100, 12, "Alpha beta")]]))
    b.write_bytes(make_pdf([[("text", 72, 100, 12, "Alpha gamma")]]))
    assert main(["compare", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert '"total": ' in out and '"total": 0' not in out
    assert main(["dump", str(a)]) == 0
    assert "Alpha beta" in capsys.readouterr().out


def test_mixed_kinds_compare_on_the_command_line(tmp_path, capsys):
    from delta.testing.makepdf import make_pdf
    a, b = tmp_path / "a.docx", tmp_path / "b.pdf"
    a.write_bytes(make_docx({"word/document.xml": DOC(P("x"))}))
    b.write_bytes(make_pdf([[("text", 72, 100, 12, "x")]]))
    assert main(["compare", str(a), str(b)]) == 0
    assert '"total": 0' in capsys.readouterr().out
