"""Delta document tree. Every later stage (diff, layout, sinks) consumes this."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

# JavaScript's \s (the reference engine's whitespace): Python's \s differs at U+0085 / U+FEFF.
WS_CHARS = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_WS = re.compile(f"[{WS_CHARS}]+")


def collapse_ws(s: str) -> str:
    return _WS.sub(" ", s).strip()


@dataclass(frozen=True)
class NoteRef:
    kind: str        # "footnote" | "endnote"
    id: int          # w:id in footnotes.xml / endnotes.xml


@dataclass
class Comment:
    id: str                       # w:comment/@w:id
    author: str
    initials: str
    date: str                     # w:date as written ("" when absent)
    paras: list                   # the comment's blocks (paragraphs), parsed like a note body
    para_ids: list                # w14:paraId of each paragraph, in order
    parent_id: str | None = None  # w15:paraIdParent of the first paragraph (the parent's last paraId)
    done: bool = False            # w15:done set on any of this comment's paraIds


@dataclass
class RunProps:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font: str | None = None
    size_pt: float | None = None
    color: str | None = None
    caps: bool = False           # w:caps: drawn in capitals (the text itself is unchanged)
    small_caps: bool = False     # w:smallCaps: drawn in capitals too (Word's smaller capitals are not modelled)
    style_bold: bool = False     # bold as drawn: the paragraph style's bold unless the run turns it off.
                                 # `bold` (the run's own w:b) is the compared property (parity with the reference).
    note: NoteRef | None = None  # this (empty) run is a footnote/endnote reference mark
    field: str | None = None     # "PAGE" | "NUMPAGES": the run is that field; its text is the literal token
    field_text: str | None = None     # that field's cached result text ("" when it has none): what is drawn
                                      # where the layout has no live value (the body); never compared
    image_w_pt: float | None = None   # an inline image (w:drawing): the run has no text and holds this box
    image_h_pt: float | None = None


@dataclass
class Run:
    text: str
    props: RunProps


@dataclass(frozen=True)
class TabStop:
    pos_pt: float
    kind: str = "left"       # left | center | right | decimal | bar | num | clear (w:tab/@w:val)
    leader: str = "none"     # none | dot | hyphen | underscore | middleDot | heavy (w:tab/@w:leader)


def merge_tabs(base, over) -> tuple:
    """Word's tab-stop inheritance: a stop at a position replaces the inherited one there, a
    `clear` removes it; the result is sorted by position with the clears dropped."""
    stops = {round(t.pos_pt, 2): t for t in base}
    for t in over:
        stops[round(t.pos_pt, 2)] = t
    return tuple(sorted((t for t in stops.values() if t.kind != "clear"), key=lambda t: t.pos_pt))


@dataclass(frozen=True)
class Border:
    """One side of a paragraph border (w:pBdr), as drawn: a single line of `width_pt` (w:sz is
    eighths of a point; double, dashed and the other line styles all draw as one solid line),
    `space_pt` between the text and the line (w:space), colour 6-hex lower-case or None (auto)."""
    width_pt: float
    space_pt: float = 0.0
    color: str | None = None


@dataclass
class NumInfo:
    num_id: int
    ilvl: int
    marker: str
    is_bullet: bool
    suff: str = "tab"        # what follows the marker: tab | space | nothing (w:suff)
    jc: str = "left"         # marker alignment at the first-line position (w:lvlJc)


@dataclass
class ParaProps:
    style_id: str | None = None
    align: str = "left"
    ind_left_pt: float = 0.0
    ind_hanging_pt: float = 0.0
    ind_first_line_pt: float = 0.0
    ind_right_pt: float = 0.0
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    line_spacing: float | None = None
    line_rule: str | None = None
    line_exact_pt: float | None = None
    keep_next: bool = False
    keep_lines: bool = False
    page_break_before: bool = False
    # page_break_before came from the section break before this paragraph (the reference's
    # reading, kept for parity); the layout starts the section's page itself and ignores it,
    # so empty paragraphs at the top of a section do not push the first text a page further.
    section_page_break: bool = False
    contextual_spacing: bool = False
    outline_level: int | None = None
    style_name: str | None = None
    section_break: bool = False   # this paragraph carries a <w:sectPr>; it is the last of its section
    tabs: tuple = ()              # resolved TabStops (style chain, numbering level, own), sorted
    border_top: Border | None = None      # w:pBdr sides, each inherited on its own through the style chain
    border_bottom: Border | None = None
    border_left: Border | None = None
    border_right: Border | None = None
    mark_font: str | None = None       # the paragraph mark's font and size (style chain, then the
    mark_size_pt: float | None = None  # paragraph's own pPr/rPr): what sizes an empty paragraph's line


@dataclass
class Paragraph:
    runs: list[Run]
    props: ParaProps
    num: NumInfo | None = None
    # the w:p element this paragraph was read from: only when the parser is asked to keep it (the
    # tracked-changes export); never compared, never shown
    xml: object = field(default=None, compare=False, repr=False)

    @property
    def text(self) -> str:
        return collapse_ws("".join(r.text for r in self.runs))

    @property
    def is_empty(self) -> bool:
        return self.text == ""


@dataclass
class Cell:
    blocks: list
    grid_span: int = 1
    v_merge: str | None = None


@dataclass
class Row:
    cells: list[Cell]
    height_pt: float | None = None     # w:trHeight (twips -> pt)
    height_rule: str | None = None     # auto | atLeast | exact (Word's default for a set height is atLeast)


@dataclass
class Table:
    rows: list[Row]
    grid_pt: list[float] = field(default_factory=list)
    ind_pt: float = 0.0                # w:tblInd (dxa) -- the table's left edge relative to the margin


@dataclass
class Section:
    page_w_pt: float = 612.0
    page_h_pt: float = 792.0
    margin_top_pt: float = 72.0
    margin_right_pt: float = 72.0
    margin_bottom_pt: float = 72.0
    margin_left_pt: float = 72.0
    header_pt: float = 36.0
    footer_pt: float = 36.0
    title_pg: bool = False
    # w:sectPr/w:type: "nextPage" (Word's default when the element is absent), "continuous",
    # "nextColumn", "evenPage", "oddPage". Every value except continuous/nextColumn starts the
    # following paragraph on a new page. Appended last so positional construction stays stable.
    type: str = "nextPage"
    # Header/footer parts named by this section's sectPr (part basename under word/, e.g.
    # "header1.xml"), per variant; None = not referenced (Word links to the previous section).
    header_default: str | None = None
    header_first: str | None = None
    header_even: str | None = None
    footer_default: str | None = None
    footer_first: str | None = None
    footer_even: str | None = None
    page_start: int | None = None    # w:pgNumType/@w:start: page numbering restarts here
    page_fmt: str = "decimal"        # w:pgNumType/@w:fmt


@dataclass
class Document:
    blocks: list
    sections: list[Section]
    default_font: str | None = None
    default_size_pt: float = 11.0
    even_and_odd: bool = False
    default_tab_pt: float = 36.0       # w:settings/w:defaultTabStop (720 twips when absent)
    footnotes: dict = field(default_factory=dict)      # note id -> the note's blocks (separators excluded)
    endnotes: dict = field(default_factory=dict)
    note_numbers: dict = field(default_factory=dict)   # NoteRef -> displayed number (body reference order, per kind)
    # Word's HTML paragraph auto spacing (the default): between two paragraphs the LARGER of the
    # first's space after and the second's space before applies, not the sum. The compatibility
    # setting w:doNotUseHTMLParagraphAutoSpacing restores the sum.
    html_spacing: bool = True
    parts: dict = field(default_factory=dict)          # header/footer part basename -> its blocks
    comments: dict = field(default_factory=dict)        # w:comment id -> Comment
    comment_anchors: dict = field(default_factory=dict)  # w:comment id -> CommentAnchor (docx.comments)
    tracked_changes: int = 0        # revision elements read as accepted (docx.revisions), for the notice
    source_kind: str = "docx"           # "docx" | "pdf": which reader produced this document
    source_pages: int | None = None     # a PDF's page count (None for a Word document)
    skipped_pages: tuple = ()           # 1-based PDF pages with no readable text, left out of the comparison

    def paragraphs(self) -> Iterator[Paragraph]:
        yield from iter_paragraphs(self.blocks)


def iter_paragraphs(blocks) -> Iterator[Paragraph]:
    for b in blocks:
        if isinstance(b, Paragraph):
            yield b
        elif isinstance(b, Table):
            for row in b.rows:
                for cell in row.cells:
                    yield from iter_paragraphs(cell.blocks)
