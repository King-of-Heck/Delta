"""TrueType metrics from the machine's font files, with substitution for absent families.

Word's single line spacing is the font's hhea ascender - descender + lineGap and text width is the
sum of hmtx advances; both are read straight from the tables here. A family the machine lacks is
mapped through SUBSTITUTIONS, then to the first present FALLBACKS family. A missing bold or italic
face falls back to the nearest present style of the same family (flagged `synthetic`).
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass

from fontTools.ttLib import TTCollection, TTFont

ENV_DIRS = "DELTA_FONT_DIRS"
OLD_ENV_DIRS = "CALANDRIA_FONT_DIRS"   # the name before the app was renamed Delta (v2.12.0)
FALLBACKS = ("Calibri", "Times New Roman", "Arial")
SUBSTITUTIONS = {
    "cambria math": "Cambria", "helvetica": "Arial", "helvetica neue": "Arial", "times": "Times New Roman",
    "courier": "Courier New", "ms sans serif": "Arial", "ms serif": "Times New Roman",
    "calibri light": "Calibri", "segoe ui light": "Segoe UI", "garamond": "Times New Roman",
    "book antiqua": "Times New Roman", "palatino linotype": "Times New Roman",
}


def default_dirs() -> list[str]:
    env = os.environ.get(ENV_DIRS) or os.environ.get(OLD_ENV_DIRS)
    if env:
        return [d for d in env.split(os.pathsep) if d]
    dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")]
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    return dirs


@dataclass(frozen=True)
class FontFile:
    path: str
    font_number: int
    weight: int
    italic: bool


class FontIndex:
    """family (lower-cased) -> {(bold, italic): FontFile}, from the name and OS/2 tables.

    A font registers under both its typographic family (name id 16, e.g. "Calibri" for Calibri
    Light) and its legacy family (name id 1, "Calibri Light"), so "Calibri Light" resolves to the
    light face while "Calibri" keeps the face whose weight is nearest 400 (or 700 for bold).
    """

    def __init__(self, dirs=None):
        self.families: dict[str, dict[tuple[bool, bool], FontFile]] = {}
        self.display: dict[str, str] = {}
        for d in (dirs if dirs is not None else default_dirs()):
            paths = {os.path.normcase(p) for pat in ("*.tt[fc]", "*.TT[FC]")
                     for p in glob.glob(os.path.join(d, pat))}
            for path in sorted(paths):
                self._add_file(path)

    def _add_file(self, path: str):
        try:
            fonts = list(TTCollection(path, lazy=True).fonts) if path.lower().endswith(".ttc") \
                else [TTFont(path, lazy=True)]
        except Exception:
            return
        for n, f in enumerate(fonts):
            try:
                self._add_font(path, n, f)
            except Exception:
                continue
            finally:
                f.close()

    def _add_font(self, path: str, n: int, f):
        names = f["name"]
        os2 = f["OS/2"] if "OS/2" in f else None
        weight = int(getattr(os2, "usWeightClass", 400) or 400)
        italic = bool(getattr(os2, "fsSelection", 0) & 1) or bool(f["head"].macStyle & 2)
        file = FontFile(path, n, weight, italic)
        for fam in {names.getDebugName(1), names.getDebugName(16)} - {None}:
            key = fam.strip().lower()
            self.display.setdefault(key, fam.strip())
            styles = self.families.setdefault(key, {})
            sk = (weight >= 600, italic)
            target = 700 if sk[0] else 400
            cur = styles.get(sk)
            if cur is None or abs(weight - target) < abs(cur.weight - target):
                styles[sk] = file

    def has(self, family: str) -> bool:
        return family.strip().lower() in self.families


class Face:
    """One loaded style of one family: metrics only (the file is closed after reading)."""

    def __init__(self, file: FontFile, family: str, bold: bool, italic: bool, synthetic: bool):
        self.path, self.font_number = file.path, file.font_number
        self.family, self.bold, self.italic, self.synthetic = family, bold, italic, synthetic
        self.key = f"{family}|{'B' if bold else ''}{'I' if italic else ''}"
        f = TTFont(file.path, fontNumber=file.font_number, lazy=True)
        try:
            self.upem = f["head"].unitsPerEm
            h = f["hhea"]
            self._asc, self._desc, self._gap = h.ascent, abs(h.descent), h.lineGap
            self._cmap = dict(f.getBestCmap() or {})
            # A symbol font (Wingdings, Symbol, ...) maps its glyphs in a (3,0) table at U+F0xx;
            # Word stores the character as U+F0xx (sometimes as the low byte). No text layer knows
            # these code points, so such a face is flagged and the viewer draws its outlines.
            sym = f["cmap"].getcmap(3, 0) if "cmap" in f else None
            self.symbol = sym is not None
            if sym is not None:
                for code, g in sym.cmap.items():
                    self._cmap.setdefault(code, g)
                    if 0xF000 <= code <= 0xF0FF:
                        self._cmap.setdefault(code - 0xF000, g)
            self._adv = {g: m[0] for g, m in f["hmtx"].metrics.items()}
            notdef = self._adv.get(".notdef")
            if notdef is None:
                os2 = f["OS/2"] if "OS/2" in f else None
                notdef = int(getattr(os2, "xAvgCharWidth", 0) or self.upem // 2)
            self._notdef = notdef
        finally:
            f.close()
        self._units: dict[str, int] = {}

    def _adv_units(self, ch: str) -> int:
        g = self._cmap.get(ord(ch))
        return self._notdef if g is None else self._adv.get(g, self._notdef)

    def units(self, text: str) -> int:
        u = self._units.get(text)
        if u is None:
            u = sum(self._adv_units(c) for c in text)
            self._units[text] = u
        return u

    def width(self, text: str, size: float) -> float:
        if not text:
            return 0.0
        u = self._units.get(text)              # units() inlined for a text measured before
        if u is None:
            u = self.units(text)
        return u * size / self.upem

    def line_height(self, size: float) -> float:
        return (self._asc + self._desc + self._gap) * size / self.upem

    def ascent(self, size: float) -> float:
        return self._asc * size / self.upem

    def descent(self, size: float) -> float:
        return self._desc * size / self.upem


class FontResolver:
    def __init__(self, dirs=None, index: FontIndex | None = None):
        self.index = index or FontIndex(dirs)
        self._faces: dict[tuple[str, bool, bool], Face] = {}
        self.fallback = next((f for f in FALLBACKS if self.index.has(f)), None)
        if self.fallback is None and self.index.families:
            self.fallback = self.index.display[sorted(self.index.families)[0]]
        if self.fallback is None:
            raise RuntimeError("no TrueType fonts found in: " + ", ".join(dirs or default_dirs()))

    def resolve_family(self, family: str | None) -> str:
        if family:
            key = family.strip().lower()
            if self.index.has(key):
                return self.index.display[key]
            sub = SUBSTITUTIONS.get(key)
            if sub and self.index.has(sub):
                return self.index.display[sub.lower()]
        return self.fallback

    def face(self, family: str | None, bold: bool = False, italic: bool = False) -> Face:
        fam = self.resolve_family(family)
        bold, italic = bool(bold), bool(italic)
        k = (fam, bold, italic)
        face = self._faces.get(k)
        if face is None:
            styles = self.index.families[fam.lower()]
            for sk in ((bold, italic), (bold, False), (False, italic), (False, False)):
                if sk in styles:
                    file, actual = styles[sk], sk
                    break
            else:
                actual, file = next(iter(styles.items()))
            face = Face(file, fam, bold, italic, synthetic=actual != (bold, italic))
            self._faces[k] = face
        return face


_OUTLINE_FONTS: dict[tuple[str, int], tuple] = {}
_OUTLINES: dict[tuple[str, int, str], tuple[str, int]] = {}


def glyph_outlines(path: str, font_number: int, text: str) -> tuple[list[tuple[str, int]], int]:
    """([(SVG path data in font units, y up; advance in font units)] per character, unitsPerEm)
    from the font file. A character the font lacks draws nothing and advances its .notdef width.
    Used for symbol faces, whose characters no text renderer resolves (see Face.symbol)."""
    from fontTools.pens.svgPathPen import SVGPathPen

    key = (path, font_number)
    entry = _OUTLINE_FONTS.get(key)
    if entry is None:
        f = TTFont(path, fontNumber=font_number, lazy=True)
        cmap = dict(f.getBestCmap() or {})
        sym = f["cmap"].getcmap(3, 0) if "cmap" in f else None
        if sym is not None:
            for code, g in sym.cmap.items():
                cmap.setdefault(code, g)
                if 0xF000 <= code <= 0xF0FF:
                    cmap.setdefault(code - 0xF000, g)
        entry = (f.getGlyphSet(), cmap, {g: m[0] for g, m in f["hmtx"].metrics.items()}, f["head"].unitsPerEm)
        _OUTLINE_FONTS[key] = entry
    glyphs, cmap, adv, upem = entry
    out = []
    for ch in text:
        g = cmap.get(ord(ch), ".notdef")
        k = (path, font_number, g)
        d = _OUTLINES.get(k)
        if d is None:
            pen = SVGPathPen(glyphs)
            if g in glyphs:
                glyphs[g].draw(pen)
            d = (pen.getCommands(), adv.get(g, adv.get(".notdef", upem // 2)))
            _OUTLINES[k] = d
        out.append(d)
    return out, upem


_DEFAULT: FontResolver | None = None


def default_resolver() -> FontResolver:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = FontResolver()
    return _DEFAULT
