# Delta

Offline Word (`.docx`) and PDF redline / compare tool from HeckSoft (a King of Heck Company).

## Running from the release zip (this was written by AI so it's probably wrong)

Download `Delta-<version>.zip` from the GitHub release, extract it anywhere (Explorer's
"Extract All" is fine) and double-click `Delta.cmd` inside the extracted folder. Delta
opens in its own window (Microsoft Edge in app mode; the default browser if Edge is absent).
Close the window, or press Quit, to stop it. Nothing is installed: the folder holds its own
64-bit Python and libraries, and removing the folder removes everything. To update, extract the
new zip and delete the old folder. If nothing appears, look at
`%LOCALAPPDATA%\Delta\delta.log`. Its first line is written by `Delta.cmd` itself,
so a missing or empty log means the launcher never ran.

If Windows says Smart App Control blocked `Delta.cmd` (or nothing happens and the log is
empty), Windows has marked the zip as downloaded from the internet, and "Extract All" copies that
mark onto every file inside. Delete the extracted folder, right-click the zip, choose
Properties, tick **Unblock**, press OK, then extract it again. The same can be done from
PowerShell with `Unblock-File <path to the zip>` before extracting.

The first launch also puts a `Delta` shortcut, carrying the icon (a `.cmd` cannot), on the Desktop
and beside `Delta.cmd`. Double-click it to start Delta from then on. Every launch refreshes both, so
they follow the folder if you move it or extract a newer version; delete the Desktop one and it is
not put back.

Inside the folder: `Delta.cmd`, `Delta.ico`, this file, the changelog and `_internal\` (`python\`, the
official embeddable Python with the libraries extracted into `Lib\site-packages`, and
`app\delta\`, the program). `_internal\python\python.exe -m delta <command>` runs the
command line from the folder.

## Command line

```
python -m delta dump <file.docx>
python -m delta compare <a.docx> <b.docx> [--ignore-case] [--no-count-numbering]
python -m delta layout <a.docx> <b.docx> [--ignore-case] [--no-count-numbering]
                           [--hide-unchanged] [--hide-insertions] [--hide-deletions]
                           [--hide-formatting] [--pages]
python -m delta pdf <a.docx> <b.docx> <out.pdf> [--ignore-case] [--no-count-numbering]
                        [--hide-unchanged] [--hide-insertions] [--hide-deletions]
                        [--hide-formatting] [--no-change-bars] [--render-set=NAME]
                        [--report=first|last|none] [--changed-only]
python -m delta docx <a.docx> <b.docx> <out.docx> [--author=NAME] [--ignore-case]
python -m delta serve [--port=N] [--idle=SECONDS] [--grace=SECONDS] [--log=PATH]
                          [--no-browser] [--verbose]
