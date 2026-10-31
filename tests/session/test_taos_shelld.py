"""taos-shelld /launch answers before it touches sway.

The desktop waits SHELL_LAUNCH_TIMEOUT_MS (1.5 s) for {"ok": true} and then
opens the app in-page instead. A launch makes up to three swaymsg calls, each
with a 5 s timeout, so a slow compositor used to answer late: the desktop had
already fallen back in-page, and then the window opened as well -- the app,
twice. The reply must not wait on sway.

Run: python3 -m pytest -q tests/session/test_taos_shelld.py
"""
from __future__ import annotations

import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

DESKTOP_DEADLINE_S = 1.5
SLOW_SWAY_S = 2.0


def _load():
    path = Path(__file__).resolve().parents[2] / "session" / "shell" / "taos-shelld.py"
    spec = importlib.util.spec_from_file_location("taos_shelld", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Done:
    def __init__(self, stdout=""):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


@pytest.fixture()
def shelld(monkeypatch):
    mod = _load()
    calls = []
    lock = threading.Lock()

    def slow_swaymsg(*args):
        time.sleep(SLOW_SWAY_S)
        with lock:
            calls.append(args)
        if args[:2] == ("-t", "get_tree"):
            return _Done(json.dumps({"nodes": []}))
        return _Done()

    monkeypatch.setattr(mod, "swaymsg", slow_swaymsg)
    server = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield mod, server.server_address[1], calls
    finally:
        server.shutdown()


def _launch(port, app="files"):
    req = urllib.request.Request(
        "http://127.0.0.1:%d/launch" % port,
        data=json.dumps({"app": app}).encode(),
        headers={"Content-Type": "application/json", "X-taOS-Shell": "1"},
        method="POST",
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read())
    return body, time.monotonic() - t0


def _execs(calls):
    return [c for c in calls if c and c[0] == "exec"]


def _wait_for_exec(calls, n=1, within=15.0):
    end = time.monotonic() + within
    while time.monotonic() < end:
        if len(_execs(calls)) >= n:
            return
        time.sleep(0.05)


def test_the_reply_beats_the_desktops_deadline_on_a_slow_compositor(shelld):
    _mod, port, calls = shelld
    body, took = _launch(port)
    assert body.get("ok") is True
    assert took < DESKTOP_DEADLINE_S, "answered after %.2fs: the desktop has already opened it in-page" % took


def test_the_window_is_still_launched_exactly_once(shelld):
    _mod, port, calls = shelld
    _launch(port)
    _wait_for_exec(calls)
    execs = _execs(calls)
    assert len(execs) == 1, calls
    assert "app.html?app=files" in execs[0][1]


def test_a_second_tap_during_the_launch_opens_nothing_more(shelld):
    _mod, port, calls = shelld
    first, _ = _launch(port)
    second, took = _launch(port)
    assert first.get("ok") is True and second.get("ok") is True
    assert second.get("already") is True
    assert took < DESKTOP_DEADLINE_S
    _wait_for_exec(calls)
    time.sleep(SLOW_SWAY_S * 2)
    assert len(_execs(calls)) == 1, calls


def _post_launch(port, raw):
    req = urllib.request.Request(
        "http://127.0.0.1:%d/launch" % port, data=raw,
        headers={"Content-Type": "application/json", "X-taOS-Shell": "1"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_an_app_name_with_a_trailing_newline_is_refused(shelld):
    # `$` matches before a final "\n": "files\n" passed .match(), took its own
    # launch slot apart from "files", and reached swaymsg and a shell string.
    mod, port, calls = shelld
    assert _post_launch(port, json.dumps({"app": "files\n"}).encode()) == 400
    time.sleep(0.2)
    assert calls == [] and mod._LAUNCHING == set()


def test_a_reply_that_fails_to_send_still_launches_and_frees_the_slot(shelld, monkeypatch):
    # The desktop aborts after 1.5 s; a reply written to a closed socket raises.
    # The slot claim() took is only released by launch(), so if the thread
    # never starts, every later tap answers "already" and opens nothing.
    mod, port, calls = shelld
    real_json = mod.Handler._json

    def broken_pipe(self, obj, status=200):
        if obj == {"ok": True}:
            raise BrokenPipeError("client went away")
        return real_json(self, obj, status)

    monkeypatch.setattr(mod.Handler, "_json", broken_pipe)
    try:
        _post_launch(port, json.dumps({"app": "files"}).encode())
    except OSError:
        pass
    _wait_for_exec(calls)
    assert len(_execs(calls)) == 1, calls
