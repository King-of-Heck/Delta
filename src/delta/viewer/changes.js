// The change list panel: summary tiles that filter (click to solo a category, again for all),
// the location select, the numbered list, one row per passage, with the passage's text styled
// like the page (two lines per row, the selected row in full), navigation from the toolbar and
// the keys, and the on-page highlight of the selected change (a translucent band over every line
// and changed table row carrying its number).
import { pageSize, flash, state } from "./app.js";
import { linesOf, textOf } from "./copy.js";
import { highlightSides, leadPane } from "./panes.js";
import { syncFrom } from "./sync.js";
import { changeText, esc } from "./rowtext.js";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const CONTEXT = 40;                   // characters of unchanged text kept on each side of a change (selected row)
const CONTEXT_SHORT = 24;             // the same for the clamped rows
const BADGE = { insertion: "Add", deletion: "Delete", numbering: "Number" };
const TILES = [["Insertions", "insertions", "insertion"], ["Deletions", "deletions", "deletion"],
               ["Numbering", "numbering_changes", "numbering"]];

let data = null;
let entries = [];                     // every passage, document order
let visible = [];                     // after the filters
let current = -1;                     // index into visible
let solo = null;                      // the one category shown, or null for all
let hasTables = false;                // any change inside a table (the table glyph is shown only then)
let menuEntry = null;                 // the entry the row menu is open for
let menuRow = null;                   // the row to give the focus back to

const COMMENT_STATE = { added: "Added", removed: "Removed", edited: "Edited", unchanged: "" };

export function initChanges() {
  document.addEventListener("delta:loaded", (e) => { data = e.detail; build(); });
  document.addEventListener("delta:restyled", () => highlight(current));
  $("fLocation").addEventListener("change", refilter);
  $("tiles").addEventListener("click", (e) => {
    const t = e.target.closest("button.tile");
    if (!t) return;
    solo = solo === t.dataset.cat ? null : t.dataset.cat;
    refilter();
  });
  $("navFirst").addEventListener("click", () => go(0));
  $("navPrev").addEventListener("click", () => go(current - 1));
  $("navNext").addEventListener("click", () => go(current + 1));
  $("navLast").addEventListener("click", () => go(visible.length - 1));
  $("goto").addEventListener("change", (e) => {
    const n = Number(e.target.value);
    const i = visible.findIndex((en) => en.cid === n);
    if (i >= 0) go(i);
    e.target.value = "";
  });
  document.addEventListener("delta:goto", (e) => {
    const i = visible.findIndex((en) => en.cid === e.detail.cid);
    if (i >= 0) go(i);
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea") || e.ctrlKey || e.altKey || e.metaKey || $("keys").open || !$("rowMenu").hidden) return;
    if (e.key === "n" || e.key === "j" || e.key === "ArrowRight") go(current + 1);
    else if (e.key === "p" || e.key === "k" || e.key === "ArrowLeft") go(current - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(visible.length - 1);
    else return;
    e.preventDefault();
  });
  const menu = $("rowMenu");
  for (const item of menu.querySelectorAll("[role=menuitem]")) {
    item.addEventListener("click", () => {
      const en = menuEntry;
      closeMenu();
      if (en) copyText(textOf(en.rows, item.dataset.side));
    });
  }
  document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target)) closeMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !menu.hidden) { e.preventDefault(); closeMenu(); } });
  // The focus stays in the open menu: Tab, Shift+Tab and the arrows cycle through its items.
  menu.addEventListener("keydown", (e) => {
    if (e.key !== "Tab" && e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const items = [...menu.querySelectorAll("[role=menuitem]")];
    const back = e.key === "ArrowUp" || (e.key === "Tab" && e.shiftKey);
    const i = items.indexOf(document.activeElement);
    items[(i + (back ? -1 : 1) + items.length) % items.length].focus();
    e.preventDefault();
  });
  $("copyFinal").addEventListener("click", () => {
    if (!data) return;
    const lines = linesOf(data.changes, "modified");
    copyText(lines.join("\n"), `Copied ${lines.length} paragraphs`);
  });
  initPanel();
}

