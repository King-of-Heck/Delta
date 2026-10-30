"""document.xml -> model.Document."""
from __future__ import annotations

from ..model import (Cell, Document, NoteRef, ParaProps, Paragraph, Row, Run, RunProps, Section, Table,
                     merge_tabs)
from .ns import wq, wval, wbool, twips_to_pt, _num
from .numbering import Numbering, NumberingCounter
from .package import Package
from .revisions import count_revisions
from .styles import Styles, read_ppr, read_rpr

_TRANSPARENT = {wq("hyperlink"), wq("smartTag"), wq("sdt"), wq("sdtContent"), wq("fldSimple"),
                wq("ins"), wq("customXml"), wq("dir"), wq("bdo")}
_SKIP = {wq("del"), wq("moveFrom"), wq("pPr"), wq("rPr"), wq("proofErr"), wq("bookmarkStart"),
         wq("bookmarkEnd")}
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
FIELDS = ("PAGE", "NUMPAGES")      # fields the layout fills live; their run text is the token {NAME}
EMU_PER_PT = 12700.0
# Clark names of the tags and attributes the paragraph walk tests, built once (hot loop).
_W_p = wq("p")
_W_tbl = wq("tbl")
_W_r = wq("r")
_W_rPr = wq("rPr")
_W_fldChar = wq("fldChar")
_W_fldCharType = wq("fldCharType")
_W_instrText = wq("instrText")
_W_t = wq("t")
_W_delText = wq("delText")
_W_drawing = wq("drawing")
_W_tab = wq("tab")
_W_br = wq("br")
_W_type = wq("type")
_W_noBreakHyphen = wq("noBreakHyphen")
_W_sym = wq("sym")
_W_cr = wq("cr")
_W_footnoteReference = wq("footnoteReference")
_W_endnoteReference = wq("endnoteReference")
_W_id = wq("id")
_W_commentReference = wq("commentReference")
_W_fldSimple = wq("fldSimple")
_W_instr = wq("instr")
_W_commentRangeStart = wq("commentRangeStart")
_W_commentRangeEnd = wq("commentRangeEnd")
_W_pPr = wq("pPr")
_W_pStyle = wq("pStyle")
_W_sectPr = wq("sectPr")


def field_name(instr: str | None) -> str | None:
    """The field's name from its instruction (" PAGE  \\* MERGEFORMAT " -> "PAGE") when it is one
    the layout fills, else None."""
    words = (instr or "").split()
    return words[0].upper() if words and words[0].upper() in FIELDS else None


class _Ctx:
    def __init__(self, styles: Styles, numbering: Numbering):
        self.styles = styles
        self.numbering = numbering
        self.counter = NumberingCounter(numbering)
        self.pending_break = False
        self.pending_section = False       # the pending break is a section break's, not a w:br
        self.sections: list[Section] = []
        self.in_part = False               # parsing a note or a header/footer part: a stray sectPr there is not a section
        self.note_numbers: dict[NoteRef, int] = {}     # filled in body reference order
        self.rels: dict[str, str] = {}
        self.comment_anchors: dict = {}     # w:comment id -> docx.comments.CommentAnchor
        self.para_markers: list = []        # (cid, which, raw_offset) collected in _runs, folded in _paragraph
        self.raw_len = 0                    # concatenated raw run text length so far in this paragraph
        self.style_props: dict = {}         # style id -> (resolved pPr, resolved rPr); styles never change mid-parse

    def resolved(self, style_id) -> tuple[dict, dict]:
        """The style chain's (pPr, rPr) dicts, resolved once per style id. Shared: read, never written."""
        got = self.style_props.get(style_id)
        if got is None:
            got = self.style_props[style_id] = (self.styles.resolved_ppr(style_id),
                                                self.styles.resolved_rpr(style_id))
        return got


def parse_docx(src) -> Document:
    # The whole tree is built before the context manager exits, so nothing reads the zip
    # afterwards -- and a path input does not leave a file handle open behind the caller.
    with Package.open(src) as pkg:
        return parse_package(pkg)


