# Delta changelog

## Unreleased — Delta keeps running while its window is open, and compares faster

- Comparing two PDFs takes well under half the time it did: a page's text is read by Delta's own
  reader rather than the general PDF library, and the two PDFs of a pair are read at the same
  time, the second by a helper process that Delta starts and stops itself. A 190-page pair went
  from about 15 seconds to about 4.5 on a desktop. Comparing two Word documents is about a third
  faster. The results are exactly the same as before.
- Changing only what is shown on the page (the Options toggles that hide unchanged text,
  insertions, deletions or formatting) no longer runs the comparison again.
- Delta no longer stops while its window is minimized, covered or behind a locked screen. Before,
  it could stop after about a minute and a half in the background, and coming back showed "Delta
  is no longer running". It still stops a few seconds after the window is closed.
- Starting Delta quits any older Delta still running, so one left over after a browser crash does
  not stay behind.
- The log (`%LOCALAPPDATA%\Delta\delta.log`) says why Delta stopped: Quit, or how long it had
  heard nothing from its window.

## v2.12.5 — Newer document as Modified (2026-10-28)

- Two documents dropped together are put in order by when each was last saved, the newer one as
  Modified: the date Word or the PDF maker wrote inside the file, or the file's own date when there
  is none. Before, they took the order of the drop. A file dropped onto a card still goes there, a
  drop still waits for Compare, and Swap still exchanges the sides.
- The README explains how to Unblock the downloaded zip when Windows Smart App Control blocks
  `Delta.cmd`.

## v2.12.4 — Version shown (2026-10-28)

- The window shows the Delta version in the bottom right corner of its status bar.
- The comparison summary in a saved PDF says which version made it: "Compared: 2026-10-28 17:33
  with Delta 2.12.4".

## v2.12.3 — PDF table rows that run on (2026-10-28)

- Comparing PDFs: a table row split over a page break whose every cell carries on to the next
  page is read as one row when a cell stops mid-sentence at its edge. Before, it was read as two
  rows, so it could show as changed when the two PDFs broke the page in different places.
- A paragraph inside a table cell that runs over a page break is read as one paragraph, as it
  already was in the body text.

## v2.12.2 — Desktop shortcut (2026-10-28)

- The first launch puts a Delta shortcut, with the Delta icon, on the Desktop (the real one, also
  when Windows keeps it in OneDrive), as well as beside `Delta.cmd`.
- While it stays on the Desktop, every launch points it at the folder just launched, so it follows
  a moved folder or a newer version you extracted and started once. Delete it and it is not put
  back.

## v2.12.1 — Word matching (2026-10-28)

- Within a changed paragraph, the comparison keeps longer shared words as unchanged text in
  preference to a lone "and" or a space, so a rewritten phrase reads as one change
  ("[design, procure, construct, commission and complete]") instead of pieces around the shared
  words.
- Swapping the two documents gives the same changes, mirrored, in every paragraph; before, two
  edits close together could be split differently depending on which document was the original.
- An added or removed sentence ends on its own full stop: "anniversary.[ The Customer may also
  terminate ... days.]".
- Comparing is faster on long documents.

## v2.12.0 — Calandria is now Delta (2026-10-28)

- The app is renamed Delta. Double-click `Delta.cmd` (or the `Delta` shortcut it writes beside
  itself) in the extracted folder; the window, the saved PDFs and the messages all say Delta.
  Entries below this one keep the old name.
- From the command line it is `python -m delta <command>`, not `python -m calandria`.
- The log is `%LOCALAPPDATA%\Delta\delta.log`. The old `Calandria` folder there is no longer
  written to and can be deleted.
- Choices the viewer remembers (Changed only, page loading, the folded panel) carry over from
  Calandria. `CALANDRIA_FONT_DIRS` is still read when `DELTA_FONT_DIRS` is not set.
- Nothing about comparing changes: the same documents give the same changes and counts.

## v2.11.3 — PDF titles and contents page numbers (2026-10-28)