// The panel folds to a narrow rail; the choice survives a restart (localStorage is per origin,
// and the origin is fixed at 127.0.0.1:<port>, so it may or may not be found again - harmless).
function initPanel() {
  const panel = $("panel");
  const btn = $("panelToggle");
  let collapsed = false;
  try { collapsed = localStorage.getItem("delta.panel") === "collapsed"; } catch (e) { /* storage off */ }
  const apply = () => {
    panel.classList.toggle("collapsed", collapsed);
    btn.textContent = collapsed ? "›" : "‹";
    btn.title = collapsed ? "Show the change list" : "Hide the change list";
    btn.setAttribute("aria-expanded", String(!collapsed));
    document.dispatchEvent(new CustomEvent("delta:resized"));
  };
  btn.addEventListener("click", () => {
    collapsed = !collapsed;
    try { localStorage.setItem("delta.panel", collapsed ? "collapsed" : "open"); } catch (e) { /* storage off */ }
    apply();
  });
  apply();
}

function build() {
  entries = entriesOf(data);
  hasTables = entries.some((en) => en.loc === "table");
  solo = null;
  $("copyFinal").disabled = false;
  trackedNote();
  sourceNote();
  tiles();
  refilter();
}

// The tracked changes each source still held, read as accepted (Word's own Compare does the
// same and says so). Shown only when a side had any; re-rendered from every full payload.
function trackedNote() {
  const el = $("trackedNote");
  const t = data && data.tracked;
  if (!t || !(t.original || t.modified)) { el.hidden = true; el.textContent = ""; return; }
  const side = (n) => n ? `${n} tracked change${n === 1 ? "" : "s"}` : "none";
  el.textContent = `Original: ${side(t.original)} · Modified: ${side(t.modified)}. Compared as if all changes were accepted.`;
  el.hidden = false;
}

// Sources that were PDFs: say once that the text was re-read from the pages, and name any pages
// that had no text (a scanned signature page) and so were left out.
const NO_TEXT_ONE = "has no text (scanned?) and was not compared.";
const NO_TEXT_MANY = "have no text (scanned?) and were not compared.";

function sourceNote() {
  const el = $("sourceNote");
  const s = data && data.source;
  if (!s || s.kind === "docx") { el.hidden = true; el.textContent = ""; return; }
  const parts = [s.kind === "mixed"
    ? "Word compared with PDF: wording and numbering only. Formatting, headers, footers and comments were not compared."
    : "Compared from PDFs: text and tables are re-read from the pages, so layout is approximate and images are not shown."];
  for (const [side, label] of [["original", "Original"], ["modified", "Modified"]]) {
    const p = s.skipped[side];
    if (p.length === 1) parts.push(`${label}: page ${p[0]} ${NO_TEXT_ONE}`);
    else if (p.length) parts.push(`${label}: pages ${ranges(p)} ${NO_TEXT_MANY}`);
  }
  el.textContent = parts.join(" ");
  el.hidden = false;
}

function ranges(pages) {
  const out = [];
  for (let i = 0; i < pages.length; i++) {
    let j = i;
    while (j + 1 < pages.length && pages[j + 1] === pages[j] + 1) j++;
    out.push(j > i ? `${pages[i]}–${pages[j]}` : `${pages[i]}`);
    i = j;
  }
  return out.join(", ");
}

// One entry per passage: its row (the paragraph it sits in; the row menu copies that), its
// category, its page from the anchors.
function entriesOf(d) {
  return d.passages.map((p) => {
    const row = d.changes[p.row];
    const a = d.anchors[String(p.cid)];
    const loc = row.stream && row.stream !== "body" ? row.stream : (row.loc ? "table" : "body");
    return { cid: p.cid, category: p.category, loc, page: a ? a.page : null,
             row, rowIndex: p.row, rows: [row] };
  });
}

