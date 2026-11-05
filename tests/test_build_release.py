import importlib.util
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "build_release", Path(__file__).resolve().parent.parent / "harness" / "build_release.py")
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

LOCK = tomllib.loads('''
version = 1

[[package]]
name = "delta"
version = "2.0.0"
source = { editable = "." }
dependencies = [
    { name = "fpdf2" },
    { name = "lxml" },
]

[package.dev-dependencies]
dev = [
    { name = "pytest" },
]

[[package]]
name = "fpdf2"
version = "2.8.8"
source = { registry = "https://pypi.org/simple" }
dependencies = [
    { name = "defusedxml" },
]
wheels = [
    { url = "https://files/fpdf2-2.8.8-py3-none-any.whl", hash = "sha256:aa" },
]

[[package]]
name = "defusedxml"
version = "0.7.1"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://files/defusedxml-0.7.1-py2.py3-none-any.whl", hash = "sha256:bb" },
]

[[package]]
name = "lxml"
version = "6.1.3"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://files/lxml-6.1.3-cp314-cp314t-win_amd64.whl", hash = "sha256:cc" },
    { url = "https://files/lxml-6.1.3-cp314-cp314-win_amd64.whl", hash = "sha256:dd" },
    { url = "https://files/lxml-6.1.3-cp314-cp314-manylinux_2_28_x86_64.whl", hash = "sha256:ee" },
]

[[package]]
name = "pytest"
version = "9.1.1"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://files/pytest-9.1.1-py3-none-any.whl", hash = "sha256:ff" },
]

[[package]]
name = "fonttools"
version = "4.64.0"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://files/fonttools-4.64.0-cp314-cp314-win_amd64.whl", hash = "sha256:11" },
    { url = "https://files/fonttools-4.64.0-py3-none-any.whl", hash = "sha256:22" },
]

[[package]]
name = "sdist-only"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
''')


def _pkg(name):
    return next(p for p in LOCK["package"] if p["name"] == name)


def test_runtime_packages_is_the_closure_without_dev_groups():
    names = [p["name"] for p in br.runtime_packages(LOCK)]
    assert names == ["defusedxml", "fpdf2", "lxml"]


def test_runtime_packages_on_the_real_lock():
    lock = tomllib.loads((Path(__file__).resolve().parent.parent / "uv.lock").read_text(encoding="utf-8"))
    names = [p["name"] for p in br.runtime_packages(lock)]
    assert names == ["defusedxml", "fonttools", "fpdf2", "lxml", "pillow", "pypdf"]
    for p in br.runtime_packages(lock):
        url, digest = br.pick_wheel(p)
        assert url.endswith(".whl") and len(digest) == 64


def test_pick_wheel_native_takes_the_cp314_non_t_windows_wheel():
    url, digest = br.pick_wheel(_pkg("lxml"))
    assert url.endswith("lxml-6.1.3-cp314-cp314-win_amd64.whl")
    assert digest == "dd"


def test_pick_wheel_pure_takes_the_universal_wheel_even_when_a_native_one_exists():
    url, digest = br.pick_wheel(_pkg("fonttools"))
    assert url.endswith("fonttools-4.64.0-py3-none-any.whl") and digest == "22"
    url, digest = br.pick_wheel(_pkg("defusedxml"))
    assert url.endswith("py2.py3-none-any.whl") and digest == "bb"


def test_pick_wheel_without_a_wheel_raises():
    with pytest.raises(LookupError):
        br.pick_wheel(_pkg("sdist-only"))


def test_pick_wheel_non_sha256_hash_raises():
    pkg = {
        "name": "md5-pkg",
        "version": "1.0",
        "wheels": [{"url": "https://files/md5_pkg-1.0-py3-none-any.whl", "hash": "md5:deadbeef"}],
    }
    with pytest.raises(RuntimeError):
        br.pick_wheel(pkg)


def test_pth_lines():
    assert br.pth_lines() == ["python314.zip", ".", "Lib\\site-packages", "..\\app"]
    assert br.PTH_TEXT == "python314.zip\n.\nLib\\site-packages\n..\\app\n"
    assert "import site" not in br.PTH_TEXT


