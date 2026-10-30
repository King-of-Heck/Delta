"""One comparison held in memory for the viewer: the parsed documents, the current options, the
comparison, its layout, and the payloads the JSON API hands the browser."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import wait as futures_wait
from time import perf_counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from .. import __version__
from ..diff.changes import Comparison
from ..diff.compare import compare
from ..layout.engine import layout
from ..layout.fonts import default_resolver
from ..layout.pages import Layout
from ..layout.pieces import LayoutOptions
from ..layout.sides import SIDES
from ..pdf.draw import PdfOptions, changed_pages
from ..pdf.report import report_info, report_lines
from ..pdf.rendersets import RENDER_SETS
from ..pdf.writer import REPORTS, render
from ..pdfread.types import PdfRefused
from ..reader import UnknownKind, documents, kind_of, read_source
from ..viewer.svg import render_pages
from ..worker import Unavailable

OPTION_KEYS = ("ignore_case", "count_numbering", "show_equal", "show_insertions", "show_deletions",
               "show_formatting")
PARSE_CACHE = 4              # sources read, kept by content hash: Swap and a re-compare skip the read


@dataclass(frozen=True)
class Options:
    ignore_case: bool = False
    count_numbering: bool = True
    show_equal: bool = True
    show_insertions: bool = True
    show_deletions: bool = True
    show_formatting: bool = True

    def layout_options(self, fonts=None) -> LayoutOptions:
        return LayoutOptions(show_equal=self.show_equal, show_insertions=self.show_insertions,
                             show_deletions=self.show_deletions, show_formatting=self.show_formatting,
                             fonts=fonts)


class BadRequest(ValueError):
    """Input the client got wrong (a 400): an unknown option, rendering set or report placement."""


class NoComparison(LookupError):
    """Nothing is loaded yet (a 409): the pages, the PDF and a relayout need a comparison first."""


class BadDocument(BadRequest):
    """A file that cannot be read as a source, or a pair that cannot be compared (the message says why)."""


def parse_options(d, base: Options | None = None) -> Options:
    """Booleans by name, merged over `base`; an unknown key or a non-boolean is a BadRequest."""
    if not isinstance(d, dict):
        raise BadRequest("options must be an object")
    bad = sorted(set(d) - set(OPTION_KEYS))
    if bad:
        raise BadRequest("unknown options: " + ", ".join(bad))
    for k, v in d.items():
        if not isinstance(v, bool):
            raise BadRequest(f"option {k} must be true or false")
    return replace(base or Options(), **d)


def check_render(render_set: str, report: str | None = None) -> None:
    if render_set not in RENDER_SETS:
        raise BadRequest(f"unknown rendering set {render_set!r}; available: " + ", ".join(RENDER_SETS))
    if report is not None and report not in REPORTS:
        raise BadRequest(f"unknown report placement {report!r}; expected first, last or none")


def _parse(name: str, data: bytes, progress=None, read=None):
    """`read(data, progress)` with its failure named for the file: the reader here, or the
    worker's result for a file it read."""
    try:
        return (read or read_source)(data, progress)
    except (PdfRefused, UnknownKind) as e:
        raise BadDocument(f"{name}: {e}") from e
    except Exception as e:
        what = "this PDF could not be read" if kind_of(data) == "pdf" else "not a Word document"
        raise BadDocument(f"{name}: {what} ({type(e).__name__})") from e


def _finish(a, b):
    try:
        return documents(a, b)
    except Exception as e:
        raise BadDocument(f"these PDFs could not be read ({type(e).__name__})") from e


def change_marks(lay: Layout, passages=()) -> tuple[dict[int, dict], list[list]]:
    """(anchors, marks). anchors[cid] = {"page", "top"} of the line a passage starts on, else the
    first line carrying one of its runs, else (when `passages` is given) the first line of its row
    -- a passage hidden by show_insertions/show_deletions carries no runs on the layout at all, so
    it falls back to wherever its row landed. marks = [page, top, height, [cids]] for every line and
    every changed table row carrying passage numbers (the highlight; nothing to highlight for a
    passage with no placed text)."""
    anchors: dict[int, dict] = {}
    marks: list[list] = []
    for pg in lay.pages:
        for ln in pg.lines:
            cids = sorted({g.cid for g in ln.runs + ln.marker if g.cid is not None} | set(ln.cid_starts))
            if not cids:
                continue
            marks.append([pg.number, round(ln.top, 2), round(ln.height, 2), cids])
            for c in ln.cid_starts:
                anchors.setdefault(c, {"page": pg.number, "top": round(ln.top, 2)})
        for row in pg.table_rows:
            if row.cids:
                marks.append([pg.number, round(row.y, 2), round(row.h, 2), sorted(set(row.cids))])
    for page, top, _h, cids in marks:
        for c in cids:
            anchors.setdefault(c, {"page": page, "top": top})
    if passages:
        rows = row_map(lay)
        for p in passages:
            if p.cid not in anchors and p.row in rows:
                r = rows[p.row]
                anchors.setdefault(p.cid, {"page": r["page"], "top": r["top"]})
    return anchors, marks


