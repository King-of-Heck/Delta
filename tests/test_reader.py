import subprocess
import sys

import pytest

from delta.reader import UnknownKind, documents, kind_of, read_document, read_source
from delta.testing.makedocx import DOC, P, make_docx
from delta.testing.makepdf import make_pdf

PDF = make_pdf([[("text", 72, 100, 12, "Hello from a PDF")]])
DOCX = make_docx({"word/document.xml": DOC(P("Hello from Word"))})


def test_the_kind_comes_from_the_first_bytes_not_the_name():
    assert (kind_of(PDF), kind_of(DOCX), kind_of(b"plain text"), kind_of(b"")) == ("pdf", "docx", None, None)
    assert kind_of(b"\xef\xbb\xbf junk %PDF-1.4 ...") == "pdf"


def test_read_document_dispatches_and_refuses_the_unknown():
    assert read_document(PDF).source_kind == "pdf" and "Hello from a PDF" in read_document(PDF).blocks[0].text
    assert read_document(DOCX).source_kind == "docx"
    with pytest.raises(UnknownKind, match="not a Word document or a PDF"):
        read_document(b"plain text")


def test_a_word_comparison_never_imports_pypdf():
    # FakeResolver's faces carry no real font file (by design: exact metrics without depending on
    # installed fonts), so a real fpdf2 write needs a stand-in for the face -> file step; a core
    # font name serves fine here, since this test only checks which modules get imported.
    code = ("import sys\n"
            "from delta.pdf.fpdf_sink import FpdfPainter\n"
            "FpdfPainter._family = lambda self, face: 'helvetica'\n"
            "from delta.server.session import Session\n"
            "from delta.testing.fakefonts import FakeResolver\n"
            "from delta.testing.makedocx import DOC, P, make_docx\n"
            "d = lambda t: make_docx({'word/document.xml': DOC(P(t))})\n"
            "s = Session(fonts=FakeResolver())\n"
            "s.load('a.docx', d('one two'), 'b.docx', d('one three'))\n"
            "s.payload(); s.pdf()\n"
            "print('pypdf' in sys.modules)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_a_pdf_is_read_on_its_own_and_finished_with_the_other_side():
    pdf, word = read_source(PDF), read_source(DOCX)
    assert (pdf.source_kind, word.source_kind) == ("pdf", "docx")
    a, b = documents(pdf, word)
    assert "Hello from a PDF" in a.blocks[0].text and "Hello from Word" in b.blocks[0].text
    assert b is not word          # a Word document against a PDF is a copy: the app caches what it read
