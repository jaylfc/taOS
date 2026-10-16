"""taos-shelld /close of the app in front goes back to the taOS desktop.

Every taOS app has its own workspace (app-<id>). Killing the focused window
that is alone on its workspace left sway focused on that now-empty workspace:
the kiosk (workspace 1) stayed hidden and the glass showed only the splash
background until someone switched workspaces by hand. Measured on the handset
2026-10-16 after a /close of app-files. An agent closing the app the user is
looking at (taOS useMobileControl) takes exactly this path.

Run: python3 -m pytest -q pmos/shell/test_taos_shelld_close.py
"""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

KIOSK = "chrome-127.0.0.1__-Default"
APP = "chrome-127.0.0.1__app.html-Default"


def _load():
    path = Path(__file__).with_name("taos-shelld.py")
    spec = importlib.util.spec_from_file_location("taos_shelld", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Done:
    def __init__(self, stdout=""):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def _win(con_id, app_id, focused=False):
    return {"id": con_id, "type": "con", "app_id": app_id, "name": "w%d" % con_id,
            "focused": focused, "nodes": [], "floating_nodes": []}


def _ws(name, *wins):
    return {"type": "workspace", "name": name, "nodes": list(wins), "floating_nodes": []}


def _tree(*workspaces):
    return {"type": "root", "nodes": [{"type": "output", "nodes": list(workspaces),
                                       "floating_nodes": []}], "floating_nodes": []}


@pytest.fixture()
def shelld(monkeypatch):
    mod = _load()
    calls = []
    state = {"tree": _tree()}

    def fake_swaymsg(*args):
        calls.append(args)
        if args[:2] == ("-t", "get_tree"):
            return _Done(json.dumps(state["tree"]))
        return _Done()

    monkeypatch.setattr(mod, "swaymsg", fake_swaymsg)
    server = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield server.server_address[1], calls, state
    finally:
        server.shutdown()


def _close(port, con_id):
    req = urllib.request.Request(
        "http://127.0.0.1:%d/close" % port,
        data=json.dumps({"con_id": con_id}).encode(),
        headers={"Content-Type": "application/json", "X-taOS-Shell": "1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _commands(calls):
    return [c for c in calls if c[:2] != ("-t", "get_tree")]


def test_an_unknown_con_id_is_refused_and_moves_nothing(shelld):
    port, calls, state = shelld
    state["tree"] = _tree(_ws("1", _win(1, KIOSK)), _ws("app-files", _win(7, APP, focused=True)))
    status, body = _close(port, 99)
    assert status == 404, body
    assert _commands(calls) == [], calls


def test_closing_the_app_in_front_returns_to_the_desktop(shelld):
    port, calls, state = shelld
    state["tree"] = _tree(_ws("1", _win(1, KIOSK)), _ws("app-files", _win(7, APP, focused=True)))
    status, body = _close(port, 7)
    print(status, body, calls)
    assert status == 200 and body.get("ok") is True
    cmds = _commands(calls)
    assert ("[con_id=7] kill",) in cmds, cmds
    assert ("workspace", "1") in cmds, "left focused on the empty app-files workspace: %r" % cmds
    assert cmds.index(("[con_id=7] kill",)) < cmds.index(("workspace", "1")), cmds


def test_closing_from_the_desktop_switcher_stays_on_the_desktop(shelld):
    # The desktop's own switcher runs in the kiosk on workspace 1, which holds
    # focus; the app it closes is in the background. No workspace change.
    port, calls, state = shelld
    state["tree"] = _tree(_ws("1", _win(1, KIOSK, focused=True)), _ws("app-files", _win(7, APP)))
    status, body = _close(port, 7)
    assert status == 200 and body.get("ok") is True
    assert _commands(calls) == [("[con_id=7] kill",)], calls


def test_closing_one_of_two_windows_on_a_workspace_stays_there(shelld):
    # Firefox can have two windows on app-browser: closing the focused one
    # leaves the other in front, so going home would hide it.
    port, calls, state = shelld
    state["tree"] = _tree(_ws("1", _win(1, KIOSK)),
                          _ws("app-browser", _win(7, "firefox", focused=True), _win(8, "firefox")))
    status, body = _close(port, 7)
    assert status == 200 and body.get("ok") is True
    assert _commands(calls) == [("[con_id=7] kill",)], calls


def test_focus_never_changes_workspace_by_itself(shelld):
    port, calls, state = shelld
    state["tree"] = _tree(_ws("1", _win(1, KIOSK, focused=True)), _ws("app-files", _win(7, APP)))
    req = urllib.request.Request(
        "http://127.0.0.1:%d/focus" % port,
        data=json.dumps({"con_id": 7}).encode(),
        headers={"Content-Type": "application/json", "X-taOS-Shell": "1"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
    assert _commands(calls) == [("[con_id=7] focus",)], calls
