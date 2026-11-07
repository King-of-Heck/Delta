"""The Painter that writes one SVG per page. The same draw_layout walk that writes the PDF emits
these, so the screen and the PDF are one drawing. User units are points (viewBox "0 0 w h"); the
page's CSS width sets the zoom."""
from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from ..layout.fonts import default_resolver, glyph_outlines
from ..layout.pages import Layout
from ..pdf.draw import PdfOptions, draw_layout
from ..pdf.fpdf_sink import DASH, DASH_GAP, ITALIC_DEGREES, STROKE_FRACTION
from ..pdf.rendersets import render_set

SVG_NS = "http://www.w3.org/2000/svg"


def _n(v: float) -> str:
    return f"{v:.2f}"


class SvgPainter:
    def __init__(self):
        self._pages: list[list[str]] = []
        self._families: dict[str, str] = {}      # family -> its quoted font-family attribute value

    # -- Painter ---------------------------------------------------------------------------
    def page(self, w: float, h: float) -> None:
        self._pages.append([f'<svg xmlns="{SVG_NS}" viewBox="0 0 {_n(w)} {_n(h)}" '
                            f'width="{_n(w)}pt" height="{_n(h)}pt">'])

    def text(self, x: float, baseline: float, text: str, face, size: float, color: str,
             fake_bold: bool = False, fake_italic: bool = False, width: float | None = None,
             role: str | None = None) -> None:
        if not text:
            return
        if getattr(face, "symbol", False) and role != "gutter":
            self._outlines(x, baseline, text, face, size, color, fake_bold, fake_italic)
            return
        # One string per run, built in place (this is the hot path of the on-screen pages); the
        # attributes and number formats are exactly those of _n and escape.
        family = self._families.get(face.family)
        if family is None:
            family = self._families[face.family] = quoteattr(face.family)
        if role == "gutter":
            # anchored at its right edge so the viewer can enlarge it leftwards, away from the bar
            s = (f'<text x="{x + (width or 0):.2f}" y="{baseline:.2f}" font-family={family} '
                 f'font-size="{size:.2f}" fill="#{color}" class="gutter" text-anchor="end"')
            width = None
        else:
            s = (f'<text x="{x:.2f}" y="{baseline:.2f}" font-family={family} '
                 f'font-size="{size:.2f}" fill="#{color}"')
        if face.bold and not face.synthetic:
            s += ' font-weight="bold"'
        if face.italic and not face.synthetic:
            s += ' font-style="italic"'
        if fake_bold:
            s += f' stroke="#{color}" stroke-width="{size * STROKE_FRACTION:.2f}"'
        if fake_italic:
            s += (f' transform="translate({x:.2f} {baseline:.2f}) skewX(-{ITALIC_DEGREES}) '
                  f'translate(-{x:.2f} -{baseline:.2f})"')
        if width:
            s += f' textLength="{width:.2f}" lengthAdjust="spacing"'
        if "&" in text or "<" in text or ">" in text:
            text = escape(text)
        self._pages[-1].append(f'{s} xml:space="preserve">{text}</text>')

    def _outlines(self, x: float, baseline: float, text: str, face, size: float, color: str,
                  fake_bold: bool, fake_italic: bool) -> None:
        """A symbol face (Wingdings, Symbol) as glyph outlines: browsers draw its private-use
        characters with a fallback glyph of another width, the PDF embeds the real one, so the
        screen takes the outlines straight from the font file to match."""
        outlines, upem = glyph_outlines(face.path, face.font_number, text)
        k = size / upem
        attrs = [f'fill="#{color}"']
        if fake_bold:
            attrs += [f'stroke="#{color}"', f'stroke-width="{_n(size * STROKE_FRACTION / k)}"']
        if fake_italic:
            attrs.append(f'transform="translate({_n(x)} {_n(baseline)}) skewX(-{ITALIC_DEGREES}) '
                         f'translate(-{_n(x)} -{_n(baseline)})"')
        parts = [f"<g {' '.join(attrs)}>"]
        for d, adv in outlines:
            if d:
                parts.append(f'<path d="{d}" transform="translate({_n(x)} {_n(baseline)}) scale({k:.6f} {-k:.6f})"/>')
            x += adv * k
        parts.append("</g>")
        self._pages[-1].append("".join(parts))

    def rule(self, x1: float, x2: float, y: float, thickness: float, color: str, dotted: bool = False) -> None:
        dash = f' stroke-dasharray="{_n(DASH)} {_n(DASH_GAP)}"' if dotted else ""
        self._pages[-1].append(f'<line x1="{_n(x1)}" y1="{_n(y)}" x2="{_n(x2)}" y2="{_n(y)}" '
                               f'stroke="#{color}" stroke-width="{_n(thickness)}"{dash}/>')

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float, color: str) -> None:
        self._pages[-1].append(f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" '
                               f'stroke="#{color}" stroke-width="{_n(width)}"/>')

    def box(self, x: float, y: float, w: float, h: float, color: str, role: str | None = None) -> None:
        self._pages[-1].append(f'<rect class="{role or "tint"}" x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" '
                               f'fill="#{color}"/>')

    # -- output -----------------------------------------------------------------------------
    def pages(self) -> list[str]:
        return ["".join(p) + "</svg>" for p in self._pages]


def render_pages(layout: Layout, render_set_name: str = "Standard", change_bars: bool = True,
                 resolver=None, marks: bool = False) -> list[str]:
    """One SVG per page of the layout, styled by the named rendering set; no report block.
    `marks` tints the changed runs of a side layout (spec §12.3); it does nothing on the blackline."""
    rs = render_set(render_set_name)
    opts = PdfOptions(render_set=render_set_name, change_bars=change_bars, report="none", marks=marks)
    painter = SvgPainter()
    draw_layout(layout, rs, opts, painter, resolver or default_resolver(), None)
    return painter.pages()
