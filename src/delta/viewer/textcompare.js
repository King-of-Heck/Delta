// The pasted-text compare on the start screen: two boxes compared live (a pause after typing, then
// /api/text), the redline under them with its count line and a Copy redline button. Start screen
// only: once a document compare is loaded the sources go compact and CSS hides this. At most one
// request is out; an answer for text that has since changed is dropped. Nothing here is saved.
import { api } from "./app.js";
import { countLine, delayFor, redlineHtml, redlineText, tooLong } from "./textredline.js";

const $ = (id) => document.getElementById(id);
let seq = 0;
let timer = null;
let rows = null;
let inflight = false;
let dirty = false;
let copyTimer = null;

export function initTextCompare() {
  for (const id of ["textA", "textB"]) {
    $(id).addEventListener("input", schedule);
    // The page area takes file drops (sources.js); a text drag into a box must stay a text drop.
    $(id).addEventListener("drop", (e) => { if (!e.dataTransfer.files.length) e.stopPropagation(); });
    $(id).addEventListener("dragover", (e) => { if (!e.dataTransfer.types.includes("Files")) e.stopPropagation(); });
  }
  $("textSwap").addEventListener("click", () => {
    const a = $("textA").value;
    $("textA").value = $("textB").value;
    $("textB").value = a;
    schedule();
  });
  $("optIgnoreCase").addEventListener("change", schedule);
  $("textCopy").addEventListener("click", copyRedline);
  $("textMail").addEventListener("click", openInOutlook);
}

function enable(on) {
  for (const id of ["textCopy", "textMail"]) $(id).disabled = !on;
}

function compact() {
  return $("sources").classList.contains("compact");     // a document compare is loaded: no hidden compares
}

function schedule() {
  if (compact()) return;
  clearTimeout(timer);
  dirty = false;                             // the timer set below carries the latest text
  ++seq;                                     // an answer for the text as it was is now stale
  const a = $("textA").value, b = $("textB").value;
  if (!a.trim() && !b.trim()) {
    rows = null;
    dirty = false;
    $("textResult").hidden = true;
    return;
  }
  timer = setTimeout(kick, delayFor(a.length + b.length));
}

// At most one request is out. A schedule that fires meanwhile only marks the boxes dirty; when the
// answer returns, one more request carries the latest text and options.
function kick() {
  if (compact()) return;
  if (inflight) { dirty = true; return; }
  run();
}

async function run() {
  const a = $("textA").value, b = $("textB").value;
  if (!a.trim() && !b.trim()) return;
  const n = seq;
  if (tooLong(a) || tooLong(b)) return showError("Too much text to compare live (200 KB a side)");
  inflight = true;
  try {
    const d = await api("/api/text", { a, b, options: { ignore_case: $("optIgnoreCase").checked } });
    if (n !== seq) return;
    rows = d.rows;
    $("textCount").textContent = countLine(d.summary);
    $("textCount").classList.remove("error");
    $("textRedline").innerHTML = redlineHtml(d.rows);      // every piece of text is escaped by redlineHtml
    enable(true);
    $("textResult").hidden = false;
  } catch (e) {
    if (n !== seq) return;
    showError(e.message);
  } finally {
    inflight = false;
    if (dirty) { dirty = false; kick(); }
  }
}

function showError(text) {
  rows = null;
  $("textCount").textContent = text;
  $("textCount").classList.add("error");
  $("textRedline").innerHTML = "";
  enable(false);
  $("textResult").hidden = false;
}

const LABELS = { textCopy: "Copy redline", textMail: "Open in Outlook" };

function say(btn, text) {
  for (const id of Object.keys(LABELS)) $(id).textContent = LABELS[id];
  btn.textContent = text;
  clearTimeout(copyTimer);
  copyTimer = setTimeout(() => { btn.textContent = LABELS[btn.id]; }, 1500);
}

async function copyRedline() {
  if (!rows) return;
  const btn = $("textCopy");
  try {
    await navigator.clipboard.write([new ClipboardItem({
      "text/html": new Blob([redlineHtml(rows)], { type: "text/html" }),
      "text/plain": new Blob([redlineText(rows)], { type: "text/plain" }),
    })]);
    say(btn, "Copied");
  } catch (e) {
    say(btn, "Copy failed");
  }
}

// The server writes the redline as an unsent email (.eml) and opens it: Outlook shows it as a new
// message, formatting and all, with no paste in between.
async function openInOutlook() {
  if (!rows) return;
  const btn = $("textMail");
  try {
    await api("/api/mail", { a: $("textA").value, b: $("textB").value,
                             options: { ignore_case: $("optIgnoreCase").checked } });
    say(btn, "Opening…");
  } catch (e) {
    say(btn, "Could not open");
    $("textCount").textContent = e.message;
  }
}