- Comparing PDFs, a changed contents line shows its page number in the list of changes and in
  Copy, where it showed "{PAGEREF}". The page number is still not compared.
- A schedule title that opens each alternative version of a schedule at the top of a page
  ("SCHEDULE 8.1" over the fixed price, target cost ... versions) is kept in the comparison; it
  was taken out as if it were a page header.
- The footer of a landscape page is left out with the footers of the portrait pages, instead of
  being read as text.

## v2.11.2 — Same count both ways (2026-10-27)

- Swapping the two documents no longer changes the count: a comma or a dot between two pieces of
  one edit (", where applicable" typed against "6.2", a date filled in around its comma) is one
  change, not two, whichever document is the original. A full stop followed by a space still
  separates two changes.

## v2.11.1 — PDF table rows over a page (2026-10-27)

- A table row that breaks across a page in a PDF is read as one row when some of its cells ended
  before the break, so a line at the top of the next page stays in its own cell instead of
  showing as moved when the other PDF breaks the page elsewhere.

## v2.11.0 — PDF borderless tables (2026-10-27)

- A table drawn without borders in a PDF (a list of schedules, an index of forms) is read as a
  table when its columns line up row after row, so an edited entry shows inside its row, as in
  Word, instead of joining the entries around it into one paragraph.
- Numbered titles that start pages ("SCHEDULE 2.2(b)", "SCHEDULE 2.9(b)") are no longer mistaken
  for a running header and left out of a PDF.

## v2.10.6 — PDFs exported from Word (2026-10-27)

- PDFs made with Word's Export to PDF are read as well as those made with Save As PDF: words are no
  longer split by the small spacing that export puts between letters, and the "[•]" placeholders
  and Symbol characters read and draw the same from either, so one document saved both ways
  compares as the same.
- A hyphen followed by a space at the end of a line ("A- or") keeps its space.
- A list item at the top of a page after a sentence that ends on the page before ("including:")
  starts its own paragraph.

## v2.10.5 — PDF line breaks (2026-10-27)

