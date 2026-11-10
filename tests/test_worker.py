import json
from datetime import datetime
from concurrent.futures import Future

import pytest

import delta.server.session as session_module
from delta.__main__ import main
from delta.pdfread import read_pdf
from delta.server.session import BadDocument, Session
from delta.testing.fakefonts import FakeResolver
from delta.testing.makepdf import make_pdf
from delta.worker import Unavailable, Worker

FR = FakeResolver()
WHEN = datetime(2026, 1, 1, 9, 30)


def _pdf(*lines):
    return make_pdf([[("text", 72, 100 + 40 * i, 12, t) for i, t in enumerate(lines)]])


A, B = _pdf("Alpha beta", "Gamma"), _pdf("Alpha beta", "Gamma delta", "Epsilon")


class FakeWorker:
    """Reads in this process, so the session's side of the hand-over is tested without a process."""

    def __init__(self, result=None, progress=None):
        self.result, self._progress, self.submitted, self.closed = result, progress, [], False

    def submit(self, data):
        self.submitted.append(data)
        f = Future()
        try:
            f.set_result(self.result(data) if self.result else read_pdf(data))
        except Exception as e:
            f.set_exception(e)
        return len(self.submitted), f

    def read(self, ticket):
        return Worker.read(self, ticket)

    def _fail(self, e):
        self.broken = str(e)

    def progress(self, ticket):
        return self._progress


def _payload(s):
    return json.dumps(s.payload(), sort_keys=True)


def test_a_real_worker_reads_the_modified_pdf_the_same():
    w = Worker()
    try:
        s = Session(fonts=FR, clock=lambda: WHEN, worker=w)
        s.load("a.pdf", A, "b.pdf", B)
        plain = Session(fonts=FR, clock=lambda: WHEN)
        plain.load("a.pdf", A, "b.pdf", B)
        assert _payload(s) == _payload(plain) and s.cmp.summary["total"] >= 1
        assert w.broken is None and s.progress is None
        ticket = w.submit(B)
        assert read_pdf(B) == w.read(ticket) and w.progress(ticket) == (1, 1)
    finally:
        w.close()


def test_only_two_different_pdfs_not_yet_read_go_to_the_worker():
    w = FakeWorker()
    s = Session(fonts=FR, worker=w)
    s.load("a.pdf", A, "a2.pdf", A)                     # the same file twice: read once, here
    assert w.submitted == []
    s.load("a.pdf", A, "b.pdf", B)                      # a was read already
    assert w.submitted == []
    s.load("b.pdf", B, "c.pdf", _pdf("x"))              # b read already too
    assert w.submitted == []
    d, e = _pdf("d"), _pdf("e")                         # made once: each PDF carries its creation time
    s.load("d.pdf", d, "e.pdf", e)
    assert w.submitted == [e]
    s.load("e.pdf", e, "d.pdf", d)                      # Swap: both cached, nothing read again
    assert len(w.submitted) == 1


def test_the_original_s_error_comes_first_as_before():
    s = Session(fonts=FR, worker=FakeWorker())
    scan = make_pdf([[("image", 0, 0, 612, 792)]])
    with pytest.raises(BadDocument) as e:
        s.load("scan.pdf", scan, "scan2.pdf", make_pdf([[("image", 0, 0, 600, 700)]]))
    assert str(e.value).startswith("scan.pdf: This PDF has no text")


def test_the_worker_s_error_is_named_for_the_modified_file():
    s = Session(fonts=FR, worker=FakeWorker())
    with pytest.raises(BadDocument) as e:
        s.load("a.pdf", A, "scan.pdf", make_pdf([[("image", 0, 0, 612, 792)]]))
    assert str(e.value).startswith("scan.pdf: This PDF has no text")


def test_a_worker_that_dies_only_costs_the_speed():
    from concurrent.futures.process import BrokenProcessPool

    def dies(data):
        raise BrokenProcessPool("gone")
    lines = []
    w = FakeWorker(result=dies)
    s = Session(fonts=FR, worker=w, log=lines.append)
    s.load("a.pdf", A, "b.pdf", B)
    assert s.cmp.summary["total"] >= 1
    assert lines == ["worker not used: gone"]


def test_a_worker_that_cannot_start_reads_here(monkeypatch):
    w = Worker()
    monkeypatch.setattr("delta.worker.ProcessPoolExecutor", lambda *a, **k: (_ for _ in ()).throw(OSError("blocked")))
    assert w.submit(B) is None and w.broken == "OSError: blocked"
    s = Session(fonts=FR, worker=w)
    s.load("a.pdf", A, "b.pdf", B)
    assert s.cmp.summary["total"] >= 1
    w.close()


def test_progress_counts_both_files_once_the_worker_has_begun(monkeypatch):
    s = Session(fonts=FR, worker=FakeWorker(progress=(3, 5)))
    seen = []
    real = session_module.read_source

    def slow(data, progress=None):
        progress(1, 2)
        seen.append(dict(s.progress))
        return real(data)
    monkeypatch.setattr(session_module, "read_source", slow)
    s.load("a.pdf", A, "b.pdf", B)
    assert seen == [{"name": "a.pdf and b.pdf", "page": 4, "pages": 7}] and s.progress is None


def test_the_command_line_reads_two_pdfs_at_once_the_same(tmp_path, capsys):
    pa, pb = tmp_path / "a.pdf", tmp_path / "b.pdf"
    pa.write_bytes(A)
    pb.write_bytes(B)
    assert main(["compare", str(pa), str(pb)]) == 0
    both = capsys.readouterr().out
    from delta.diff.compare import compare
    from delta.reader import documents
    assert json.loads(both) == json.loads(json.dumps(compare(*documents(read_pdf(A), read_pdf(B))).to_dict()))
    pb.write_bytes(make_pdf([[("image", 0, 0, 612, 792)]]))
    with pytest.raises(SystemExit, match="b.pdf: This PDF has no text"):
        main(["compare", str(pa), str(pb)])


def test_unavailable_is_what_a_dead_pool_becomes():
    from concurrent.futures.process import BrokenProcessPool
    w = Worker()
    f = Future()
    f.set_exception(BrokenProcessPool("x"))
    with pytest.raises(Unavailable):
        w.read((1, f))
    assert w.broken == "BrokenProcessPool: x"
    assert w.submit(B) is None                          # not tried again
