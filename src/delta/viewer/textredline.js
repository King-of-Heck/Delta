// The pasted-text compare's pure parts (no DOM: importable under node): the count line, the redline
// as HTML (inline colours, as Word keeps them on paste) and as plain text with [-deleted-]{+inserted+}
// markers, how long to wait after typing, and the size cap the server holds too (textread.py).
export const TEXT_MAX_BYTES = 200 * 1024;
const INS = "#0000ff";
const DEL = "#ff0000";

export function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function countLine(summary) {
  if (!summary.total) return "No differences";
  const parts = [];
  if (summary.insertions) parts.push(plural(summary.insertions, "insertion"));
  if (summary.deletions) parts.push(plural(summary.deletions, "deletion"));
  return `${plural(summary.total, "change")}: ${parts.join(", ")}`;
}

export function redlineHtml(rows) {
  return rows.map((r) => "<p>" + r.segments.map((s) =>
    s.m === "ins" ? `<u style="color:${INS}">${esc(s.t)}</u>` :
    s.m === "del" ? `<s style="color:${DEL}">${esc(s.t)}</s>` : esc(s.t)).join("") + "</p>").join("");
}

export function redlineText(rows) {
  return rows.map((r) => r.segments.map((s) =>
    s.m === "ins" ? `{+${s.t}+}` : s.m === "del" ? `[-${s.t}-]` : s.t).join("")).join("\n");
}

// 300 ms after the last keystroke, 100 ms more per 20 000 characters, never over a second.
export function delayFor(chars) {
  return Math.min(1000, 300 + 100 * Math.floor(chars / 20000));
}

export function tooLong(s) {
  return new TextEncoder().encode(s).length > TEXT_MAX_BYTES;
}
