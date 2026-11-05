// Delta viewer: loads two .docx files, shows the server's page drawings, restyles them by
// rendering set, zooms, downloads the PDF, and keeps the server alive while this window is open
// (it stops a few seconds after the pings stop; each ping says whether the page is hidden, since
// a hidden window's timers are throttled or frozen and the server then waits for as long as it takes).
// The change list lives
// in changes.js and listens for the events dispatched here.
import { initChanges } from "./changes.js";
import { initSources, refreshSources } from "./sources.js";
import { initStrip } from "./strip.js";
import { SIDE_ORDER, buildPages, initPanes, leadPane, paneOf, renderPanes, requestedSides, resetPanes, visiblePanes } from "./panes.js";
import { initSync, refreshSync } from "./sync.js";

const $ = (id) => document.getElementById(id);
const PING_MS = 2000;
const PING_MISSES = 3;                              // a single dropped ping is not a dead server
const PT = 4 / 3;                                   // CSS px per pt
const GUTTER_MIN_PX = 9;   // the change numbers never shrink below this on screen
const ZOOM_MIN = 0.2, ZOOM_MAX = 4, ZOOM_STEP = 0.1;
const NOTICE_DELAY_MS = 400;                        // Task 4 uses it; declared here so the constants sit together
const OPTION_IDS = ["optIgnoreCase", "optCountNumbering", "showUnchanged", "showInsertions", "showDeletions", "showFormatting"];

export const state = {
  data: null, files: { a: null, b: null }, compared: null, busy: false, zoom: 1, fit: false, changedOnly: false, shown: 1,
  views: { original: false, blackline: true, modified: false }, marks: false,
  deferPages: true,                                // off-screen pages skip layout until scrolled to (Options)
  renderSet: "Standard", changeBars: true, closed: false, timer: null, noticeTimer: null,
  // Every server round trip that changes what is on screen takes a ticket; a reply whose ticket
  // is no longer the current one lost the race (a second option toggled while the first was in
  // flight) and is dropped, so the page always shows the answer to the LAST request.
  seq: 0, misses: 0, flashTimer: null,
};

export async function api(path, body) {
  const init = body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let r;
  try { r = await fetch(path, init); } catch (e) { throw new Error("Delta is not running"); }
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new Error((json && json.error) || `${r.status} ${r.statusText}`);
  return json;
}

export function pageSize(svg) {
  const [, , w, h] = svg.getAttribute("viewBox").split(/\s+/).map(Number);
  return { w, h };
}

// Deferred page rendering ("Defer page rendering" under Options, on by default): a page carries
// content-visibility: auto with its size reserved, so the browser lays out and paints only the
// pages near the view, and the rest as they scroll in. After the comparison is on screen the
// remaining pages are laid out in idle time, a few per slice, so a later scroll finds them ready.
export function deferPage(page) {
  const svg = page.querySelector("svg");
  if (!svg) return;
  const { w, h } = pageSize(svg);
  const z = Number(page.dataset.scale) || state.zoom;
  page.style.contentVisibility = state.deferPages ? "auto" : "";
  page.style.containIntrinsicSize = state.deferPages ? `auto ${w * z}pt ${h * z}pt` : "";
}

let warmGen = 0;
export function warmPages() {
  const gen = ++warmGen;
  if (!state.deferPages || !state.data) return;
  const pages = [];
  for (const pane of visiblePanes()) pages.push(...pane.el.querySelectorAll(".page[style*='content-visibility: auto']"));
  const idle = window.requestIdleCallback || ((fn) => setTimeout(() => fn({ timeRemaining: () => 8 }), 16));
  let i = 0;
  const slice = (deadline) => {
    if (gen !== warmGen) return;
    do {
      if (i >= pages.length) return;
      pages[i++].style.contentVisibility = "visible";        // laid out now, in the background
    } while (deadline.timeRemaining() > 4);
    idle(slice);
  };
  idle(slice);
}

function applyDefer() {
  for (const side of SIDE_ORDER) for (const page of paneOf(side).querySelectorAll(".page")) deferPage(page);
  warmPages();
}

function msg(text, isError) {
  const m = $("msg");
  m.textContent = text || "";
  m.classList.toggle("error", !!isError);
}

// A two-second message in the status line (a copy result); a busy message put there meanwhile wins.
export function flash(text, isError) {
  msg(text, isError);
  clearTimeout(state.flashTimer);
  state.flashTimer = setTimeout(() => { if ($("msg").textContent === text) msg(""); }, 2000);
}