def parse_package(pkg: Package) -> Document:
    styles = Styles.parse(pkg.xml("word/styles.xml"))
    numbering = Numbering.parse(pkg.xml("word/numbering.xml"))
    ctx = _Ctx(styles, numbering)
    ctx.rels = pkg.rels("word/document.xml")
    root = pkg.xml("word/document.xml")
    body = root.find(wq("body")) if root is not None else None
    blocks = _blocks(body, ctx) if body is not None else []
    if body is not None:
        sp = body.find(wq("sectPr"))
        ctx.sections.append(_section(sp, ctx) if sp is not None else Section())
    if not ctx.sections:
        ctx.sections.append(Section())
    settings = pkg.xml("word/settings.xml")
    eao = settings is not None and wbool(settings.find(wq("evenAndOddHeaders")))
    tab = twips_to_pt(wval(settings.find(wq("defaultTabStop")))) if settings is not None else None
    html_spacing = not (settings is not None
                        and wbool(settings.find(f"{wq('compat')}/{wq('doNotUseHTMLParagraphAutoSpacing')}")))
    ctx.in_part = True
    footnotes = _notes(pkg.xml("word/footnotes.xml"), "footnote", ctx)
    endnotes = _notes(pkg.xml("word/endnotes.xml"), "endnote", ctx)
    from .comments import read_comments
    comments = read_comments(pkg, ctx)
    parts: dict = {}
    for sec in list(ctx.sections):
        for attr in ("header_default", "header_first", "header_even",
                     "footer_default", "footer_first", "footer_even"):
            name = getattr(sec, attr)
            if name is None or name in parts:
                continue
            root_part = pkg.xml("word/" + name)
            if root_part is None:
                continue
            ctx.pending_break = False
            parts[name] = _blocks(root_part, ctx)
            ctx.pending_break = False
    ctx.in_part = False
    ctx.pending_section = False
    return Document(blocks, ctx.sections, default_font=styles.defaults["font"],
                    default_size_pt=styles.defaults["size_pt"], even_and_odd=eao,
                    default_tab_pt=tab if tab is not None else 36.0,
                    footnotes=footnotes, endnotes=endnotes, note_numbers=ctx.note_numbers,
                    html_spacing=html_spacing, parts=parts,
                    comments=comments, comment_anchors=ctx.comment_anchors,
                    tracked_changes=count_revisions(pkg))


def _notes(root, kind: str, ctx: _Ctx) -> dict:
    """footnotes.xml / endnotes.xml: note id -> blocks, parsed like the body. The separator and
    continuation-separator notes are Word's rules, not content, and are skipped. A page break
    inside a note never travels into the body."""
    out: dict = {}
    if root is None:
        return out
    for el in root.iter(wq(kind)):
        if el.get(wq("type")) in ("separator", "continuationSeparator"):
            continue
        nid = el.get(wq("id"))
        if nid is None:
            continue
        ctx.pending_break = False
        out[int(nid)] = _blocks(el, ctx)
    ctx.pending_break = False
    return out


def _blocks(parent, ctx: _Ctx) -> list:
    out = []
    for el in parent:
        if el.tag == _W_p:
            out.append(_paragraph(el, ctx))
        elif el.tag == _W_tbl:
            out.append(_table(el, ctx))
        elif el.tag in _TRANSPARENT:
            out.extend(_blocks(el, ctx))
    return out


