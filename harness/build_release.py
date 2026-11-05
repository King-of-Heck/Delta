"""Assemble the release zip: the official embeddable Python, the runtime wheels from uv.lock
extracted next to it, the app and the launcher, in one folder.

    uv run python harness/build_release.py [--skip-tests] [--skip-smoke] [--keep-stage]
    uv run python harness/build_release.py --notes        # print the release notes and stop
    uv run python harness/build_release.py --notes-out PATH  # write the release notes and stop

Layout of the zip (one top folder; one thing to double-click):

    Delta-<version>/
      Delta.cmd  Delta.ico  README.md  CHANGELOG.md
      _internal/python/   python.exe, pythonw.exe, python314.zip, python314._pth
                          (python314.zip / . / Lib\\site-packages / ..\\app), the DLLs,
                          Lib/site-packages/<wheels extracted>
      _internal/app/delta/   the package (no bytecode)

Wheels come from the URLs in uv.lock and are checked against the lock's sha256. lxml and pillow
take the compiled cp314-cp314-win_amd64 wheel (never cp314t); everything else takes the pure
wheel, so the zip carries the fewest native files. The whole test suite runs first (the parity
gates included) and the staged interpreter is smoke-tested before the zip is written. Downloads
are cached in build/cache/.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP_FILES = ("Delta.cmd", "Delta.ico", "README.md", "CHANGELOG.md")   # beside _internal
PY_VERSION = "3.14.7"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
EMBED_SHA256 = "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15"
NATIVE = {"lxml", "pillow"}    # the only packages whose compiled wheel is vendored
PLATFORM_TAG = "cp314-cp314-win_amd64"
PURE_TAGS = ("py3-none-any", "py2.py3-none-any")
PTH_NAME = "python314._pth"
PTH_TEXT = "python314.zip\n.\nLib\\site-packages\n..\\app\n"
INTERNAL = "_internal"
SMOKE_SERVE = ["-m", "delta", "serve", "--no-browser", "--idle=1", "--grace=1"]
CACHE = ROOT / "build" / "cache"
STAGE = ROOT / "build" / "stage"
DIST = ROOT / "dist"
SMOKE_IMPORTS = ("from lxml import etree; from PIL import Image; from fontTools.ttLib import TTFont; "
                  "import fpdf, delta, pypdf")

DESCRIPTION = (
    "**Delta** is an offline Word (`.docx`) and PDF redline / compare tool from HeckSoft (a King of "
    "Heck Company), the successor to SorkWhare Compare. Extract the zip anywhere, double-click "
    "`Delta.cmd`: Delta opens in its own window (no console, no browser tabs). Drop the "
    "original and the modified document on the page: the redline is laid out page by page on "
    "screen and saved as a PDF from the same drawing. Closing the window stops it. No install, no "
    "admin rights, no internet connection; the documents never leave your computer."
)


def pth_lines() -> list[str]:
    return PTH_TEXT.splitlines()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def runtime_packages(lock: dict) -> list[dict]:
    """The lock entries of the project's runtime closure, sorted by name (dev groups excluded)."""
    by_name = {p["name"]: p for p in lock["package"]}
    project = next(p for p in lock["package"] if "editable" in p.get("source", {}))
    wanted, todo = set(), [d["name"] for d in project.get("dependencies", [])]
    while todo:
        name = todo.pop()
        if name in wanted:
            continue
        wanted.add(name)
        todo.extend(d["name"] for d in by_name[name].get("dependencies", []))
    return [by_name[n] for n in sorted(wanted)]


def pick_wheel(pkg: dict) -> tuple[str, str]:
    """(url, sha256) of the wheel to vendor: compiled for NATIVE packages, pure for the rest."""
    tags = (PLATFORM_TAG,) if pkg["name"] in NATIVE else PURE_TAGS
    for tag in tags:
        for w in pkg.get("wheels", []):
            if w["url"].endswith(f"-{tag}.whl"):
                if not w["hash"].startswith("sha256:"):
                    raise RuntimeError(f"{pkg['name']} {pkg['version']}: hash {w['hash']!r} is not sha256")
                return w["url"], w["hash"].removeprefix("sha256:")
    raise LookupError(f"{pkg['name']} {pkg['version']}: no wheel tagged {' / '.join(tags)} in uv.lock")


