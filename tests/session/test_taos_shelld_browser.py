"""taos-shelld opens links in Firefox, and lets the desktop drive the switcher.

Jay (2026-10-11): a real browser, links from taOS apps opening in it, and one
task switcher. The desktop page (DESKTOP_ORIGIN) posts /open-url, /focus and
/close; anything else on the device, Firefox's own pages included, must not.

Run: python3 -m pytest -q pmos/shell/test_taos_shelld_browser.py
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

DESKTOP = "http://127.0.0.1:6969"
TREE = {"nodes": [{"type": "workspace", "name": "app-browser", "nodes": [
    {"id": 27, "app_id": "firefox", "name": "Example Domain", "nodes": []}]}]}


def _load():
    path = Path(__file__).with_name("taos-shelld.py")
    spec = importlib.util.spec_from_file_location("taos_shelld_browser", path)
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
    sway, spawned = [], []
    tree = {"now": TREE}

    def fake_swaymsg(*args):
        sway.append(args)
        if args[:2] == ("-t", "get_tree"):
            return _Done(json.dumps(tree["now"]))
        return _Done()

    def fake_popen(argv, **kw):
        spawned.append(list(argv))
        # As on the handset: the window maps on the daemon's own workspace.
        if not any(c.get("app_id") == "firefox" for c in _all(tree["now"])):
            tree["now"] = {"nodes": [{"type": "workspace", "name": "1", "nodes": [
                {"id": 31, "app_id": "firefox", "name": "Mozilla Firefox", "nodes": []}]}]}

    monkeypatch.setattr(mod, "swaymsg", fake_swaymsg)
    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    server = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1], sway, spawned, tree
    finally:
        server.shutdown()


def _all(node):
    yield node
    for c in node.get("nodes", []):
        yield from _all(c)


def _req(port, path, body, method="POST", headers=None):
    h = {"Content-Type": "application/json", "X-taOS-Shell": "1"}
    h.update(headers or {})
    h = {k: v for k, v in h.items() if v is not None}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data,
                                 headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _settle(spawned, n=1, within=3.0):
    end = time.monotonic() + within
    while time.monotonic() < end and len(spawned) < n:
        time.sleep(0.02)
    time.sleep(0.1)


# --- refused first: nothing below may ever reach Firefox ------------------

@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "file:///etc/shadow",
    "data:text/html,<script>1</script>",
    "chrome://settings",
    "--screenshot=/tmp/x",
    "-new-instance",
    "https://",
    "https://exa mple.com/",
    "https://example.com/\nfile:///etc/shadow",
    "https://example.com/" + "a" * 5000,
    "",
    42,
    None,
])
def test_a_malformed_url_is_refused_and_never_spawned(shelld, url):
    port, sway, spawned, _ = shelld
    status, _ = _req(port, "/open-url", {"url": url}, headers={"Origin": DESKTOP})
    assert status == 400, url
    time.sleep(0.2)
    assert spawned == [] and sway == []


def test_open_url_without_the_shell_header_is_refused(shelld):
    port, sway, spawned, _ = shelld
    status, _ = _req(port, "/open-url", {"url": "https://example.com/"},
                     headers={"X-taOS-Shell": None, "Origin": DESKTOP})
    assert status == 403
    time.sleep(0.2)
    assert spawned == []


def test_open_url_from_a_web_page_in_the_browser_is_refused(shelld):
    port, sway, spawned, _ = shelld
    status, _ = _req(port, "/open-url", {"url": "https://example.com/"},
                     headers={"Origin": "https://evil.example"})
    assert status == 403
    time.sleep(0.2)
    assert spawned == []


def test_preflight_from_another_origin_is_refused(shelld):
    port, _, _, _ = shelld
    for path in ("/open-url", "/focus", "/close"):
        status, _ = _req(port, path, None, method="OPTIONS",
                         headers={"Origin": "https://evil.example",
                                  "Access-Control-Request-Method": "POST"})
        assert status == 403, path


def test_a_body_without_url_is_a_bad_request(shelld):
    port, _, spawned, _ = shelld
    status, _ = _req(port, "/open-url", {"href": "https://example.com/"}, headers={"Origin": DESKTOP})
    assert status == 400
    assert spawned == []


# --- then what must work ----------------------------------------------------

def test_the_desktop_opens_a_link_in_firefox_on_the_browser_workspace(shelld):
    port, sway, spawned, _ = shelld
    url = "https://github.com/login/device?x=1&y=2"
    status, body = _req(port, "/open-url", {"url": url}, headers={"Origin": DESKTOP})
    assert status == 200 and json.loads(body)["ok"] is True
    _settle(spawned)
    assert spawned == [["firefox", url]]
    # Shown, not just opened: a running Firefox takes the tab off-screen.
    assert ("workspace", "app-browser") in sway


def test_the_desktop_preflight_is_allowed_for_its_switcher_calls(shelld):
    port, _, _, _ = shelld
    for path in ("/launch", "/open-url", "/focus", "/close"):
        status, _ = _req(port, path, None, method="OPTIONS",
                         headers={"Origin": DESKTOP, "Access-Control-Request-Method": "POST"})
        assert status == 204, path


def test_the_desktop_switcher_can_focus_a_window(shelld):
    port, sway, _, _ = shelld
    status, _ = _req(port, "/focus", {"con_id": 27}, headers={"Origin": DESKTOP})
    assert status == 200
    assert ("[con_id=27] focus",) in sway


def test_the_desktop_switcher_cannot_touch_an_unlisted_container(shelld):
    port, sway, _, _ = shelld
    status, _ = _req(port, "/close", {"con_id": 5}, headers={"Origin": DESKTOP})
    assert status == 404
    assert not any("kill" in a[0] for a in sway if a)


def test_launching_the_browser_app_when_open_switches_to_it(shelld):
    port, sway, spawned, _ = shelld
    status, _ = _req(port, "/launch", {"app": "browser"}, headers={"Origin": DESKTOP})
    assert status == 200
    # TREE already has Firefox on app-browser: it is switched to, not restarted.
    time.sleep(0.4)
    assert ("workspace", "app-browser") in sway
    assert spawned == [] and not any(a and a[0] == "exec" for a in sway)


def test_launching_the_browser_when_closed_starts_one_firefox(shelld):
    port, sway, spawned, tree = shelld
    tree["now"] = {"nodes": []}
    status, _ = _req(port, "/launch", {"app": "browser"}, headers={"Origin": DESKTOP})
    assert status == 200
    _settle(spawned)
    assert spawned == [["firefox"]]
    end = time.monotonic() + 3
    while time.monotonic() < end and ("workspace", "app-browser") not in sway[-1:]:
        time.sleep(0.05)
    # It mapped on workspace 1, behind the desktop: moved, then shown.
    assert ("[con_id=31] move container to workspace app-browser",) in sway
    assert sway[-1] == ("workspace", "app-browser")
    assert not any(a and a[0] == "exec" for a in sway), "chromium was exec'd for the browser"


def test_launching_the_browser_when_it_mapped_elsewhere_reuses_it(shelld):
    port, sway, spawned, tree = shelld
    tree["now"] = {"nodes": [{"type": "workspace", "name": "1", "nodes": [
        {"id": 32, "app_id": "firefox", "name": "Wikipedia", "nodes": []}]}]}
    status, _ = _req(port, "/launch", {"app": "browser"}, headers={"Origin": DESKTOP})
    assert status == 200
    time.sleep(0.5)
    assert spawned == [], "a second Firefox window was started"
    assert ("[con_id=32] move container to workspace app-browser",) in sway
    assert sway[-1] == ("workspace", "app-browser")


def test_another_app_still_launches_as_a_chromium_window(shelld):
    port, sway, spawned, tree = shelld
    tree["now"] = {"nodes": []}
    status, _ = _req(port, "/launch", {"app": "files"}, headers={"Origin": DESKTOP})
    assert status == 200
    end = time.monotonic() + 3
    while time.monotonic() < end and not any(a and a[0] == "exec" for a in sway):
        time.sleep(0.02)
    assert any(a and a[0] == "exec" and "app.html?app=files" in a[1] for a in sway)
    assert spawned == []


def test_the_tests_see_a_real_url_check():
    mod = _load()
    assert mod.valid_url("https://example.com/") and not mod.valid_url("file:///x")


def test_a_rebound_host_cannot_read_or_drive_the_shell(shelld):
    # DNS rebinding: evil.example resolves to 127.0.0.1, so to Firefox the
    # page is same-origin and needs no CORS. Only the Host header tells.
    port, sway, spawned, _ = shelld
    evil = {"Host": "evil.example:%d" % port}
    assert _req(port, "/windows", None, method="GET", headers=evil)[0] == 403
    assert _req(port, "/", None, method="GET", headers=evil)[0] == 403
    status, _ = _req(port, "/open-url", {"url": "https://example.com/"},
                     headers=dict(evil, Origin="http://evil.example:%d" % port))
    assert status == 403
    time.sleep(0.2)
    assert spawned == []
    # Control: the loopback name still works.
    assert _req(port, "/windows", None, method="GET")[0] == 200


def test_cors_never_allows_another_origin_to_read_windows(shelld):
    port, _, _, _ = shelld
    req = urllib.request.Request("http://127.0.0.1:%d/windows" % port,
                                 headers={"Origin": "https://evil.example"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.headers.get("Access-Control-Allow-Origin") is None
    req = urllib.request.Request("http://127.0.0.1:%d/windows" % port, headers={"Origin": DESKTOP})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.headers.get("Access-Control-Allow-Origin") == DESKTOP