def _runs(el, ctx: _Ctx, para_rpr: dict, out: list[Run], state: dict):
    """Collect runs under el into out. state['text_seen'] tracks page-break placement."""
    for child in el:
        tag = child.tag
        if tag in _SKIP:
            continue
        if tag == _W_r:
            props = _run_props(child.find(_W_rPr), para_rpr, ctx)
            buf = []
            fld = state.setdefault("field", None)
            for x in child:
                xt = x.tag
                if xt == _W_rPr:                  # read above; matches no branch below
                    continue
                if xt == _W_fldChar:
                    kind = x.get(_W_fldCharType)
                    if kind == "begin":
                        state["field"] = fld = {"instr": "", "phase": "instr", "props": props,
                                                "result": []}
                    elif kind == "separate" and fld is not None:
                        fld["phase"] = "result"
                        fld["props"] = None            # the first result run's formatting wins
                    elif kind == "end" and fld is not None:
                        name = field_name(fld["instr"])
                        if name is not None:
                            if buf:
                                flushed = "".join(buf)
                                out.append(Run(flushed, props))
                                ctx.raw_len += len(flushed)
                                buf = []
                            fp = fld["props"] or props
                            token = "{" + name + "}"
                            out.append(Run(token,
                                           RunProps(**{**fp.__dict__, "field": name,
                                                       "field_text": "".join(fld["result"])})))
                            ctx.raw_len += len(token)
                            state["text_seen"] = True
                        elif (number := ctx.counter.listnum(fld["instr"])) is not None:
                            buf.append(number)
                            state["text_seen"] = True
                        state["field"] = fld = None
                elif xt == _W_instrText:
                    if fld is not None and fld["phase"] == "instr":
                        fld["instr"] += x.text or ""
                elif xt == _W_t or xt == _W_delText:
                    if fld is not None and fld["phase"] == "result" and (
                            field_name(fld["instr"]) is not None or ctx.counter.is_listnum(fld["instr"])):
                        if fld["props"] is None:
                            fld["props"] = props
                        # the cached result of a live field: replaced by the token, kept as field_text
                        fld["result"].append(x.text or "")
                        continue
                    buf.append(x.text or "")
                elif xt == _W_drawing:
                    ext = x.find(f".//{_WP}extent")
                    if ext is not None:
                        if buf:
                            flushed = "".join(buf)
                            out.append(Run(flushed, props))
                            ctx.raw_len += len(flushed)
                            buf = []
                        cx, cy = _num(ext.get("cx")), _num(ext.get("cy"))
                        out.append(Run("", RunProps(**{**props.__dict__, "image_w_pt": (cx or 0) / EMU_PER_PT,
                                                       "image_h_pt": (cy or 0) / EMU_PER_PT})))
                elif xt == _W_tab:
                    buf.append("\t")
                elif xt == _W_br:
                    if x.get(_W_type) == "page":
                        if state["text_seen"] or "".join(buf).strip():
                            ctx.pending_break = True          # applies to the NEXT paragraph
                        else:
                            state["break_before"] = True      # applies to THIS paragraph
                    else:
                        buf.append("\n")
                elif xt == _W_noBreakHyphen:
                    buf.append("-")
                elif xt == _W_sym:
                    buf.append("\ufffd")
                elif xt == _W_cr:
                    buf.append("\n")
                elif xt in (_W_footnoteReference, _W_endnoteReference):
                    # The mark: no text of its own (the number is drawn, never compared); an empty
                    # run anchors it at this point of the paragraph. w:footnoteRef (the note's own
                    # number inside its body) is not read: the layout draws it from note_numbers.
                    if buf:
                        flushed = "".join(buf)
                        out.append(Run(flushed, props))
                        ctx.raw_len += len(flushed)
                        buf = []
                    ref = NoteRef("footnote" if xt == _W_footnoteReference else "endnote",
                                  int(x.get(_W_id) or 0))
                    ctx.note_numbers.setdefault(ref, 1 + sum(1 for r in ctx.note_numbers if r.kind == ref.kind))
                    out.append(Run("", RunProps(**{**props.__dict__, "note": ref})))
                elif xt == _W_commentReference:
                    # The mark: no text of its own. Record the anchor at the current raw offset
                    # (a comment marker never emits a run or text of its own).
                    if buf:
                        flushed = "".join(buf)
                        out.append(Run(flushed, props))
                        ctx.raw_len += len(flushed)
                        buf = []
                    cid = x.get(_W_id)
                    if cid is not None:
                        ctx.para_markers.append((cid, "ref", ctx.raw_len))
            text = "".join(buf)
            if text.strip():
                state["text_seen"] = True
            if text:
                out.append(Run(text, props))
            ctx.raw_len += len(text)
        elif tag == _W_fldSimple and (fs_name := field_name(child.get(_W_instr))) is not None:
            first = child.find(_W_r)
            props = _run_props(first.find(_W_rPr) if first is not None else None, para_rpr, ctx)
            cached = "".join(t.text or "" for r in child.findall(_W_r) for t in r.findall(_W_t))
            token = "{" + fs_name + "}"
            out.append(Run(token,
                           RunProps(**{**props.__dict__, "field": fs_name, "field_text": cached})))
            ctx.raw_len += len(token)
            state["text_seen"] = True
        elif tag == _W_fldSimple and (number := ctx.counter.listnum(child.get(_W_instr) or "")) is not None:
            first = child.find(_W_r)
            out.append(Run(number, _run_props(first.find(_W_rPr) if first is not None else None, para_rpr, ctx)))
            ctx.raw_len += len(number)
            state["text_seen"] = True
        elif tag in (_W_commentRangeStart, _W_commentRangeEnd):
            cid = child.get(_W_id)
            if cid is not None:
                which = "start" if tag == _W_commentRangeStart else "end"
                ctx.para_markers.append((cid, which, ctx.raw_len))
        elif tag in _TRANSPARENT:
            _runs(child, ctx, para_rpr, out, state)


