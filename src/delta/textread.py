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
    between them ("notvery"). Where the words around them are spaced (a space after them, or before
    them), put a plain, unmarked space between the two: "~~not~~ very". It is in neither text (each
    side keeps its own single space), so it is shown only; the deleted and inserted words are not
    given a space to mark as changed."""
    out: list[dict] = []
    for i, s in enumerate(segs):
        out.append(dict(s))
        if i + 1 == len(segs):
            break
        nxt = segs[i + 1]
        if {s["m"], nxt["m"]} != {"del", "ins"} or s["t"][-1:].isspace() or nxt["t"][:1].isspace():
            continue
        after = segs[i + 2]["t"] if i + 2 < len(segs) and segs[i + 2]["m"] == "eq" else ""
        before = segs[i - 1]["t"] if i > 0 and segs[i - 1]["m"] == "eq" else ""
        if after[:1].isspace() or before[-1:].isspace():
            out.append({"m": "eq", "t": " "})
    return out


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
