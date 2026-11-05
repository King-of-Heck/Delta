import json
import os
import socket

from delta.__main__ import main
from delta.server import previous
from delta.server.session import Session
from delta.testing.fakefonts import FakeResolver

from test_app import Served


def _record(data):
    with open(os.path.join(data, previous.FILE), encoding="utf-8") as f:
        return json.load(f)


def test_the_next_start_quits_the_previous_server(tmp_path):
    data = str(tmp_path / "data")
    old = Served(Session(fonts=FakeResolver()))
    try:
        previous.record(data, old.url)
        line = previous.take_over(data, "http://127.0.0.1:1/")
        old.thread.join(5)
        assert not old.thread.is_alive() and old.server.stopped
        assert line == f"previous server {old.url} quit"
        assert _record(data)["url"] == "http://127.0.0.1:1/"           # the new one is recorded
    finally:
        old.stop()


def test_a_server_never_quits_itself(tmp_path):
    data = str(tmp_path / "data")
    me = Served(Session(fonts=FakeResolver()))
    try:
        previous.record(data, me.url)
        assert previous.take_over(data, me.url) is None
        assert me.thread.is_alive()
    finally:
        me.stop()


def test_a_server_that_is_gone_is_one_line(tmp_path):
    with socket.socket() as s:                       # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    data = str(tmp_path / "data")
    previous.record(data, f"http://127.0.0.1:{port}/")
    line = previous.take_over(data, "http://127.0.0.1:1/", timeout=2)
    assert line.startswith(f"previous server http://127.0.0.1:{port}/ not quit (")


def test_a_garbage_record_is_ignored_and_replaced(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    for junk in ("not json", "[1, 2]", '{"url": "http://example.com:80/"}', '{"url": "file:///c:/x"}',
                 '{"url": "http://127.0.0.1:99999/"}', '{"url": 7}'):
        (data / previous.FILE).write_text(junk, encoding="utf-8")
        line = previous.take_over(str(data), "http://127.0.0.1:1/")
        assert line in (None, "previous server record ignored"), junk
        assert _record(str(data))["url"] == "http://127.0.0.1:1/"


def test_no_data_folder_skips_it_all():
    assert previous.take_over(None, "http://127.0.0.1:1/") is None
    previous.record(None, "http://127.0.0.1:1/")
    previous.forget(None, "http://127.0.0.1:1/")


def test_a_clean_stop_removes_the_record_only_while_it_is_ours(tmp_path):
    data = str(tmp_path / "data")
    previous.record(data, "http://127.0.0.1:1/")
    previous.forget(data, "http://127.0.0.1:2/")                        # a newer server's record
    assert _record(data)["url"] == "http://127.0.0.1:1/"
    with open(os.path.join(data, previous.FILE), "w", encoding="utf-8") as f:
        json.dump({"url": "http://127.0.0.1:1/", "pid": os.getpid() + 1}, f)
    previous.forget(data, "http://127.0.0.1:1/")                        # same port, another process
    assert os.path.exists(os.path.join(data, previous.FILE))
    previous.record(data, "http://127.0.0.1:1/")
    previous.forget(data, "http://127.0.0.1:1/")
    assert not os.path.exists(os.path.join(data, previous.FILE))


def test_serve_quits_the_previous_server_and_clears_its_record_on_stop(capsys, monkeypatch, _no_real_desktop):
    monkeypatch.setattr("delta.server.app.open_viewer", lambda url: None)
    data = str(_no_real_desktop / "data")
    old = Served(Session(fonts=FakeResolver()))
    try:
        previous.record(data, old.url)
        assert main(["serve", "--no-browser", "--port=0", "--idle=0.4", "--grace=0.4"]) == 0
        old.thread.join(5)
        assert not old.thread.is_alive()
        out = capsys.readouterr().out.splitlines()
        assert json.loads(out[0])["url"].startswith("http://127.0.0.1:")    # the url is still first
        assert f"previous server {old.url} quit" in out
        assert any(line.startswith("stopped: no request for") for line in out)
        assert not os.path.exists(os.path.join(data, previous.FILE))
    finally:
        old.stop()