def wheel_members(names: list[str]) -> list[str]:
    """The wheel members to extract: no `<dist>.data/` trees (scripts, headers) and no RECORD.

    A `.data/purelib/` or `.data/platlib/` member would land its files on `sys.path` if it were
    installed properly (unlike `scripts` or `headers`, which are silently dropped) -- vendoring a
    wheel that has one and dropping it would silently omit code, so that is refused instead.
    """
    out = []
    for n in names:
        top = n.split("/", 1)[0]
        if top.endswith(".data"):
            rest = n[len(top) + 1:]
            if rest.startswith("purelib/") or rest.startswith("platlib/"):
                raise RuntimeError(f"{n}: a {top}/ purelib or platlib member would be dropped")
            continue
        if top.endswith(".dist-info") and n.endswith("/RECORD"):
            continue
        out.append(n)
    return out


def stage_app(src: Path, dst: Path) -> list[Path]:
    """Copy the package tree without bytecode or the test-only `testing` package; return the
    copied files, sorted."""
    copied = []
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if "__pycache__" in rel.parts or path.suffix == ".pyc" or path.is_dir() or rel.parts[0] == "testing":
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        copied.append(target)
    return copied


def changelog_entry(text: str, version: str) -> str:
    """The `## v<version>` section of the changelog (heading through the line before the next `## `
    outside a fenced code block)."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith(f"## v{version} ")
                  or l == f"## v{version}"), None)
    if start is None:
        raise LookupError(f"CHANGELOG.md has no '## v{version}' entry")
    end, fenced = len(lines), False
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("```"):
            fenced = not fenced
        elif lines[i].startswith("## ") and not fenced:
            end = i
            break
    return "\n".join(lines[start:end]).rstrip() + "\n"


def release_notes(changelog_text: str, version: str) -> str:
    entry = changelog_entry(changelog_text, version)
    return (f"{DESCRIPTION}\n\n---\n\n{entry}\n---\n\n"
            f"**Download:** `Delta-{version}.zip` (attached below) — extract it anywhere and "
            f"double-click `Delta.cmd` inside the extracted folder. 64-bit Windows; no install.\n")


def _fetch(url: str, dest: Path) -> None:
    print(f"  downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def download(url: str, cache: Path, sha256: str) -> Path:
    """The file at `url` in `cache`, fetched once and verified every time; a mismatch is deleted.

    Fetches into a `.part` file next to `dest` and only renames it into place once the sha256
    verifies (or there is no digest to check), so a crash or a failed verification never leaves a
    partial or corrupt file at `dest`.
    """
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / url.rsplit("/", 1)[1]
    if not dest.exists():
        part = dest.with_name(dest.name + ".part")
        if part.exists():
            part.unlink()
        _fetch(url, part)
        actual = sha256_of(part)
        if sha256 and actual != sha256:
            part.unlink()
            raise RuntimeError(f"{dest.name}: sha256 {actual} != expected {sha256}")
        os.replace(part, dest)
        return dest
    actual = sha256_of(dest)
    if sha256 and actual != sha256:
        dest.unlink()
        raise RuntimeError(f"{dest.name}: sha256 {actual} != expected {sha256}")
    return dest


def unpack_wheel(whl: Path, site: Path) -> None:
    with zipfile.ZipFile(whl) as z:
        for name in wheel_members(z.namelist()):
            z.extract(name, site)


def unpack_embed(embed_zip: Path, python_dir: Path) -> None:
    with zipfile.ZipFile(embed_zip) as z:
        z.extractall(python_dir)
    pth = python_dir / PTH_NAME
    if not pth.exists():
        raise RuntimeError(f"{embed_zip.name} has no {PTH_NAME}: not the {PY_VERSION} embeddable zip?")
    pth.write_text(PTH_TEXT, encoding="ascii", newline="")


def layout_paths(stage_dir: Path) -> dict[str, Path]:
    python_dir = stage_dir / INTERNAL / "python"
    return {"python": python_dir, "site": python_dir / "Lib" / "site-packages",
            "app": stage_dir / INTERNAL / "app" / "delta"}


def stage(version: str) -> Path:
    """Build build/stage/Delta-<version>/ from scratch; return it."""
    stage_dir = STAGE / f"Delta-{version}"
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    paths = layout_paths(stage_dir)
    python_dir, site = paths["python"], paths["site"]
    print("embeddable Python")
    embed = download(EMBED_URL, CACHE, EMBED_SHA256)
    if not EMBED_SHA256:
        print(f"  NOTE: EMBED_SHA256 is not pinned; this download's sha256 is {sha256_of(embed)}")
    unpack_embed(embed, python_dir)
    print("wheels")
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    for pkg in runtime_packages(lock):
        url, digest = pick_wheel(pkg)
        unpack_wheel(download(url, CACHE, digest), site)
        print(f"  {pkg['name']} {pkg['version']}  {url.rsplit('/', 1)[1]}")
    bad = [p for p in site.rglob("*.pyd") if "cp314t" in p.name or not p.name.endswith("win_amd64.pyd")]
    bad += list(site.rglob("*.dll"))
    if bad:
        raise RuntimeError(f"unexpected native extension(s): {', '.join(p.name for p in bad)}")
    print("app")
    stage_app(ROOT / "src" / "delta", paths["app"])
    for name in TOP_FILES:
        shutil.copyfile(ROOT / name, stage_dir / name)
    return stage_dir


def child_env(base: dict[str, str]) -> dict[str, str]:
    """`base` without any PYTHON* variable (case-insensitive), plus PYTHONDONTWRITEBYTECODE=1.

    Keeps a subprocess -- especially the staged interpreter, whose whole point is that its
    `._pth` alone defines `sys.path` -- from inheriting a PYTHONPATH / PYTHONHOME /
    PYTHONSAFEPATH set in this process's own environment.
    """
    env = {k: v for k, v in base.items() if not k.upper().startswith("PYTHON")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(cmd: list[str], cwd: Path, timeout: float = 300) -> str:
    print("  " + " ".join(cmd[1:] if cmd[0].endswith("python.exe") else cmd))
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace",
                        timeout=timeout, env=child_env(os.environ))
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({p.returncode}):\n{p.stdout}\n{p.stderr}")
    return p.stdout


def _src_on_path() -> None:
    """Make the repo's `delta` importable in THIS process (for the version and the smoke pair)."""
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