function tiles() {
  const s = data.summary;
  $("tiles").innerHTML = TILES.map(([label, key, cat]) =>
    `<button type="button" class="tile ${cat}" data-cat="${cat}" aria-pressed="false" ` +
    `title="Show only ${label.toLowerCase()}; click again for all"><b>${s[key]}</b><span>${label}</span></button>`).join("") +
    `<div class="tile formatting" title="Formatting changes are shown on the page but not counted or listed">` +
    `<b>${s.formatting}</b><span>Formatting · shown, not counted</span></div>`;
}

function markTiles() {
  for (const t of $("tiles").querySelectorAll("button.tile")) {
    const on = solo === t.dataset.cat;
    t.setAttribute("aria-pressed", String(on));
    t.classList.toggle("on", on);
    t.classList.toggle("dim", solo !== null && !on);
  }
  for (const t of $("tiles").querySelectorAll(".tile.formatting")) t.classList.toggle("dim", solo !== null);
}

// A tile solos exactly its category: the three counts add up to the total.
function matchesTile(en, tile) {
  return tile === null || en.category === tile;
}

function isCommentView() { return $("fLocation").value === "comment"; }

// Comments are not passages: they arrive as data.comments (their own list, never inside
// entries/rows), so the Comments filter renders that list directly instead of filtering entries.
function renderComments() {
  const ol = $("changes");
  ol.innerHTML = "";
  ol.classList.remove("tables");
  const list = (data.comments || []);
  list.forEach((c) => {
    const li = document.createElement("li");
    li.className = "comment " + c.state;
    li.style.marginLeft = `${c.depth * 12}px`;          // replies indented under their parent
    const head = (c.initials || c.author) + (c.date ? " · " + c.date.slice(0, 10) : "") + (c.done ? " ✓" : "");
    const text = c.segments.map((s) =>
      s.m === "ins" ? `<ins>${esc(s.t)}</ins>` :
      s.m === "del" ? `<del>${esc(s.t)}</del>` : esc(s.t)).join("");
    li.innerHTML = `<span class="badge">${COMMENT_STATE[c.state] || c.state}</span>` +
                   `<span class="no">C${c.cid ?? ""}</span>` +
                   `<span class="chead">${esc(head)}</span>` +
                   `<span class="text">${text}</span>`;
    // Only a numbered comment (one the diff flagged, not an "unchanged" one) has a bubble to jump
    // to -- it is the only kind the panel gives a C-label to begin with.
    if (c.cid !== null && c.cid !== undefined) li.addEventListener("click", () => goComment(c, li));
    ol.appendChild(li);
  });
  $("listCount").textContent = list.length ? `${list.length} comments` : "No comments";
}

// Scrolls #pages to a comment's own bubble (comments are drawn only on the blackline) and flashes
// its box, mirroring go()'s blackline jump for a passage -- but comments have no shared `visible`/
// `current` state of their own (refilter() leaves those alone in the Comments view), so this only
// tracks which row and which bubble are "current" locally, in the DOM.
function goComment(c, li) {
  for (const other of $("changes").querySelectorAll("li.current")) other.classList.remove("current");
  li.classList.add("current");
  for (const r of $("pages").querySelectorAll("rect.hl")) r.remove();
  const a = data.comment_anchors && data.comment_anchors[String(c.cid)];
  if (!a) { flash("This comment has no page position"); return; }
  const page = $("pages").querySelector(`.page[data-page="${a.page}"]`);
  if (!page) return;
  const svg = page.querySelector("svg");
  const scale = svg.getBoundingClientRect().height / pageSize(svg).h;
  $("pages").scrollTo({ top: page.offsetTop + a.top * scale - 80, behavior: "smooth" });
  syncFrom("blackline");
  const r = document.createElementNS(SVG_NS, "rect");
  r.setAttribute("class", "hl");
  r.setAttribute("x", String(a.left));
  r.setAttribute("y", String(a.top));
  r.setAttribute("width", String(a.width));
  r.setAttribute("height", String(a.height));
  svg.insertBefore(r, svg.firstChild);
}