def _run_props(rpr, para_rpr: dict, ctx: _Ctx) -> RunProps:
    d = dict(para_rpr)
    own = read_rpr(rpr)
    d.update(own)
    # The compared bold comes from the run alone (the reference engine's rule; see
    # tests/parity/KNOWN_DIVERGENCES.md); the drawn bold (style_bold) resolves through the
    # paragraph style like italic. w:rStyle is not applied to any property.
    return RunProps(bold=bool(own.get("bold", False)), style_bold=bool(d.get("bold", False)),
                    italic=bool(d.get("italic", False)), underline=bool(d.get("underline", False)),
                    font=d.get("font") or ctx.styles.defaults["font"],
                    size_pt=d.get("size_pt") or ctx.styles.defaults["size_pt"], color=d.get("color"),
                    caps=bool(d.get("caps", False)), small_caps=bool(d.get("small_caps", False)))


def _paragraph(el, ctx: _Ctx) -> Paragraph:
    ctx.raw_len = 0        # concatenated raw run text length so far (for comment anchor offsets)
    ctx.para_markers = []  # (cid, which, offset) markers collected in _runs, folded in below once p exists
    ppr = el.find(_W_pPr)
    style_id = wval(ppr.find(_W_pStyle)) if ppr is not None else None
    # resolved_ppr(None) (no pStyle) resolves through Word's default paragraph style chain,
    # not an empty chain -- see Styles._chain.
    style_ppr, para_rpr = ctx.resolved(style_id)
    own = read_ppr(ppr)
    merged = dict(style_ppr)
    merged.update(own)
    mark = dict(para_rpr)
    mark.update(read_rpr(ppr.find(_W_rPr)) if ppr is not None else {})   # the paragraph mark's own run properties

    # ---- Numbering: paragraph's own numPr, else the style chain's own numPr (already folded
    # into `merged` above), else a level that names this style via its own <w:pStyle>.
    num = None
    lv = None
    num_id, ilvl = merged.get("num_id"), merged.get("ilvl", 0)
    if num_id is None and style_id in ctx.numbering.style_to_num:
        num_id, ilvl = ctx.numbering.style_to_num[style_id]
    if num_id:
        num = ctx.counter.next(num_id, ilvl)
        lv = ctx.numbering.level(num_id, ilvl)
        if lv is not None:
            if "ind_left_pt" not in merged and lv.ind_left_pt is not None:
                merged["ind_left_pt"] = lv.ind_left_pt
            if "ind_hanging_pt" not in merged and lv.ind_hanging_pt is not None:
                merged["ind_hanging_pt"] = lv.ind_hanging_pt

    # ---- Spacing (w:spacing), resolved PER ATTRIBUTE: paragraph's own -> the numbering
    # LEVEL's own <w:pPr>/<w:spacing> -> the style chain (or the default paragraph style
    # when the paragraph names no pStyle, already folded into `style_ppr` above) ->
    # docDefaults' <w:pPrDefault>. spaceBefore/spaceAfter fall back independently; the
    # "line" group (line_spacing/line_rule/line_exact_pt) is taken atomically from whichever
    # source is the first to set a line rule at all -- these three always arrive together
    # from one <w:spacing> element and describe mutually exclusive line-height concepts
    # (a multiplier for "auto", a fixed point height for "exact"/"atLeast"), so they must
    # never be picked from two different sources.
    level_ppr = lv.ppr if lv is not None else {}
    defaults = ctx.styles.defaults
    space_before_pt = own.get("space_before_pt")
    if space_before_pt is None:
        space_before_pt = level_ppr.get("space_before_pt")
    if space_before_pt is None:
        space_before_pt = style_ppr.get("space_before_pt")
    if space_before_pt is None:
        space_before_pt = defaults.get("space_before_pt")
    space_after_pt = own.get("space_after_pt")
    if space_after_pt is None:
        space_after_pt = level_ppr.get("space_after_pt")
    if space_after_pt is None:
        space_after_pt = style_ppr.get("space_after_pt")
    if space_after_pt is None:
        space_after_pt = defaults.get("space_after_pt")
    line_spacing = line_rule = line_exact_pt = None
    for src in (own, level_ppr, style_ppr, defaults):
        if src.get("line_rule") is not None:
            line_spacing, line_rule, line_exact_pt = (
                src.get("line_spacing"), src.get("line_rule"), src.get("line_exact_pt"))
            break
    merged["space_before_pt"], merged["space_after_pt"] = space_before_pt, space_after_pt
    for key, val in (("line_spacing", line_spacing), ("line_rule", line_rule), ("line_exact_pt", line_exact_pt)):
        if val is None:
            merged.pop(key, None)
        else:
            merged[key] = val

    # `heading` (Title/Heading-N style detection) is a projection concern, not a model one --
    # see harness/flatten.py. The model keeps Word semantics: outline_level is w:outlineLvl
    # from the paragraph's own pPr, else the style chain, else None (already resolved into
    # `merged` by the dict merge above; never derived from styleId/style name here).
    outline = merged.get("outline_level")
    # Tab stops accumulate: the style chain's, then the numbering level's, then the paragraph's own.
    tabs = merge_tabs(merge_tabs(style_ppr.get("tabs", ()), level_ppr.get("tabs", ())), own.get("tabs", ()))
    st = ctx.styles.get(style_id)
    style_name = st.name if st is not None else None

    # A pending break travels from paragraph to paragraph. Snapshot what arrived from
    # earlier paragraphs, then let _runs discover (and reset) whatever this paragraph's
    # OWN content contributes going forward -- the two must never be conflated, or a
    # break that lands after this paragraph's own text (which is destined for the NEXT
    # paragraph) gets misattributed to this one instead.
    had_pending = ctx.pending_break
    from_section = had_pending and ctx.pending_section
    ctx.pending_break = False
    ctx.pending_section = False

    runs: list[Run] = []
    state = {"text_seen": False, "break_before": False}
    _runs(el, ctx, para_rpr, runs, state)

    props = ParaProps(style_id=style_id, align=merged.get("align", "left"),
                      ind_left_pt=merged.get("ind_left_pt", 0.0), ind_hanging_pt=merged.get("ind_hanging_pt", 0.0),
                      ind_first_line_pt=merged.get("ind_first_line_pt", 0.0),
                      ind_right_pt=merged.get("ind_right_pt", 0.0),
                      space_before_pt=merged.get("space_before_pt"), space_after_pt=merged.get("space_after_pt"),
                      line_spacing=merged.get("line_spacing"), line_rule=merged.get("line_rule"),
                      line_exact_pt=merged.get("line_exact_pt"),
                      keep_next=merged.get("keep_next", False), keep_lines=merged.get("keep_lines", False),
                      page_break_before=merged.get("page_break_before", False),
                      contextual_spacing=merged.get("contextual_spacing", False), outline_level=outline,
                      style_name=style_name, tabs=tabs,
                      border_top=merged.get("border_top"), border_bottom=merged.get("border_bottom"),
                      border_left=merged.get("border_left"), border_right=merged.get("border_right"),
                      mark_font=mark.get("font") or ctx.styles.defaults["font"],
                      mark_size_pt=mark.get("size_pt") or ctx.styles.defaults["size_pt"])
    p = Paragraph(runs, props, num)
    if ctx.para_markers:
        from .comments import CommentAnchor
        for cid, which, off in ctx.para_markers:
            a = ctx.comment_anchors.setdefault(cid, CommentAnchor())
            setattr(a, which, (p, off))
        ctx.para_markers = []
    own_break = ctx.pending_break              # this paragraph's own trailing w:br (set by _runs)

    if p.is_empty:
        # Empty: no distinction between "before" and "after" text (there is none), so any
        # break found here (or still unresolved from before) simply keeps traveling. A break
        # that only reaches _runs from a skipped subtree (e.g. inside w:del) never sets
        # break_before or pending_break in the first place, so it correctly does not travel.
        ctx.pending_break = had_pending or state["break_before"] or ctx.pending_break
        ctx.pending_section = from_section and not state["break_before"] and not own_break
    else:
        if state["break_before"] or had_pending:
            p.props.page_break_before = True
            p.props.section_page_break = from_section and not state["break_before"]
        # else: ctx.pending_break already holds whatever this paragraph's own trailing
        # break (if any) set during _runs, correctly destined for the NEXT paragraph.

    if ppr is not None:
        sp = ppr.find(_W_sectPr)
        if sp is not None and not ctx.in_part:
            p.props.section_break = True
            section = _section(sp, ctx)
            ctx.sections.append(section)
            # A section break stored on a paragraph takes effect AFTER that paragraph: the
            # following paragraph starts a new page. "continuous" flows on, and "nextColumn"
            # only starts a new column, so neither breaks the page.
            if section.type not in ("continuous", "nextColumn"):
                if not ctx.pending_break:          # a real break already travelling keeps its nature
                    ctx.pending_section = True
                ctx.pending_break = True
    return p


