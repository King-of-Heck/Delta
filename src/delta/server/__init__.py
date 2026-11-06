"""The local server behind the viewer: stdlib http.server on 127.0.0.1, one comparison in memory.

API (JSON unless stated; errors are {"error": message} with 400 for bad input, 409 when no
comparison is loaded, 404 / 405 / 413 as usual). A POST carrying an Origin that is not this
server, or a Sec-Fetch-Site other than same-origin / none, is refused with 403 before its body is
read: a loopback server is reachable from every page the browser has open.
  GET  /                      the viewer page; GET /static/<name> its script and style
  GET  /api/state             {"version", "loaded", "names", "tracked"}
  POST /api/compare           {"a": {"name", "data" (base64 .docx)}, "b": {...}, "options"?: {...},
                              "render_set"?: NAME, "change_bars"?: true|false}
                              -> the full payload (names, tracked, options, summary, changes, passages,
                              page_count, changed_pages, anchors, marks, render_sets, render_set_styles,
                              render_set, change_bars, pages (SVG strings), report_lines)
  POST /api/layout            {"options": {...}, "render_set"?: NAME, "change_bars"?: true|false}
                              -> the full payload after re-compare + re-layout
                              (options: ignore_case, count_numbering, show_equal, show_insertions,
                              show_deletions, show_formatting; every key optional, merged over the
                              current options; render_set and change_bars default to "Standard" and
                              true when omitted)
  GET  /api/pages?render_set=NAME&change_bars=0|1
                              -> {"render_set", "change_bars", "pages", "report_lines"}
  GET  /api/pdf?render_set=NAME&change_bars=0|1&report=first|last|none&changed_only=0|1
                              -> application/pdf as an attachment
  POST /api/inspect           {"name", "data" (base64 .docx or PDF)} -> {"name", "kind", "tracked",
                              "pages", "saved"}: a Word document's tracked changes (read as accepted,
                              "pages" null) or a PDF's page count ("tracked" null, its text not read
                              here); "saved" is when it was last saved (ISO 8601 UTC) or null;
                              touches no session state
  POST /api/text              {"a": str, "b": str, "options"?: {"ignore_case"}} -> {"summary", "rows":
                              [{"type", "segments": [{"m": "eq"|"ins"|"del", "t"}]}]}: two pasted texts,
                              one paragraph per non-blank line, compared by the same engine (numbering
                              never counted); 413 over 204800 bytes a side; touches no session state
  GET  /api/progress          {} when idle, else {"name", "page", "pages"}: how far the PDF being
                              read by a compare in flight has got (two PDFs read at once: both names,
                              pages summed); never takes the session lock
  POST /api/ping?hidden=0|1[&closing=0|1]
                              {"ok": true}; the page sends one every 2 s and on every change of
                              its visibility, saying whether it is hidden, and a closing=1 beacon
                              on pagehide
  POST /api/quit              {"ok": true}, then the server stops
The server also stops --idle seconds after the last request once the page has been seen
(default 8, so closing the window stops it; 0 = never), and --grace seconds (default 120) after
start if no page ever arrives. A machine that sleeps does not count as silence, and a page that
says it is hidden has no limit: the browser throttles or freezes a hidden window's timers. A page
back from hidden, like a machine back from sleep, gets the --grace seconds until its next request. The
closing beacon is final, so the close's own hidden=1 ping arriving after it does not keep the
server waiting; a page loading again clears it. `python -m delta serve` records its address in
the data folder and quits the server recorded there before it (`previous.py`).
"""