function refilter() {
  if (isCommentView()) {
    markTiles();                 // leaves the tiles as-is; comments are uncounted
    renderComments();
    current = -1;
    highlight(-1);
    status();
    return;
  }
  const loc = $("fLocation").value;
  visible = entries.filter((en) => matchesTile(en, solo) && (loc === "all" || en.loc === loc));
  current = -1;
  markTiles();
  renderList();
  highlight(-1);
  status();
  document.dispatchEvent(new CustomEvent("delta:filtered", { detail: { visible: visible.map((en) => en.cid) } }));
}

function renderList() {
  const ol = $("changes");
  ol.innerHTML = "";
  ol.classList.toggle("tables", hasTables);
  visible.forEach((en, i) => {
    const li = document.createElement("li");
    li.className = en.category;
    li.dataset.i = String(i);
    li.tabIndex = i === 0 ? 0 : -1;                // one tab stop: the first row until one is selected
    li.innerHTML = `<span class="badge">${BADGE[en.category] || en.category}</span><span class="no">${en.cid}</span>` +
                   `<span class="pg" title="Page">${en.page === null ? "" : "p. " + en.page}</span>` +
                   (hasTables ? `<span class="tbl" title="${en.loc === "table" ? "In a table" : ""}">${en.loc === "table" ? "⊞" : ""}</span>` : "") +
                   `<button type="button" class="more" aria-haspopup="menu" tabindex="-1" title="Copy this change's text (Shift+F10 on the row)">⋯</button>` +
                   `<span class="text">${changeText(en, CONTEXT_SHORT)}</span>`;
    // The list is one tab stop (the current row), so the ⋯ buttons are not: the keyboard opens
    // the current row's menu with Shift+F10 or the Menu key, as a context menu is opened.
    const menuAt = () => {
      const r = li.querySelector(".more").getBoundingClientRect();
      openMenu(en, li, r.left, r.bottom + 2);
    };
    li.addEventListener("click", () => go(i));
    li.addEventListener("keydown", (e) => {
      if (e.key === "Enter") go(i);
      else if (!state.closed && (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey))) { e.preventDefault(); menuAt(); }
    });
    li.querySelector(".more").addEventListener("click", (e) => {
      if (state.closed) return;   // the button is disabled anyway; harmless
      e.stopPropagation();
      menuAt();
    });
    li.addEventListener("contextmenu", (e) => {
      if (state.closed) return;
      e.preventDefault();
      openMenu(en, li, e.clientX, e.clientY);
    });
    ol.appendChild(li);
  });
  $("listCount").textContent = !entries.length ? "No changes" :
    visible.length === entries.length ? `${entries.length} changes` : `${visible.length} of ${entries.length} changes`;
}

function status() {
  const el = $("changeStatus");
  if (!data) { el.textContent = ""; return; }
  if (!visible.length) el.textContent = entries.length ? "No changes match the filters" : "No changes";
  else el.textContent = current >= 0 ? `Change ${current + 1} of ${visible.length}` : `${visible.length} changes`;
}