def _table(el, ctx: _Ctx) -> Table:
    grid = [twips_to_pt(g.get(wq("w"))) or 0.0 for g in el.findall(f"{wq('tblGrid')}/{wq('gridCol')}")]
    ind = 0.0
    ti = el.find(f"{wq('tblPr')}/{wq('tblInd')}")
    if ti is not None and (ti.get(wq("type")) or "dxa") == "dxa":
        ind = twips_to_pt(ti.get(wq("w"))) or 0.0
    rows = []
    for tr in el.findall(wq("tr")):
        cells = []
        for tc in tr.findall(wq("tc")):
            tcpr = tc.find(wq("tcPr"))
            span, vm = 1, None
            if tcpr is not None:
                gs = tcpr.find(wq("gridSpan"))
                if gs is not None:
                    span = int(wval(gs, "1"))
                v = tcpr.find(wq("vMerge"))
                if v is not None:
                    vm = "continue" if wval(v) == "continue" or wval(v) is None else "restart"
            cells.append(Cell(_blocks(tc, ctx), grid_span=span, v_merge=vm))
        h_pt = h_rule = None
        th = tr.find(f"{wq('trPr')}/{wq('trHeight')}")
        if th is not None and twips_to_pt(th.get(wq("val"))) is not None:
            h_pt = twips_to_pt(th.get(wq("val")))
            h_rule = th.get(wq("hRule")) or "atLeast"
        rows.append(Row(cells, h_pt, h_rule))
    ctx.pending_break = False   # a break inside a cell never escapes the table (SorkWhare 1.3.1)
    return Table(rows, grid_pt=grid, ind_pt=ind)


