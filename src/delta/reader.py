"""One seam for reading a source file: the reader is picked from the file's first bytes, never
from its name. Both readers return the same Document. The PDF reader is imported on first use.

A PDF is read in two steps: `read_source` reads each file on its own (what a cache keeps), and
`documents` finishes a pair together, so both PDFs of a pair are read by the same rules.
`read_document` is the one-file shortcut."""
from __future__ import annotations

from .model import Document


class UnknownKind(ValueError):
    """Neither a Word document nor a PDF."""


def kind_of(data: bytes) -> str | None:
    if b"%PDF-" in data[:1024]:            # the header may follow a few stray bytes
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        return "docx"
    return None


def read_source(data: bytes, progress=None):
    """A Word document's Document, or a PDF's PdfReading for `documents` to finish. Either has a
    `source_kind`."""
    kind = kind_of(data)
    if kind == "pdf":
        from .pdfread import read_pdf
        return read_pdf(data, progress)
    if kind == "docx":
        from .docx.parser import parse_docx
        return parse_docx(data)
    raise UnknownKind("not a Word document or a PDF")


def documents(*sources) -> list[Document]:
    """The Documents of `read_source` results: the PDFs among them finished together, and a Word
    document and a PDF then put in one form (`mixed.level`)."""
    pdfs = [s for s in sources if not isinstance(s, Document)]
    if not pdfs:
        return list(sources)
    from .pdfread import finish
    done = iter(finish(*pdfs))
    docs = [s if isinstance(s, Document) else next(done) for s in sources]
    if len(docs) == 2 and docs[0].source_kind != docs[1].source_kind:
        from .mixed import level
        return list(level(*docs))
    return docs


def read_document(data: bytes, progress=None) -> Document:
    (doc,) = documents(read_source(data, progress))
    return doc