def comment_anchor_map(lay: Layout) -> dict[int, dict]:
    """cid -> {"page", "top", "left", "width", "height"} of each comment's own bubble box on the
    blackline layout (the only layout that draws comment bubbles) -- the viewer's click-to-scroll
    target for a comment row, and the rectangle it flashes there, mirroring `change_marks`'s
    anchors for a passage."""
    out: dict[int, dict] = {}
    for pg in lay.pages:
        for pc in pg.comments:
            if pc.bubble.cid is not None:
                out.setdefault(pc.bubble.cid, {"page": pg.number, "top": round(pc.y, 2), "left": round(pc.x, 2),
                                               "width": round(pc.w, 2), "height": round(pc.bubble.height, 2)})
    return out


def row_map(lay: Layout) -> dict[int, dict]:
    """rows[k] = {"page", "top", "height"} of the first line of comparison row k on this layout
    (spec §12.2): the scroll sync's map between the sides."""
    out: dict[int, dict] = {}
    for pg in lay.pages:
        for ln in pg.lines:
            if ln.row_index is not None and ln.row_index not in out:
                out[ln.row_index] = {"page": pg.number, "top": round(ln.top, 2), "height": round(ln.height, 2)}
    return out


def _stem(name: str) -> str:
    return os.path.splitext(os.path.basename(name))[0]


def parse_sides(value) -> list[str]:
    """The sides a pages request wants, in SIDES order without duplicates; None or empty = all
    three. Takes a list (a JSON body) or a comma-separated string (a query); an unknown name is a
    BadRequest."""
    if value is None or value == "" or value == []:
        return list(SIDES)
    names = value.split(",") if isinstance(value, str) else value
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise BadRequest("sides must be a list of side names")
    bad = sorted(set(names) - set(SIDES))
    if bad:
        raise BadRequest("unknown sides: " + ", ".join(bad) + "; expected " + ", ".join(SIDES))
    return [s for s in SIDES if s in names]