def _section(sp, ctx: _Ctx) -> Section:
    s = Section()
    sz = sp.find(wq("pgSz"))
    if sz is not None:
        w, h = twips_to_pt(sz.get(wq("w"))), twips_to_pt(sz.get(wq("h")))
        if w:
            s.page_w_pt = round(w, 2)
        if h:
            s.page_h_pt = round(h, 2)
    mar = sp.find(wq("pgMar"))
    if mar is not None:
        for attr, key in (("top", "margin_top_pt"), ("right", "margin_right_pt"), ("bottom", "margin_bottom_pt"),
                          ("left", "margin_left_pt"), ("header", "header_pt"), ("footer", "footer_pt")):
            v = twips_to_pt(mar.get(wq(attr)))
            if v is not None:
                setattr(s, key, abs(v))
    s.title_pg = wbool(sp.find(wq("titlePg")))
    s.type = wval(sp.find(wq("type")), "nextPage") or "nextPage"
    for el in sp:
        if el.tag in (wq("headerReference"), wq("footerReference")):
            kind = "header" if el.tag == wq("headerReference") else "footer"
            variant = el.get(wq("type")) or "default"
            if variant not in ("default", "first", "even"):
                continue
            target = ctx.rels.get(el.get(_R_ID) or "")
            if target:
                setattr(s, f"{kind}_{variant}", target)
    pn = sp.find(wq("pgNumType"))
    if pn is not None:
        start = pn.get(wq("start"))
        if start is not None and start.lstrip("-").isdigit():
            s.page_start = int(start)
        s.page_fmt = pn.get(wq("fmt")) or "decimal"
    return s