WORKER_SMOKE = """\
import sys
from delta.worker import Worker
from delta.pdfread import read_pdf
data = open(sys.argv[1], 'rb').read()
w = Worker()
try:
    got = w.read(w.submit(data))
    msg = 'worker ok' if w.broken is None and got == read_pdf(data) else f'worker broken: {w.broken}'
except Exception as e:
    msg = f'worker failed: {type(e).__name__}: {e}'
finally:
    w.close()
open(sys.argv[2], 'w', encoding='utf-8').write(msg)
"""


def smoke(stage_dir: Path, version: str) -> None:
    """Run the staged interpreter the way the launcher will: version, imports, a PDF, a serve."""
    python_dir = layout_paths(stage_dir)["python"]
    py = str(python_dir / "python.exe")
    if not (python_dir / "pythonw.exe").is_file():
        raise RuntimeError("the embeddable Python has no pythonw.exe; the launcher needs it")
    print("smoke")
    out = _run([py, "-m", "delta", "version"], stage_dir)
    if out.strip() != f"delta {version}":
        raise RuntimeError(f"version smoke printed {out!r}")
    _run([py, "-c", SMOKE_IMPORTS], stage_dir)
    _run([py, "-c", "import os, sys; real = [p for p in sys.path if p and p != os.getcwd()]; "
                    "assert len(real) == 4, sys.path"], stage_dir)   # -c may prepend '' / the cwd
    _src_on_path()
    from delta.testing.makedocx import make_docx, DOC, P
    work = stage_dir.parent / "smoke"
    if work.exists():
        shutil.rmtree(work)                  # only this run's files count, not an earlier build's
    work.mkdir()
    (work / "a.docx").write_bytes(make_docx({"word/document.xml": DOC(P("Alpha beta gamma") + P("Delta"))}))
    (work / "b.docx").write_bytes(make_docx({"word/document.xml": DOC(P("Alpha beta gamma") + P("Delta epsilon"))}))
    out = _run([py, "-m", "delta", "pdf", str(work / "a.docx"), str(work / "b.docx"), str(work / "out.pdf")],
               stage_dir)
    if '"pages": 1' not in out or not (work / "out.pdf").read_bytes().startswith(b"%PDF"):
        raise RuntimeError(f"pdf smoke: {out!r}")
    out = _run([py, "-m", "delta", "compare", str(work / "out.pdf"), str(work / "out.pdf")], stage_dir)
    if '"total": 0' not in out:
        raise RuntimeError(f"pdf compare smoke: {out!r}")
    # Two different PDFs: the second is read in a worker process started from this same Python,
    # under python.exe and under pythonw.exe (what the launcher runs). The check writes to a file
    # because pythonw has no console.
    for exe in ("python.exe", "pythonw.exe"):
        done = work / f"worker-{exe}.txt"
        _run([str(python_dir / exe), "-c", WORKER_SMOKE, str(work / "out.pdf"), str(done)], stage_dir)
        if not done.exists() or done.read_text(encoding="utf-8") != "worker ok":
            raise RuntimeError(f"worker smoke under {exe}: {done.read_text(encoding='utf-8') if done.exists() else 'nothing written'}")
    log = work / "delta.log"
    out = _run([py, *SMOKE_SERVE, f"--log={log}"], stage_dir, timeout=60)
    if '"url": "http://127.0.0.1:' not in out:
        raise RuntimeError(f"serve smoke: {out!r}")
    logged = log.read_text(encoding="utf-8") if log.exists() else ""
    if '"url": "http://127.0.0.1:' not in logged or not logged.startswith("--- "):
        raise RuntimeError(f"log smoke: {logged!r}")