- A line broken by hand in the middle of a sentence in a PDF (such as "a corporation" / "existing
  under the laws of ...") is read as one paragraph, as in Word, so a block wrapped differently on
  the other side no longer shows as changed.

## v2.10.4 — PDF footnotes and contents (2026-10-26)

- Footnote numbers in a PDF are recognised in more places, so renumbered footnotes no longer show
  as changes: a mark on a placeholder such as "[•]", two marks side by side, and a footnote long
  enough to fill most of its page.
- Page numbers in a table of contents are drawn as printed but no longer compared, as in Word, so
  an edit that moves the pages after it no longer changes every contents line.

## v2.10.3 — PDF pair reading (2026-10-26)

- The two PDFs of a comparison are now read by the same rules: whether the document's text wraps
  and whether it is justified are judged from both PDFs together, so a short or lightly edited
  side can no longer be read differently from the other and show changes that are not there.
- A long line that starts at an indent and runs to the margin is no longer shown right aligned.

## v2.10.2 — PDF paragraph fixes (2026-10-26)

- A heading at the top of a page in a PDF is no longer joined to the end of the paragraph on the
  page before it.
- Justified text in a PDF is shown justified, including two-line paragraphs; line ends are
  measured where the text ends, not after the spaces that follow a full stop, and the right
  margin is no longer measured too wide.
- Numbered clauses and table of contents lines that fill their line are no longer shown centred
  or right aligned.

## v2.10.1 — PDF comparison fixes (2026-10-26)

- A PDF with one landscape page no longer lays out every page too wide; the text column is now
  measured from the pages of the usual size, so two-line paragraphs are no longer split in two
  and shown as rewritten.
- Table of contents lines no longer show as changed because their dot leaders differ in length;
  a leader is read back as a tab with a dot leader, and each contents line is its own paragraph.
- A footnote repeated at the same place under several pages (such as a note under each
  signature page) is read as a footnote instead of being dropped as a page footer, so its
  reference number no longer shows as a change when notes are renumbered.
- Page breaks in the PDFs are kept: a page that the text did not fill (a title page, the end of
  a table of contents, a schedule) now ends the same way in the comparison.

## v2.10.0 — PDF comparison (2026-09-21)

- Two text PDFs made from Word can now be compared: the text, ruled tables and footnotes are
  re-read from the pages and shown as the usual redline, so layout is approximate and images are
  not shown. Headers, footers and page numbers are detected and left out of a PDF comparison,
  though a one-page document has nothing for its header to repeat against (so its header reads as
  body text) and a short document can occasionally have a real line of text mistaken for one.
  Pages with no text (a scanned signature page) are skipped and named rather than compared; a
  scanned PDF with no text anywhere, and a password-protected PDF, are both refused with a
  message. A Word document cannot yet be compared with a PDF — both files of a pair must be the
  same kind.
- Known limits of the reader: clause and list numbers are plain text, so an inserted clause
  renumbers what follows and each renumbered marker shows as a change; only tables with drawn
  borders are read as tables, and a table row split across a page break is read as two rows;
  footnotes are recognised only when printed smaller than the body text, and endnotes are always
  read as ordinary paragraphs; comments, tracked changes and anything drawn as a picture are not
  in a PDF's text and are not compared; paragraph boundaries are inferred from spacing and line
  ends, so an unusual layout can split or join a paragraph; wrapped text at one-and-a-half or
  double line spacing is the least-tested case in this release. The reader has been measured only
  against PDFs saved from Word (Save As PDF).
- A fifth vendored library, pypdf (pure Python), reads the PDF file structure. Reading a long
  PDF shows its progress page by page, and Swap no longer re-reads the files.

## v2.9.0 — Tracked changes notice (2026-09-17)

- A source document that still holds tracked changes has always been read as if every change were
  accepted (as Word's own Compare does). That is now said out loud: the source card shows
  "N tracked changes, compared as if accepted" as soon as the file is dropped, the change list
  carries one line naming each side's count after Compare, and the PDF summary report adds a
  "Tracked changes in sources" line. Nothing about how documents are read or compared changed;
  a clean document shows nothing new.
- New `POST /api/inspect` returns one file's count without touching the loaded comparison.

## v2.8.0 — Comments (2026-09-16)

- Comments are compared as displayed: matched by identity across the two documents (a durable id,
  then anchor overlap plus author, then text similarity), classified added / removed / edited /
  unchanged, threaded (a reply follows its parent), and their text diffed like body text. They are
  an uncounted, navigable side list — never a row in the change list, never part of the counted
  summary.
- Drawn as right-margin bubbles on the page holding their anchor, with a connector to the anchored
  text and a bracket around it; a resolved comment shows a check mark. A commented document widens
  its sheet to the right to hold the comment column, so the body keeps its full width and a document
  paginates the same with or without comments; comment-free documents are unchanged.
- The viewer's location filter gains a Comments entry, listing every comment change with its
  author, initials, date, resolved state and diffed text, indented by reply depth.

## v2.7.0 — Headers and footers (2026-09-15)

- Headers and footers are compared, as they are displayed: for every section, the header and
  footer Word shows on its first, odd and even pages (linked to the previous section when the
  section sets none), blank ones dropped, repeated ones once, in document order. Changes in them
  are counted like body changes, listed with the location Header or Footer, and the location
  filter has both.
- Headers and footers are drawn on every page where Word draws them: the header at the header
  distance from the top, the footer above the footer distance from the bottom, the right variant
  per page (first page, even, odd). A header or footer taller than the room above or below the
  margin pushes the body, as in Word. PAGE and NUMPAGES fields show the page's own number
  (numbering restarts and roman or letter formats honoured) and the page total; a page number
  is never a change.
- A changed header shows its redline on every page it appears on; the change bar and margin
  number sit on the first of those pages, and the change list goes there.
- An inline image in an otherwise empty paragraph keeps its box (the template's logo headers are
  as tall as in Word); the picture itself is not drawn.
- Known limits: a table inside a header is laid out as its cells' paragraphs stacked; a header
  change inside a continuous section shows from the next page; Word's red notice for a changed
  first-page or link-to-previous setting is not produced.

## v2.6.1 — Paragraph spacing as Word adds it (2026-09-15)

- The space between two paragraphs is the larger of the first one's space after and the second
  one's space before, as Word lays it out (Word's HTML paragraph auto spacing, on unless the
  document's compatibility settings turn it off). Before, the two were added, so a run of
  paragraphs with both values set came out taller than in Word; on the EPC template's cover that
  pushed its last line onto a blank page once the cover's footnote took its room. Measured against
  Word's own PDF of the template: the cover's lines now sit within a point of Word's.
- Empty paragraphs at the start of a section no longer push the section's first text a page
  further: the page a section break starts was being charged twice. On the EPC template this
  removes the blank page before the list of schedules; the template alone now lays out at 199
  pages against Word's 198.
- An empty paragraph is as tall as its paragraph mark says (its style's font and size), as in
  Word, instead of the document default size.

## v2.6.0 — Footnotes and endnotes (2026-09-15)

- Footnotes and endnotes are compared. A note that was edited, added or removed shows as a
  redline like body text, with its own change number, change bar and place in the totals and
  the tiles; the change list's location filter gains Footnotes and Endnotes. A note's changes
  are numbered right after the paragraph that references it, so the numbers read in page order.
- Footnotes are drawn at the foot of the page that references them, under Word's short
  separator rule, and endnotes after the last body paragraph. Reference marks are drawn as
  superscript numbers in the text, and a note opens with its number. A note dropped with its
  paragraph keeps a struck mark where it stood. A page keeps room for the notes it carries, so a
  line moves to the next page together with its notes. A long note is not split across pages in
  this version.
- Before, references and note text were ignored: a note's edits were invisible and the pages had
  no footnotes.

## v2.5.3 — Style bold and paragraph borders (2026-09-15)

- Bold set by a paragraph style (a TOC level, a heading, a caption) is drawn in bold, as Word
  shows it. Before, only a run's own bold was drawn, so "SECTION 1" in a table of contents whose
  style is bold came out in regular weight. The comparison is unchanged: bold from a style is
  drawn, never reported as a formatting change.
- Paragraph borders (`w:pBdr`: top, bottom, left, right, on the paragraph or its style) are drawn
  as rules in the pages and the PDF, at the border's width and colour, with Word's space between
  the text and the line. Adjacent paragraphs with the same borders and indents share one box, as
  in Word. Double, dashed and the other line styles draw as a single line. Before, borders were
  not drawn at all (the two rules on the EPC template's cover were missing).

## v2.5.2 — Tabs do not size the line (2026-09-15)

- A tab formatted larger than the text around it no longer makes its line taller, as in Word.
  A table of contents whose entries carry an 11 pt tab between 10 pt text (every entry of the
  EPC template's stored TOC) now sits at Word's line pitch, so its page breaks fall where Word's
  do. Before, each such line took the tab's height.

## v2.5.1 — Capitals, symbol characters, tab stops (2026-09-15)

- Text formatted as capitals or small capitals (`w:caps`, `w:smallCaps`, on the run or its
  paragraph style) is drawn in capitals, as Word shows it; the comparison still runs on the text
  as typed. Small capitals are drawn at full size.
- A character from a symbol font (Wingdings, Wingdings 2, Symbol: a checkbox, an arrow) measures
  its own glyph width instead of a placeholder's, and the on-screen pages draw the glyph's outline
  from the font file, so it no longer runs into the text beside it. The PDF already embedded the
  right glyph.
- Tabs advance to tab stops: the paragraph's own stops and its style's and list level's, then
  Word's default stops beyond them. Right, centre and decimal stops align the text that follows,
  and dot, hyphen and underscore leaders are drawn. A table of contents now lays out as in Word:
  number, title, leader dots, page number at the right stop. Before, every tab was one space.
- The change strip beside the pages lines up with the scrollbar: its marks and the band that
  shows the part on screen sit on the scrollbar's own track (below the top arrow button, above
  the bottom one and the horizontal scrollbar), placed by where each change really is in the
  scroll, gaps between pages and zoom included. Before, the strip spanned the whole pane height
  and placed marks by page number, so a mark sat a little off the thumb.

## v2.5.0 — Changes counted by passage (2026-09-15)

- A change is a passage: a contiguous run of inserted text, a contiguous run of deleted text, or
  a renumbered list marker. A paragraph that swaps a word holds two changes, the deletion then
  the insertion, numbered in reading order and labelled together in the margin ("22-23"). This
  is the unit other comparison tools count; the amendment class is gone, and the three tiles,
  Insertions, Deletions and Numbering, add up to the total. Before, every changed paragraph was
  one change and counted as an insertion and a deletion as well, so a 200-change document could
  read 200 insertions, 200 deletions, 200 amendments and 200 changes.
- The change list has one row per passage, showing that passage with a little unchanged text
  either side; the margin numbers sit beside the line each passage starts on; the density strip,
  the on-page highlight and the side panes follow the passage.
- The PDF summary line reads `Changes: T (insertions I, deletions D, numbering N); formatting F
  (not counted)`.
- Version 2.4.7 was built but never released; its work ships here.

## v2.4.6 — Review minors (2026-09-14)

- The row menu of the changes list keeps the keyboard focus while it is open (Tab and the arrow
  keys cycle through it) and opens from the keyboard on the current row (Shift+F10 or the Menu
  key); the row's menu buttons leave the tab order.
- "Changed pages only" keeps the reader's page when it is turned on or off.
- Copy Final reports the number of lines it copied.
- The marks on the page strip carry an accessible label, and the strip reads the page heights
  once per layout.
- Inside: the blackline and the side panes share one page builder and one side map; the server
  checks a requested side in one place; a PDF request checks the document names before its flags;
  an empty layout draws nothing instead of failing.

## v2.4.5 — Shortcut without PowerShell (2026-09-12)

- The `Calandria` shortcut beside the launcher is written by Calandria itself, in the Windows
  shortcut file format, instead of through a PowerShell command: a machine that runs PowerShell
  in Constrained Language Mode refused that command, so no shortcut appeared and its errors
  filled the log. The shortcut is rewritten only when its contents change, so a synced folder
  is left alone. The log carries one `shortcut` line per start.
- "Defer page rendering" is on by default and has moved from the main display to
  Options > Rendering, where it can still be turned off. It is remembered.

## v2.4.4 — Compare on request (2026-09-12)

- Dropping documents fills the two slots and waits for the Compare button; a first drop of two
  files no longer compares by itself.
- A test toggle, "Defer page rendering", lays out the pages as they come into view instead of all
  at once, and finishes the rest in the background once the comparison is on screen. It is off
  by default and remembered.

## v2.4.3 — Faster compare (2026-09-11)

- The compare runs about three times faster: the formatting of a paragraph is worked out run by
  run instead of character by character. The result is the same to the character (the old walk
  stays in the tests as the reference).
- The Original and Modified pages are laid out and drawn the first time their pane is turned on,
  and kept for the rest of the comparison; a new comparison draws the blackline alone. A pane
  turned on while a request is still running waits for it.
- The log (`%LOCALAPPDATA%\Calandria\calandria.log`) carries one `timing` line per compare,
  relayout and redraw, with the page count and the seconds of every stage.

## v2.4.2 — Shortcut with the icon (2026-09-11)

- `Calandria.ico` ships in the zip and the launcher writes a `Calandria` shortcut beside itself with
  that icon, refreshed on every launch: a `.cmd` cannot carry an icon, a shortcut can. Start
  Calandria from the shortcut, or drag it to the Desktop or pin it.

## v2.4.1 — Icon (2026-09-11)

- Calandria has an icon: a delta with a red and a blue bar. It is embedded in the page, so the
  app window's title bar and taskbar entry show it with no file to fetch and nothing new to install.

## v2.4.0 — Side-by-side panes (2026-09-11)

- Three toggles in the toolbar, Original, Blackline and Modified, show any combination of the
  three views side by side in that order (keys 1, 2, 3). Blackline alone is the default for
  every new comparison; the last pane on cannot be turned off.
- The Original and Modified panes are clean pages laid out by the same engine as the blackline,
  with no change marks. Marks (key m) tints the deleted text in Original and the inserted text
  in Modified.
- The panes scroll together by content: the paragraph at the top of the pane you scroll is put
  at the top of the others. Next, Prev, the change list, the density strip, zoom, Fit and Changed
  pages only act on every pane; the current change is highlighted in each.
- The PDF is unchanged: it is the blackline.

## v2.3.3 — Toolbar layout (2026-09-11)

- Once a comparison exists, the Original and Modified fields, Swap and Compare sit in the
  toolbar as a second row instead of above the pages; the remove buttons sit in line with
  the file names. The empty state's drop cards are unchanged.
- The change navigation (First, Prev, Next, Last, the number box and the count) sits at the
  right of the toolbar, beside the zoom controls and Save PDF.

## v2.3.2 — Tile filters (2026-09-11)

- The Insertions and Deletions tiles filter the way their counts count: an amendment (deleted
  and inserted text in one paragraph) counts as an insertion and as a deletion, so those two
  tiles now include amendments and a tile's list matches its number. The tooltips say so.

## v2.3.1 — Two fixes (2026-09-11)

- Gutter change numbers: changes that start on one baseline (the cells of a table row) share
  one label such as "7-9" instead of printing on top of each other, on screen and in the PDF.
- Viewer: dropping a file on the Original or Modified card clears the page area's drag
  highlight, which used to stay as a dashed outline.

## v2.3.0 — Viewer majors (2026-09-10)

- A change-density strip beside the pages: one mark per change at its place in the whole
  document, coloured by category; the current change's mark is bigger, filtered-out ones dim; a
  band shows the part of the document on screen; a click jumps to the change.
- "Changed pages only": a view toggle under Options → On the page hides the pages without a
  change (page numbers stay real; the counter says how many pages are shown), and a PDF option
  writes only the first page and the changed pages (`calandria pdf --changed-only`,
  `/api/pdf?changed_only=1`; the file name says "(changed pages)" and the summary block says
  how many of the document's pages are shown).
- Copy per change: a ⋯ button on the row, or right-click, copies the modified or the original
  text of that change; Copy Final in the change list header copies the whole modified document
  as plain text.
- The change numbers in the margin stay legible below 100 % zoom on screen; the PDF is unchanged.

## v2.2.1 — Small fixes (2026-09-10)

- Viewer: the panel's footer note is gone; the caveat stays on the empty-state card.
- Viewer: a `?` button opens the shortcut sheet; the change list is a single tab stop and the
  keys move the focus within it; the panel toggle and the sheet stay usable after the session
  closes, and the Options popover folds; Fit shows the on-screen scale of the page under the top
  of the view and follows the scroll; Escape on the popover returns the focus to Options; aria
  roles on the source cards, the panel toggle, the progress bar and the notice; the Formatting
  tile dims like the others when a category is soloed; a little room under the two-line clamp.
- Server: a refused cross-origin request no longer resets the idle clock; only the session's
  own bad-request and no-comparison errors map to 400 and 409, anything else is a 500; the page
  sends a beacon on pagehide so a last hidden ping does not delay the stop; the log rotates to
  `.1` instead of being truncated; `--verbose` is dropped when there is no stderr.
- Launcher: the stamp path is quoted; when the log folder cannot be made the log goes next to
  the launcher.
- Build: the zip leaves the testing package out; the stage smoke starts from a clean folder; a
  fenced heading no longer ends a changelog entry; `--notes` with `--notes-out` prints as well.

## v2.2.0 — Viewer refinements (2026-09-10)

- Two labelled drop cards, "Original" and "Modified", replace the file buttons: drop one file
  on a card, or two files anywhere, or click a card to choose; a Swap button exchanges the
  sides. After a comparison the cards stay above the pages as a compact strip; a new file
  waits for Compare, Swap compares again at once.
- The toolbar keeps only what every session needs: First / Prev / Next / Last with the go-to
  box and "Change x of N", the zoom stepper (−, +, 100 %, Fit; Ctrl+wheel and Ctrl+= / Ctrl+-
  / Ctrl+0), Save PDF and an Options popover holding the comparison, page, rendering and PDF
  settings and Quit. The page toggles now say "Show unchanged text", "Show insertions"…
- Keys `j` / `k` and the left / right arrows join `n` / `p`; the keys are shown on the buttons
  and `?` opens a shortcut sheet.
- A progress bar and a card over the pages during a comparison; errors and the closed state
  show as a card instead of a line in the status bar.
- Change list: each row shows its page and clamps to two lines (the selected row shows the
  full text); the count tiles are the category filters (click one to show only that category,
  click again for all); a table glyph marks changes inside tables when there are any.
- The change list folds to a narrow rail with the ‹ button and remembers it.
- Empty state explains what Calandria does, that nothing is written to Word, and that closing
  the window closes Calandria. Focus rings, one button height and readable page numbers.

## v2.1.0 — One window (2026-09-09)

- `Calandria.cmd` no longer leaves a console window open (it flashes and closes): it starts
  the program in the background and the viewer opens in its own window (Microsoft Edge in app
  mode: no tabs, no address bar, its own taskbar entry). Without Edge the default browser is
  used as before.
- Closing the window stops Calandria within a few seconds (the page pings the server every
  2 s; `--idle` now defaults to 8 s after the first visit, with a 120 s `--grace` before it).
  A laptop that sleeps with the window open resumes where it was.
- `serve --log=PATH` writes the start line and any error to a file; the launcher uses
  `%LOCALAPPDATA%\Calandria\calandria.log`.
- The extracted folder holds `Calandria.cmd`, `README.md`, `CHANGELOG.md` and one `_internal`
  folder (the Python runtime and the program). Command line from the folder:
  `_internal\python\python.exe -m calandria <command>`.

## v2.0.0 — First release (2026-09-09)

- Offline Word (`.docx`) redline / compare tool: a paged on-screen viewer and a PDF, both drawn
  from one layout engine, so the screen and the PDF never disagree. Documents never leave the
  machine.
- Runs from a single downloaded folder on any 64-bit Windows machine: extract the zip, double-click
  `Calandria.cmd`. No install, no admin rights, no internet connection.
- Compares body text, tables and list numbering; reports insertions, deletions, amendments,
  numbering changes and formatting changes; options to ignore case and to leave numbering
  changes uncounted.
- Layout engine with Word's pagination rules: font metrics from the installed fonts, widow and
  orphan control, keep-with-next and keep-lines, page-break-before, section breaks with their
  own page geometry, indents, list markers with tab stops, atomic table rows.
- Viewer: drop two files, compare, page through the redline, switch the rendering set
  ("Standard": blue double underline / red strikethrough; "Black and White"), toggle change bars,
  hide unchanged text or any category, zoom 50–200 % or fit width, and navigate the numbered
  change list (badges, count tiles, category and location filters, First / Previous / Next /
  Last, go-to, keys `n`, `p`, `Home`, `End`).
- PDF: embedded TrueType subsets, redline styling, change bars, gutter change numbers, a uniform
  table grid and a summary report block (first page, last page or none). Saved from the viewer or
  from the command line (`python -m calandria pdf`).
- Command line: `dump`, `compare`, `layout`, `pdf`, `serve`, `version`.
- Change bars, change numbers and the summary exist only in the viewer and the PDF; nothing is
  written back to Word. No `.docx` output.
- Successor to SorkWhare Compare 1.16.0 (frozen); extraction and change lists are held to it by
  the parity harness.