function go(i) {
  // The Comments view keeps its own row/bubble state (goComment) and never touches `visible`, so
  // the toolbar buttons, the keys and #goto -- which all land here -- must no-op while it is
  // active instead of navigating (or highlighting) the passage list it left stale.
  if (isCommentView() || !visible.length) return;
  i = Math.max(0, Math.min(visible.length - 1, i));
  const ol = $("changes");
  const was = ol.querySelector("li.current");
  if (was && Number(was.dataset.i) !== i) was.querySelector(".text").innerHTML = changeText(visible[Number(was.dataset.i)], CONTEXT_SHORT);
  const focusInList = ol.contains(document.activeElement);
  current = i;
  highlight(i);
  status();
  document.dispatchEvent(new CustomEvent("delta:selected", { detail: { cid: visible[i].cid } }));
  for (const li of ol.querySelectorAll("li")) {
    const on = Number(li.dataset.i) === i;
    li.classList.toggle("current", on);
    li.tabIndex = on ? 0 : -1;                       // the list is one tab stop: the current row
  }
  const li = ol.querySelector(`li[data-i="${i}"]`);
  if (li) {
    li.querySelector(".text").innerHTML = changeText(visible[i], CONTEXT);   // the selected row shows the full text
    li.scrollIntoView({ block: li.offsetHeight > ol.clientHeight ? "start" : "nearest" });
    if (focusInList) li.focus({ preventScroll: true });   // the keys move the focus with the selection
  }
  if (state.views.blackline) {
    // These lookups are blackline-only: a change absent from the blackline pane (hidden there,
    // say) must not cancel the side-pane jump the else branch runs below.
    const a = data.anchors[String(visible[i].cid)];
    if (!a) return;
    const page = $("pages").querySelector(`.page[data-page="${a.page}"]`);
    if (!page) return;
    const svg = page.querySelector("svg");
    const scale = svg.getBoundingClientRect().height / pageSize(svg).h;
    // The followers track each step of the smooth scroll through the scroll events; the
    // immediate syncFrom aligns them before the first step lands.
    $("pages").scrollTo({ top: page.offsetTop + a.top * scale - 80, behavior: "smooth" });
    syncFrom("blackline");
  } else {
    // The blackline is off: the jump goes by the passage's row in the lead pane.
    const lead = leadPane();
    if (lead) jumpTo(lead, visible[i].rowIndex);
  }
}

function jumpTo(lead, k) {
  const blk = state.data.sides[lead.side];
  if (!blk) return;                                  // the pane's pages have not arrived yet
  const rows = blk.rows;
  let j = k;
  while (j >= 0 && !rows[String(j)]) j--;          // an absent row (an insertion seen from Original): the nearest earlier row
  if (j < 0) return;
  const a = rows[String(j)];
  const y = j === k ? a.top : a.top + a.height;      // ...and the place after it, where the missing text would sit
  const page = lead.el.querySelector(`.page[data-page="${a.page}"]`);
  if (!page) return;
  const svg = page.querySelector("svg");
  const scale = svg.getBoundingClientRect().height / pageSize(svg).h;
  lead.el.scrollTo({ top: page.offsetTop + y * scale - 80, behavior: "smooth" });
  syncFrom(lead.side);
}

function copyText(text, done) {
  if (!navigator.clipboard) { flash("Copy needs a secure page (127.0.0.1 is one)", true); return; }
  navigator.clipboard.writeText(text).then(() => flash(done || "Copied"), (e) => flash(`Copy failed: ${e && e.message ? e.message : e}`, true));
}

function openMenu(en, li, x, y) {
  const menu = $("rowMenu");
  menuEntry = en;
  menuRow = li;
  menu.hidden = false;
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(x, window.innerWidth - r.width - 4)}px`;
  menu.style.top = `${Math.min(y, window.innerHeight - r.height - 4)}px`;
  menu.querySelector("[role=menuitem]").focus();
}

function closeMenu() {
  const menu = $("rowMenu");
  if (menu.hidden) return;
  menu.hidden = true;
  menuEntry = null;
  if (menuRow) menuRow.focus({ preventScroll: true });
  menuRow = null;
}

function highlight(i) {
  for (const r of $("pages").querySelectorAll("rect.hl")) r.remove();
  if (i < 0 || !visible[i] || !data) { highlightSides(null); return; }
  const cid = visible[i].cid;
  for (const [page, top, height, cids] of data.marks) {
    if (!cids.includes(cid)) continue;
    const svg = $("pages").querySelector(`.page[data-page="${page}"] svg`);
    if (!svg) continue;
    const r = document.createElementNS(SVG_NS, "rect");
    r.setAttribute("class", "hl");
    r.setAttribute("x", "0");
    r.setAttribute("y", String(top));
    r.setAttribute("width", String(pageSize(svg).w));
    r.setAttribute("height", String(height));
    svg.insertBefore(r, svg.firstChild);
  }
  highlightSides(visible[i].cid);
}