def zip_stage(stage_dir: Path, out: Path) -> list[str]:
    """Zip stage_dir under its own name as the top folder; return the arcnames, sorted."""
    out.parent.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(out, "w") as z:
        for path in sorted(stage_dir.rglob("*")):
            if path.is_dir():
                continue
            arc = f"{stage_dir.name}/{path.relative_to(stage_dir).as_posix()}"
            info = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, path.read_bytes())
            names.append(arc)
    return names


def _version() -> str:
    _src_on_path()
    from delta import __version__
    return __version__


def build(skip_tests: bool = False, skip_smoke: bool = False, keep_stage: bool = False) -> Path:
    version = _version()
    changelog_entry((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)   # refuse an unlisted version
    if not skip_tests:
        print("tests")
        _run(["uv", "run", "pytest", "-q"], ROOT, timeout=1800)
    stage_dir = stage(version)
    if not skip_smoke:
        smoke(stage_dir, version)
    out = DIST / f"Delta-{version}.zip"
    if out.exists():
        out.unlink()
    names = zip_stage(stage_dir, out)
    print(f"{out}  {out.stat().st_size / 1e6:.1f} MB, {len(names)} files, sha256 {sha256_of(out)}")
    if not keep_stage:
        shutil.rmtree(stage_dir)
        smoke_dir = stage_dir.parent / "smoke"
        if smoke_dir.exists():
            shutil.rmtree(smoke_dir)
    return out


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--skip-tests", action="store_true", help="do not run the test suite first")
    ap.add_argument("--skip-smoke", action="store_true", help="do not run the staged interpreter")
    ap.add_argument("--keep-stage", action="store_true", help="leave build/stage/ in place")
    ap.add_argument("--notes", action="store_true", help="print the release notes and stop")
    ap.add_argument("--notes-out", metavar="PATH",
                     help="write the release notes to PATH (UTF-8) and stop")
    a = ap.parse_args(argv)
    if a.notes or a.notes_out:
        notes = release_notes((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), _version())
        if a.notes_out:
            Path(a.notes_out).write_bytes(notes.encode("utf-8"))
        if a.notes or not a.notes_out:
            sys.stdout.buffer.write(notes.encode("utf-8"))
        return 0
    build(a.skip_tests, a.skip_smoke, a.keep_stage)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