function busy(on, text) {
  state.busy = on;
  document.body.classList.toggle("busy", on);
  if (state.closed) return;            // closed() already owns the card, the bar and the message
  msg(on ? text : "");
  $("progress").hidden = !on;
  clearTimeout(state.noticeTimer);
  if (on) state.noticeTimer = setTimeout(() => notice(text, "wait"), NOTICE_DELAY_MS);
  else if ($("notice").dataset.kind === "wait") hideNotice();   // an error card set meanwhile stays
  enableControls(!on);
}

// The card over the pages: "wait" after 400 ms of a request, "error" (dismissible) for a failed
// one, "closed" (not dismissible) once the page has shut itself down.
function notice(text, kind) {
  const n = $("notice");
  n.dataset.kind = kind;
  n.className = `notice ${kind}`;
  $("noticeText").textContent = text;
  $("noticeClose").hidden = kind !== "error";
  n.hidden = false;
}

function hideNotice() {
  const n = $("notice");
  n.hidden = true;
  n.dataset.kind = "";
  n.className = "notice";
}

function fail(e) {
  if (state.closed) return;            // the closed card is the one that must stay
  msg(e.message, true);
  notice(e.message, "error");
}

// The controls whose requests could overlap (relayout, restyle, compare) are disabled while any
// one of those requests is in flight, so a second toggle can't fire a request whose reply races
// the first and wins with stale data. `on` is true only when nothing is in flight; `closed` still
// wins even then, and `pdf` re-enables only when a comparison exists; `compare` is decided by `sources.js`.
function enableControls(on) {
  if (state.closed) return;
  for (const id of OPTION_IDS) $(id).disabled = !on;
  $("renderSet").disabled = !on;
  $("changeBars").disabled = !on;
  $("pdf").disabled = !(on && state.data);
  // Marks stays off while a request is in flight; re-enabling still respects the "neither side
  // pane on" rule (panes.js's applyPanes disables it too, but a request can finish after that).
  $("viewMarks").disabled = !on || !(state.views.original || state.views.modified);
  refreshSources();                                  // Compare: both slots filled and not the compared pair
}

export function readBase64(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(",")[1]);
    fr.onerror = () => reject(fr.error);
    fr.readAsDataURL(file);
  });
}

function options() {
  return {
    ignore_case: $("optIgnoreCase").checked, count_numbering: $("optCountNumbering").checked,
    show_equal: $("showUnchanged").checked, show_insertions: $("showInsertions").checked,
    show_deletions: $("showDeletions").checked, show_formatting: $("showFormatting").checked,
  };
}

function setOptions(o) {
  $("optIgnoreCase").checked = o.ignore_case;
  $("optCountNumbering").checked = o.count_numbering;
  $("showUnchanged").checked = o.show_equal;
  $("showInsertions").checked = o.show_insertions;
  $("showDeletions").checked = o.show_deletions;
  $("showFormatting").checked = o.show_formatting;
}

async function compareNow() {
  const { a, b } = state.files;
  if (!a || !b || state.closed) return;
  const seq = ++state.seq;
  busy(true, `Comparing ${a.name} with ${b.name}…`);
  try {
    const body = { a: { name: a.name, data: await readBase64(a) }, b: { name: b.name, data: await readBase64(b) },
                   options: options(), render_set: state.renderSet, change_bars: state.changeBars, marks: state.marks,
                   sides: ["blackline"] };              // a new comparison opens with the blackline alone (resetPanes)
    const label = `Comparing ${a.name} with ${b.name}…`;
    const poll = setInterval(async () => {
      try {
        const p = await api("/api/progress");
        if (state.seq === seq && state.busy) msg(p && p.pages ? `Reading ${p.name}… page ${p.page} of ${p.pages}` : label);
      } catch (e) { /* the compare request reports failures */ }
    }, 500);
    let d;
    try { d = await api("/api/compare", body); } finally { clearInterval(poll); }
    if (state.seq !== seq) return;
    if (state.closed) return;
    state.compared = { a, b };
    resetPanes();
    show(d);
  } catch (e) {
    if (state.seq === seq) fail(e);            // a superseded request's error is not the current one's
  } finally {
    if (state.seq === seq) busy(false);   // a superseded reply must not clear the newer one's status
  }
}

export async function relayout() {
  if (!state.data || state.closed) return;
  const seq = ++state.seq;
  busy(true, "Laying out…");
  try {
    const d = await api("/api/layout", { options: options(), render_set: state.renderSet, change_bars: state.changeBars, marks: state.marks, sides: requestedSides() });
    if (state.seq !== seq) return;
    show(d);
  } catch (e) {
    if (state.seq === seq) fail(e);            // a superseded request's error is not the current one's
  } finally {
    if (state.seq === seq) busy(false);   // a superseded reply must not clear the newer one's status
  }
}

