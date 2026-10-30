"""A Word document against a PDF: both put in one form before they are compared.

Each file is read as it would be alone. A PDF has its list numbers in its text and prints capitals
as capitals; Word holds both apart from the text. This runs only for a pair of one of each, so a
same-kind comparison cannot move. The Word Document is a deep copy: the app caches what it read."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, replace

from .model import Document, NumInfo, Paragraph, Run, Table, collapse_ws, iter_paragraphs

_SPACE = " \t "
_SHAPE = re.compile(r"(\(?(?:\d+(?:\.\d+)*|[A-Za-z]{1,4}|[ivxlcIVXLC]{1,7})[).]|\d+(?:\.\d+)+)(?=[\s ]|$)")
_WORDED = re.compile(r"([A-Za-z]+) (\d+(?:\.\d+)*)(?=[\s ]|$)")
_BULLETS = str.maketrans({"�": "•", "": "•", "": "•", "": "•",
                          "·": "•"})
_TOC_NUMBER = re.compile(r"\t([^\t ]{1,8})\s*$")
_LINE = re.compile(r"[\n\v]")


@dataclass
class _Numbers:
    """What Word's own numbering says a leading number can look like."""
    exact: list[str]             # longest first
    letters: set[str]            # one character: only a PDF's hanging line, else "A contractor" is a number
    words: dict[str, str]        # "SECTION" -> "Section"


def level(a: Document, b: Document) -> tuple[Document, Document]:
    source, pdf = (a, b) if b.source_kind == "pdf" else (b, a)
    word = _word_as_printed(source)
    _drop_page_titles(pdf, source, word)
    numbers = _known_numbers(word)
    for doc in (word, pdf):
        for p in _all_paragraphs(doc):
            _bullets(p)
            _lift(p, numbers, doc is pdf and p.props.ind_hanging_pt > 0)
        _join_cells(doc.blocks)
    return (word, pdf) if b.source_kind == "pdf" else (pdf, word)


def _all_paragraphs(doc: Document):
    yield from iter_paragraphs(doc.blocks)
    for notes in (doc.footnotes, doc.endnotes):
        for blocks in notes.values():
            yield from iter_paragraphs(blocks)


def _word_as_printed(source: Document) -> Document:
    word = copy.deepcopy(source)
    word.parts, word.comments, word.comment_anchors = {}, {}, {}
    for p in _all_paragraphs(word):
        _contents_number(p)
        p.runs = [replace(r, text=r.text.upper()) if (r.props.caps or r.props.small_caps) and r.props.field is None
                  else r for r in p.runs]
    return word


def _drop_page_titles(pdf: Document, source: Document, word: Document) -> None:
    """A line the PDF prints that is Word's page header or footer text and nowhere in Word's body:
    a title the reader could not tell from the page's first paragraph."""
    titles = set()
    for blocks in source.parts.values():
        for p in iter_paragraphs(blocks):
            whole = "".join(r.text for r in p.runs)
            titles |= {_key(t) for t in [whole, *_LINE.split(whole)]}
    titles.discard("")
    titles -= {_key(p.text) for p in _all_paragraphs(word)}
    if titles:
        pdf.blocks = [b for b in pdf.blocks if not (isinstance(b, Paragraph) and _key(b.text) in titles)]


def _key(text: str) -> str:
    return collapse_ws(text).upper()


def _join_cells(blocks: list) -> None:
    """A table cell's plain lines as one paragraph: Word's separate paragraphs and line breaks are
    not the lines a PDF's cell is read into."""
    for b in blocks:
        if isinstance(b, Table):
            for row in b.rows:
                for cell in row.cells:
                    _join_cells(cell.blocks)
                    cell.blocks = _joined(cell.blocks)


def _joined(blocks: list) -> list:
    out: list = []
    for b in blocks:
        last = out[-1] if out else None
        if isinstance(b, Paragraph) and isinstance(last, Paragraph) and b.num is None and last.num is None:
            if b.is_empty and not any(r.props.note for r in b.runs):
                continue
            if last.is_empty:
                out[-1] = b
                continue
            last.runs = [*last.runs, Run(" ", last.runs[-1].props), *b.runs]
            continue
        out.append(b)
    return out


def _contents_number(p: Paragraph) -> None:
    """A contents line's page number as the PDF reader holds it: drawn as printed, compared as its token."""
    if not (p.props.style_id or "").lower().startswith("toc") or not p.runs:
        return
    text = "".join(r.text for r in p.runs)
    m = _TOC_NUMBER.search(text)
    if m:
        p.runs = [Run(text[:m.start(1)], p.runs[0].props),
                  Run("{PAGEREF}", replace(p.runs[-1].props, field="PAGEREF", field_text=m[1]))]


def _known_numbers(word: Document) -> _Numbers:
    found = {p.num.marker.strip() for p in _all_paragraphs(word) if p.num and not p.num.is_bullet}
    found.discard("")
    return _Numbers(sorted((m for m in found if len(m) > 1), key=len, reverse=True),
                    {m for m in found if len(m) == 1},
                    {m.split()[0].upper(): m.split()[0] for m in found if _WORDED.fullmatch(m)})


def _bullets(p: Paragraph) -> None:
    p.runs = [replace(r, text=r.text.translate(_BULLETS)) if r.props.field is None else r for r in p.runs]


def _leading_number(text: str, numbers: _Numbers, letters: bool) -> tuple[str, int] | None:
    """(the number as it will be held, its length in `text`), or None."""
    for known in numbers.exact:
        if text.startswith(known) and (len(text) == len(known) or text[len(known)] in _SPACE):
            return known, len(known)
    m = _WORDED.match(text)
    if m and m[1].upper() in numbers.words:
        return f"{numbers.words[m[1].upper()]} {m[2]}", m.end()
    if letters and text[:1] in numbers.letters and text[1:2] in _SPACE and len(text) > 1:
        return text[0], 1
    m = _SHAPE.match(text)
    return (m[1], m.end()) if m else None


def _lift(p: Paragraph, numbers: _Numbers, letters: bool) -> None:
    if p.num is not None or not p.runs:
        return
    text = "".join(r.text for r in p.runs)
    body = text.lstrip(_SPACE)
    found = _leading_number(body, numbers, letters)
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
