"""The page's files exist, agree with each other (every id the scripts look up is
in the HTML, every static reference is a served file) and parse (node --check when node is here)."""
import json
import re
import shutil
import subprocess
from importlib import resources

import pytest

from delta.server.app import DEFAULT_IDLE, STATIC

VIEWER = resources.files("delta.viewer")


def _read(name):
    return VIEWER.joinpath(name).read_text("utf-8")


def _ids_in_html():
    return set(re.findall(r'\sid="([A-Za-z0-9_-]+)"', _read("index.html")))


@pytest.mark.parametrize("name", sorted(STATIC))
def test_static_file_exists(name):
    assert VIEWER.joinpath(name).read_bytes(), name


def test_index_references_only_served_files():
    refs = re.findall(r'(?:src|href)="/static/([^"]+)"', _read("index.html"))
    assert refs and set(refs) <= set(STATIC)
    assert "app.js" in refs and "style.css" in refs


def test_index_declares_a_module_script_charset_and_title():
    html = _read("index.html")
    assert '<meta charset="utf-8">' in html and "<title>Delta</title>" in html
    assert '<script type="module" src="/static/app.js">' in html


@pytest.mark.parametrize("script", ["app.js", "changes.js", "sources.js", "strip.js"])
def test_every_id_the_script_looks_up_exists(script):
    wanted = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', _read(script)))
    assert wanted, script
    missing = wanted - _ids_in_html()
    assert not missing, f"{script} looks up ids missing from index.html: {sorted(missing)}"


def test_the_v41_caveat_is_on_the_page():
    assert "nothing is written to Word" in _read("index.html")


def test_location_filter_lists_headers_and_footers():
    assert '<option value="header">Headers</option><option value="footer">Footers</option>' in _read("index.html")


def test_the_heartbeat_is_faster_than_the_server_idle_timeout():
    js = _read("app.js")
    ping_ms = int(re.search(r"const PING_MS = (\d+);", js).group(1))
    misses = int(re.search(r"const PING_MISSES = (\d+);", js).group(1))
    assert ping_ms == 2000
    # the server stops DEFAULT_IDLE s after the last ping: three pings must fit inside that
    assert ping_ms * misses / 1000 < DEFAULT_IDLE
    assert "close this window" in js and "close this tab" not in js


def test_the_ping_carries_the_page_visibility():
    js = _read("app.js")
    # a hidden page's timers are throttled to one wake a minute, so the server has to be told
    assert "/api/ping?hidden=" in js
    assert "visibilitychange" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
