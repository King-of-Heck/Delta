"""Command line entry:
    python -m delta dump <file.docx>
    Every <file.docx> may also be a text PDF; a Word document and a PDF compare on wording and numbering only.
    python -m delta compare <a.docx> <b.docx> [--ignore-case] [--no-count-numbering]
    python -m delta layout <a.docx> <b.docx> [--ignore-case] [--no-count-numbering]
                               [--hide-unchanged] [--hide-insertions] [--hide-deletions]
                               [--hide-formatting] [--pages]
    python -m delta pdf <a.docx> <b.docx> <out.pdf> [--ignore-case] [--no-count-numbering]
                            [--hide-unchanged] [--hide-insertions] [--hide-deletions]
                            [--hide-formatting] [--no-change-bars] [--render-set=NAME]
                            [--report=first|last|none] [--changed-only]
    python -m delta docx <a.docx> <b.docx> <out.docx> [--author=NAME] [--ignore-case]
                            (Word documents only: b with the differences as Word tracked changes)
    python -m delta serve [--port=N] [--idle=SECONDS] [--grace=SECONDS] [--log=PATH]
                              [--shortcut=LAUNCHER.cmd] [--no-browser] [--verbose]
    python -m delta version
"""
import json
import math
import os
import sys
from datetime import datetime

from . import __version__
from .diff.compare import compare
from .harness.flatten import flatten
from .layout.engine import layout
from .layout.pieces import LayoutOptions
from .pdf.draw import PdfOptions
from .pdf.rendersets import RENDER_SETS
from .pdf.report import report_info
from .pdf.writer import REPORTS, render
from .pdfread.types import PdfRefused
from .reader import documents, kind_of, read_source
from .server import previous
from .server.app import DEFAULT_GRACE, DEFAULT_IDLE, serve
from .server.shortcut import data_folder, desktop_folder, desktop_shortcut, write_shortcut

USAGE = ("usage: python -m delta dump <file.docx>\n"
         "       Every <file.docx> may also be a text PDF; a Word document and a PDF compare on wording and numbering only.\n"
         "       python -m delta compare <a.docx> <b.docx> [--ignore-case] [--no-count-numbering]\n"
         "       python -m delta layout <a.docx> <b.docx> [--ignore-case] [--no-count-numbering]\n"
         "                                  [--hide-unchanged] [--hide-insertions] [--hide-deletions]\n"
         "                                  [--hide-formatting] [--pages]\n"
         "       python -m delta pdf <a.docx> <b.docx> <out.pdf> [--ignore-case] [--no-count-numbering]\n"
         "                               [--hide-unchanged] [--hide-insertions] [--hide-deletions]\n"
         "                               [--hide-formatting] [--no-change-bars] [--render-set=NAME]\n"
         "                               [--report=first|last|none] [--changed-only]\n"
         "       python -m delta serve [--port=N] [--idle=SECONDS] [--grace=SECONDS] [--log=PATH]\n"
         "                                 [--shortcut=LAUNCHER.cmd] [--no-browser] [--verbose]\n"
         "       python -m delta version")
_COMPARE_FLAGS = {"--ignore-case", "--no-count-numbering"}
_HIDE_FLAGS = {"--hide-unchanged", "--hide-insertions", "--hide-deletions", "--hide-formatting"}
_LAYOUT_FLAGS = _COMPARE_FLAGS | _HIDE_FLAGS | {"--pages"}
_PDF_FLAGS = _COMPARE_FLAGS | _HIDE_FLAGS | {"--no-change-bars", "--changed-only"}
_PDF_VALUED = {"--render-set", "--report"}
_DOCX_VALUED = {"--author"}
_SERVE_FLAGS = {"--no-browser", "--verbose"}
_SERVE_VALUED = {"--port", "--idle", "--grace", "--log", "--shortcut"}
LOG_ROTATE_BYTES = 1_000_000


def _parse(rest, allowed, valued=frozenset(), n=2):
    """Flags (`--x`), valued flags (`--x=y`) and exactly n positionals, in any order."""
    flags, values, positionals = set(), {}, []
    for a in rest:
        if a.startswith("--"):
            k, eq, v = a.partition("=")
            if eq and k in valued:
                values[k] = v
            elif not eq and a in allowed:
                flags.add(a)
            else:
                return None
        else:
            positionals.append(a)
    if len(positionals) != n:
        return None
    return flags, values, positionals


