import base64
import io
import http.client
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit

import pytest

from delta import __version__
from delta.layout.fonts import default_dirs
from delta.server import app as appmod
from delta.server.app import DEFAULT_GRACE, DEFAULT_IDLE, MAX_BODY, STATIC, make_server, run, url_of
from delta.server.session import Session
from delta.testing.fakefonts import FakeResolver
from delta.testing.makedocx import DOC, P, STYLES, make_docx
from delta.testing.makepdf import make_pdf

STY = STYLES('<w:rFonts w:ascii="Fake"/><w:sz w:val="20"/>')
WHEN = datetime(2026, 9, 9, 14, 5)


def _docx(body):
    return make_docx({"word/document.xml": DOC(body), "word/styles.xml": STY})


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _req(url, method="GET", body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _json(url, method="GET", body=None, raw=None):
    status, headers, data = _req(url, method, body, raw)
    return status, json.loads(data)


class Served:
    def __init__(self, session, idle=0.0, grace=0.0):
        self.session = session
        self.server = make_server(session)
        self.url = url_of(self.server)
        self.thread = threading.Thread(target=run, args=(self.server, idle, grace), daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread.is_alive():
            self.server.shutdown()
        self.thread.join(5)


@pytest.fixture
def srv():
    s = Served(Session(fonts=FakeResolver(), clock=lambda: WHEN))
    yield s
    s.stop()


def _compare_body(a=P("aaaa"), b=P("aaaa bbbb"), options=None):
    body = {"a": {"name": "a.docx", "data": _b64(_docx(a))}, "b": {"name": "b.docx", "data": _b64(_docx(b))}}
    if options is not None:
        body["options"] = options
    return body


def test_defaults():
    assert DEFAULT_IDLE == 8.0 and DEFAULT_GRACE == 120.0 and MAX_BODY == 64 * 1024 * 1024
    assert set(STATIC) == {"index.html", "style.css", "app.js", "changes.js", "sources.js", "strip.js", "copy.js", "panes.js", "sync.js", "sides.js", "rowtext.js", "textredline.js", "textcompare.js"}


def test_url_is_loopback_with_the_bound_port(srv):
    assert srv.url.startswith("http://127.0.0.1:") and srv.url.endswith("/")


def test_state_and_unknown_routes(srv):
    assert _json(srv.url + "api/state") == (200, {"version": __version__, "loaded": False, "names": None,
                                                  "tracked": None, "source": None})
    assert _json(srv.url + "nope")[0] == 404
    assert _json(srv.url + "api/nope")[0] == 404
    assert _json(srv.url + "static/../pyproject.toml")[0] == 404
    assert _json(srv.url + "static/secret.txt")[0] == 404


def test_methods_are_checked(srv):
    assert _json(srv.url + "api/pages", "POST", {})[0] == 405
    assert _json(srv.url + "api/compare")[0] == 405
    assert _json(srv.url + "api/ping")[0] == 405
    assert _json(srv.url + "api/inspect")[0] == 405


def test_before_a_comparison_is_loaded(srv):
    status, d = _json(srv.url + "api/pages")
    assert status == 409 and d == {"error": "no comparison loaded"}
    assert _json(srv.url + "api/layout", "POST", {"options": {}})[0] == 409
    assert _req(srv.url + "api/pdf")[0] == 409


def test_compare_then_pages_layout_and_state(srv):
    status, d = _json(srv.url + "api/compare", "POST", _compare_body())
    assert status == 200
    assert d["names"] == {"original": "a.docx", "modified": "b.docx"} and d["page_count"] == 1
    assert d["summary"]["total"] == 1 and "bbbb" in d["pages"][0] and d["anchors"] == {"1": {"page": 1, "top": 72.0}}
    assert d["options"]["ignore_case"] is False and d["render_set"] == "Standard"
    assert _json(srv.url + "api/state")[1]["loaded"] is True

    status, d = _json(srv.url + "api/pages?render_set=Black%20and%20White&change_bars=0")
    assert status == 200 and d["render_set"] == "Black and White" and d["change_bars"] is False
    assert "#0000ff" not in d["pages"][0] and d["report_lines"][3] == "Rendering set: Black and White"
    assert _json(srv.url + "api/pages")[1]["change_bars"] is True

    status, d = _json(srv.url + "api/layout", "POST", {"options": {"show_insertions": False}})
    assert status == 200 and d["options"]["show_insertions"] is False and "bbbb" not in d["pages"][0]
    status, d = _json(srv.url + "api/layout", "POST", {"options": {}})
    assert status == 200 and d["options"]["show_insertions"] is False     # merged over the current options


def test_compare_options_are_applied(srv):
    body = _compare_body(P("Alpha") + P("Beta gamma"), P("Alpha") + P("beta gamma"), {"ignore_case": True})
    status, d = _json(srv.url + "api/compare", "POST", body)
    assert status == 200 and d["summary"]["total"] == 0 and d["options"]["ignore_case"] is True


def test_bad_requests(srv):
    assert _json(srv.url + "api/compare", "POST", raw=b"{not json")[0] == 400
    assert _json(srv.url + "api/compare", "POST", raw=b"[]") == (400, {"error": "expected a JSON object"})
    assert _json(srv.url + "api/compare", "POST", {"a": {"name": "a", "data": "AA=="}}) == \
        (400, {"error": "both files are required (a and b)"})
    assert _json(srv.url + "api/compare", "POST", {"a": {"name": "", "data": "AA=="}, "b": {"name": "b", "data": "AA=="}}) == \
        (400, {"error": "a file name is required"})
    assert _json(srv.url + "api/compare", "POST", {"a": {"name": "a", "data": "@@"}, "b": {"name": "b", "data": "AA=="}}) == \
        (400, {"error": "a: the file data is not base64"})
    body = _compare_body()
    body["b"]["data"] = _b64(b"not a zip")
    status, d = _json(srv.url + "api/compare", "POST", body)
    assert status == 400 and d["error"].startswith("b.docx: not a Word document")
    status, d = _json(srv.url + "api/compare", "POST", _compare_body(options={"zoom": 2}))
    assert status == 400 and d["error"] == "unknown options: zoom"
    assert _json(srv.url + "api/compare", "POST", _compare_body(options=[]))[0] == 400
    _json(srv.url + "api/compare", "POST", _compare_body())
    assert _json(srv.url + "api/layout", "POST", {"options": {"ignore_case": "yes"}}) == \
        (400, {"error": "option ignore_case must be true or false"})
    assert _json(srv.url + "api/layout", "POST", {})[0] == 400
    assert _json(srv.url + "api/pages?render_set=Sepia")[0] == 400
    assert _json(srv.url + "api/pages?change_bars=maybe") == (400, {"error": "change_bars must be 0 or 1"})
    assert _json(srv.url + "api/pdf?report=middle")[0] == 400
    assert _json(srv.url + "api/pdf?changed_only=maybe") == (400, {"error": "changed_only must be 0 or 1"})
    # the names are checked before the flags, so a request wrong in both is told about the name
    assert "rendering set" in _json(srv.url + "api/pdf?render_set=Sepia&changed_only=maybe")[1]["error"]


def _post_with_headers(url, extra):
    req = urllib.request.Request(url, data=b"", method="POST", headers=extra)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_a_cross_origin_post_is_refused(srv):
    port = srv.server.server_address[1]
    assert _post_with_headers(srv.url + "api/ping", {"Origin": "http://evil.example"}) == \
        (403, {"error": "cross-origin request refused"})
    assert _post_with_headers(srv.url + "api/ping", {"Origin": f"http://localhost:{port + 1}"})[0] == 403
    assert _post_with_headers(srv.url + "api/ping", {"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert _post_with_headers(srv.url + "api/ping", {"Origin": f"http://127.0.0.1:{port}"}) == (200, {"ok": True})
    assert _post_with_headers(srv.url + "api/ping", {"Origin": f"http://localhost:{port}"}) == (200, {"ok": True})
    assert _post_with_headers(srv.url + "api/ping", {"Sec-Fetch-Site": "same-origin"}) == (200, {"ok": True})
    assert _post_with_headers(srv.url + "api/ping", {"Sec-Fetch-Site": "none"}) == (200, {"ok": True})
    assert _post_with_headers(srv.url + "api/ping", {}) == (200, {"ok": True})
    assert _json(srv.url + "api/state")[0] == 200                  # a GET is never refused


def test_a_cross_origin_post_is_refused_before_its_body_is_read(srv):
    parts = urlsplit(srv.url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        payload = json.dumps(_compare_body()).encode()
        conn.request("POST", "/api/compare", body=payload,
                     headers={"Content-Type": "application/json", "Origin": "http://evil.example"})
        r = conn.getresponse()
        assert r.status == 403 and json.loads(r.read()) == {"error": "cross-origin request refused"}
        conn.request("GET", "/api/state")
        r = conn.getresponse()
        assert r.status == 200 and json.loads(r.read())["loaded"] is False    # nothing was compared
    finally:
        conn.close()


def test_body_cap(srv, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_BODY", 10)
    status, d = _json(srv.url + "api/compare", "POST", raw=b"x" * 20)
    assert status == 413 and "10" in d["error"]


def test_a_response_that_closes_the_socket_says_so(srv, monkeypatch):
    # The 413 path always sets close_connection before writing its response; an HTTP/1.1 client
    # (which defaults to keeping the socket, unlike urllib) must be told not to reuse it, or it
    # will send its next request into a socket we are about to drop.
    monkeypatch.setattr(appmod, "MAX_BODY", 10)
    parts = urlsplit(srv.url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request("POST", "/api/compare", body=b"x" * 20, headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        assert r.status == 413 and r.getheader("Connection") == "close"
        r.read()
    finally:
        conn.close()


def test_a_normal_response_has_no_connection_close(srv):
    parts = urlsplit(srv.url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request("GET", "/api/state")
        r = conn.getresponse()
        assert r.status == 200 and r.getheader("Connection") is None
        r.read()
    finally:
        conn.close()


def test_body_cap_drains_so_the_client_sees_the_413(srv, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_BODY", 10)
    host, port = srv.server.server_address[:2]
    body = b"x" * (200 * 1024)
    for _ in range(20):
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.request("POST", "/api/compare", body=body, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            status = resp.status
            data = json.loads(resp.read())
        finally:
            conn.close()
        assert status == 413 and "10" in data["error"]


def test_body_over_the_drain_cap_is_closed_without_reading(srv, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_BODY", 10)
    monkeypatch.setattr(appmod, "DRAIN_CAP", 100)
    host, port = srv.server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.putrequest("POST", "/api/compare")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "1000")
        conn.endheaders()
        conn.send(b"x" * 50)
        try:
            resp = conn.getresponse()
            assert resp.status == 413
        except (ConnectionError, http.client.HTTPException):
            pass
    finally:
        conn.close()


def test_file_names_are_reduced_to_their_base_name(srv):
    body = _compare_body()
    body["a"]["name"] = "C:\\docs\\one.docx"
    body["b"]["name"] = "/tmp/two.docx"
    status, d = _json(srv.url + "api/compare", "POST", body)
    assert status == 200 and d["names"] == {"original": "one.docx", "modified": "two.docx"}


def test_compare_and_layout_keep_the_requested_style(srv):
    body = _compare_body()
    body["render_set"] = "Black and White"
    body["change_bars"] = False
    status, d = _json(srv.url + "api/compare", "POST", body)
    assert status == 200
    assert d["render_set"] == "Black and White" and d["change_bars"] is False
    assert "#0000ff" not in d["pages"][0] and d["report_lines"][3] == "Rendering set: Black and White"

    status, d = _json(srv.url + "api/layout", "POST",
                       {"options": {"show_insertions": False}, "render_set": "Black and White", "change_bars": False})
    assert status == 200
    assert d["render_set"] == "Black and White" and d["change_bars"] is False
    assert "#0000ff" not in d["pages"][0] and d["report_lines"][3] == "Rendering set: Black and White"

    status, d = _json(srv.url + "api/layout", "POST", {"options": {}})
    assert status == 200 and d["render_set"] == "Standard" and d["change_bars"] is True


def test_style_keys_are_validated(srv):
    body = _compare_body()
    body["render_set"] = "Sepia"
    status, d = _json(srv.url + "api/compare", "POST", body)
    assert status == 400 and "Sepia" in d["error"]

    body = _compare_body()
    body["change_bars"] = "no"
    assert _json(srv.url + "api/compare", "POST", body) == (400, {"error": "change_bars must be true or false"})

    _json(srv.url + "api/compare", "POST", _compare_body())
    status, d = _json(srv.url + "api/layout", "POST", {"options": {}, "render_set": "Sepia"})
    assert status == 400


def test_ping_touches_and_quit_stops_the_server(srv):
    before = srv.session.last_seen
    time.sleep(0.01)
    assert _json(srv.url + "api/ping", "POST", {}) == (200, {"ok": True})
    assert srv.session.last_seen > before
    assert _json(srv.url + "api/quit", "POST", {}) == (200, {"ok": True})
    srv.thread.join(5)
    assert not srv.thread.is_alive() and srv.server.stopped


def test_ping_without_a_body_is_fine(srv):
    req = urllib.request.Request(srv.url + "api/ping", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200 and json.loads(r.read()) == {"ok": True}


def test_ping_reports_whether_the_page_is_hidden(srv):
    assert srv.server.hidden is False
    assert _json(srv.url + "api/ping?hidden=1", "POST", {}) == (200, {"ok": True})
    assert srv.server.hidden is True
    assert _json(srv.url + "api/ping?hidden=0", "POST", {}) == (200, {"ok": True})
    assert srv.server.hidden is False
    assert _json(srv.url + "api/ping?hidden=maybe", "POST", {}) == (400, {"error": "hidden must be 0 or 1"})


def _fake_server(**state):
    import types
    t = [0.0]
    session = types.SimpleNamespace(last_seen=0.0, touch=lambda: setattr(session, "last_seen", t[0]))
    shut, said = [], []
    server = types.SimpleNamespace(session=session, stopped=False, visited=True, hidden=False, closing=False,
                                   shutdown=lambda: shut.append(t[0]), log=said.append)
    for k, v in state.items():
        setattr(server, k, v)
    return t, server, shut, said


def _run_watchdog(t, server, advances, idle=4.0, grace=0.0):
    advances = iter(advances)

    def sleep(_):
        try:
            t[0] += next(advances)
        except StopIteration:
            server.stopped = True

    appmod._watchdog(server, idle=idle, grace=grace, clock=lambda: t[0], sleep=sleep)


def test_watchdog_never_stops_for_a_hidden_page():
    # Edge throttles a hidden window to one wake a minute or freezes it: hours of silence is not a close
    t, server, shut, _ = _fake_server(hidden=True)
    _run_watchdog(t, server, [1.0] * (6 * 3600))
    assert shut == [] and not hasattr(appmod, "HIDDEN_IDLE")


def test_watchdog_never_stops_for_a_hidden_page_after_a_sleep():
    # the sleep check gives the grace back (visited=False); a hidden page still has no limit
    t, server, shut, _ = _fake_server(hidden=True)
    _run_watchdog(t, server, [1.0, 600.0] + [1.0] * 3600, grace=120.0)
    assert shut == [] and server.visited is False


def test_watchdog_logs_why_it_stopped():
    t, server, shut, said = _fake_server()
    _run_watchdog(t, server, [1.0] * 100)
    assert shut == [5.0]
    assert said == ["stopped: no request for 5 s (limit 4 s; visited=1 hidden=0 closing=0)"]


def test_a_quit_is_logged():
    lines = []
    s = Served(Session(fonts=FakeResolver(), clock=lambda: WHEN))
    s.server.log = lines.append
    assert _json(s.url + "api/quit", "POST", {}) == (200, {"ok": True})
    s.thread.join(5)
    assert lines == ["stopped: Quit"]


def test_a_close_is_final_even_when_its_hidden_ping_arrives_late(srv):
    # a close sends hidden=1 (visibilitychange) and the closing beacon (pagehide), in either order
    assert _json(srv.url + "api/ping?hidden=0&closing=1", "POST", {}) == (200, {"ok": True})
    assert srv.server.closing is True and srv.server.hidden is False
    assert _json(srv.url + "api/ping?hidden=1", "POST", {}) == (200, {"ok": True})
    assert srv.server.hidden is False                 # so the watchdog applies the normal idle limit
    t, server, shut, _ = _fake_server(hidden=srv.server.hidden, closing=srv.server.closing)
    _run_watchdog(t, server, [1.0] * 100)
    assert shut == [5.0]


def test_a_hidden_ping_before_the_closing_beacon_is_taken_back(srv):
    assert _json(srv.url + "api/ping?hidden=1", "POST", {}) == (200, {"ok": True})
    assert srv.server.hidden is True
    assert _json(srv.url + "api/ping?hidden=0&closing=1", "POST", {}) == (200, {"ok": True})
    assert srv.server.hidden is False and srv.server.closing is True


def test_a_reload_after_the_closing_beacon_clears_it(srv):
    assert _json(srv.url + "api/ping?hidden=0&closing=1", "POST", {}) == (200, {"ok": True})
    assert _req(srv.url)[0] == 200                    # the reloaded page asks for itself
    assert srv.server.closing is False
    assert _json(srv.url + "api/ping?hidden=0&closing=1", "POST", {}) == (200, {"ok": True})
    assert _req(srv.url + "static/app.js")[0] == 200
    assert srv.server.closing is False
    assert _json(srv.url + "api/ping?hidden=0&closing=1", "POST", {}) == (200, {"ok": True})
    assert _json(srv.url + "api/ping?hidden=0", "POST", {}) == (200, {"ok": True})
    assert srv.server.closing is False
    assert _json(srv.url + "api/ping?hidden=1", "POST", {}) == (200, {"ok": True})
    assert srv.server.hidden is True                  # and the page may be hidden again


def test_a_page_back_from_hidden_gets_the_grace(srv):
    # A window hidden all night (screen off or locked) pings visible once as the screen wakes, then
    # Edge may hold it paused behind the lock screen for well over the idle limit: not a close.
    assert _json(srv.url + "api/ping?hidden=1", "POST", {}) == (200, {"ok": True})
    assert _json(srv.url + "api/ping?hidden=0", "POST", {}) == (200, {"ok": True})
    assert srv.server.hidden is False and srv.server.visited is False
    t, server, shut, _ = _fake_server(hidden=False, visited=srv.server.visited)
    _run_watchdog(t, server, [1.0] * 100, grace=120.0)
    assert shut == []                                 # 100 s of silence, within the grace
    t, server, shut, _ = _fake_server(hidden=False, visited=srv.server.visited)
    _run_watchdog(t, server, [1.0] * 200, grace=120.0)
    assert shut == [121.0]                            # a window that never resumes still stops
    assert _json(srv.url + "api/ping?hidden=0", "POST", {}) == (200, {"ok": True})
    assert srv.server.visited is True                 # the next ping is back on the normal limit


def test_a_visible_ping_while_visible_keeps_the_normal_limit(srv):
    assert _json(srv.url + "api/ping?hidden=0", "POST", {}) == (200, {"ok": True})
    assert _json(srv.url + "api/ping?hidden=0", "POST", {}) == (200, {"ok": True})
    assert srv.server.visited is True


def test_a_close_from_hidden_gets_no_grace(srv):
    assert _json(srv.url + "api/ping?hidden=1", "POST", {}) == (200, {"ok": True})
    assert _json(srv.url + "api/ping?hidden=0&closing=1", "POST", {}) == (200, {"ok": True})
    assert srv.server.visited is True and srv.server.closing is True


def test_ping_checks_the_closing_flag(srv):
    assert _json(srv.url + "api/ping?closing=maybe", "POST", {}) == (400, {"error": "closing must be 0 or 1"})
    assert srv.server.closing is False


def test_any_request_marks_the_server_visited(srv):
    assert srv.server.visited is False
    # a page on another site can reach a loopback server: its refused POST must not spend the grace
    assert _post_with_headers(srv.url + "api/ping", {"Origin": "http://evil.example"}) == \
        (403, {"error": "cross-origin request refused"})
    assert srv.server.visited is False
    assert _req(srv.url)[0] == 200
    assert srv.server.visited is True


def test_grace_covers_the_time_before_the_first_visit():
    s = Served(Session(fonts=FakeResolver()), idle=0.3, grace=5.0)
    try:
        time.sleep(0.8)
        assert s.thread.is_alive()                    # 0.8 s > idle, but nobody has visited yet
        assert _json(s.url + "api/state")[0] == 200
        s.thread.join(5)
        assert not s.thread.is_alive() and s.server.stopped     # after the visit, idle rules
    finally:
        s.stop()


def test_watchdog_treats_a_clock_jump_as_a_suspend_not_as_silence():
    import types
    t = [0.0]
    session = types.SimpleNamespace(last_seen=0.0)
    touched = []

    def touch():
        touched.append(t[0])
        session.last_seen = t[0]

    session.touch = touch
    shut = []
    server = types.SimpleNamespace(session=session, stopped=False, visited=True, hidden=False,
                                   shutdown=lambda: shut.append(t[0]))
    advances = iter([1.0, 60.0] + [1.0] * 300)

    def sleep(_):
        try:
            t[0] += next(advances)
        except StopIteration:                     # pragma: no cover - the watchdog stops first
            server.stopped = True

    appmod._watchdog(server, idle=4.0, grace=120.0, clock=lambda: t[0], sleep=sleep)
    assert touched == [61.0]           # the 60 s jump was a sleep: the page gets its ping back
    assert server.visited is False     # and the grace back, so a woken browser has time to resume
    assert shut[0] > 91.0              # 30 s of post-resume silence is not a closed window
    assert shut == [182.0]             # then genuine silence past the grace stops the server


def test_idle_watchdog_stops_an_unvisited_server():
    s = Served(Session(fonts=FakeResolver()), idle=0.3)
    s.thread.join(5)
    assert not s.thread.is_alive() and s.server.stopped


def test_requests_keep_the_watchdog_quiet():
    s = Served(Session(fonts=FakeResolver()), idle=3.0)
    try:
        for _ in range(4):
            time.sleep(0.5)
            assert _json(s.url + "api/state")[0] == 200
        assert s.thread.is_alive()
    finally:
        s.stop()


def test_concurrent_requests_are_serialized(srv):
    _json(srv.url + "api/compare", "POST", _compare_body())
    results = []

    def hit():
        results.append(_json(srv.url + "api/pages")[0])

    threads = [threading.Thread(target=hit) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert results == [200] * 6


def test_a_slow_response_write_does_not_hold_the_session(srv, monkeypatch):
    """A payload is built under the session lock but written after it, so a client that is slow to
    take its response cannot stall the others. A genuinely slow reader will not do as the probe:
    Windows' loopback buffers megabytes, so the server never blocks on the write however large the
    body and however small the client's receive buffer. The write is slowed directly instead, and
    both requests still travel over real sockets. Timing-based, with generous margins: the slow
    write is 2 s and /api/state (three keys, no layout work) must come back inside 1 s."""
    real_json = appmod.Handler._json

    def slow_json(self, obj, status=200):
        if self.path.startswith("/api/pages"):
            time.sleep(2.0)
        return real_json(self, obj, status)

    assert _json(srv.url + "api/compare", "POST", _compare_body())[0] == 200
    monkeypatch.setattr(appmod.Handler, "_json", slow_json)
    done = threading.Event()

    def slow():
        try:
            assert _json(srv.url + "api/pages")[0] == 200
        finally:
            done.set()

    t = threading.Thread(target=slow, daemon=True)
    t.start()
    time.sleep(0.5)                          # let that request reach the slow write
    t0 = time.perf_counter()
    assert _json(srv.url + "api/state")[0] == 200
    assert time.perf_counter() - t0 < 1.0
    assert done.wait(30)
    t.join(30)


def test_error_before_the_body_is_read_keeps_the_connection_usable(srv):
    parts = urlsplit(srv.url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        payload = json.dumps({"filler": "x" * 300}).encode()
        conn.request("POST", "/api/nope", body=payload, headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        assert r.status == 404
        r.read()

        conn.request("GET", "/api/state")
        r = conn.getresponse()
        assert r.status == 200
        assert json.loads(r.read())["loaded"] is False

        conn.request("POST", "/api/pages", body=b"{}", headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        assert r.status == 405
        r.read()

        conn.request("GET", "/api/state")
        r = conn.getresponse()
        assert r.status == 200
        assert json.loads(r.read())["loaded"] is False
    finally:
        conn.close()


def test_a_body_that_never_arrives_times_out_instead_of_holding_a_thread(srv, monkeypatch):
    """A half-written body must not pin a handler thread until the client goes away. The socket
    timeout is shortened to 0.5 s; the server either answers 400 or hangs up, and either way the
    next connection is served straight away."""
    monkeypatch.setattr(appmod.Handler, "timeout", 0.5)
    parts = urlsplit(srv.url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    try:
        conn.putrequest("POST", "/api/ping")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "100")
        conn.endheaders()
        conn.send(b"0123456789")
        t0 = time.perf_counter()
        try:
            r = conn.getresponse()
            assert r.status == 400 and json.loads(r.read()) == {"error": "request body incomplete"}
        except (ConnectionError, http.client.HTTPException):
            pass
        assert time.perf_counter() - t0 < 5
    finally:
        conn.close()
    t0 = time.perf_counter()
    assert _json(srv.url + "api/state")[0] == 200
    assert time.perf_counter() - t0 < 5


def test_negative_content_length_is_rejected(srv):
    parts = urlsplit(srv.url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.putrequest("POST", "/api/ping")
        conn.putheader("Content-Length", "-5")
        conn.endheaders()
        r = conn.getresponse()
        assert r.status == 400
        assert json.loads(r.read()) == {"error": "bad Content-Length"}
    finally:
        conn.close()


def test_state_is_read_under_the_lock(srv):
    done = threading.Event()
    result = {}

    def hit():
        result["status"], result["body"] = _json(srv.url + "api/state")
        done.set()

    with srv.session.lock:
        t = threading.Thread(target=hit, daemon=True)
        t.start()
        assert not done.wait(0.3)

    assert done.wait(5)
    t.join(5)
    assert result["status"] == 200


def test_unexpected_exception_is_a_json_500(srv, monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(srv.session, "state", boom)
    status, d = _json(srv.url + "api/state")
    assert status == 500 and d == {"error": "internal error: RuntimeError"}


@pytest.mark.skipif(not any(os.path.isdir(d) for d in default_dirs()), reason="no system font directory")
def test_pdf_download_with_real_fonts():
    s = Served(Session(clock=lambda: WHEN))
    try:
        body = {"a": {"name": "Draft v1.docx", "data": _b64(make_docx({"word/document.xml": DOC(P("aaaa"))}))},
                "b": {"name": "Draft v2.docx", "data": _b64(make_docx({"word/document.xml": DOC(P("aaaa bbbb"))}))}}
        assert _json(s.url + "api/compare", "POST", body)[0] == 200
        status, headers, data = _req(s.url + "api/pdf?render_set=Standard&change_bars=1&report=none")
        assert status == 200 and headers["Content-Type"] == "application/pdf" and data.startswith(b"%PDF")
        assert headers["Content-Disposition"] == appmod._disposition("Draft v1 vs Draft v2 redline.pdf")
        assert int(headers["Content-Length"]) == len(data)
        status, headers, data = _req(s.url + "api/pdf?report=none&changed_only=1")
        assert status == 200 and data.startswith(b"%PDF")
        assert headers["Content-Disposition"] == appmod._disposition("Draft v1 vs Draft v2 redline (changed pages).pdf")
    finally:
        s.stop()


def test_disposition_of_a_plain_name():
    header = appmod._disposition("Draft v1 vs Draft v2 redline.pdf")
    assert header == ('attachment; filename="Draft v1 vs Draft v2 redline.pdf"; '
                      "filename*=UTF-8''Draft%20v1%20vs%20Draft%20v2%20redline.pdf")


@pytest.mark.skipif(not any(os.path.isdir(d) for d in default_dirs()), reason="no system font directory")
def test_pdf_download_name_survives_a_non_latin1_quoted_and_newline_bearing_file_name():
    s = Served(Session(clock=lambda: WHEN))
    try:
        body = {"a": {"name": "Draft – v1.docx", "data": _b64(make_docx({"word/document.xml": DOC(P("aaaa"))}))},
                "b": {"name": "Dr\"aft\r\nv2.docx", "data": _b64(make_docx({"word/document.xml": DOC(P("aaaa bbbb"))}))}}
        assert _json(s.url + "api/compare", "POST", body)[0] == 200
        status, headers, data = _req(s.url + "api/pdf?report=none")
        assert status == 200 and headers["Content-Type"] == "application/pdf" and data.startswith(b"%PDF")
        cd = headers["Content-Disposition"]
        assert "filename*=UTF-8''Draft%20%E2%80%93%20v1%20vs%20Dr_aftv2%20redline.pdf" in cd
        plain = cd.split('filename="', 1)[1].split('"', 1)[0]
        assert plain.isascii() and all(0x20 <= ord(c) != 0x7f for c in plain)
    finally:
        s.stop()


def test_index_and_static_files_are_served_with_their_types(srv):
    status, headers, data = _req(srv.url)
    assert status == 200 and headers["Content-Type"] == "text/html; charset=utf-8" and b"<title>Delta</title>" in data
    assert f'<span id="version" class="version" title="Delta version">Delta {__version__}</span>'.encode() in data
    assert b"{{version}}" not in data
    status, headers, data = _req(srv.url + "static/app.js")
    assert status == 200 and headers["Content-Type"] == "text/javascript; charset=utf-8" and b"api" in data
    status, headers, data = _req(srv.url + "static/style.css")
    assert status == 200 and headers["Content-Type"] == "text/css; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"


def test_a_client_that_resets_the_connection_leaves_no_traceback(srv, capsys):
    import socket
    import struct
    host, port = srv.server.server_address[:2]
    for _ in range(3):
        s = socket.create_connection((host, port), timeout=5)
        s.sendall(b"GET /api/state HTTP/1.1\r\nHost: x\r\n\r\n")
        s.recv(1)                                        # the request is being served on a live socket
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))   # close = RST
        s.close()
    time.sleep(0.5)
    assert _json(srv.url + "api/state")[0] == 200
    assert capsys.readouterr().err == ""


def test_a_refused_cross_origin_post_does_not_reset_the_idle_clock(srv):
    before = srv.session.last_seen
    time.sleep(0.01)
    assert _post_with_headers(srv.url + "api/ping", {"Origin": "http://evil.example"})[0] == 403
    assert srv.session.last_seen == before          # only a request that could be our page counts


def test_an_internal_value_or_lookup_error_is_a_500_not_a_client_error(srv, monkeypatch):
    from delta.server.session import Session
    for exc in (ValueError("bug"), LookupError("bug")):
        monkeypatch.setattr(Session, "state", lambda self, exc=exc: (_ for _ in ()).throw(exc))
        status, d = _json(srv.url + "api/state")
        assert status == 500 and d["error"] == f"internal error: {type(exc).__name__}"


def test_the_client_errors_keep_their_statuses(srv):
    status, d = _json(srv.url + "api/layout", "POST", {"options": {"bogus": True}})
    assert status == 400 and "unknown options" in d["error"]
    status, d = _json(srv.url + "api/pages")
    assert status == 409 and d["error"] == "no comparison loaded"


# v2.4.0: the sides and the marks flag on the API (spec §12.2).

def test_compare_and_pages_carry_the_sides_and_honour_marks(srv):
    status, d = _json(srv.url + "api/compare", "POST", _compare_body(P("aaaa") + P("old"), P("aaaa") + P("new")))
    assert status == 200 and set(d["sides"]) == {"blackline", "original", "modified"} and d["side_marks"] is False
    assert d["sides"]["original"]["rows"] == {"0": {"page": 1, "top": 72.0, "height": 12.0},
                                              "1": {"page": 1, "top": 84.0, "height": 12.0}}
    status, d = _json(srv.url + "api/pages?marks=1")
    assert status == 200 and d["side_marks"] is True and "<rect" in d["sides"]["modified"]["pages"][0]
    assert _json(srv.url + "api/pages")[1]["side_marks"] is False
    assert _json(srv.url + "api/pages?marks=maybe") == (400, {"error": "marks must be 0 or 1"})
    status, d = _json(srv.url + "api/layout", "POST", {"options": {}, "marks": True})
    assert status == 200 and d["side_marks"] is True and "<rect" in d["sides"]["original"]["pages"][0]
    assert _json(srv.url + "api/layout", "POST", {"options": {}, "marks": "yes"}) == (400, {"error": "marks must be true or false"})
    # v2.4.3: a request names the sides it wants; the others are laid out and drawn only when asked for.
    status, d = _json(srv.url + "api/pages?sides=modified")
    assert status == 200 and set(d["sides"]) == {"modified"} and d["pages"] == []
    status, d = _json(srv.url + "api/pages?sides=blackline,original")
    assert status == 200 and set(d["sides"]) == {"blackline", "original"} and d["pages"] == d["sides"]["blackline"]["pages"]
    assert _json(srv.url + "api/pages?sides=left") == (400, {"error": "unknown sides: left; expected blackline, original, modified"})
    status, d = _json(srv.url + "api/layout", "POST", {"options": {}, "sides": ["blackline"]})
    assert status == 200 and set(d["sides"]) == {"blackline"} and set(srv.session.layouts) == {"blackline"}
    assert _json(srv.url + "api/layout", "POST", {"options": {}, "sides": "blackline"})[1]["sides"].keys() == {"blackline"}
    assert _json(srv.url + "api/layout", "POST", {"options": {}, "sides": [2]}) == (400, {"error": "sides must be a list of side names"})
    body = _compare_body(P("aaaa") + P("old"), P("aaaa") + P("new"))
    body["sides"] = ["blackline"]
    status, d = _json(srv.url + "api/compare", "POST", body)
    assert status == 200 and set(d["sides"]) == {"blackline"} and set(srv.session.layouts) == {"blackline"}
    body = _compare_body(P("aaaa") + P("old"), P("aaaa") + P("new"))
    body["marks"] = True
    assert _json(srv.url + "api/compare", "POST", body)[1]["side_marks"] is True



def test_every_compare_layout_and_pages_call_logs_one_timing_line():
    lines = []
    served = Served(Session(fonts=FakeResolver(), clock=lambda: WHEN, log=lines.append))
    try:
        assert _json(served.url + "api/compare", "POST", _compare_body())[0] == 200
        assert _json(served.url + "api/layout", "POST", {"options": {}, "sides": ["blackline"]})[0] == 200
        assert _json(served.url + "api/pages?sides=original")[0] == 200
        assert _json(served.url + "api/pages?sides=left")[0] == 400        # a refused call logs nothing
        assert _json(served.url + "api/state")[0] == 200
    finally:
        served.stop()
    assert [ln.split()[:2] for ln in lines] == [["timing", "compare"], ["timing", "layout"], ["timing", "pages"]]
    assert "parse=" in lines[0] and "compare=" in lines[0] and "render.blackline=" in lines[0]
    assert "layout.original=" in lines[2] and "compare=" not in lines[2]
    assert all(ln.isascii() for ln in lines)


INS_RUN = '<w:ins w:id="1" w:author="A"><w:r><w:t>new</w:t></w:r></w:ins>'


def test_inspect_counts_one_file_without_touching_the_session(srv):
    body = {"name": "draft.docx", "data": _b64(_docx("<w:p>" + INS_RUN + "</w:p>" + P("x")))}
    assert _json(srv.url + "api/inspect", "POST", body) == (
        200, {"name": "draft.docx", "kind": "docx", "tracked": 1, "pages": None, "saved": None})
    assert _json(srv.url + "api/inspect", "POST", {"name": "c.docx", "data": _b64(_docx(P("x")))})[1]["tracked"] == 0
    assert _json(srv.url + "api/state")[1]["loaded"] is False


def test_inspect_counts_a_pdfs_pages_without_reading_its_text(srv):
    data = make_pdf([[("text", 72, 100, 12, "one")], [("text", 72, 100, 12, "two")]])
    status, d = _json(srv.url + "api/inspect", "POST", {"name": "deal.pdf", "data": _b64(data)})
    assert status == 200 and {k: v for k, v in d.items() if k != "saved"} == {
        "name": "deal.pdf", "kind": "pdf", "tracked": None, "pages": 2}
    assert _json(srv.url + "api/state")[1]["loaded"] is False


CORE = ('<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dcterms="http://purl.org/dc/terms/"><dcterms:modified>{}</dcterms:modified></cp:coreProperties>')


def test_inspect_gives_when_a_file_was_last_saved(srv):
    def docx(modified):
        return make_docx({"word/document.xml": DOC(P("x")), "word/styles.xml": STY,
                          "docProps/core.xml": CORE.format(modified)})
    def saved(name, data):
        return _json(srv.url + "api/inspect", "POST", {"name": name, "data": _b64(data)})[1]["saved"]
    assert saved("new.docx", docx("2026-10-28T17:33:00Z")) == "2026-10-28T17:33:00Z"
    assert saved("tz.docx", docx("2026-10-28T13:33:00-04:00")) == "2026-10-28T17:33:00Z"
    assert saved("unset.docx", docx("1601-01-01T00:00:00Z")) is None
    assert saved("junk.docx", docx("yesterday")) is None
    import io
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf([[("text", 72, 100, 12, "x")]]))))
    w.add_metadata({"/ModDate": "D:20261028133300-04'00'", "/CreationDate": "D:20250101000000Z"})
    out = io.BytesIO()
    w.write(out)
    assert saved("deal.pdf", out.getvalue()) == "2026-10-28T17:33:00Z"


def test_inspect_gives_a_protected_pdfs_reason(srv):
    data = make_pdf([[("text", 72, 100, 12, "x")]], user_password="secret")
    status, d = _json(srv.url + "api/inspect", "POST", {"name": "locked.pdf", "data": _b64(data)})
    assert status == 400 and d["error"] == "locked.pdf: This PDF is password-protected. Open it and print it to a new PDF first."


def test_progress_is_empty_when_idle_and_answers_while_the_session_lock_is_held(srv):
    assert _json(srv.url + "api/progress") == (200, {})
    assert _json(srv.url + "api/progress", "POST", {})[0] == 405
    srv.session.progress = {"name": "a.pdf", "page": 3, "pages": 9}
    with srv.session.lock:                                   # a compare in flight holds this
        assert _json(srv.url + "api/progress") == (200, {"name": "a.pdf", "page": 3, "pages": 9})


def test_two_pdfs_compare_over_http(srv):
    body = {"a": {"name": "a.pdf", "data": _b64(make_pdf([[("text", 72, 100, 12, "Alpha beta")]]))},
            "b": {"name": "b.pdf", "data": _b64(make_pdf([[("text", 72, 100, 12, "Alpha gamma")]]))}}
    status, d = _json(srv.url + "api/compare", "POST", body)
    assert status == 200 and d["source"]["kind"] == "pdf" and d["summary"]["total"] >= 1


def test_inspect_rejects_a_missing_file_and_a_non_docx(srv):
    status, d = _json(srv.url + "api/inspect", "POST", {})
    assert status == 400 and "name" in d["error"]
    status, d = _json(srv.url + "api/inspect", "POST", {"name": "x.docx", "data": _b64(b"not a zip")})
    assert status == 400 and d["error"].startswith("x.docx: not a Word document")


def test_inspect_refuses_a_cross_origin_post(srv):
    body = json.dumps({"name": "a.docx", "data": _b64(_docx(P("x")))}).encode()
    req = urllib.request.Request(srv.url + "api/inspect", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Origin": "http://evil.example"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 403


def test_compare_payload_carries_tracked(srv):
    status, d = _json(srv.url + "api/compare", "POST", _compare_body(a="<w:p>" + INS_RUN + "</w:p>", b=P("new")))
    assert status == 200 and d["tracked"] == {"original": 1, "modified": 0}
    assert _json(srv.url + "api/state")[1]["tracked"] == {"original": 1, "modified": 0}


def test_text_compares_two_pastes_without_touching_the_session(srv):
    status, d = _json(srv.url + "api/text", "POST", {"a": "The fee is 10 days.", "b": "The fee is 30 days."})
    assert status == 200 and d["summary"]["total"] == 2
    assert [s["m"] for s in d["rows"][0]["segments"]] == ["eq", "del", "ins", "eq"]
    assert _json(srv.url + "api/state")[1]["loaded"] is False


def test_text_leaves_a_loaded_comparison_alone(srv):
    assert _json(srv.url + "api/compare", "POST", _compare_body())[0] == 200
    before = srv.session.cmp
    assert _json(srv.url + "api/text", "POST", {"a": "x", "b": "y"})[0] == 200
    assert srv.session.cmp is before and _json(srv.url + "api/state")[1]["loaded"] is True


def test_text_honours_ignore_case(srv):
    body = {"a": "Hello World", "b": "hello world"}
    assert _json(srv.url + "api/text", "POST", body)[1]["summary"]["total"] > 0
    assert _json(srv.url + "api/text", "POST", {**body, "options": {"ignore_case": True}})[1]["summary"]["total"] == 0


def test_text_refuses_bad_input(srv):
    assert _json(srv.url + "api/text", "POST", {"a": "x"}) == (400, {"error": "a and b must be text"})
    assert _json(srv.url + "api/text", "POST", {"a": 1, "b": "y"})[0] == 400
    assert _json(srv.url + "api/text", "POST", {"a": "x", "b": "y", "options": {"nope": True}})[0] == 400
    assert _json(srv.url + "api/text")[0] == 405


def test_text_refuses_too_much(srv):
    from delta.textread import TEXT_MAX_BYTES
    status, d = _json(srv.url + "api/text", "POST", {"a": "x" * (TEXT_MAX_BYTES + 1), "b": ""})
    assert status == 413 and d == {"error": f"too much text to compare (over {TEXT_MAX_BYTES} bytes on one side)"}
    assert _json(srv.url + "api/text", "POST", {"a": "é" * (TEXT_MAX_BYTES // 2 + 1), "b": ""})[0] == 413


def test_text_refuses_a_broken_character(srv):
    raw = rb'{"a": "\ud800x", "b": "y"}'
    assert _json(srv.url + "api/text", "POST", raw=raw) == (400, {"error": "the text contains a broken character"})


def test_text_is_refused_cross_origin(srv):
    assert _post_with_headers(srv.url + "api/text", {"Origin": "http://evil.example"}) == \
        (403, {"error": "cross-origin request refused"})


def test_word_export_download(srv):
    import zipfile
    assert _json(srv.url + "api/compare", "POST", _compare_body(P("aaaa cccc"), P("aaaa bbbb")))[0] == 200
    status, headers, data = _req(srv.url + "api/docx?author=Pat%20Lee")
    assert status == 200
    assert headers["Content-Type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert headers["Content-Disposition"] == appmod._disposition("a vs b tracked.docx")
    assert 'w:author="Pat Lee"' in zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()


def test_word_export_before_a_comparison_is_409(srv):
    assert _req(srv.url + "api/docx")[0] == 409


def test_mail_writes_an_unsent_email_and_opens_it(srv, tmp_path):
    opened = []
    srv.server.mail_dir = str(tmp_path / "mail")
    srv.server.opener = opened.append
    old = tmp_path / "mail" / "old.eml"
    old.parent.mkdir()
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    status, d = _json(srv.url + "api/mail", "POST", {"a": "The fee is 10 days.", "b": "The fee is 30 days."})
    assert status == 200 and opened == [d["path"]] and d["path"].endswith(".eml")
    assert not old.exists()
    import email
    from email import policy
    msg = email.message_from_bytes(open(d["path"], "rb").read(), policy=policy.default)
    assert msg["X-Unsent"] == "1" and msg.get_content_type() == "text/html"
    body = msg.get_content()
    assert '<s style="color:#ff0000">10 </s><u style="color:#0000ff">30 </u>' in body


def test_mail_says_when_nothing_can_open_it(srv, tmp_path):
    def refuse(path):
        raise OSError(1155, "No application is associated with the specified file")
    srv.server.mail_dir = str(tmp_path)
    srv.server.opener = refuse
    status, d = _json(srv.url + "api/mail", "POST", {"a": "x", "b": "y"})
    assert status == 500 and "could not open the email" in d["error"]


def test_mail_refuses_bad_input_and_cross_origin(srv, tmp_path):
    srv.server.mail_dir = str(tmp_path)
    srv.server.opener = lambda p: None
    assert _json(srv.url + "api/mail", "POST", {"a": "x"}) == (400, {"error": "a and b must be text"})
    assert _json(srv.url + "api/mail")[0] == 405
    assert _post_with_headers(srv.url + "api/mail", {"Origin": "http://evil.example"})[0] == 403