def test_wheel_members_drops_data_dirs_and_record():
    names = ["lxml/__init__.py", "lxml/etree.cp314-win_amd64.pyd",
             "lxml-6.1.3.dist-info/METADATA", "lxml-6.1.3.dist-info/RECORD",
             "fonttools-4.64.0.data/scripts/fonttools", "x-1.0.data/headers/x.h"]
    assert br.wheel_members(names) == ["lxml/__init__.py", "lxml/etree.cp314-win_amd64.pyd",
                                       "lxml-6.1.3.dist-info/METADATA"]


def test_wheel_members_refuses_a_purelib_or_platlib_data_tree():
    with pytest.raises(RuntimeError):
        br.wheel_members(["x-1.0.data/purelib/x.py"])
    with pytest.raises(RuntimeError):
        br.wheel_members(["x-1.0.data/platlib/x.py"])


def test_stage_app_copies_without_bytecode(tmp_path):
    src = tmp_path / "src" / "delta"
    (src / "viewer").mkdir(parents=True)
    (src / "__pycache__").mkdir()
    (src / "__init__.py").write_text("x = 1")
    (src / "viewer" / "index.html").write_text("<p>")
    (src / "__pycache__" / "__init__.cpython-314.pyc").write_bytes(b"\x00")
    (src / "stale.pyc").write_bytes(b"\x00")
    dst = tmp_path / "app" / "delta"
    copied = br.stage_app(src, dst)
    assert copied == [dst / "__init__.py", dst / "viewer" / "index.html"]
    assert not (dst / "__pycache__").exists() and not (dst / "stale.pyc").exists()
    assert (dst / "viewer" / "index.html").read_text() == "<p>"


CHANGELOG = """# Delta changelog

## v2.1.0 — Later (2026-10-01)

- later bullet

## v2.0.0 — First release (2026-09-09)

- first bullet
- second bullet
"""


def test_changelog_entry_returns_one_section():
    entry = br.changelog_entry(CHANGELOG, "2.0.0")
    assert entry.startswith("## v2.0.0 — First release (2026-09-09)")
    assert "second bullet" in entry and "later" not in entry
    entry = br.changelog_entry(CHANGELOG, "2.1.0")
    assert "later bullet" in entry and "first bullet" not in entry


def test_changelog_entry_missing_raises():
    with pytest.raises(LookupError):
        br.changelog_entry(CHANGELOG, "3.0.0")


def test_release_notes_has_description_entry_and_download_line():
    notes = br.release_notes(CHANGELOG, "2.0.0")
    assert notes.startswith("**Delta**")
    assert "## v2.0.0 — First release (2026-09-09)" in notes
    assert "`Delta-2.0.0.zip`" in notes
    assert "later" not in notes


