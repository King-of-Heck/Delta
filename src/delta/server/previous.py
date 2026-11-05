"""The last started server, remembered in the data folder so the next start can quit it.

A hidden Delta window has no idle limit, so a server whose window Edge lost without a close
(a crash, a killed process) keeps running; the next launch quits it. The record is one small
JSON file, `server.json`: {"url": ..., "pid": ...}. Every failure here is silent or one log
line, never a stopped start."""
from __future__ import annotations

import json
import os
import urllib.request
from urllib.parse import urlsplit

FILE = "server.json"
QUIT_TIMEOUT = 1.5          # seconds; an old server answers at once, anything else is not worth waiting on


def _path(data: str) -> str:
    return os.path.join(data, FILE)


def _read(data: str) -> dict | None:
    try:
        with open(_path(data), encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _local_url(url) -> str | None:
    """Only a plain http address on this machine is ever sent a quit; anything else in the file
    (garbage, an edited file) is ignored."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost") or not port:
        return None
    return f"http://{parts.hostname}:{port}/"


def take_over(data: str | None, own_url: str, timeout: float = QUIT_TIMEOUT) -> str | None:
    """Record this server as the running one, then ask the server the record named before to quit.
    Recording first means the old server's clean stop finds our record, not its own, and leaves it.
    Returns a log line, or None when there was nothing to quit (no data folder, no earlier record,
    or the record already named this server)."""
    if not data:
        return None
    previous = _read(data)
    record(data, own_url)
    if previous is None:
        return None
    url = _local_url(previous.get("url"))
    if url is None:
        return "previous server record ignored"
    if url == _local_url(own_url):
        return None
    # no Origin header: the server refuses only a POST whose Origin or Sec-Fetch-Site says
    # another site sent it, and urllib sends neither
    req = urllib.request.Request(url + "api/quit", data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
    except Exception as e:                                              # noqa: BLE001
        return f"previous server {url} not quit ({type(e).__name__})"   # gone already, or not ours
    return f"previous server {url} quit"


def record(data: str | None, url: str) -> None:
    """Remember this server as the one the next start quits."""
    if not data:
        return
    os.makedirs(data, exist_ok=True)
    tmp = _path(data) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"url": url, "pid": os.getpid()}, f)
    os.replace(tmp, _path(data))


def forget(data: str | None, url: str) -> None:
    """On a clean stop, remove the record, but only while it still names this server: a newer
    start has written its own over it."""
    if not data:
        return
    rec = _read(data)
    if rec is None or rec.get("url") != url or rec.get("pid") != os.getpid():
        return
    try:
        os.remove(_path(data))
    except OSError:
        pass
