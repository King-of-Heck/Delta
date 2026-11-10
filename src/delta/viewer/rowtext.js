// The row's text for the change list: a numbering passage shows the old marker struck, the new
// one underlined, then the start of the paragraph; a text passage shows its own segments (from
// its first marked segment to its last, with whatever whitespace and other-side segments lie
// between) and a little unchanged text either side. Other passages of the same paragraph have
// their own rows. No DOM: importable under node.
export function esc(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

export function keepEnd(t, n) { return t.length > n ? "…" + t.slice(-n) : t; }
export function keepStart(t, n) { return t.length > n ? t.slice(0, n) + "…" : t; }

const MAX_SEGMENT = 200;              // characters of one inserted / deleted segment shown

// A word replaced by another (a deletion and an insertion side by side, in spaced text) gets a
// plain space between them, as on the page: "~~not~~ very", not "notvery".
export function gapAfter(segs, i) {
  const a = segs[i], b = segs[i + 1];
  if (!b || !((a.m === "del" && b.m === "ins") || (a.m === "ins" && b.m === "del"))) return false;
  if (!a.t || !b.t || /\s$/.test(a.t) || /^\s/.test(b.t)) return false;
  const before = i > 0 && segs[i - 1].m === "eq" ? segs[i - 1].t : "";
  const after = i + 2 < segs.length && segs[i + 2].m === "eq" ? segs[i + 2].t : "";
  return /\s$/.test(before) || /^\s/.test(after);
}

export function changeText(en, context) {
  const segs = en.row.segments;
  const mark = (s, t) => s.m === "del" ? `<s class="del">${esc(t)}</s>` : s.m === "ins" ? `<u class="ins">${esc(t)}</u>` : esc(t);
  if (en.category === "numbering") {
    const start = segs.filter((s) => s.m !== "del").map((s) => s.t).join("");
    return `<s class="del">${esc(en.row.old_marker || "")}</s> <u class="ins">${esc(en.row.marker || "")}</u> ${esc(keepStart(start, context))}`;
  }
  let first = -1, last = -1;
  segs.forEach((s, i) => { if (s.cid === en.cid) { if (first < 0) first = i; last = i; } });
  if (first < 0) return "";
  let before = "", after = "";
  for (let i = first - 1; i >= 0 && segs[i].m === "eq"; i--) before = segs[i].t + before;
  for (let i = last + 1; i < segs.length && segs[i].m === "eq"; i++) after += segs[i].t;
  let middle = "";
  for (let i = first; i <= last; i++) {
    const s = segs[i];
    middle += mark(s, s.m === "eq" ? s.t : keepStart(s.t, MAX_SEGMENT));
    if (i < last && gapAfter(segs, i)) middle += " ";
  }
  return esc(keepEnd(before, context)) + middle + esc(keepStart(after, context));
}