export async function restyle() {
  if (!state.data || state.closed) return;
  const seq = ++state.seq;
  busy(true, "Redrawing…");
  try {
    const q = `render_set=${encodeURIComponent(state.renderSet)}&change_bars=${state.changeBars ? 1 : 0}&marks=${state.marks ? 1 : 0}&sides=${requestedSides().join(",")}`;
    const d = await api(`/api/pages?${q}`);
    if (state.seq !== seq) return;
    Object.assign(state.data, { pages: d.pages, report_lines: d.report_lines, render_set: d.render_set, change_bars: d.change_bars, sides: d.sides, side_marks: d.side_marks });
    renderPages();
    applyStyles();
    document.dispatchEvent(new CustomEvent("delta:restyled"));
  } catch (e) {
    if (state.seq === seq) fail(e);            // a superseded request's error is not the current one's
  } finally {
    if (state.seq === seq) busy(false);   // a superseded reply must not clear the newer one's status
  }
}

function show(data) {
  if (state.closed) return;                        // a reply after Quit or a dead server changes nothing
  state.data = data;
  refreshSources();
  const sel = $("renderSet");
  sel.innerHTML = "";
  for (const name of data.render_sets) {
    const o = document.createElement("option");
    o.value = o.textContent = name;
    sel.appendChild(o);
  }
  state.renderSet = data.render_set;
  state.changeBars = data.change_bars;
  sel.value = data.render_set;
  $("changeBars").checked = data.change_bars;
  setOptions(data.options);
  $("pdf").disabled = false;
  document.title = `${data.names.original} vs ${data.names.modified} — Delta`;
  renderPages();
  applyStyles();
  hideNotice();
  msg("");
  document.dispatchEvent(new CustomEvent("delta:loaded", { detail: data }));
}

function renderPages() {
  const main = $("pages");
  const keepScroll = main.scrollTop;
  buildPages(main, state.data);                    // the blackline: pages and page_count at the top level
  renderPanes();
  applyChangedOnly();
  applyZoom();
  main.scrollTop = keepScroll;
  refreshSync();
  updatePageStatus();
  warmPages();
}

// The shown page at the top of a pane (the first one whose bottom is below the scroll position).
function anchorPage(pane) {
  for (const page of pane.querySelectorAll(".page:not([hidden])")) {
    if (page.offsetTop + page.offsetHeight > pane.scrollTop) return Number(page.dataset.page);
  }
  return 1;
}

// Scroll a pane to page n, or to the nearest shown page after it (the last one when none follows).
function scrollToPage(pane, n) {
  const shown = [...pane.querySelectorAll(".page:not([hidden])")];
  const target = shown.find((p) => Number(p.dataset.page) >= n) || shown[shown.length - 1];
  if (target) pane.scrollTop = target.offsetTop;
}

// The view toggle: pages without a change mark get the hidden attribute; nothing is requested,
// the page numbers stay real (data-page), and the strip keeps mapping the whole document. When
// nothing changed, page 1 stays visible (the PDF keeps page 1 too).
function applyChangedOnly() {
  for (const side of SIDE_ORDER) {
    const blk = state.data ? state.data.sides[side] : null;
    const keep = new Set(blk ? blk.changed_pages : []);
    if (state.changedOnly && keep.size === 0) keep.add(1);
    for (const page of paneOf(side).querySelectorAll(".page")) {
      page.hidden = state.changedOnly && !keep.has(Number(page.dataset.page));
    }
  }
}

export function applyZoom() {
  for (const pane of visiblePanes()) {
    for (const page of pane.el.querySelectorAll(".page:not([hidden])")) {
      const svg = page.querySelector("svg");
      const { w, h } = pageSize(svg);
      const z = state.fit ? Math.max(ZOOM_MIN, (pane.el.clientWidth - 48) / (w * PT)) : state.zoom;
      page.dataset.scale = String(z);
      svg.setAttribute("width", `${w * z}pt`);
      svg.setAttribute("height", `${h * z}pt`);
      page.style.width = `${w * z}pt`;
      if (page.style.contentVisibility === "auto") page.style.containIntrinsicSize = `auto ${w * z}pt ${h * z}pt`;
      // 7 pt at 100 % is 9.33 px; below that the numerals are held at GUTTER_MIN_PX on screen
      const floor = z < 1 ? `${Math.max(7, GUTTER_MIN_PX / (z * PT)).toFixed(2)}px` : "";
      for (const t of svg.querySelectorAll("text.gutter")) t.style.fontSize = floor;
    }
  }
  refreshSync();
  showZoom();
  $("zoomFit").setAttribute("aria-pressed", String(state.fit));
  $("zoomFit").classList.toggle("on", state.fit);
}