def _layout_options(flags) -> LayoutOptions:
    return LayoutOptions(show_equal="--hide-unchanged" not in flags,
                         show_insertions="--hide-insertions" not in flags,
                         show_deletions="--hide-deletions" not in flags,
                         show_formatting="--hide-formatting" not in flags)


def _read(path: str):
    with open(path, "rb") as f:
        data = f.read()
    try:
        return read_source(data)
    except PdfRefused as e:
        raise SystemExit(f"{os.path.basename(path)}: {e}") from None


def _read_two(pa: str, pb: str):
    """Both files read; two different PDFs at once, the second in a worker process (`worker.py`)."""
    with open(pa, "rb") as f:
        da = f.read()
    with open(pb, "rb") as f:
        db = f.read()
    if not (kind_of(da) == kind_of(db) == "pdf" and da != db):
        return _read(pa), _read(pb)
    from .worker import Unavailable, Worker
    worker = Worker()
    try:
        ticket = worker.submit(db)
        a = _read(pa)
        if ticket is None:
            return a, _read(pb)
        try:
            return a, worker.read(ticket)
        except Unavailable:
            return a, _read(pb)
        except PdfRefused as e:
            raise SystemExit(f"{os.path.basename(pb)}: {e}") from None
    finally:
        worker.close()


def _compare(flags, pa, pb):
    a, b = documents(*_read_two(pa, pb))
    return compare(a, b, ignore_case="--ignore-case" in flags,
                   count_numbering="--no-count-numbering" not in flags)