```

## Running the parity gate

Extraction is held to the reference engine by `tests/parity`. The corpus and the reference
output it compares against are both generated and git-ignored, so a fresh clone is red until
they have been built:

```
uv run python harness/build_corpus.py
uv run python harness/gen_pairs.py
node harness/oracle_export.mjs
```

The first copies the regression pairs into `tests/corpus/` and writes its manifest; the second
writes five synthetic pairs (formatting, tables, punctuation, dense rewrites, the inline token
cap) into `tests/corpus/` alongside `manifest.gen.json`; the third runs the reference engine over
both manifests and writes `tests/oracle/`. Re-run the third whenever the exporter's field list
changes. Then `uv run pytest tests/parity -q` should be green with an empty
`tests/parity/allow.json`; deliberate differences are listed in `tests/parity/KNOWN_DIVERGENCES.md`.

## Layout, page counts and the Word smoke check

`layout` prints the page model (pages, positioned lines, glyph runs, table boxes) that the PDF
writer and the viewer draw; `--pages` prints only the page count. Metrics come from the fonts
installed under `C:\Windows\Fonts` (override with `DELTA_FONT_DIRS`).

Page counts on the corpus, and a digest of each redline's whole page model (so a line that merely
moves is caught too), are pinned by `tests/parity/golden-pages.json`. After a deliberate layout
change, regenerate it and review the diff:

```
uv run python harness/golden_pages.py
```

Word's own page count is a smoke check, not a gate: export a corpus document to PDF from Word,
drop it in `tests/fixtures/wordpdf/<name>.pdf` (or a `<name>.pages` file with the count) and run
`uv run python harness/word_pages.py`.

## PDF

`pdf` draws the same page model into a PDF: the fonts the layout resolved are embedded as
TrueType subsets, insertions and deletions are styled by a rendering set (`--render-set=Standard`,
the default: blue double underline / red strikethrough; `--render-set="Black and White"`: black
text, same effects), changed lines get a change bar in the left margin (`--no-change-bars` to
omit) and a change number in the gutter, tables draw a uniform 0.5 pt grid, and a summary block
(names, date, rendering set, options, counts) goes on the last page (`--report=first` for its own
page before the document, `--report=none` to omit). `--changed-only` writes only the first page
and the pages with a change; the summary block says how many of the document's pages are shown.
Change bars, change numbers and the summary exist only in the PDF and the viewer; the two
source files are never changed.

## Word with tracked changes

`docx` (and Save Word in the app, beside Save PDF) writes the Modified document again with the
comparison as Word tracked changes: inserted text marked as inserted, the Original's deleted text
put back and marked as deleted, a reformatted run as a formatting change, a paragraph whose list
or level changed as a paragraph formatting change, a table row only one side has as an inserted or
deleted row, and footnotes, endnotes, headers and footers the same way. Everything else is the
Modified's own: styles, numbering, images, page setup, comments. In Word, Accept All gives the
Modified and Reject All the Original. Each change carries the author given (`--author=NAME`, in
the app Options > Word; "Delta" when none) and the time of the export. Tracked changes already in
either file are read as accepted first, as for the redline.

Only two Word documents can be exported this way; a PDF has no Word document to carry the marks.
Limits: a header or footer the Original shows and the Modified's section no longer names is not
put back on Reject (Word cannot track that); empty Original paragraphs and images inside deleted
text are not put back; comments only the Original has are not brought back; a paragraph holding a
page-number field is marked whole (all of the old paragraph deleted, all of the new inserted).

The corpus gate `tests/parity/test_pdf.py` checks that every pair's PDF has exactly the layout's
page count and that its text reads back.

## Comparing PDFs

A pair of text PDFs — the ones a "Save As PDF" from Word produces — can be dropped and compared
the same way a pair of `.docx` files is. A `.docx` can also be compared with a PDF, in either
order, on wording and numbering only: formatting, headers, footers and comments are not compared,
and the app says so. Reading a PDF re-extracts its text, tables and
footnotes from the printed pages rather than from any document structure, so the result is
approximate and comes with limits worth knowing before relying on it:

- Comparing a `.docx` with a PDF can report changes that are not real, mostly on form and signature
  pages: a Word table without visible borders holds its text cell by cell, while a PDF holds lines
  across the page, so the same words read in a different order. On the one long contract this has
  been measured against, the Word file against an unedited PDF of itself showed 96 such changes, all
  in schedules and forms and none in the main clauses, so check those pages by eye. Every real edit
  in that contract still appeared. Only PDFs saved from Word have been tried.
- Running headers, footers and page numbers are detected and left out of the comparison, so an
  edit made only inside a header or footer is not reported. A one-page document has nothing for
  its header to repeat against, so its header text is compared as body text instead, and on a
  two-page document a line of real text sitting at the very top or bottom of both pages can
  occasionally be mistaken for a running header and left out too.
- Clause and list numbering is plain text in a PDF, not a generated field, so inserting a clause
  renumbers what follows and every renumbered marker shows as its own change.
- A table without borders is read as a table when its columns line up on every row (one line
  per row, or wrapped only after the first column); one with an empty cell, a right-aligned
  column or a wrapped first column is read as ordinary lines, so an added row may show as one
  changed line rather than an inserted row. A table row split
  across a page break is read as one row when some of its cells ended before the break, or when a
  cell stops mid-sentence at its edge (a paragraph split there is read whole). When every cell
  runs on but none shows it (each ends a sentence at the break, or the text under it starts with a
  capital in a column sized to its text), it is read as two rows, so it can show as changed if
  the two PDFs paginate differently.
- Footnotes are recognised only when they print smaller than the body text; footnotes at body
  size, and endnotes always, are compared as ordinary paragraphs.
- Comments, tracked changes and anything drawn as a picture are not part of a PDF's text and are
  not compared, and images are not shown.
- A page with no readable text (a scanned signature page, say) is named in the panel and the
  report and left out rather than compared; a PDF with no readable text anywhere is refused. A
  password-protected PDF is also refused, with a message — open it and print it to a new,
  unprotected PDF first.
- Paragraph boundaries are inferred from spacing and where lines end, so an unusual layout can
  split one paragraph into two or join two into one, which shows up as changed text.
- Wrapped text set at one-and-a-half or double line spacing is the least-tested case in this
  release and may report more differences than it should.
- The reader has been measured only against PDFs saved from Word (Save As PDF and Export to PDF);
  a PDF from a scanner, a phone, "Print to PDF", or another program's export is outside what has
  been tested.
- The redline itself is re-laid-out text: fonts, spacing and page breaks are approximate, and the
  pages do not look like the source PDFs.

Reading a long PDF shows its progress page by page. The two PDFs of a pair are read at the same time,
the second by a helper process that Delta starts from its own Python and stops when it closes (when
no such process can be started, they are read one after the other). Documents never leave the
machine, the same as for `.docx`.

## Viewer

`python -m delta serve` (or a double-click on `Delta.cmd`) starts a local server on
127.0.0.1 and opens the viewer in an Edge app window (or the default browser). Drop the original
`.docx` on the "Original" card and the modified one on "Modified" (or click a card to choose, or
drop both files anywhere), then press Compare; Swap exchanges the sides. Two files dropped together
are put in order by when each was last saved: the date Word or the PDF maker wrote inside the file,
or the file's own date when there is none, the newer one as Modified. The pages shown are the
same drawing the PDF gets — the server draws every page as SVG from the page model, so the screen
and the PDF never disagree. The toolbar has the change navigation (First / Prev / Next / Last, a
go-to box, "Change X of Y"; keys `n` / `p`, `j` / `k`, the arrows, `Home`, `End`, and `?` for
the sheet), the zoom stepper (20–400 % or fit width; Ctrl+wheel), "Save PDF", and an Options
popover with the comparison options and the page toggles (a re-compare and re-layout), the
rendering set and change bars (a redraw), the PDF summary placement and Quit. The left panel has
the count tiles (click one to filter), the location select, and the numbered change list (page
number per row; click a row to jump to it); it folds to a rail with ‹. Documents never leave
the machine.

A density strip sits beside the pages: one mark per change over the whole document, coloured by
category, with the current change's mark bigger; clicking a mark jumps to that change. "Changed
pages only" under Options → On the page hides the pages without a change (page numbers stay
real; the counter says how many pages are shown), with a matching PDF option. A ⋯ button on a
change row, or a right-click on it, copies that change's modified or original text; Copy Final
in the change list header copies the whole modified document as plain text. The gutter change
numbers stay legible below 100 % zoom on screen.

The server keeps one comparison in memory, stops on Quit, and stops by itself a few seconds
after the window closes (`--idle=SECONDS` after the last request, default 8, 0 = never;
`--grace=SECONDS` before the first request, default 120). While the window is minimized or
covered it keeps running however long that lasts, and starting Delta again quits any older
Delta still running. `--port=N` fixes the port,
`--no-browser` only prints the URL, `--verbose` logs requests, `--log=PATH` writes the start
line, why the server stopped and any error to a file. Change bars, change numbers
and the summary exist only in the viewer and the PDF; nothing is written to Word.

The corpus gate `tests/parity/test_viewer.py` checks that every pair's viewer payload has one
well-formed page drawing per layout page and that every numbered change has a place to jump to.

## Building a release

```
uv run python harness/build_release.py
```

runs the whole test suite (the parity gates included), downloads the embeddable Python and the
runtime wheels named in `uv.lock` into `build/cache/` (each checked against its recorded sha256),
extracts them into `build/stage/Delta-<version>/`, smoke-tests the staged interpreter
(`version`, the imports, a `pdf`, a `serve`) and writes `dist/Delta-<version>.zip`. The
version is `delta.__version__` and must have an entry in `CHANGELOG.md`.
`--skip-tests`, `--skip-smoke` and `--keep-stage` are for iterating on the script; `--notes`
prints the release notes for the GitHub release.