// Fit gives every page its own scale (a landscape page fits smaller), so the percentage shown is
// the scale of the page under the top of the view, and it follows the scroll.
function showZoom() {
  const page = currentPage();
  state.shown = state.fit && page ? Number(page.dataset.scale) : state.zoom;
  $("zoomPct").textContent = `${Math.round(state.shown * 100)} %`;
}

// The page whose top is at or above the top of the view (the first page before any scroll).
function currentPage() {
  const lead = leadPane();
  const main = lead ? lead.el : $("pages");
  const top = main.getBoundingClientRect().top + 8;
  let current = null;
  for (const p of main.querySelectorAll(".page:not([hidden])")) {
    if (current === null || p.getBoundingClientRect().top <= top) current = p;
    else break;
  }
  return current;
}

function setZoom(z) {
  state.fit = false;
  state.zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z * 10) / 10));
  applyZoom();
}

function toggleFit() {
  state.fit = !state.fit;
  applyZoom();
}

function stepZoom(direction) {
  setZoom((state.fit ? state.shown : state.zoom) + direction * ZOOM_STEP);
}

function applyStyles() {
  const rs = state.data.render_set_styles[state.renderSet];
  if (!rs) return;
  const root = document.documentElement.style;
  root.setProperty("--ins", "#" + rs.insert.color);
  root.setProperty("--del", "#" + rs.delete.color);
  root.setProperty("--fmt", "#" + rs.formatting.color);
}

function updatePageStatus() {
  if (!state.data) {
    $("pageStatus").textContent = "No comparison";
    return;
  }
  const page = currentPage();
  const lead = leadPane();
  const blk = lead ? state.data.sides[lead.side] : null;       // absent until a newly shown pane's pages arrive
  const count = blk ? blk.page_count : state.data.page_count;
  const k = (blk ? blk.changed_pages : state.data.changed_pages).length;
  const shown = !state.changedOnly ? "" : k === 0 ? " · no changed pages, page 1 shown" : ` · ${k} changed page${k === 1 ? "" : "s"}`;
  $("pageStatus").textContent = `Page ${page ? page.dataset.page : 1} of ${count}${shown}`;
  if (state.fit) showZoom();
}

function savePdf() {
  const q = `render_set=${encodeURIComponent(state.renderSet)}&change_bars=${state.changeBars ? 1 : 0}&report=${$("report").value}&changed_only=${$("changedOnly").checked ? 1 : 0}`;
  window.location.href = `/api/pdf?${q}`;
}

function closed(text) {
  state.closed = true;
  clearInterval(state.timer);
  clearTimeout(state.noticeTimer);
  document.body.classList.add("closed");
  for (const el of document.querySelectorAll("button, input, select")) el.disabled = true;
  for (const id of ["panelToggle", "keysOpen", "keysClose"]) $(id).disabled = false;   // reading aids, not requests
  for (const b of document.querySelectorAll("#strip .mark")) b.disabled = false;   // a strip mark only scrolls, no request
  $("options").open = false;
  $("progress").hidden = true;
  msg(text, true);
  notice(text, "closed");
}

async function quit() {
  try { await api("/api/quit", {}); } catch (e) { /* already gone */ }
  closed("Delta has quit. You can close this window.");
}

function ping() {
  if (state.closed) return;                         // after Quit a visibility change must not revive the pinging
  fetch("/api/ping?hidden=" + (document.visibilityState === "hidden" ? 1 : 0), { method: "POST" })
    .then((r) => { if (!r.ok) throw new Error(); state.misses = 0; })
    .catch(() => {
      // One lost ping is a hiccup (a sleeping laptop, a busy server); three in a row is a server
      // that has gone, and only then does the page shut itself down.
      if (++state.misses >= PING_MISSES) {
        closed("Delta is no longer running. Double-click Delta.cmd to start it again.");
      }
    });
}

// The Options popover is a native <details>; it closes on Escape and on a click outside it,
// and stays open while its own controls are used.
function wirePopover() {
  const d = $("options");
  const summary = d.querySelector("summary");
  summary.addEventListener("click", (e) => { if (state.closed) e.preventDefault(); });   // the settings are dead after close
  document.addEventListener("click", (e) => { if (d.open && !d.contains(e.target)) d.open = false; });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && d.open) { d.open = false; e.preventDefault(); summary.focus(); }
  });
}