def _open_log(path: str):
    """The serve log: appended to (rotated to `<path>.1` first past LOG_ROTATE_BYTES), UTF-8, line-buffered.
    Under pythonw.exe there is no console, so sys.stdout / sys.stderr are None; they are bound to
    this file so the url line, the --verbose access log and any traceback have somewhere to go."""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) > LOG_ROTATE_BYTES:
        os.replace(path, path + ".1")            # the previous log is kept once, not lost
    f = open(path, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f
    print(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} delta {__version__} serve", file=f, flush=True)
    return f


def main(argv) -> int:
    if argv == ["version"]:
        print(f"delta {__version__}")
        return 0
    if len(argv) == 2 and argv[0] == "dump":
        print(json.dumps(flatten(documents(_read(argv[1]))[0]), ensure_ascii=True, indent=1))
        return 0
    if argv and argv[0] in ("compare", "layout"):
        parsed = _parse(argv[1:], _COMPARE_FLAGS if argv[0] == "compare" else _LAYOUT_FLAGS)
        if parsed is None:
            print(USAGE)
            return 2
        flags, _, (pa, pb) = parsed
        cmp = _compare(flags, pa, pb)
        if argv[0] == "compare":
            print(json.dumps(cmp.to_dict(), ensure_ascii=True, indent=1))
            return 0
        result = layout(cmp, _layout_options(flags))
        out = {"pages": result.page_count} if "--pages" in flags else result.to_dict()
        print(json.dumps(out, ensure_ascii=True, indent=1))
        return 0
    if argv and argv[0] == "pdf":
        parsed = _parse(argv[1:], _PDF_FLAGS, _PDF_VALUED, n=3)
        if parsed is None or parsed[1].get("--report", "last") not in REPORTS:
            print(USAGE)
            return 2
        flags, values, (pa, pb, out_path) = parsed
        rs_name = values.get("--render-set", "Standard")
        if rs_name not in RENDER_SETS:
            print(f"unknown rendering set: {rs_name} (available: {', '.join(RENDER_SETS)})")
            return 2
        cmp = _compare(flags, pa, pb)
        result = layout(cmp, _layout_options(flags))
        now = datetime.now()          # one clock: the report's time stamp and the PDF creation date
        opts = PdfOptions(render_set=rs_name, change_bars="--no-change-bars" not in flags,
                          report=values.get("--report", "last"), now=now,
                          changed_only="--changed-only" in flags)
        info = report_info(cmp, os.path.basename(pa), os.path.basename(pb), rs_name, now)
        data, drawn = render(result, info, opts)
        with open(out_path, "wb") as f:
            f.write(data)
        print(json.dumps({"pages": drawn.pages, "out": out_path}, ensure_ascii=True))
        return 0
    if argv and argv[0] == "docx":
        parsed = _parse(argv[1:], {"--ignore-case"}, _DOCX_VALUED, n=3)
        if parsed is None:
            print(USAGE)
            return 2
        flags, values, (pa, pb, out_path) = parsed
        from .docx.tracked import NotWord, export_tracked
        with open(pa, "rb") as f:
            da = f.read()
        with open(pb, "rb") as f:
            db = f.read()
        try:
            data = export_tracked(da, db, author=values.get("--author", "Delta"), when=datetime.now(),
                                  ignore_case="--ignore-case" in flags)
        except NotWord as e:
            print(str(e))
            return 2
        with open(out_path, "wb") as f:
            f.write(data)
        print(json.dumps({"out": out_path}, ensure_ascii=True))
        return 0
    if argv and argv[0] == "serve":
        parsed = _parse(argv[1:], _SERVE_FLAGS, _SERVE_VALUED, n=0)
        if parsed is None:
            print(USAGE)
            return 2
        flags, values, _ = parsed
        try:
            port = int(values.get("--port", "0"))
            idle = float(values.get("--idle", str(DEFAULT_IDLE)))
            grace = float(values.get("--grace", str(DEFAULT_GRACE)))
        except ValueError:
            print(USAGE)
            return 2
        if not 0 <= port <= 65535 or not all(math.isfinite(x) and x >= 0 for x in (idle, grace)):
            print(USAGE)            # float() takes "nan" and "inf"; neither is a timeout
            return 2
        logf = None
        if values.get("--log"):
            try:
                logf = _open_log(values["--log"])
            except OSError as e:
                # a log that cannot be opened must not stop the app (under pythonw.exe nothing else
                # is visible); say so where a console exists and carry on without one
                print("cannot open the log file", ascii(values["--log"]), ascii(str(e)), file=sys.stderr)

        def emit(line):
            """One line to the console (when there is one) and to the log (when there is one)."""
            print(line, flush=True)
            if logf is not None and logf is not sys.stdout:
                print(line, file=logf, flush=True)

        data = data_folder()
        started = []

        def ready(url):
            emit(json.dumps({"url": url}))
            # this server is now the one the next launch quits, and the one before it is quit:
            # a hidden window keeps its server for as long as it stays open, so a server whose
            # window was lost without a close (a crashed browser) lasts only until the next launch
            try:
                line = previous.take_over(data, url)
                started.append(url)
                if line:
                    emit(line)
            except Exception as e:                                          # noqa: BLE001
                emit("previous server check failed " + ascii(str(e)))

        if values.get("--shortcut"):
            # the launcher's shortcut with the icon, refreshed on every start so a moved folder is
            # followed; a failure is one log line, never a stopped app
            try:
                emit("shortcut " + write_shortcut(values["--shortcut"]))
            except Exception as e:                                          # noqa: BLE001
                emit("shortcut failed " + ascii(str(e)))
            # and once on the Desktop (refreshed while it stays there, never put back once deleted)
            try:
                emit("desktop shortcut " + desktop_shortcut(values["--shortcut"], desktop_folder(), data_folder()))
            except Exception as e:                                          # noqa: BLE001
                emit("desktop shortcut failed " + ascii(str(e)))

        # under pythonw.exe without --log there is no stderr for the access log to go to
        verbose = "--verbose" in flags and sys.stderr is not None
        try:
            serve(port=port, open_browser="--no-browser" not in flags, idle=idle, grace=grace,
                  verbose=verbose, ready=ready, log=emit)
        finally:
            for url in started:
                try:
                    previous.forget(data, url)
                except Exception:                                           # noqa: BLE001
                    pass
            if logf is not None and logf is not sys.stdout and logf is not sys.stderr:
                logf.close()
        return 0
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
