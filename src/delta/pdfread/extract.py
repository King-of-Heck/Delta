"""Open a PDF and hand each page's drawing to the interpreter. The ONLY module that imports
pypdf, and only inside its functions, so a Word-only run never loads it. pypdf does the file
structure (cross-references, filters, decryption; tokenizing the streams tokens.py does not take);
the reading is ours."""
from __future__ import annotations

import io
from datetime import datetime, timezone

from .content import base_matrix, interpret
from .pdffonts import PdfFont, load_font
from .tokens import parse as parse_content
from .types import PageData, PdfRefused

NO_TEXT = ("This PDF has no text, so it looks scanned. Delta can only compare PDFs "
           "that contain real text.")
PROTECTED = "This PDF is password-protected. Open it and print it to a new PDF first."
DAMAGED = "This PDF could not be read; the file looks damaged."
GIBBERISH = 0.3       # a page with more than this share of unmappable characters is unreadable


def unreadable(page: PageData) -> bool:
    """A scanned page (an image, no text), a page whose text lives in a form XObject we did not
    walk into, or one whose text does not decode. A blank page is fine."""
    if page.chars == 0:
        return page.images > 0 or page.forms > 0
    return page.bad_chars / page.chars > GIBBERISH


def _refusal(e: Exception) -> PdfRefused:
    """Pypdf raises its own DependencyError (crypto extras not installed) when it hits an
    encryption scheme it cannot decrypt without them; everything else means a damaged file."""
    return PdfRefused(PROTECTED if type(e).__name__ == "DependencyError" else DAMAGED)


def _obj(x):
    return x.get_object() if hasattr(x, "get_object") else x


def _plain(o):
    """A content-stream operand as plain Python: shown strings as bytes, names as str."""
    if isinstance(o, bytes):
        return bytes(o)
    if hasattr(o, "original_bytes"):
        return o.original_bytes
    if isinstance(o, list):
        return [_plain(x) for x in o]
    if isinstance(o, bool):
        return o
    if isinstance(o, int):
        return int(o)
    if isinstance(o, float):
        return float(o)
    return str(o)


def _reader(data: bytes):
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
    except Exception:
        raise PdfRefused(DAMAGED) from None
    if encrypted:
        try:
            opened = reader.decrypt("")
        except Exception:
            opened = 0
        if not opened:
            raise PdfRefused(PROTECTED)
    return reader


def page_count(data: bytes) -> int:
    return page_count_and_date(data)[0]


def page_count_and_date(data: bytes) -> tuple[int, datetime | None]:
    """The page count, and when the PDF was last saved (its ModDate, else its CreationDate; a date
    without a time zone is taken as UTC); None when it says neither."""
    reader = _reader(data)
    try:
        pages = len(reader.pages)
    except Exception as e:
        raise _refusal(e) from None
    when = None
    try:
        meta = reader.metadata
        if meta is not None:
            when = meta.modification_date or meta.creation_date
    except Exception:
        when = None                                # a malformed date string is no date
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return pages, when


def _page(reader, page, font_cache: dict, memo: dict | None = None) -> PageData:
    from pypdf.generic import ContentStream
    box = tuple(float(v) for v in page.cropbox)
    rotation = int(page.rotation or 0) % 360
    w, h = box[2] - box[0], box[3] - box[1]
    out = PageData(*((h, w) if rotation in (90, 270) else (w, h)))
    if hasattr(page, "get_inherited"):
        res = _obj(page.get_inherited("/Resources"))
    else:
        res = _obj(page.get("/Resources"))
    res = res or {}
    fonts: dict[str, PdfFont] = {}
    for name, ref in (_obj(res.get("/Font")) or {}).items():
        key = (getattr(ref, "idnum", None), getattr(ref, "generation", None))
        if key[0] is None:
            fonts[str(name)] = load_font(ref)
        else:
            if key not in font_cache:
                font_cache[key] = load_font(ref)
            fonts[str(name)] = font_cache[key]
    xobjects = {}
    for name, ref in (_obj(res.get("/XObject")) or {}).items():
        xobjects[str(name)] = "image" if str(_obj(ref).get("/Subtype")) == "/Image" else "form"
    contents = page.get_contents()
    if contents is not None:
        stream = ContentStream(contents, reader)
        ops = parse_content(stream.get_data())        # our fast reader; None = leave it to pypdf
        if ops is None:
            ops = [([_plain(o) for o in operands], bytes(op)) for operands, op in stream.operations]
        interpret(ops, fonts, xobjects, base_matrix(box, rotation), out, memo)
    return out


def extract_pages(data: bytes, progress=None) -> list[PageData]:
    reader = _reader(data)
    font_cache: dict = {}
    memo: dict = {}                    # decoded strings, kept from page to page
    try:
        total = len(reader.pages)
        out = []
        for i, page in enumerate(reader.pages):
            out.append(_page(reader, page, font_cache, memo))
            if progress is not None:
                progress(i + 1, total)
        return out
    except PdfRefused:
        raise
    except Exception as e:
        raise _refusal(e) from None