class Session:
    def __init__(self, fonts=None, clock=None, log=None, worker=None):
        self.fonts = fonts                      # a FontResolver / FakeResolver; None = system fonts, resolved once
        self._clock = clock or datetime.now
        self._log = log                         # a callable taking one ASCII line (the timing line), or None
        self.lock = threading.Lock()            # the app serializes every API call through it
        self.last_seen = time.monotonic()
        self.a_name = self.b_name = None
        self.a_doc = self.b_doc = None
        self.options = Options()
        self.cmp: Comparison | None = None
        self.layout: Layout | None = None
        # side -> Layout; layouts["blackline"] is self.layout, laid out with the comparison. Original
        # and Modified are laid out on their first request (v2.4.3) and kept until the next commit,
        # as is the drawing of every (side, rendering set, change bars, marks) asked for.
        self.layouts: dict[str, Layout] = {}
        self._base: LayoutOptions | None = None
        self._drawn: dict[tuple, list[str]] = {}
        self.when: datetime | None = None
        self.timings: list[tuple[str, float]] = []   # (stage, seconds) since begin(): the timing line
        self._parsed: OrderedDict[str, object] = OrderedDict()   # sha256 of the bytes -> read_source's result
        self.progress: dict | None = None      # {"name", "page", "pages"} while a PDF is read; read WITHOUT the lock
        self.worker = worker                    # a delta.worker.Worker reading one PDF of a pair, or None

    @property
    def loaded(self) -> bool:
        return self.layout is not None

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    # -- timings ------------------------------------------------------------------------------
    def begin(self) -> None:
        """Start the timings of one API call (the handlers call it under the lock)."""
        self.timings = []

    def _timed(self, stage: str, t0: float) -> None:
        self.timings.append((stage, perf_counter() - t0))

    def timing_line(self, what: str) -> str:
        """One ASCII line: the call, the blackline page count, the seconds of every stage the call
        ran (parse, compare, layout.<side>, render.<side>) and their sum."""
        parts = [f"{stage}={secs:.3f}" for stage, secs in self.timings]
        pages = self.layout.page_count if self.layout is not None else 0
        total = sum(secs for _stage, secs in self.timings)
        return " ".join(["timing", what, f"pages={pages}", *parts, f"total={total:.3f}"])

    def _log_line(self, line: str) -> None:
        if self._log is not None:
            self._log(line)

    def log_timings(self, what: str) -> str:
        """Emit the timing line of the call just made (to the log sink, when there is one)."""
        line = self.timing_line(what)
        if self._log is not None:
            self._log(line)
        self.timings = []
        return line

    def tracked(self) -> dict | None:
        """Tracked changes read as accepted in each source, or None before a comparison."""
        if not self.loaded:
            return None
        return {"original": self.a_doc.tracked_changes, "modified": self.b_doc.tracked_changes}

    def source(self) -> dict | None:
        """Which reader produced the sources, and the PDF pages left out for having no text."""
        if not self.loaded:
            return None
        kinds = {self.a_doc.source_kind, self.b_doc.source_kind}
        return {"kind": "mixed" if len(kinds) == 2 else kinds.pop(),
                "skipped": {"original": list(self.a_doc.skipped_pages), "modified": list(self.b_doc.skipped_pages)}}

    def state(self) -> dict:
        names = {"original": self.a_name, "modified": self.b_name} if self.loaded else None
        return {"version": __version__, "loaded": self.loaded, "names": names, "tracked": self.tracked(),
                "source": self.source()}

    # -- loading and layout -----------------------------------------------------------------
    def _read(self, name: str, data: bytes):
        key = hashlib.sha256(data).hexdigest()
        doc = self._parsed.get(key)
        if doc is not None:
            self._parsed.move_to_end(key)
            return doc

        def tick(done: int, total: int) -> None:
            self.progress = {"name": name, "page": done, "pages": total}
        return self._keep(key, _parse(name, data, tick))

    def _keep(self, key: str, doc):
        self._parsed[key] = doc
        while len(self._parsed) > PARSE_CACHE:
            self._parsed.popitem(last=False)
        return doc

    def _read_pair(self, a_name: str, a_bytes: bytes, b_name: str, b_bytes: bytes):
        """Both files read, two PDFs read at once when the worker can take the Modified one: the
        same results, cached the same way, and the same error first (the Original's)."""
        ka, kb = hashlib.sha256(a_bytes).hexdigest(), hashlib.sha256(b_bytes).hexdigest()
        ticket = None
        if (self.worker is not None and ka != kb and ka not in self._parsed and kb not in self._parsed
                and kind_of(a_bytes) == "pdf" and kind_of(b_bytes) == "pdf"):
            ticket = self.worker.submit(b_bytes)
        if ticket is None:
            return self._read(a_name, a_bytes), self._read(b_name, b_bytes)
        both = f"{a_name} and {b_name}"
        seen = [0, 0]

        def tick(done: int, total: int) -> None:
            seen[:] = done, total
            other = self.worker.progress(ticket)
            if other is None:
                self.progress = {"name": a_name, "page": done, "pages": total}
            else:
                self.progress = {"name": both, "page": done + other[0], "pages": total + other[1]}
        try:
            a = self._keep(ka, _parse(a_name, a_bytes, tick))
        except BaseException:
            ticket[1].cancel()
            raise
        while not ticket[1].done():             # the Original is read; the Modified is still going
            other = self.worker.progress(ticket)
            if other is not None:
                self.progress = {"name": both, "page": seen[0] + other[0], "pages": seen[1] + other[1]}
            futures_wait([ticket[1]], timeout=0.25)
        try:
            b = _parse(b_name, b_bytes, read=lambda _data, _progress: self.worker.read(ticket))
        except BadDocument as e:
            if not isinstance(e.__cause__, Unavailable):
                raise
            self._log_line(f"worker not used: {e.__cause__}")
            return a, self._read(b_name, b_bytes)
        return a, self._keep(kb, b)

    def load(self, a_name: str, a_bytes: bytes, b_name: str, b_bytes: bytes,
             options: Options | None = None) -> None:
        """Nothing is committed until the new pair has compared AND laid out, so a bad file or a
        failure part way leaves the session showing exactly what it was showing before."""
        t0 = perf_counter()
        try:
            a, b = self._read_pair(a_name, a_bytes, b_name, b_bytes)
        finally:
            self.progress = None
        a, b = _finish(a, b)
        self._timed("parse", t0)
        self._commit(a_name, b_name, a, b, options or self.options)

    def relayout(self, options: Options) -> None:
        if self.a_doc is None:
            raise NoComparison("no comparison loaded")
        self._commit(self.a_name, self.b_name, self.a_doc, self.b_doc, options)

    def _commit(self, a_name: str, b_name: str, a_doc, b_doc, options: Options) -> None:
        if self.fonts is None:
            self.fonts = default_resolver()
        when = self._clock()
        t0 = perf_counter()
        if (self.cmp is not None and a_doc is self.a_doc and b_doc is self.b_doc
                and options.ignore_case == self.options.ignore_case
                and options.count_numbering == self.options.count_numbering):
            # The same two documents under the same compare options (a change of the show_*
            # options only): the comparison would come out the same, and nothing downstream
            # changes it, so the one already made is kept.
            cmp = self.cmp
        else:
            cmp = compare(a_doc, b_doc, ignore_case=options.ignore_case,
                          count_numbering=options.count_numbering)
        self._timed("compare", t0)
        base = options.layout_options(self.fonts)
        t0 = perf_counter()
        lay = layout(cmp, replace(base, side="blackline"))
        self._timed("layout.blackline", t0)
        self.a_name, self.b_name, self.a_doc, self.b_doc = a_name, b_name, a_doc, b_doc
        self.options, self.when, self.cmp = options, when, cmp
        self.layout, self.layouts, self._base, self._drawn = lay, {"blackline": lay}, base, {}

    def side_layout(self, side: str) -> Layout:
        """The layout of one side, laid out on its first request. A side that fails to lay out
        raises and changes nothing: the comparison and the sides already laid out stay as shown."""
        self._need()
        if side not in SIDES:
            raise BadRequest(f"unknown side {side!r}; expected " + ", ".join(SIDES))
        if side not in self.layouts:
            t0 = perf_counter()
            lay = layout(self.cmp, replace(self._base, side=side))
            self._timed("layout." + side, t0)
            self.layouts[side] = lay
        return self.layouts[side]

    def _draw(self, side: str, render_set: str, change_bars: bool, marks: bool) -> list[str]:
        key = (side, render_set, change_bars, marks)
        if key not in self._drawn:
            lay = self.side_layout(side)
            t0 = perf_counter()
            self._drawn[key] = render_pages(lay, render_set, change_bars, self.fonts, marks)
            self._timed("render." + side, t0)
        return self._drawn[key]

    def _need(self) -> None:
        if not self.loaded:
            raise NoComparison("no comparison loaded")

    def _info(self, render_set: str, when: datetime | None = None):
        return report_info(self.cmp, self.a_name, self.b_name, render_set, when or self.when)

    # -- payloads ---------------------------------------------------------------------------
    def pages(self, render_set: str = "Standard", change_bars: bool = True, marks: bool = False,
              sides: list[str] | None = None) -> dict:
        """The drawn pages of the sides asked for (all three by default); "pages" is the
        blackline's when it is among them, else empty."""
        self._need()
        check_render(render_set)
        out = {}
        for side in parse_sides(sides):
            lay = self.side_layout(side)
            out[side] = {"pages": self._draw(side, render_set, change_bars, marks),
                         "page_count": lay.page_count, "changed_pages": changed_pages(lay),
                         "rows": row_map(lay)}
        return {"render_set": render_set, "change_bars": change_bars, "side_marks": marks,
                "pages": out["blackline"]["pages"] if "blackline" in out else [], "sides": out,
                "report_lines": report_lines(self._info(render_set))}

    def payload(self, render_set: str = "Standard", change_bars: bool = True, marks: bool = False,
                sides: list[str] | None = None) -> dict:
        self._need()
        anchors, marks_ = change_marks(self.layout, self.cmp.passages)
        d = self.cmp.to_dict()
        return {"names": {"original": self.a_name, "modified": self.b_name}, "tracked": self.tracked(),
                "source": self.source(),
                "options": asdict(self.options), "summary": d["summary"], "changes": d["changes"],
                "passages": d["passages"], "comments": d["comments"],
                "page_count": self.layout.page_count, "changed_pages": changed_pages(self.layout),
                "anchors": anchors, "marks": marks_, "comment_anchors": comment_anchor_map(self.layout),
                "render_sets": list(RENDER_SETS),
                "render_set_styles": {k: v.to_dict() for k, v in RENDER_SETS.items()},
                **self.pages(render_set, change_bars, marks, sides)}

    def pdf(self, render_set: str = "Standard", change_bars: bool = True, report: str = "last",
            changed_only: bool = False) -> tuple[bytes, str]:
        """(PDF bytes, a file name for the download)."""
        self._need()
        check_render(render_set, report)
        now = self._clock()
        opts = PdfOptions(render_set=render_set, change_bars=change_bars, report=report, fonts=self.fonts, now=now,
                          changed_only=changed_only)
        data, _ = render(self.layout, self._info(render_set, now), opts)
        tag = " (changed pages)" if changed_only else ""
        return data, f"{_stem(self.a_name)} vs {_stem(self.b_name)} redline{tag}.pdf"