def test_notes_out_writes_the_release_notes_as_utf8(tmp_path):
    import delta
    out = tmp_path / "n.md"
    assert br.main(["--notes-out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("**Delta**")
    assert f"## v{delta.__version__} —" in text


def test_layout_paths_put_python_and_app_under_internal(tmp_path):
    stage = tmp_path / "Delta-9.9.9"
    paths = br.layout_paths(stage)
    assert br.INTERNAL == "_internal"
    assert paths == {"python": stage / "_internal" / "python",
                     "site": stage / "_internal" / "python" / "Lib" / "site-packages",
                     "app": stage / "_internal" / "app" / "delta"}
    assert br.pth_lines()[-1] == "..\\app"           # python/ and app/ stay siblings


def test_smoke_serve_arguments_cover_the_console_less_launch():
    assert "--grace=1" in br.SMOKE_SERVE and "--idle=1" in br.SMOKE_SERVE and "--no-browser" in br.SMOKE_SERVE


def _fake_stage(tmp_path):
    stage = tmp_path / "Delta-9.9.9"
    python_dir = br.layout_paths(stage)["python"]
    python_dir.mkdir(parents=True)
    (python_dir / "python.exe").write_bytes(b"MZ")
    (python_dir / "pythonw.exe").write_bytes(b"MZ")
    return stage


def _fake_run(serve_writes_log, worker="worker ok"):
    """A stand-in for _run that answers the smoke's commands: the version, the imports, the sys.path
    check, the worker check (its file says `worker`, or is not written when that is None), the pdf
    (a %PDF file), the pdf compare (itself, total 0) and the serve (the url line, and the log only
    when asked)."""
    def run(cmd, cwd, timeout=300):
        if cmd[1:] == ["-m", "delta", "version"]:
            return "delta 9.9.9\n"
        if cmd[1] == "-c" and cmd[2] == br.WORKER_SMOKE:
            if worker is not None:
                Path(cmd[-1]).write_text(worker, encoding="utf-8")
            return ""
        if cmd[1] == "-c":
            return ""
        if cmd[1:4] == ["-m", "delta", "pdf"]:
            Path(cmd[6]).write_bytes(b"%PDF-1.7 fake")
            return '{"pages": 1, "out": "x"}\n'
        if cmd[1:4] == ["-m", "delta", "compare"]:
            return '{\n "total": 0\n}\n'
        assert cmd[1:1 + len(br.SMOKE_SERVE)] == br.SMOKE_SERVE
        log = Path(cmd[-1].removeprefix("--log="))
        if serve_writes_log:
            log.write_text('--- 2026-09-09 12:00:00 delta 9.9.9 serve\n{"url": "http://127.0.0.1:5/"}\n',
                           encoding="utf-8")
        return '{"url": "http://127.0.0.1:5/"}\n'
    return run


def test_the_serve_smoke_passes_on_a_fresh_log(tmp_path, monkeypatch):
    stage = _fake_stage(tmp_path)
    monkeypatch.setattr(br, "_run", _fake_run(serve_writes_log=True))
    br.smoke(stage, "9.9.9")
    assert (stage.parent / "smoke" / "delta.log").read_text(encoding="utf-8").startswith("--- ")


def test_the_serve_smoke_fails_when_the_serve_writes_no_log(tmp_path, monkeypatch):
    stage = _fake_stage(tmp_path)
    monkeypatch.setattr(br, "_run", _fake_run(serve_writes_log=False))
    with pytest.raises(RuntimeError, match="log smoke"):
        br.smoke(stage, "9.9.9")


def test_the_serve_smoke_ignores_a_log_left_by_an_earlier_build(tmp_path, monkeypatch):
    stage = _fake_stage(tmp_path)
    work = stage.parent / "smoke"
    work.mkdir()
    (work / "delta.log").write_text('--- stale\n{"url": "http://127.0.0.1:5/"}\n', encoding="utf-8")
    monkeypatch.setattr(br, "_run", _fake_run(serve_writes_log=False))
    with pytest.raises(RuntimeError, match="log smoke"):
        br.smoke(stage, "9.9.9")


@pytest.mark.parametrize("worker, says", [(None, "nothing written"), ("worker broken: OSError: blocked", "worker broken")])
def test_the_smoke_fails_when_the_worker_process_does_not_read(tmp_path, monkeypatch, worker, says):
    stage = _fake_stage(tmp_path)
    monkeypatch.setattr(br, "_run", _fake_run(serve_writes_log=True, worker=worker))
    with pytest.raises(RuntimeError, match=f"worker smoke under python.exe: {says}"):
        br.smoke(stage, "9.9.9")


def test_the_smoke_refuses_a_stage_without_pythonw(tmp_path, monkeypatch):
    stage = _fake_stage(tmp_path)
    (br.layout_paths(stage)["python"] / "pythonw.exe").unlink()
    monkeypatch.setattr(br, "_run", _fake_run(serve_writes_log=True))
    with pytest.raises(RuntimeError, match="pythonw"):
        br.smoke(stage, "9.9.9")


def test_sha256_of(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    assert br.sha256_of(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_download_uses_the_cache_and_verifies(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(url, dest):
        calls.append(url)
        dest.write_bytes(b"abc")

    monkeypatch.setattr(br, "_fetch", fake_fetch)
    good = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    p = br.download("https://x/abc.whl", tmp_path, good)
    assert p == tmp_path / "abc.whl" and p.read_bytes() == b"abc" and calls == ["https://x/abc.whl"]
    br.download("https://x/abc.whl", tmp_path, good)
    assert calls == ["https://x/abc.whl"]           # cached: no second fetch
    with pytest.raises(RuntimeError):
        br.download("https://x/abc.whl", tmp_path, "00" * 32)
    assert not p.exists()                            # a mismatch is deleted, never reused


def test_download_fetches_into_a_part_file_then_renames(tmp_path, monkeypatch):
    def fake_fetch(url, dest):
        assert dest.name == "abc.whl.part"
        dest.write_bytes(b"abc")

    monkeypatch.setattr(br, "_fetch", fake_fetch)
    good = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    p = br.download("https://x/abc.whl", tmp_path, good)
    assert p == tmp_path / "abc.whl" and p.read_bytes() == b"abc"
    assert not (tmp_path / "abc.whl.part").exists()


def test_download_bad_hash_leaves_no_dest_and_no_part(tmp_path, monkeypatch):
    def fake_fetch(url, dest):
        dest.write_bytes(b"wrong bytes")

    monkeypatch.setattr(br, "_fetch", fake_fetch)
    with pytest.raises(RuntimeError):
        br.download("https://x/abc.whl", tmp_path, "00" * 32)
    assert not (tmp_path / "abc.whl").exists()
    assert not (tmp_path / "abc.whl.part").exists()


def test_download_discards_a_stale_part_file(tmp_path, monkeypatch):
    part = tmp_path / "abc.whl.part"
    part.write_bytes(b"junk from an interrupted fetch")

    def fake_fetch(url, dest):
        assert dest == part
        dest.write_bytes(b"abc")

    monkeypatch.setattr(br, "_fetch", fake_fetch)
    good = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    p = br.download("https://x/abc.whl", tmp_path, good)
    assert p.read_bytes() == b"abc" and not part.exists()


def test_unpack_wheel_skips_data_and_record(tmp_path):
    whl = tmp_path / "x-1.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr("x/__init__.py", "y = 2")
        z.writestr("x-1.0.dist-info/METADATA", "Name: x")
        z.writestr("x-1.0.dist-info/RECORD", "x/__init__.py,,")
        z.writestr("x-1.0.data/scripts/x", "#!")
    site = tmp_path / "site"
    br.unpack_wheel(whl, site)
    assert (site / "x" / "__init__.py").read_text() == "y = 2"
    assert (site / "x-1.0.dist-info" / "METADATA").exists()
    assert not (site / "x-1.0.dist-info" / "RECORD").exists()
    assert not (site / "x-1.0.data").exists()


def test_unpack_embed_writes_lf_only_pth(tmp_path):
    embed_zip = tmp_path / "embed.zip"
    with zipfile.ZipFile(embed_zip, "w") as z:
        z.writestr("python314._pth", "python314.zip\n.\n#import site\n")
        z.writestr("python.exe", "x")
    python_dir = tmp_path / "python"
    br.unpack_embed(embed_zip, python_dir)
    assert (python_dir / "python314._pth").read_bytes() == br.PTH_TEXT.encode("ascii")
    assert (python_dir / "python.exe").exists()


def test_unpack_embed_without_pth_raises(tmp_path):
    embed_zip = tmp_path / "embed.zip"
    with zipfile.ZipFile(embed_zip, "w") as z:
        z.writestr("python.exe", "x")
    with pytest.raises(RuntimeError):
        br.unpack_embed(embed_zip, tmp_path / "python")


def test_zip_stage_puts_everything_under_the_top_folder(tmp_path):
    stage = tmp_path / "Delta-9.9.9"
    (stage / "python").mkdir(parents=True)
    (stage / "Delta.cmd").write_bytes(b"@echo off\r\n")
    (stage / "python" / "python314._pth").write_text(br.PTH_TEXT)
    out = tmp_path / "out.zip"
    names = br.zip_stage(stage, out)
    assert names == ["Delta-9.9.9/Delta.cmd", "Delta-9.9.9/python/python314._pth"]
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == names
        assert z.read("Delta-9.9.9/Delta.cmd") == b"@echo off\r\n"
        assert z.getinfo(names[0]).compress_type == zipfile.ZIP_DEFLATED


def test_zip_stage_is_byte_identical_across_builds(tmp_path):
    import os
    import time

    def make_stage(d):
        d.mkdir(parents=True)
        (d / "python").mkdir()
        (d / "Delta.cmd").write_bytes(b"@echo off\r\n")
        (d / "python" / "python314._pth").write_text(br.PTH_TEXT)

    stage1 = tmp_path / "one" / "Delta-9.9.9"
    make_stage(stage1)
    out1 = tmp_path / "out1.zip"
    br.zip_stage(stage1, out1)

    stage2 = tmp_path / "two" / "Delta-9.9.9"
    make_stage(stage2)
    later = time.time() + 5000
    for p in stage2.rglob("*"):
        if p.is_file():
            os.utime(p, (later, later))
    out2 = tmp_path / "out2.zip"
    br.zip_stage(stage2, out2)

    assert out1.read_bytes() == out2.read_bytes()


def test_child_env_drops_python_variables_and_disables_bytecode():
    env = br.child_env({"PATH": "x", "PYTHONPATH": "y", "pythonhome": "z", "SystemRoot": "C:\\Windows"})
    assert env == {"PATH": "x", "SystemRoot": "C:\\Windows", "PYTHONDONTWRITEBYTECODE": "1"}


def test_src_on_path_is_added_once():
    import delta

    src = str(br.ROOT / "src")
    # Some other module already on sys.path (delta's own editable install, a harness script
    # imported by another test module) may have put `src` there before this test runs -- that is
    # not what is under test. What matters is that _src_on_path() itself never grows the count.
    before = sys.path.count(src)
    br._version()
    br._version()
    assert sys.path.count(src) == max(before, 1)
    assert br._version() == delta.__version__


def test_stage_app_leaves_the_testing_package_out(tmp_path):
    src = tmp_path / "src" / "delta"
    (src / "testing").mkdir(parents=True)
    (src / "__init__.py").write_text("x = 1")
    (src / "testing" / "makedocx.py").write_text("y = 2")
    dst = tmp_path / "app" / "delta"
    assert br.stage_app(src, dst) == [dst / "__init__.py"]
    assert not (dst / "testing").exists()


def test_the_smoke_starts_from_a_clean_work_folder(tmp_path, monkeypatch):
    stage = _fake_stage(tmp_path)
    work = stage.parent / "smoke"
    work.mkdir()
    (work / "stale.txt").write_text("x")
    monkeypatch.setattr(br, "_run", _fake_run(serve_writes_log=True))
    br.smoke(stage, "9.9.9")
    assert not (work / "stale.txt").exists()


FENCED = """# Delta changelog

## v2.1.0 — Later (2026-10-01)

- later bullet

```
## not a heading, a fenced line
```

- after the fence

## v2.0.0 — First release (2026-09-09)

- first bullet
"""


def test_changelog_entry_does_not_end_at_a_fenced_heading_line():
    entry = br.changelog_entry(FENCED, "2.1.0")
    assert "not a heading" in entry and "after the fence" in entry and "first bullet" not in entry


def test_notes_and_notes_out_together_write_the_file_and_print(tmp_path, capfdbinary):
    out = tmp_path / "n.md"
    assert br.main(["--notes", "--notes-out", str(out)]) == 0
    printed = capfdbinary.readouterr().out
    assert printed.startswith(b"**Delta**") and out.read_bytes() == printed


def test_top_files_include_the_launcher_its_icon_and_the_docs():
    assert br.TOP_FILES == ("Delta.cmd", "Delta.ico", "README.md", "CHANGELOG.md")
