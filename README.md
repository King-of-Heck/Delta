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
