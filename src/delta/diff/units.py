"""The flat stream of paragraph units the diff runs over.

Body paragraphs and table-cell paragraphs are one stream in document order, each unit carrying
its table location; the paragraphs of a footnote or endnote follow the paragraph that references
it (in reference order, a note's body once), tagged with their stream, so note changes fall into
reading order with everything else. Empty paragraphs are not units (they are layout, not content).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from ..docx.hf import displayed
from ..model import Document, NoteRef, Paragraph, Table, iter_paragraphs
from .chars import FmtSpan, char_fmt, note_marks, tab_marks


@dataclass(frozen=True)
class Loc:
    ti: int
    ri: int
    ci: int
    cols: int

    def as_dict(self) -> dict:
        return {"ti": self.ti, "ri": self.ri, "ci": self.ci, "cols": self.cols}


@dataclass(frozen=True)
class Where:
    stream: str = "body"             # body | footnote | endnote | header | footer
    note: NoteRef | None = None      # the note this paragraph belongs to
    part: str | None = None          # the header/footer part this paragraph belongs to


BODY = Where()


@dataclass
class Unit:
    index: int
    text: str
    marker: str
    bold_runs: list[list[int]]
    fmt_spans: list[FmtSpan]
    loc: Loc | None
    para: Paragraph
    lead_tabs: int = 0                                  # tabs before the first word (trimmed from text)
    tabs: dict[int, int] = field(default_factory=dict)  # offset of a collapsed space -> tabs it held
    stream: str = "body"
    note: NoteRef | None = None
    note_refs: list = field(default_factory=list)       # (offset in text, NoteRef) of the paragraph's marks
    part: str | None = None


def walk(doc: Document) -> Iterator[tuple[Paragraph, Loc | None, Where]]:
    """Every paragraph in stream order (empty ones included) with its table location and its
    stream: a body paragraph, then the paragraphs of each note it references (first reference
    only; a reference to a note the document does not hold yields nothing).

    This is the ONE definition of stream order: units() is built on it, and the layout's merged
    walk relies on empties and units sharing it."""
    seen: set[NoteRef] = set()

    def with_notes(p: Paragraph, loc):
        yield p, loc, BODY
        for run in p.runs:
            ref = run.props.note
            if ref is None or ref in seen:
                continue
            seen.add(ref)
            blocks = (doc.footnotes if ref.kind == "footnote" else doc.endnotes).get(ref.id)
            for np in iter_paragraphs(blocks or []):
                yield np, None, Where(ref.kind, ref)

    ti = 0
    for b in doc.blocks:
        if isinstance(b, Paragraph):
            yield from with_notes(b, None)
        elif isinstance(b, Table):
            cols = len(b.grid_pt) or max((len(r.cells) for r in b.rows), default=0)
            for ri, row in enumerate(b.rows):
                for ci, cell in enumerate(row.cells):
                    for p in iter_paragraphs(cell.blocks):
                        yield from with_notes(p, Loc(ti, ri, ci, cols))
            ti += 1

    for kind in ("header", "footer"):
        entries, _alias = displayed(doc, kind)
        for name, blocks in entries:
            where = Where(kind, None, name)
            ti = 0
            for b in blocks:
                if isinstance(b, Paragraph):
                    yield b, None, where
                elif isinstance(b, Table):
                    cols = len(b.grid_pt) or max((len(r.cells) for r in b.rows), default=0)
                    for ri, row in enumerate(b.rows):
                        for ci, cell in enumerate(row.cells):
                            for p in iter_paragraphs(cell.blocks):
                                yield p, Loc(ti, ri, ci, cols), where
                    ti += 1


def units(doc: Document) -> list[Unit]:
    out: list[Unit] = []
    for p, loc, where in walk(doc):
        text = p.text                   # collapsed once: is_empty is text == ""
        if text == "":
            continue
        bold, spans = char_fmt(p)
        lead, marks = tab_marks(p)
        out.append(Unit(len(out), text,p.num.marker if p.num else "", bold, spans, loc, p, lead, marks,
                        where.stream, where.note, note_marks(p), where.part))
    return out


def table_by_ti(doc: Document, ti: int) -> Table:
    """The ti-th top-level table -- the table a Loc.ti refers to."""
    return [b for b in doc.blocks if isinstance(b, Table)][ti]