// The app was called Calandria up to v2.11.3: its saved choices carry over once, under the new names.
function carryOldSettings() {
  try {
    for (const key of ["deferPages", "changedOnly", "panel"]) {
      const old = localStorage.getItem(`calandria.${key}`);
      if (old !== null && localStorage.getItem(`delta.${key}`) === null) localStorage.setItem(`delta.${key}`, old);
      localStorage.removeItem(`calandria.${key}`);
    }
  } catch (e) { /* storage off */ }
}

function wire() {
  carryOldSettings();
  initSources({ compare: compareNow, swap: compareNow });   // the slots are already exchanged when swap fires
  for (const id of OPTION_IDS) $(id).addEventListener("change", relayout);
  $("renderSet").addEventListener("change", (e) => { state.renderSet = e.target.value; restyle(); });
  $("changeBars").addEventListener("change", (e) => { state.changeBars = e.target.checked; restyle(); });
  $("zoomOut").addEventListener("click", () => stepZoom(-1));
  $("zoomIn").addEventListener("click", () => stepZoom(1));
  $("zoomPct").addEventListener("click", () => setZoom(1));
  $("zoomFit").addEventListener("click", toggleFit);
  try { state.deferPages = localStorage.getItem("delta.deferPages") !== "off"; } catch (e) { /* storage off */ }
  $("deferPages").checked = state.deferPages;
  $("deferPages").addEventListener("change", (e) => {
    state.deferPages = e.target.checked;
    try { localStorage.setItem("delta.deferPages", state.deferPages ? "on" : "off"); } catch (e2) { /* storage off */ }
    if (state.data) applyDefer();
  });
  try { state.changedOnly = localStorage.getItem("delta.changedOnly") === "on"; } catch (e) { /* storage off */ }
  $("showChangedOnly").checked = state.changedOnly;
  $("showChangedOnly").addEventListener("change", (e) => {
    state.changedOnly = e.target.checked;
    try { localStorage.setItem("delta.changedOnly", state.changedOnly ? "on" : "off"); } catch (e2) { /* storage off */ }
    const lead = leadPane().el;
    const at = anchorPage(lead);                   // the reader's place survives the toggle
    applyChangedOnly();
    applyZoom();
    scrollToPage(lead, at);
    updatePageStatus();
    document.dispatchEvent(new CustomEvent("delta:resized"));   // the strip's band follows the new scroll height
  });
  $("keysOpen").addEventListener("click", () => { if (!$("keys").open) $("keys").showModal(); });
  for (const el of [$("paneOriginal"), $("pages"), $("paneModified")]) el.addEventListener("wheel", (e) => {
    if (state.closed) return;
    if (!e.ctrlKey) return;                          // plain wheel scrolls; Ctrl+wheel zooms instead of Edge's page zoom
    e.preventDefault();
    stepZoom(e.deltaY < 0 ? 1 : -1);
  }, { passive: false });
  document.addEventListener("keydown", (e) => {
    if (state.closed) return;
    if (e.target.matches("input, select, textarea") || e.altKey || e.metaKey) return;   // keys never act inside a text box
    if (e.ctrlKey && (e.key === "=" || e.key === "+")) { e.preventDefault(); stepZoom(1); }
    else if (e.ctrlKey && e.key === "-") { e.preventDefault(); stepZoom(-1); }
    else if (e.ctrlKey && e.key === "0") { e.preventDefault(); setZoom(1); }
    else if (e.key === "?" && !e.ctrlKey) {
      e.preventDefault();
      if (!$("keys").open) $("keys").showModal();   // a second ? while the sheet is open must not throw
    }
  });
  document.addEventListener("delta:resized", () => { if (state.fit) applyZoom(); });
  window.addEventListener("resize", () => { if (state.fit) applyZoom(); });
  for (const el of [$("paneOriginal"), $("pages"), $("paneModified")]) el.addEventListener("scroll", updatePageStatus);
  $("pdf").addEventListener("click", savePdf);
  $("quit").addEventListener("click", quit);
  document.addEventListener("visibilitychange", ping);   // tell the server at once, either way
  // The window closing fires visibilitychange (hidden=1, which would keep the server waiting for
  // ever) and then pagehide; a beacon still gets out of an unloading page and says it is closing,
  // which the server holds to even when the hidden=1 ping reaches it later. A reload clears it.
  window.addEventListener("pagehide", () => { if (!state.closed) navigator.sendBeacon("/api/ping?hidden=0&closing=1"); });
  state.timer = setInterval(ping, PING_MS);
  $("noticeClose").addEventListener("click", hideNotice);
  wirePopover();
  initStrip();
  initSync();
  initPanes();
  initChanges();
}

wire();
