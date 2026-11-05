import io
import sys

import pytest

from delta.pdfread import extract
from delta.pdfread.extract import DAMAGED, NO_TEXT, PROTECTED, extract_pages, page_count, unreadable
from delta.pdfread.types import PageData, PdfRefused
from delta.testing.makepdf import make_pdf

ONE = [[("text", 72, 100, 12, "Hello world"), ("text", 72, 130, 12, "Bold", "B"),
        ("rect", 72, 200, 200, 40), ("line", 172, 200, 172, 240)]]


def test_a_page_gives_runs_segments_and_its_size():
    (p,) = extract_pages(make_pdf(ONE))
    assert (p.width, p.height) == (612, 792)
    hello, bold = p.runs
    assert (hello.text, hello.font, hello.size) == ("Hello world", "Helvetica", 12)
    assert (hello.x0, hello.y) == pytest.approx((72, 100), abs=0.01)
    assert hello.x1 > hello.x0
    assert (bold.text, bold.font) == ("Bold", "Helvetica-Bold")
    assert len(p.segs) == 5 and p.chars == 15


def test_progress_is_called_per_page_and_page_count_matches():
    data = make_pdf([[("text", 72, 100, 12, "a")], [("text", 72, 100, 12, "b")], []])
    seen = []
    pages = extract_pages(data, lambda done, total: seen.append((done, total)))
    assert seen == [(1, 3), (2, 3), (3, 3)] and page_count(data) == 3
    assert [len(p.runs) for p in pages] == [1, 1, 0]


def test_unreadable_means_a_scan_or_gibberish_not_a_blank_page():
    assert unreadable(PageData(1, 1, images=1))
    assert unreadable(PageData(1, 1, forms=1))
    assert unreadable(PageData(1, 1, chars=10, bad_chars=4))
    assert not unreadable(PageData(1, 1))
    assert not unreadable(PageData(1, 1, chars=10, bad_chars=3, images=2))
    (scan,) = extract_pages(make_pdf([[("image", 0, 0, 612, 792)]]))
    assert unreadable(scan)


def test_a_user_password_is_refused_and_an_owner_only_password_opens():
    with pytest.raises(PdfRefused) as e:
        extract_pages(make_pdf(ONE, user_password="secret"))
    assert str(e.value) == PROTECTED
    with pytest.raises(PdfRefused):
        page_count(make_pdf(ONE, user_password="secret"))
    (p,) = extract_pages(make_pdf(ONE, owner_password="own"))
    assert p.runs[0].text == "Hello world"


def test_a_pdf_that_needs_a_crypto_library_is_refused_as_protected(monkeypatch):
    from pypdf.errors import DependencyError

    def boom(*a, **k):
        raise DependencyError("cryptography>=3.1 is required for AES algorithm")
    monkeypatch.setattr(extract, "_page", boom)
    with pytest.raises(PdfRefused) as e:
        extract_pages(make_pdf(ONE))
    assert str(e.value) == PROTECTED


def test_a_damaged_file_is_refused():
    for junk in (b"%PDF-1.7\nnot really", b""):
        with pytest.raises(PdfRefused) as e:
            extract_pages(junk)
        assert str(e.value) == DAMAGED


def test_the_messages_are_the_agreed_wording():
    assert NO_TEXT == ("This PDF has no text, so it looks scanned. Delta can only compare PDFs "
                       "that contain real text.")
    assert PROTECTED == "This PDF is password-protected. Open it and print it to a new PDF first."


def test_page_reads_resources_inherited_from_the_pages_tree():
    """pypdf's own reader.pages flattens /Resources onto every page it hands out (verified against
    the installed pypdf 6.19 by reading PdfDocCommon._flatten), so the inherited case cannot be
    reached through the public extract_pages() API; PdfWriter re-inlines it too when a page is
    moved and re-written. This stands _page() up directly against a minimal page-like object whose
    plain get("/Resources") is empty but whose get_inherited("/Resources") is not, the shape a
    genuinely un-flattened pypdf page would have."""
    from pypdf import PdfReader

    data = make_pdf(ONE)
    real_page = PdfReader(io.BytesIO(data)).pages[0]

    class NoDirectResources:
        cropbox = real_page.cropbox
        rotation = real_page.rotation

        def get(self, key, default=None):
            return default if key == "/Resources" else real_page.get(key, default)

        def get_inherited(self, key, default=None):
            return real_page.get(key, default) if key == "/Resources" else default

        def get_contents(self):
            return real_page.get_contents()

    reader = PdfReader(io.BytesIO(data))
    out = extract._page(reader, NoDirectResources(), {})
    assert out.runs[0].text == "Hello world" and out.runs[0].font == "Helvetica"


def test_importing_the_reader_does_not_import_pypdf():
    import subprocess
    code = "import sys, delta.pdfread.extract, delta.pdfread.content; print('pypdf' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_a_stream_the_fast_reader_leaves_to_pypdf_reads_the_same(monkeypatch):
    data = make_pdf(ONE + [[("text", 72, 100, 12, "Second (page)"), ("line", 72, 300, 400, 300)]])
    fast = extract_pages(data)
    monkeypatch.setattr(extract, "parse_content", lambda _data: None)
    assert extract_pages(data) == fast
