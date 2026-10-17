"""taos-shelld switcher previews: a snapshot of each app taken when it is left.

Jay 2026-10-17 picked "snapshot on leave": when focus moves off an app window,
shelld captures that window (grim -T <foreign_toplevel_identifier>, which works
on a hidden window, measured on the handset) and serves the picture for the
switcher card. Never the kiosk: it is the lock screen and the auth pages.
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
DESKTOP = "http://127.0.0.1:6969"


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


def _con(con_id, app_id, focused=False):
    return {"id": con_id, "type": "con", "app_id": app_id, "name": "w%d" % con_id,
            "focused": focused, "foreign_toplevel_identifier": "ident%d" % con_id,
            "nodes": [], "floating_nodes": []}


def _ws(name, *wins):
    return {"type": "workspace", "name": name, "nodes": list(wins), "floating_nodes": []}


def _tree(*workspaces):
    return {"type": "root", "nodes": [{"type": "output", "nodes": list(workspaces),
                                       "floating_nodes": []}], "floating_nodes": []}


def _focus(con):
    return {"change": "focus", "container": con}


# -- the leave rule ----------------------------------------------------------

def test_leaving_an_app_captures_it():
    mod = _load()
    actions, last = mod.on_window_event(_focus(_con(7, APP)), None)
    assert actions == []
    actions, last = mod.on_window_event(_focus(_con(1, KIOSK)), last)
    assert actions == [("capture", 7, "ident7")]


def test_switching_app_to_app_captures_the_one_left():
    mod = _load()
    _, last = mod.on_window_event(_focus(_con(7, APP)), None)
    actions, last = mod.on_window_event(_focus(_con(8, "firefox")), last)
    assert actions == [("capture", 7, "ident7")]
    assert last == (8, "ident8")


def test_the_kiosk_is_never_captured():
    # The kiosk is the lock screen and the auth pages: leaving it must not
    # put a picture of it on disk.
    mod = _load()
    _, last = mod.on_window_event(_focus(_con(1, KIOSK)), None)
    assert last is None
    actions, _ = mod.on_window_event(_focus(_con(7, APP)), last)
    assert actions == []


def test_the_switcher_window_is_never_captured():
    mod = _load()
    _, last = mod.on_window_event(_focus(_con(9, "chrome-shell.taos__-Default")), None)
    actions, _ = mod.on_window_event(_focus(_con(7, APP)), last)
    assert actions == []


def test_refocusing_the_same_app_captures_nothing():
    mod = _load()
    _, last = mod.on_window_event(_focus(_con(7, APP)), None)
    actions, _ = mod.on_window_event(_focus(_con(7, APP)), last)
    assert actions == []


def test_closing_an_app_prunes_it_and_never_captures_it_later():
    mod = _load()
    _, last = mod.on_window_event(_focus(_con(7, APP)), None)
    actions, last = mod.on_window_event({"change": "close", "container": _con(7, APP)}, last)
    assert actions == [("prune", 7)]
    actions, _ = mod.on_window_event(_focus(_con(1, KIOSK)), last)
    assert actions == []


def test_capture_runs_grim_on_that_window_into_the_store(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "SNAP_DIR", str(tmp_path))
    seen = []

    def fake_grim(*args):
        seen.append(args)
        Path(args[-1]).write_bytes(b"\xff\xd8jpeg")
        return _Done()

    monkeypatch.setattr(mod, "grim", fake_grim)
    mod.apply_action(("capture", 7, "ident7"))
    assert seen and seen[0][:2] == ("-T", "ident7") and "jpeg" in seen[0], seen
    assert (tmp_path / "7.jpg").read_bytes() == b"\xff\xd8jpeg"
    mod.apply_action(("prune", 7))
    assert not (tmp_path / "7.jpg").exists()


def test_ipc_message_framing_round_trips():
    mod = _load()
    raw = mod.ipc_pack(2, b'["window"]')
    assert raw[:6] == b"i3-ipc" and len(raw) == 14 + len(b'["window"]')


# -- serving -----------------------------------------------------------------

@pytest.fixture()
def shelld(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "SNAP_DIR", str(tmp_path))
    state = {"tree": _tree(_ws("1", _con(1, KIOSK)), _ws("app-files", _con(7, APP, focused=True)))}

    def fake_swaymsg(*args):
        if args[:2] == ("-t", "get_tree"):
            return _Done(json.dumps(state["tree"]))
        return _Done()

    monkeypatch.setattr(mod, "swaymsg", fake_swaymsg)
    server = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1], tmp_path, state
    finally:
        server.shutdown()


def _get(port, path, origin=None):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path))
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_a_listed_window_snapshot_is_served_as_jpeg(shelld):
    port, snaps, _ = shelld
    (snaps / "7.jpg").write_bytes(b"\xff\xd8pic")
    status, headers, body = _get(port, "/snapshot/7", origin=DESKTOP)
    assert status == 200 and body == b"\xff\xd8pic"
    assert headers.get("Content-Type") == "image/jpeg"
    assert headers.get("Access-Control-Allow-Origin") == DESKTOP


def test_another_origin_gets_no_cors_header(shelld):
    port, snaps, _ = shelld
    (snaps / "7.jpg").write_bytes(b"\xff\xd8pic")
    _, headers, _ = _get(port, "/snapshot/7", origin="http://evil.example")
    assert "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize("path", ["/snapshot/1", "/snapshot/99", "/snapshot/x", "/snapshot/../7",
                                  "/snapshot/7.jpg", "/snapshot/"])
def test_only_a_listed_window_by_number_is_served(shelld, path):
    port, snaps, _ = shelld
    # A file exists for the kiosk too: the gate is the window list, not the disk.
    (snaps / "1.jpg").write_bytes(b"\xff\xd8kiosk")
    (snaps / "7.jpg").write_bytes(b"\xff\xd8pic")
    status, _, body = _get(port, path)
    assert status == 404, (path, body)


def test_a_listed_window_with_no_snapshot_yet_is_404(shelld):
    port, _, _ = shelld
    assert _get(port, "/snapshot/7")[0] == 404


def test_windows_reports_snapshot_time_and_prunes_closed_ones(shelld):
    port, snaps, state = shelld
    (snaps / "7.jpg").write_bytes(b"\xff\xd8pic")
    (snaps / "42.jpg").write_bytes(b"\xff\xd8gone")
    status, _, body = _get(port, "/windows")
    wins = json.loads(body)
    assert status == 200 and [w["con_id"] for w in wins] == [7]
    assert isinstance(wins[0]["snapshot"], (int, float)) and wins[0]["snapshot"] > 0
    assert not (snaps / "42.jpg").exists()
    (snaps / "7.jpg").unlink()
    wins = json.loads(_get(port, "/windows")[2])
    assert wins[0]["snapshot"] is None
