"""A second process that reads one PDF of a pair while this process reads the other.

Reading a PDF is pure Python and the slow part of a PDF comparison, and one process runs Python
on one core at a time, so two PDFs read side by side take about as long as the longer one alone.
The worker is a `ProcessPoolExecutor` of one, started from the same interpreter (`sys.executable`,
the same `sys.path`), so the release folder's own Python runs it too. Its result comes back
pickled: the same PdfReading the reader would have returned here.

Anything that stops the worker from running (it cannot be started, or it dies) only costs the
speed: `read` then says so and the caller reads the file here, as before. An error the reader
raises (a refused PDF, a file it cannot read) comes back as itself, the same as a local read.

Progress: the worker writes (job, page, pages) into a small shared array that this process reads
when asked, so a progress poll never waits on the worker."""
from __future__ import annotations

import threading
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

_shared = None                     # in the worker: the (job, page, pages) array from `_init`


def _init(shared) -> None:
    global _shared
    _shared = shared


def _ready() -> bool:
    from . import pdfread           # noqa: F401  -- imported once, before the first file arrives
    return True


def _read(job: int, data: bytes):
    from .pdfread import read_pdf

    def tick(done: int, total: int) -> None:
        _shared[1], _shared[2] = done, total
    _shared[1] = _shared[2] = 0
    _shared[0] = job
    return read_pdf(data, tick)


class Unavailable(Exception):
    """The worker could not run the read; the caller reads the file itself."""


class Worker:
    """One worker process, started on first use (or by `warm`) and kept until `close`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pool: ProcessPoolExecutor | None = None
        self._shared = None
        self._job = 0
        self.broken: str | None = None     # why the worker is not used any more, once it failed

    def _start(self) -> ProcessPoolExecutor | None:
        with self._lock:
            if self._pool is None and self.broken is None:
                try:
                    import multiprocessing
                    ctx = multiprocessing.get_context("spawn")
                    self._shared = ctx.RawArray("q", 3)
                    self._pool = ProcessPoolExecutor(1, mp_context=ctx, initializer=_init,
                                                     initargs=(self._shared,))
                    self._pool.submit(_ready)
                except Exception as e:     # no process can be started here: read in this one
                    self.broken = f"{type(e).__name__}: {e}"
                    self._pool = None
            return self._pool

    def warm(self) -> None:
        """Start the worker now, so the first comparison does not wait for it to start."""
        self._start()

    def submit(self, data: bytes) -> tuple[int, Future] | None:
        """Read the PDF `data` in the worker; None when there is no worker to read it."""
        pool = self._start()
        if pool is None:
            return None
        with self._lock:
            self._job += 1
            job = self._job
        try:
            return job, pool.submit(_read, job, data)
        except Exception as e:             # a pool already broken, or shut down
            self._fail(e)
            return None

    def read(self, ticket: tuple[int, Future]):
        """The worker's PdfReading, or the reader's own exception; Unavailable when the worker
        itself failed (the caller then reads the file here)."""
        try:
            return ticket[1].result()
        except BrokenProcessPool as e:
            self._fail(e)
            raise Unavailable(self.broken) from e

    def progress(self, ticket: tuple[int, Future]) -> tuple[int, int] | None:
        """(page, pages) of the read `ticket` once the worker has started it, else None."""
        s = self._shared
        if s is None or s[0] != ticket[0] or not s[2]:
            return None
        return s[1], s[2]

    def _fail(self, e: BaseException) -> None:
        with self._lock:
            self.broken = self.broken or f"{type(e).__name__}: {e}"
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def close(self) -> None:
        """Stop the worker, even part way through a read nobody waits for any more."""
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.terminate_workers()
