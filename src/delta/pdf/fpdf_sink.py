"""The Painter that writes a PDF with fpdf2. Fonts are registered once per (file, TTC index)
and embedded as subsets at output; fake bold is fill + stroke, fake italic a 12 degree skew about
the run's baseline origin. Units are points, origin top-left (fpdf2's own frame with unit="pt")."""
from __future__ import annotations

from datetime import datetime

from fpdf import FPDF
from fpdf.drawing_primitives import convert_to_device_color
from fpdf.enums import TextMode

from .painter import rgb

STROKE_FRACTION = 0.03      # fake-bold stroke width as a fraction of the size
ITALIC_DEGREES = 12
DASH, DASH_GAP = 1.0, 1.5   # the dotted underline


class FpdfPainter:
    def __init__(self, creation: datetime | None = None, title: str = "Redline comparison"):
        self.pdf = FPDF(unit="pt", format=(612, 792))
        self.pdf.set_auto_page_break(False)
        self.pdf.set_margins(0, 0, 0)
        self.pdf.set_title(title)
        self.pdf.set_creator("Delta")
        if creation is not None:
            self.pdf.set_creation_date(creation)
        self._fonts: dict[tuple[str, int], str] = {}
        self._colors: dict[str, object] = {}       # 6-hex -> fpdf2 device colour (see _color)
        self._font: tuple | None = None            # (family, size) of the last set_font call

    # -- Painter ---------------------------------------------------------------------------
    def page(self, w: float, h: float) -> None:
        self.pdf.add_page(format=(w, h))

    def text(self, x: float, baseline: float, text: str, face, size: float, color: str,
             fake_bold: bool = False, fake_italic: bool = False, width: float | None = None,
             role: str | None = None) -> None:
        if not text:
            return
        p = self.pdf
        font = (self._family(face), size)
        if font != self._font:
            # The same font again is a call fpdf2 returns from at once (its font state is ours:
            # nothing else here sets a font, and a new page or a local context keeps it).
            p.set_font(font[0], "", size)
            self._font = font
        p.set_text_color(self._color(color))
        if not fake_bold:
            self._emit(x, baseline, text, fake_italic)
            return
        # fpdf2 writes no "0 Tr" to leave the fill mode again, and only brackets a text object in
        # q/Q when the fill colour differs from the text colour: without a context of our own the
        # stroke mode would stay set for every later run on the page.
        with p.local_context(text_mode=TextMode.FILL_STROKE, line_width=size * STROKE_FRACTION,
                             draw_color=rgb(color)):
            self._emit(x, baseline, text, fake_italic)

    def _emit(self, x: float, baseline: float, text: str, skewed: bool) -> None:
        p = self.pdf
        if skewed:
            with p.skew(ax=ITALIC_DEGREES, x=x, y=baseline):
                p.text(x, baseline, text)
        else:
            p.text(x, baseline, text)

    def rule(self, x1: float, x2: float, y: float, thickness: float, color: str, dotted: bool = False) -> None:
        p = self.pdf
        p.set_draw_color(self._color(color))
        p.set_line_width(thickness)
        if dotted:
            p.set_dash_pattern(dash=DASH, gap=DASH_GAP)
        p.line(x1, y, x2, y)
        if dotted:
            p.set_dash_pattern()

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float, color: str) -> None:
        p = self.pdf
        p.set_draw_color(self._color(color))
        p.set_line_width(width)
        p.line(x1, y1, x2, y2)

    def box(self, x: float, y: float, w: float, h: float, color: str, role: str | None = None) -> None:
        p = self.pdf
        p.set_fill_color(self._color(color))
        p.rect(x, y, w, h, style="F")

    # -- output -----------------------------------------------------------------------------
    def output(self) -> bytes:
        return bytes(self.pdf.output())

    def _color(self, color: str):
        """fpdf2's device colour for a 6-hex colour, converted once per colour (as fpdf2 itself
        converts an (r, g, b) triple; the colour objects are frozen values)."""
        dev = self._colors.get(color)
        if dev is None:
            dev = self._colors[color] = convert_to_device_color(*rgb(color))
        return dev

    def _family(self, face) -> str:
        key = (face.path, face.font_number)
        name = self._fonts.get(key)
        if name is None:
            name = f"cf{len(self._fonts)}"
            self.pdf.add_font(name, "", face.path, collection_font_number=face.font_number)
            self._fonts[key] = name
        return name
