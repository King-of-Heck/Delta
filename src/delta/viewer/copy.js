// Plain text of one side of a list of change rows: "modified" keeps the inserted and unchanged
// segments after the row's marker, "original" the deleted and unchanged ones after the old
// marker; a row that has no paragraph on that side (oi or ni null) adds nothing; one line per
// paragraph. A shared segment printed differently in the original (a contents line's page
// number) carries that text as "o". A paragraph mark drawn for a split or merge ("pm") is in
// neither text; a paragraph that is one on that side and several on the other (a split's
// original, a merge's modified) is one line: its head row's, the original's own text and number
// ("a", "a_marker") on the original side.
// No DOM: importable under node.
export function linesOf(rows, side) {
  const keep = side === "modified" ? new Set(["ins", "eq"]) : new Set(["del", "eq"]);
  const lines = [];
  for (const row of rows) {
    if (side === "modified" ? row.ni === null || row.ni === undefined : row.oi === null || row.oi === undefined) continue;
    if (row.join && !row.head && row.join === (side === "original" ? "split" : "merge")) continue;
    const marker = side === "original" && row.join ? (row.a_marker || "") : side === "original" && row.num_changed && row.old_marker !== null && row.old_marker !== undefined
      ? row.old_marker : (row.marker || "");
    const body = side === "original" && row.join ? row.a : row.segments.filter((s) => keep.has(s.m) && !s.pm).map((s) => side === "original" && s.o !== undefined ? s.o : s.t).join("");
    lines.push(marker ? `${marker} ${body}` : body);
  }
  return lines;
}

// One paragraph per line; the count of lines is the count of paragraphs copied.
export function textOf(rows, side) {
  return linesOf(rows, side).join("\n");
}
