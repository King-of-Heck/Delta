"""A Word document against a PDF: both put in one form before they are compared.

Each file is read as it would be alone. A PDF has its list numbers in its text and prints capitals
as capitals; Word holds both apart from the text. This runs only for a pair of one of each, so a
same-kind comparison cannot move. The Word Document is a deep copy: the app caches what it read."""
from __future__ import annotations

import copy
import re
from dataclasses import replace

from .model import Document, NumInfo, Paragraph, Run, Table, iter_paragraphs

_SPACE = " \t "
_SHAPE = re.compile(r"(\(?(?:\d+(?:\.\d+)*|[A-Za-z]{1,4}|[ivxlcIVXLC]{1,7})[).]|\d+(?:\.\d+)+)(?=[\s ]|$)")
_WORDED = re.compile(r"([A-Za-z]+) (\d+(?:\.\d+)*)(?=[\s ]|$)")
_BULLETS = str.maketrans({"�": "•", "": "•", "": "•", "·": "•"})
_TOC_NUMBER = re.compile(r"\t([^\t ]{1,8})\s*$")
_BREAK = re.compile(r"[\n\v]")


def level(a: Document, b: Document) -> tuple[Document, Document]:
    word, pdf = (a, b) if b.source_kind == "pdf" else (b, a)
    word = _word_as_printed(word)
    markers, words = _known_markers(word)
    for doc in (word, pdf):
        for p in _all_paragraphs(doc):
            _bullets(p)
            _lift(p, markers, words)
    return (word, pdf) if b.source_kind == "pdf" else (pdf, word)


def _all_paragraphs(doc: Document):
    yield from iter_paragraphs(doc.blocks)
    for notes in (doc.footnotes, doc.endnotes):
        for blocks in notes.values():
            yield from iter_paragraphs(blocks)


def _word_as_printed(source: Document) -> Document:
    word = copy.deepcopy(source)
    word.parts, word.comments, word.comment_anchors = {}, {}, {}
    _split_cells(word.blocks)
    for p in _all_paragraphs(word):
        _contents_number(p)
        p.runs = [replace(r, text=r.text.upper()) if (r.props.caps or r.props.small_caps) and r.props.field is None
                  else r for r in p.runs]
    return word


def _split_cells(blocks: list) -> None:
    for b in blocks:
        if isinstance(b, Table):
            for row in b.rows:
                for cell in row.cells:
                    _split_cells(cell.blocks)
                    cell.blocks = [q for x in cell.blocks for q in _split_breaks(x)]


def _split_breaks(block) -> list:
    if not isinstance(block, Paragraph) or not any(_BREAK.search(r.text) for r in block.runs):
        return [block]
    out, runs = [], []
    for r in block.runs:
        parts = _BREAK.split(r.text)
        for i, part in enumerate(parts):
            if i:
                out.append(runs)
                runs = []
            if part or r.props.note is not None:
                runs.append(replace(r, text=part))
    out.append(runs)
    return [Paragraph(rs, block.props, block.num if i == 0 else None) for i, rs in enumerate(out)]


def _contents_number(p: Paragraph) -> None:
    """A contents line's page number as the PDF reader holds it: drawn as printed, compared as its token."""
    if not (p.props.style_id or "").lower().startswith("toc") or not p.runs:
        return
    text = "".join(r.text for r in p.runs)
    m = _TOC_NUMBER.search(text)
    if m:
        p.runs = [Run(text[:m.start(1)], p.runs[0].props),
                  Run("{PAGEREF}", replace(p.runs[-1].props, field="PAGEREF", field_text=m[1]))]


def _known_markers(word: Document) -> tuple[dict[str, str], set[str]]:
    """Word's own numbers, by capitals, longest first; and the first words of its worded numbers."""
    found = {p.num.marker.strip() for p in _all_paragraphs(word)
             if p.num and not p.num.is_bullet and len(p.num.marker.strip()) > 1}
    ordered = sorted(found, key=len, reverse=True)
    words = {m.split()[0].upper() for m in found if _WORDED.fullmatch(m)}
    return {m.upper(): m for m in ordered}, words


def _bullets(p: Paragraph) -> None:
    p.runs = [replace(r, text=r.text.translate(_BULLETS)) if r.props.field is None else r for r in p.runs]


def _leading_number(text: str, markers: dict[str, str], words: set[str]) -> tuple[str, int] | None:
    """(the number as it will be held, its length in `text`), or None."""
    upper = text.upper()
    for key, spelled in markers.items():
        if upper.startswith(key) and (len(text) == len(key) or text[len(key)] in _SPACE):
            return spelled, len(key)
    m = _WORDED.match(text)
    if m and m[1].upper() in words:
        return m[0], m.end()
    m = _SHAPE.match(text)
    return (m[1], m.end()) if m else None


def _lift(p: Paragraph, markers: dict[str, str], words: set[str]) -> None:
    if p.num is not None or not p.runs:
        return
    text = "".join(r.text for r in p.runs)
    body = text.lstrip(_SPACE)
    found = _leading_number(body, markers, words)
    if found is None:
        return
    number, length = found
    left = len(text) - len(body) + length
    left += len(text[left:]) - len(text[left:].lstrip(_SPACE))
    runs = []
    for r in p.runs:
        take = min(left, len(r.text))
        left -= take
        if take == len(r.text) and take and r.props.note is None:
            continue
        runs.append(replace(r, text=r.text[take:]) if take else r)
    p.runs = runs
    p.num = NumInfo(0, 0, number, False)
