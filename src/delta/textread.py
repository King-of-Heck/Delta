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
    rows = [{"type": r["type"], "segments": _spaced([{"m": s["m"], "t": s["t"]} for s in r["segments"]])}
            for r in d["changes"]]
    return {"summary": d["summary"], "rows": rows}


def _spaced(segs: list[dict]) -> list[dict]:
    """A word replaced by another comes as a deletion and an insertion side by side with no space
    between them ("notvery"). Give both the space that follows them (or, before punctuation or at
    the end, the one before them), as a lone deletion or insertion already has: "~~not ~~very"."""
    segs = [dict(s) for s in segs]
    i = 0
    while i + 1 < len(segs):
        a, b = segs[i], segs[i + 1]
        if {a["m"], b["m"]} != {"del", "ins"}:
            i += 1
            continue
        after = segs[i + 2] if i + 2 < len(segs) and segs[i + 2]["m"] == "eq" else None
        before = segs[i - 1] if i > 0 and segs[i - 1]["m"] == "eq" else None
        if after and after["t"][:1].isspace() and not a["t"][-1:].isspace() and not b["t"][-1:].isspace():
            n = len(after["t"]) - len(after["t"].lstrip())
            space, after["t"] = after["t"][:n], after["t"][n:]
            a["t"] += space
            b["t"] += space
        elif before and before["t"][-1:].isspace() and not a["t"][:1].isspace() and not b["t"][:1].isspace():
            n = len(before["t"]) - len(before["t"].rstrip())
            space, before["t"] = before["t"][-n:], before["t"][:-n]
            a["t"] = space + a["t"]
            b["t"] = space + b["t"]
        i += 2
    return [s for s in segs if s["t"]]


INS_COLOR = "#0000ff"                # the viewer's textredline.js uses the same two colours
DEL_COLOR = "#ff0000"


def redline_html(rows: list[dict]) -> str:
    """The redline as HTML paragraphs, insertions blue underlined, deletions red struck through."""
    from html import escape

    def seg(s: dict) -> str:
        t = escape(s["t"], quote=True)
        if s["m"] == "ins":
            return f'<u style="color:{INS_COLOR}">{t}</u>'
        if s["m"] == "del":
            return f'<s style="color:{DEL_COLOR}">{t}</s>'
        return t
    return "".join("<p>" + "".join(seg(s) for s in r["segments"]) + "</p>" for r in rows)


def redline_email(rows: list[dict], subject: str = "Redline") -> bytes:
    """An unsent email (.eml) holding the redline. `X-Unsent: 1` makes Outlook open it as a new
    message to edit and send, not as a received one."""
    from email.message import EmailMessage
    from email.policy import SMTP

    body = ('<html><head><meta charset="utf-8"></head>'
            '<body style="font-family:Calibri,Arial,sans-serif;font-size:11pt">'
            + redline_html(rows) + "</body></html>")
    msg = EmailMessage(policy=SMTP)
    msg["X-Unsent"] = "1"
    msg["Subject"] = subject
    msg.set_content(body, subtype="html", charset="utf-8", cte="base64")
    return msg.as_bytes()
