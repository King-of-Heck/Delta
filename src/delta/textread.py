"""Pasted text as a Document, and two texts compared by the same engine as two documents.

Each non-blank line is one plain paragraph (no style, numbering or formatting): what is in the box is
what is compared, so a line typed as "1." is text, not a list number. Blank lines only separate."""
from __future__ import annotations

from .diff.compare import compare
from .model import Document, Paragraph, ParaProps, Run, RunProps, Section

TEXT_MAX_BYTES = 200 * 1024          # per side; the viewer's textredline.js holds the same number


def read_text(s: str) -> Document:
    lines = s.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = [Paragraph([Run(t, RunProps())], ParaProps()) for t in (line.strip() for line in lines) if t]
    return Document(blocks=blocks, sections=[Section()], source_kind="text")


def compare_texts(a: str, b: str, *, ignore_case: bool = False) -> dict:
    d = compare(read_text(a), read_text(b), ignore_case=ignore_case, count_numbering=False).to_dict()
    rows = [{"type": r["type"], "segments": [{"m": s["m"], "t": s["t"]} for s in r["segments"]]}
            for r in d["changes"]]
    return {"summary": d["summary"], "rows": rows}