@pytest.mark.parametrize("script", ["app.js", "changes.js", "sources.js", "strip.js", "copy.js", "panes.js", "sync.js", "sides.js", "rowtext.js", "textredline.js", "textcompare.js"])
def test_scripts_parse(script):
    path = VIEWER.joinpath(script)
    r = subprocess.run([shutil.which("node"), "--check", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_a_closed_page_does_not_ping():
    js = _read("app.js")
    body = js[js.index("function ping()"):]
    body = body[:body.index("\n}\n")]
    assert "if (state.closed) return;" in body.splitlines()[1]


def _main_html():
    html = _read("index.html")
    return html[:html.index("<main")], html[html.index("<main"):]


def test_the_source_cards_start_in_the_page_area_and_move_to_the_toolbar():
    head, main = _main_html()
    for id_ in ("cardA", "cardB", "fileA", "fileB", "nameA", "nameB", "clearA", "clearB", "swap", "compare", "sources"):
        assert f'id="{id_}"' in main, id_
        assert f'id="{id_}"' not in head, id_
    # once a comparison exists the strip is a second toolbar row, and it comes back for a fresh start
    js = _read("sources.js")
    body = js[js.index("function placeSources("):]
    assert 'compact ? $("bar") : $("pages")' in body and "home.append(src)" in body
    css = _read("style.css")
    assert "#bar {\n  display: flex; flex-wrap: wrap;" in css
    assert "#bar .sources.compact { flex: 1 0 100%;" in css
    # the remove button sits in the row with the name, not pinned to a corner
    assert "#bar .sources.compact .card .clear { position: static;" in css


def test_the_empty_state_teaches():
    _, main = _main_html()
    assert "nothing is written to Word" in main
    assert "Closing this window closes Delta" in main
    assert "Drop the .docx or .pdf here or click to choose" in main


def test_the_toolbar_holds_navigation_zoom_pdf_and_options_only():
    html = _read("index.html")
    header = html[html.index("<header"):html.index("</header>")]
    for id_ in ("navFirst", "navPrev", "navNext", "navLast", "goto", "changeStatus", "pdf", "options"):
        assert f'id="{id_}"' in header, id_
    # the per-comparison and per-office settings sit inside the Options popover, Quit with them
    popover = header[header.index('id="options"'):]
    for id_ in ("optIgnoreCase", "optCountNumbering", "showUnchanged", "showInsertions", "showDeletions",
                "showFormatting", "renderSet", "changeBars", "report", "quit"):
        assert f'id="{id_}"' in popover, id_
    assert "hideUnchanged" not in html and "hideInsertions" not in html


def test_the_option_labels_say_show_not_hide():
    html = _read("index.html")
    assert "Hide unchanged" not in html and "Show unchanged text" in html


def test_zoom_is_a_stepper_and_the_keys_have_a_sheet():
    html = _read("index.html")
    assert '<select id="zoom"' not in html
    for id_ in ("zoomOut", "zoomPct", "zoomIn", "zoomFit", "keys", "keysClose"):
        assert f'id="{id_}"' in html, id_
    assert '<dialog id="keys"' in html
    js = _read("changes.js")
    assert '"j"' in js and '"k"' in js and '"ArrowRight"' in js and '"ArrowLeft"' in js
    app = _read("app.js")
    assert '"?"' in app and "e.ctrlKey" in app and '"wheel"' in app


def test_progress_and_notice_elements_exist_and_closed_uses_the_card():
    html = _read("index.html")
    for id_ in ("progress", "notice", "noticeText", "noticeClose"):
        assert f'id="{id_}"' in html, id_
    js = _read("app.js")
    body = js[js.index("function closed("):]
    body = body[:body.index("\n}\n")]
    assert 'notice(text, "closed")' in body


def test_tiles_filter_and_the_checkbox_row_is_gone():
    html = _read("index.html")
    for id_ in ("fInsertion", "fDeletion", "fAmendment", "fNumbering"):
        assert f'id="{id_}"' not in html, id_
    js = _read("changes.js")
    assert "data-cat" in js and "anchors[String(" in js and "line-clamp" in _read("style.css")


def test_the_panel_collapses_and_remembers_it():
    assert 'id="panelToggle"' in _read("index.html")
    js = _read("changes.js")
    assert '"delta.panel"' in js and "delta:resized" in js
    css = _read("style.css")
    assert "#panel.collapsed" in css and ":focus-visible" in css


def test_the_old_names_saved_choices_carry_over_first():
    # v2.12.0: the app was Calandria; its saved choices move to the delta.* keys before anything reads them.
    app = _read("app.js")
    carry = _function_body(app, "carryOldSettings")
    keys = {k for js in (app, _read("changes.js")) for k in re.findall(r'localStorage\.getItem\("delta\.(\w+)"\)', js)}
    assert keys and all(f'"{k}"' in carry for k in keys)
    wire = _function_body(app, "wire")
    assert wire.index("carryOldSettings();") < wire.index("localStorage") and wire.index("carryOldSettings();") < wire.index("initChanges();")


# v2.2.1: the minors deferred from the v2.2.0 reviews.

def _function_body(js, name):
    body = js[js.index(f"function {name}("):]
    return body[:body.index("\n}\n")]


def test_the_keys_sheet_has_an_opener_button():
    html = _read("index.html")
    header = html[html.index("<header"):html.index("</header>")]
    assert 'id="keysOpen"' in header
    assert 'id="keysOpen"' in html[html.index("<header"):html.index('id="options"')]   # before Options
    assert '$("keysOpen")' in _read("app.js")


def test_the_caveat_appears_once_in_the_empty_state():
    _, main = _main_html()
    assert main.count("nothing is written to Word") == 1


def test_closed_keeps_the_panel_toggle_and_the_keys_sheet_usable():
    body = _function_body(_read("app.js"), "closed")
    for id_ in ("panelToggle", "keysOpen", "keysClose"):
        assert id_ in body, id_
    assert '$("options").open = false' in body


def test_the_options_button_does_not_toggle_after_close_and_escape_returns_focus():
    body = _function_body(_read("app.js"), "wirePopover")
    assert "state.closed" in body
    assert "summary.focus()" in body


def test_fit_reports_the_scale_of_the_page_on_screen():
    js = _read("app.js")
    assert "function currentPage()" in js
    assert "showZoom()" in _function_body(js, "applyZoom")
    assert "currentPage()" in _function_body(js, "showZoom")
    assert "currentPage()" in _function_body(js, "updatePageStatus")


def test_the_change_rows_share_one_tab_stop():
    js = _read("changes.js")
    assert "li.tabIndex = 0;" not in js
    assert "tabIndex = " in js and "li.focus(" in js


def test_the_clamped_row_leaves_room_for_the_underline():
    css = _read("style.css")
    rule = next(line for line in css.splitlines() if "-webkit-line-clamp: 2" in line)
    assert "padding-bottom" in rule


def test_the_aria_roles_are_on_the_cards_toggle_progress_and_notice():
    html = _read("index.html")
    assert re.search(r'<div id="progress"[^>]*role="progressbar"', html)
    assert re.search(r'<div id="notice"[^>]*role="status"', html)
    assert re.search(r'<div id="cardA"[^>]*role="button"', html)
    assert re.search(r'<div id="cardB"[^>]*role="button"', html)
    assert re.search(r'<button id="panelToggle"[^>]*aria-controls="panel"', html)
    assert "aria-expanded" in _function_body(_read("changes.js"), "initPanel")


def test_the_formatting_tile_dims_with_the_others():
    assert ".formatting" in _function_body(_read("changes.js"), "markTiles")


def test_the_page_tells_the_server_it_is_going_when_it_unloads():
    js = _read("app.js")
    # the last ping before a close says hidden=1 (visibilitychange), which would keep the server
    # waiting for ever; a closing beacon on pagehide takes that back so the stop takes the normal 8 s
    assert '"pagehide"' in js and 'navigator.sendBeacon("/api/ping?hidden=0&closing=1")' in js


# v2.3.0: the majors.
from pathlib import Path


def _node(script, code):
    """Runs `code` under node with the viewer module imported as `m` (modules whose top level
    never touches the DOM: strip.js, copy.js)."""
    uri = Path(str(VIEWER.joinpath(script))).as_uri()
    r = subprocess.run([shutil.which("node"), "--input-type=module", "-e", f'import * as m from "{uri}";\n{code}'],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_the_strip_is_served_and_sits_after_the_pages():
    assert "strip.js" in STATIC
    html = _read("index.html")
    assert html.index('id="strip"') > html.index("</main>") and html.index('id="strip"') < html.index('id="rowMenu"')
    assert 'initStrip()' in _function_body(_read("app.js"), "wire")
    js = _read("changes.js")
    assert "delta:filtered" in _function_body(js, "refilter")
    assert "delta:selected" in _function_body(js, "go")
    assert "delta:goto" in _function_body(js, "initChanges")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_marks_sit_on_the_scrollbar_track_in_scroll_geometry():
    # A 1149 px strip beside a pane with 15 px scrollbars: the track runs from the 15 px top arrow
    # button to above the bottom button and the horizontal scrollbar (1149 - 15 - 30 = 1104).
    # An overlay scrollbar (width 0) has no buttons: the whole strip is the track.
    out = _node("strip.js", "const t = m.track(1149, 15, 15); const o = m.track(600, 0, 0);"
                "console.log(JSON.stringify([t, o, m.markTop(0, 200000, t), m.markTop(100000, 200000, t), m.markTop(300, 600, o)]));")
    assert out == '[{"top":15,"len":1104},{"top":0,"len":600},15,567,300]'


def test_the_strip_places_marks_by_page_offset_and_zoom_and_follows_relayouts():
    js = _read("strip.js")
    body = _function_body(js, "initStrip")
    assert "page.offsetTop + a.top * (Number(page.dataset.scale) || 1) * PT" in body
    assert "main.offsetWidth - main.clientWidth" in body and "main.offsetHeight - main.clientHeight" in body
    assert 'document.addEventListener("delta:resized", relayout)' in body
    assert 'window.addEventListener("resize", relayout)' in body


def test_changed_pages_only_is_a_view_toggle_and_a_pdf_flag():
    html = _read("index.html")
    popover = html[html.index('id="options"'):html.index("</details>")]
    on_page = popover[popover.index("On the page"):popover.index("<h4>Rendering")]
    pdf = popover[popover.index("<h4>PDF"):]
    assert 'id="showChangedOnly"' in on_page and 'id="changedOnly"' in pdf
    js = _read("app.js")
    option_ids = re.search(r"const OPTION_IDS = \[(.*?)\];", js).group(1)
    assert "showChangedOnly" not in option_ids and "changedOnly" not in option_ids
    assert '"delta.changedOnly"' in js
    assert "changed_only=" in _function_body(js, "savePdf")
    assert ".page:not([hidden])" in _function_body(js, "currentPage")
    assert ".page:not([hidden])" in _function_body(js, "applyZoom")
    assert "changed page" in _function_body(js, "updatePageStatus")


def test_copy_controls_exist():
    assert "copy.js" in STATIC
    html = _read("index.html")
    head = html[html.index('<div class="head">'):html.index('id="tiles"')]
    assert 'id="copyFinal"' in head
    menu = html[html.index('id="rowMenu"'):]
    assert 'role="menu"' in html[html.index('<div id="rowMenu"'):html.index('id="rowMenu"') + 60]
    assert menu.count('role="menuitem"') == 2 and 'data-side="modified"' in menu and 'data-side="original"' in menu
    js = _read("changes.js")
    assert 'class="more"' in _function_body(js, "renderList") and "contextmenu" in js
    assert "navigator.clipboard.writeText" in js and "flash(" in js
    assert "export function flash(" in _read("app.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_text_of_joins_one_side_of_the_rows():
    rows = ("[{oi:0,ni:0,marker:'1.',old_marker:null,num_changed:false,segments:[{m:'eq',t:'aa '},{m:'del',t:'bb'},{m:'ins',t:'cc'}]},"
            "{oi:null,ni:1,marker:'',old_marker:null,num_changed:false,segments:[{m:'ins',t:'new para'}]},"
            "{oi:1,ni:null,marker:'',old_marker:null,num_changed:false,segments:[{m:'del',t:'gone'}]},"
            "{oi:2,ni:2,marker:'b)',old_marker:'a)',num_changed:true,segments:[{m:'eq',t:'same'}]}]")
    out = _node("copy.js", f"const r = {rows}; console.log(JSON.stringify([m.textOf(r, 'modified'), m.textOf(r, 'original')]));")
    assert out == '["1. aa cc\\nnew para\\nb) same","1. aa bb\\ngone\\na) same"]'


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_text_of_the_original_takes_a_shared_segments_own_text():
    # a contents line's page number: shared as compared, printed differently on each side
    rows = "[{oi:0,ni:0,marker:'',old_marker:null,num_changed:false,segments:[{m:'eq',t:'Scope 9',o:'Scope 7'}]}]"
    out = _node("copy.js", f"const r = {rows}; console.log(JSON.stringify([m.textOf(r, 'modified'), m.textOf(r, 'original')]));")
    assert out == '["Scope 9","Scope 7"]'


def test_gutter_numerals_get_a_floor_on_screen():
    body = _function_body(_read("app.js"), "applyZoom")
    assert "text.gutter" in body and "GUTTER_MIN_PX" in body
    assert "const GUTTER_MIN_PX = 9;" in _read("app.js")


def test_the_list_is_built_from_the_passages():
    # v2.5.0: one row per passage; a tile solos exactly its category; the three counts add up.
    js = _read("changes.js")
    assert "function entriesOf(" in js and "d.passages.map(" in _function_body(js, "entriesOf")
    assert "function group(" not in js
    assert 'const TILES = [["Insertions", "insertions", "insertion"], ["Deletions", "deletions", "deletion"],' in js
    assert '["Numbering", "numbering_changes", "numbering"]];' in js and "Amendments" not in js
    assert "amendment" not in js and "inserted_runs" not in js and "passages\"" not in js
    body = _function_body(js, "matchesTile")
    assert "en.category === tile" in body
    assert "jumpTo(lead, visible[i].rowIndex)" in _function_body(js, "go")


# v2.4.0: the side-by-side panes (spec section 12.4).

def test_the_toolbar_holds_the_four_view_toggles_before_the_navigation():
    html = _read("index.html")
    header = html[html.index("<header"):html.index("</header>")]
    views = header[header.index('id="views"'):header.index('class="nav"')]
    for id_, key in (("viewOriginal", "1"), ("viewBlackline", "2"), ("viewModified", "3"), ("viewMarks", "m")):
        assert f'id="{id_}"' in views and f"<kbd>{key}</kbd>" in views, id_
        assert 'aria-pressed=' in views[views.index(f'id="{id_}"'):views.index(f'id="{id_}"') + 160]
    assert 'id="viewBlackline" aria-pressed="true"' in views.replace("\n", " ")
    keys = html[html.index('<dialog id="keys"'):html.index("</dialog>")]
    assert "<kbd>1</kbd>" in keys and "<kbd>m</kbd>" in keys


def test_the_page_area_is_a_row_of_three_panes_with_the_blackline_keeping_its_id():
    html = _read("index.html")
    body = html[html.index('<div id="body">'):html.index('<div id="strip"')]
    assert body.index('id="view"') < body.index('id="paneOriginal"') < body.index('id="pages"') < body.index('id="paneModified"')
    for id_ in ("paneOriginal", "pages", "paneModified"):
        seg = body[body.index(f'id="{id_}"'):]
        assert 'class="caption"' in seg[:400], id_          # every pane starts with its caption
    assert 'id="paneOriginal" class="pane" data-side="original" hidden' in body.replace("\n", " ")
    assert 'id="pages" class="pane" data-side="blackline"' in body.replace("\n", " ")
    css = _read("style.css")
    assert "#view { display: flex;" in css and "#view .pane" in css and "#view.multi .caption" in css
    assert "#view .page rect.hl" in css                  # the current-change band is drawn in the side panes too


def test_panes_js_is_served_wired_and_owns_the_toggles():
    assert "panes.js" in STATIC
    js = _read("panes.js")
    for name in ("initPanes", "applyPanes", "renderPanes", "resetPanes", "visiblePanes", "leadPane", "paneOf", "highlightSides"):
        assert f"export function {name}(" in js, name
    assert 'SIDE_ORDER = ["original", "blackline", "modified"]' in _read("sides.js")   # declared once
    body = _function_body(js, "toggleView")
    assert "visiblePanes().length === 1" in body           # the last pane on cannot be turned off
    assert '"delta:panes"' in _function_body(js, "applyPanes")
    assert "$(\"viewMarks\").disabled" in _function_body(js, "applyPanes")
    assert "state.data.side_marks !== state.marks" in _function_body(js, "applyPanes")
    app = _read("app.js")
    assert "initPanes()" in _function_body(app, "wire") and "views: { original: false, blackline: true, modified: false }" in app
    assert "marks: false" in app and "resetPanes()" in _function_body(app, "compareNow")
    assert "&marks=" in _function_body(app, "restyle") and "marks: state.marks" in _function_body(app, "compareNow")
    assert "renderPanes()" in _function_body(app, "renderPages")


def test_requests_name_the_visible_sides_and_a_shown_pane_fetches_its_pages():
    # v2.4.3: the server lays out and draws a side on its first request; the viewer asks only for
    # the panes on screen, a new comparison for the blackline alone, and a pane turned on whose
    # pages the data lacks brings a redraw request. Toggles wait for the request in flight.
    js = _read("panes.js")
    assert "export function requestedSides(" in js and "state.views[s]" in _function_body(js, "requestedSides")
    assert "missingSide()" in _function_body(js, "applyPanes") and "!state.busy" in _function_body(js, "applyPanes")
    assert "if (state.busy) return;" in _function_body(js, "toggleView")
    assert "if (blk) {" in _function_body(js, "buildPages")
    assert "!state.data.sides[side]" in _function_body(js, "highlightSides")
    app = _read("app.js")
    assert 'sides: ["blackline"]' in _function_body(app, "compareNow")
    assert "sides: requestedSides()" in _function_body(app, "relayout")
    assert '&sides=${requestedSides().join(",")}' in _function_body(app, "restyle")
    assert "blk ? blk.page_count : state.data.page_count" in _function_body(app, "updatePageStatus")
    assert "if (!blk) return;" in _function_body(_read("changes.js"), "jumpTo")
    assert "blk ? buildIndex(blk.rows, pageTop, scale) : []" in _function_body(_read("sync.js"), "refreshSync")


def test_a_drop_never_compares_by_itself_and_the_defer_toggle_lives_under_options():
    # v2.4.4: every fill waits for the Compare button; the "Defer page rendering" toggle puts
    # content-visibility on the pages and warms the rest up in idle time. v2.4.5: it is on by
    # default and sits under Options > Rendering, not on the main display.
    src = _read("sources.js")
    assert "handlers.compare()" not in _function_body(src, "takeDrop")
    html = _read("index.html")
    popover = html[html.index('<div class="popover">'):html.index("</details>")]
    assert 'id="deferPages" checked' in popover
    assert popover.index("<h4>Rendering</h4>") < popover.index('id="deferPages"') < popover.index("<h4>PDF</h4>")
    assert html.count('id="deferPages"') == 1 and 'class="test"' not in html
    app = _read("app.js")
    assert "deferPages: true," in _read("app.js")
    assert 'localStorage.getItem("delta.deferPages") !== "off"' in app       # absent = on
    assert "export function deferPage(" in app and "export function warmPages(" in app
    assert 'contentVisibility = state.deferPages ? "auto" : ""' in _function_body(app, "deferPage")
    assert "buildPages(main, state.data)" in _function_body(app, "renderPages") and "warmPages()" in _function_body(app, "renderPages")
    assert "deferPage(page)" in _function_body(_read("panes.js"), "buildPages")
    assert "containIntrinsicSize" in _function_body(app, "applyZoom")
    assert ".test" not in _read("style.css")


def test_zoom_changed_pages_and_lookups_span_the_panes():
    app = _read("app.js")
    assert "visiblePanes()" in _function_body(app, "applyZoom") and "pane.el.clientWidth" in _function_body(app, "applyZoom")
    assert "state.data.sides[" in _function_body(app, "applyChangedOnly")
    assert "leadPane()" in _function_body(app, "currentPage")
    ch = _read("changes.js")
    assert 'document.querySelector(`.page[data-page=' not in ch and '$("pages").querySelector(`.page[data-page=' in ch
    assert "highlightSides(" in _function_body(ch, "highlight")
    assert 'main.querySelector(`.page[data-page="${a.page}"]`)' in _read("strip.js")   # the blackline's page


# v2.4.0: the scroll sync (spec §12.5).

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_row_index_is_sorted_by_position_and_skips_hidden_pages():
    code = """
const rows = {"0": {page: 1, top: 72, height: 12}, "1": {page: 1, top: 84, height: 12}, "5": {page: 2, top: 72, height: 24}, "7": {page: 3, top: 72, height: 12}};
const pageTop = (p) => ({1: 0, 2: 1000, 3: null})[p];     // page 3 is hidden (changed pages only)
const ix = m.buildIndex(rows, pageTop, 2);
console.log(JSON.stringify(ix));
console.log(m.rowAt(ix, 0), m.rowAt(ix, 167), m.rowAt(ix, 168), m.rowAt(ix, 5000), m.rowAt(ix, -1));
console.log(m.follow(ix, 5, 0.5), m.follow(ix, 3, 0.25), m.follow(ix, 9, 0), m.follow([], 0, 0));
"""
    out = _node("sync.js", code).splitlines()
    assert out[0] == '[{"row":0,"top":144,"height":24},{"row":1,"top":168,"height":24},{"row":5,"top":1144,"height":48}]'
    assert out[1] == "-1 0 1 2 -1"                      # index positions: the greatest top <= y; -1 above the first
    assert out[2] == "1168 192 1192 0"                  # row 5 + half its height; row 3 is absent -> the end of row 1; row 9 -> the end of row 5; nothing -> 0


def test_sync_js_is_served_and_hung_on_the_panes():
    assert "sync.js" in STATIC
    js = _read("sync.js")
    for name in ("buildIndex", "rowAt", "follow", "initSync", "refreshSync", "syncFrom"):
        assert f"export function {name}(" in js, name
    assert "lastSet" in _function_body(js, "onScroll")     # a follower's own programmatic scroll is not a lead
    app = _read("app.js")
    assert "initSync()" in _function_body(app, "wire") and "refreshSync()" in _function_body(app, "applyZoom")
    assert "syncFrom(" in _function_body(_read("changes.js"), "go")
    assert "syncFrom(" in _function_body(_read("panes.js"), "applyPanes") or "delta:panes" in js


# Task 5 review fixes: per-page scale under Fit, a clamped follower write, a jump that lands past
# an absent row, and initSync registered before initPanes first announces the panes.

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_sync_index_scales_each_page_by_its_own_fit():
    code = """
const rows = {"0": {page: 1, top: 10, height: 10}, "1": {page: 2, top: 10, height: 10}};
const pageTop = (p) => ({1: 0, 2: 500})[p];
const scale = (p) => (p === 1 ? 2 : 4);
console.log(JSON.stringify(m.buildIndex(rows, pageTop, scale)));
"""
    out = _node("sync.js", code)
    assert out == '[{"row":0,"top":20,"height":20},{"row":1,"top":540,"height":40}]'


def test_the_follower_write_is_clamped_to_the_panes_end():
    assert "pane.scrollHeight - pane.clientHeight" in _function_body(_read("sync.js"), "syncFrom")


def test_a_jump_to_an_absent_row_lands_after_the_nearest_earlier_one():
    assert "while (j >= 0 && !rows[String(j)]) j--;" in _function_body(_read("changes.js"), "jumpTo")


def test_sync_listens_before_the_panes_first_announce_themselves():
    body = _function_body(_read("app.js"), "wire")
    assert body.index("initSync()") < body.index("initPanes()")


# Final whole-branch review fixes.


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_follow_searches_by_row_in_any_index_order():
    # Inside a table, cells of one row start at the same y: a cell with two paragraphs (rows 5, 6)
    # followed by a one-paragraph cell (row 7) yields index order 5, 7, 6 by top - follow must
    # still search by row, not assume the top-sorted array is row-sorted too.
    code = ('const ix = m.buildIndex({"5": {page:1, top:100, height:12}, "6": {page:1, top:112, height:12}, '
            '"7": {page:1, top:100, height:12}}, () => 0, 1);\n'
            "console.log(m.follow(ix, 6, 0.5), m.follow(ix, 7, 0), m.follow(ix, 8, 0));")
    assert _node("sync.js", code) == "118 100 112"


def test_the_notice_and_the_drop_zone_cover_the_whole_pane_row():
    html = _read("index.html")
    assert html.index('id="notice"') > html.index('id="view"')
    assert html.index('id="notice"') < html.index('id="paneOriginal"')
    js = _read("sources.js")
    assert '$("view")' in _function_body(js, "initSources")
    assert "home.append(src)" in _function_body(js, "placeSources")
    css = _read("style.css")
    assert "#view.over" in css and "#view .notice" in css
    assert "#pages.over" not in css


def test_refresh_sync_builds_its_page_map_in_one_walk():
    assert '.querySelectorAll(".page")' in _function_body(_read("sync.js"), "refreshSync")


def test_resized_no_longer_duplicates_apply_zooms_refresh():
    js = _read("sync.js")
    # delta:resized only re-syncs; app.js's applyZoom already calls refreshSync()
    body = js[js.index('addEventListener("delta:resized"'):]
    body = body[:body.index("});")]
    lines = [ln for ln in body.splitlines() if not ln.strip().startswith("//")]
    assert "refreshSync()" not in "\n".join(lines) and "syncFrom(" in body


def test_the_stray_left_border_is_gone():
    css = _read("style.css")
    assert "#view .pane:not([hidden]) ~ .pane:not([hidden])" in css
    assert "#view .pane + .pane:not([hidden])" not in css


def test_a_hidden_blackline_never_cancels_a_side_pane_jump():
    body = _function_body(_read("changes.js"), "go")
    assert body.index("if (state.views.blackline)") < body.index("data.anchors[")


def test_highlight_calls_highlight_sides_directly():
    body = _function_body(_read("changes.js"), "highlight")
    assert "highlightSides(visible[i].cid);" in body


def test_marks_follows_the_busy_rule():
    assert "viewMarks" in _function_body(_read("app.js"), "enableControls")
    assert "state.busy" in _function_body(_read("panes.js"), "toggleMarks")


def test_index_carries_an_inline_svg_icon():
    """The app window's icon is the page's favicon: an inline data URI (no fetch, works offline)
    holding the delta mark — a stroked triangle with a red and a blue bar."""
    from urllib.parse import unquote

    html = _read("index.html")
    href = re.search(r'<link rel="icon" type="image/svg\+xml" href="([^"]+)">', html).group(1)
    assert href.startswith("data:image/svg+xml,")
    svg = unquote(href[len("data:image/svg+xml,"):])
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<metadata" not in svg and "http" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert svg.count("<rect") == 2 and svg.count("<path") == 1
    assert "#FF0000" in svg.upper() and "#0000FF" in svg.upper()
    assert len(href) < 600


# 2.4.6: the minors left from the v2.3.0 and v2.4.0 reviews.
def test_the_side_map_is_declared_once_and_shared():
    assert "sides.js" in STATIC
    sides = _read("sides.js")
    assert "export const PANE = " in sides and not re.search(r"^import ", sides, re.M)
    assert 'from "./sides.js"' in _read("panes.js") and 'from "./sides.js"' in _read("sync.js")
    assert "PANE = {" not in _read("panes.js") and "PANE = {" not in _read("sync.js")


def test_one_page_builder_serves_the_blackline_and_the_side_panes():
    js = _read("panes.js")
    assert "export function buildPages(" in js
    assert "buildPages(paneOf(side), state.data.sides[side])" in _function_body(js, "renderPanes")
    assert "document.createElement" not in _function_body(_read("app.js"), "renderPages")
    assert "pageSize(svg).w" in _function_body(js, "highlightSides")


def test_strip_marks_carry_an_aria_label_and_keep_their_anchor():
    js = _read("strip.js")
    body = _function_body(js, "initStrip")
    assert 'b.setAttribute("aria-label", b.title)' in body
    assert "marks.set(p.cid, { button: b, anchor: a })" in body      # placed again at every relayout


def test_the_row_menu_keeps_focus_and_opens_from_the_keyboard():
    js = _read("changes.js")
    init = _function_body(js, "initChanges")
    assert 'menu.addEventListener("keydown"' in init and '"Tab"' in init and '"ArrowDown"' in init
    lst = _function_body(js, "renderList")
    assert 'class="more" aria-haspopup="menu" tabindex="-1"' in lst
    assert '"ContextMenu"' in lst and '"F10"' in lst
    assert "Shift</kbd>+<kbd>F10" in _read("index.html")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_copied_paragraph_count_is_the_line_count():
    rows = ('[{"ni": 0, "oi": 0, "marker": "", "segments": [{"m": "eq", "t": "a"}]},'
            ' {"ni": null, "oi": 1, "marker": "", "segments": [{"m": "del", "t": "b"}]},'
            ' {"ni": 1, "oi": null, "marker": "", "segments": [{"m": "ins", "t": "c"}]}]')
    out = _node("copy.js", f"const r = {rows}; console.log(m.linesOf(r, 'modified').length, JSON.stringify(m.textOf(r, 'modified')));")
    assert out == '2 "a\\nc"'                          # node prints the JSON escape: a backslash and an n
    assert "linesOf(data.changes" in _function_body(_read("changes.js"), "initChanges")


def test_changed_pages_only_keeps_the_readers_place():
    app = _read("app.js")
    assert "function anchorPage(" in app and "function scrollToPage(" in app
    wire = _function_body(app, "wire")
    assert "const at = anchorPage(lead);" in wire and "scrollToPage(lead, at);" in wire
    assert wire.index("anchorPage(lead)") < wire.index("applyChangedOnly();") < wire.index("scrollToPage(lead, at)")


# v2.5.0: the numbered unit of a comparison is a passage, not a paragraph.
def test_the_strip_the_panes_and_the_styles_speak_passages():
    strip, panes, css = _read("strip.js"), _read("panes.js"), _read("style.css")
    assert "data.passages" in strip and "row.category" not in strip and "amendment" not in strip
    assert "row.cids.includes(cid)" in panes
    assert "amendment" not in css and ".tile.passages" not in css
    assert "grid-template-columns: repeat(3, 1fr)" in css


def test_a_passage_row_shows_its_own_passage_with_context():
    js = _read("rowtext.js")
    assert "rowtext.js" in STATIC
    assert 'from "./rowtext.js"' in _read("changes.js")
    body = _function_body(js, "changeText")
    assert "s.cid === en.cid" in body                       # the passage's segments
    assert 'en.category === "numbering"' in body            # the marker row
    assert "keepEnd(before, context)" in body and "keepStart(after, context)" in body


# v2.5.0 review: changeText's row-HTML is exercised behaviourally under node (it moved to
# rowtext.js, which touches no DOM, unlike changes.js which cannot be imported under node).

def _entry(cid, category, segments, old_marker=None, marker=None):
    return {"cid": cid, "category": category,
            "row": {"segments": segments, "old_marker": old_marker, "marker": marker}}


def _change_text(en, context):
    return _node("rowtext.js", f"console.log(m.changeText({json.dumps(en)}, {context}));")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_a_replacements_delete_row_shows_only_its_own_passage():
    en = _entry(1, "deletion", [{"m": "del", "t": "old", "cid": 1}, {"m": "ins", "t": "new", "cid": 2},
                                {"m": "eq", "t": " value", "cid": None}])
    assert _change_text(en, 40) == '<s class="del">old</s>'


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_a_replacements_add_row_shows_only_its_own_passage():
    en = _entry(2, "insertion", [{"m": "del", "t": "old", "cid": 1}, {"m": "ins", "t": "new", "cid": 2},
                                 {"m": "eq", "t": " value", "cid": None}])
    assert _change_text(en, 40) == '<u class="ins">new</u> value'


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_an_insertion_with_context_both_sides_bridges_a_deletion():
    segs = [{"m": "eq", "t": "During the term", "cid": None}, {"m": "ins", "t": ",", "cid": 1},
            {"m": "eq", "t": " ", "cid": None}, {"m": "del", "t": "OPG", "cid": 2},
            {"m": "ins", "t": "where applicableOPG ", "cid": 1}, {"m": "eq", "t": "engaged in", "cid": None}]
    en = _entry(1, "insertion", segs)
    assert _change_text(en, 40) == ('During the term<u class="ins">,</u> <s class="del">OPG</s>'
                                    '<u class="ins">where applicableOPG </u>engaged in')
    assert _change_text(en, 6) == ('…e term<u class="ins">,</u> <s class="del">OPG</s>'
                                   '<u class="ins">where applicableOPG </u>engage…')


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_a_numbering_passage_shows_the_marker_change_then_plain_text():
    segs = [{"m": "eq", "t": "Second item", "cid": None}, {"m": "ins", "t": " edited", "cid": 3}]
    en = _entry(2, "numbering", segs, old_marker="2.", marker="3.")
    assert _change_text(en, 40) == '<s class="del">2.</s> <u class="ins">3.</u> Second item edited'


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_an_inserted_segment_is_escaped():
    segs = [{"m": "ins", "t": "a < b & c", "cid": 1}]
    en = _entry(1, "insertion", segs)
    assert _change_text(en, 40) == '<u class="ins">a &lt; b &amp; c</u>'


def test_tracked_changes_notice_markup_and_copy():
    html = _read("index.html")
    for i in ("trackedA", "trackedB", "trackedNote"):
        assert i in _ids_in_html(), i
    assert 'id="trackedNote"' in html and html.index('id="trackedNote"') < html.index('id="tiles"')
    sources = _read("sources.js")
    assert "/api/inspect" in sources and "compared as if accepted" in sources
    changes = _read("changes.js")
    assert "Compared as if all changes were accepted." in changes
    assert "export function readBase64" in _read("app.js")


def test_pdf_sources_markup_and_copy():
    html = _read("index.html")
    assert html.count('accept=".docx,.pdf"') == 2 and 'accept=".docx"' not in html
    assert "sourceNote" in _ids_in_html()
    assert html.index('id="trackedNote"') < html.index('id="sourceNote"') < html.index('id="tiles"')
    sources = _read("sources.js")
    assert r"/\.(docx|pdf)$/i" in sources and "docxOnly" not in sources
    assert "text re-read from the pages" in sources
    changes = _read("changes.js")
    assert "Compared from PDFs: text and tables are re-read from the pages, so layout is approximate and images are not shown." in changes
    assert "have no text (scanned?) and were not compared" in changes
    app = _read("app.js")
    assert "/api/progress" in _function_body(app, "compareNow") and "clearInterval" in _function_body(app, "compareNow")


TEXT_ROWS = ('[{"type":"changed","segments":[{"m":"eq","t":"Fee <b> & "},{"m":"del","t":"10"},{"m":"ins","t":"30"}]},'
             '{"type":"inserted","segments":[{"m":"ins","t":"New."}]}]')


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_text_redline_html_and_markers():
    out = _node("textredline.js", f"const r = {TEXT_ROWS}; console.log(JSON.stringify([m.redlineHtml(r), m.redlineText(r)]));")
    html, text = json.loads(out)
    assert html == ('<p>Fee &lt;b&gt; &amp; <s style="color:#ff0000">10</s><u style="color:#0000ff">30</u></p>'
                    '<p><u style="color:#0000ff">New.</u></p>')
    assert text == "Fee <b> & [-10-]{+30+}\n{+New.+}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_text_count_line():
    code = ("console.log(JSON.stringify([m.countLine({total:0,insertions:0,deletions:0}),"
            "m.countLine({total:1,insertions:1,deletions:0}), m.countLine({total:3,insertions:2,deletions:1})]));")
    assert json.loads(_node("textredline.js", code)) == ["No differences", "1 change: 1 insertion",
                                                          "3 changes: 2 insertions, 1 deletion"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_text_delay_and_size():
    code = ("console.log(JSON.stringify([m.delayFor(10), m.delayFor(45000), m.delayFor(10**7),"
            "m.tooLong('x'.repeat(m.TEXT_MAX_BYTES)), m.tooLong('x'.repeat(m.TEXT_MAX_BYTES + 1)),"
            "m.tooLong('\u00e9'.repeat(m.TEXT_MAX_BYTES / 2 + 1))]));")
    assert json.loads(_node("textredline.js", code)) == [300, 500, 1000, False, True, True]


def test_text_cap_is_the_same_in_the_viewer_and_the_server():
    from delta.textread import TEXT_MAX_BYTES
    m = re.search(r"export const TEXT_MAX_BYTES = (\d+) \* (\d+);", _read("textredline.js"))
    assert int(m.group(1)) * int(m.group(2)) == TEXT_MAX_BYTES
    assert "textredline.js" in STATIC and "textcompare.js" in STATIC


def test_the_paste_boxes_sit_under_compare_on_the_start_screen():
    html = _read("index.html")
    for id_ in ("paste", "textA", "textB", "textSwap", "textResult", "textCount", "textCopy", "textRedline"):
        assert f'id="{id_}"' in html, id_
    src = html[html.index('id="sources"'):html.index("</section>", html.index('id="sources"'))]
    assert src.index('id="compare"') < src.index('id="paste"') < src.index('class="hint"')
    assert "initTextCompare()" in _function_body(_read("app.js"), "wire")
    css = _read("style.css")
    assert "#bar .sources.compact .paste" in css          # hidden once a document compare is loaded


def test_text_compare_drops_stale_answers_and_keeps_text_drops():
    js = _read("textcompare.js")
    assert "/api/text" in js and "optIgnoreCase" in js
    assert "seq !==" in js or "!== seq" in js              # an answer older than the newest is dropped
    assert "stopPropagation" in js                          # a text drag into a box is not a file drop
    assert "ClipboardItem" in js and "text/html" in js and "text/plain" in js
