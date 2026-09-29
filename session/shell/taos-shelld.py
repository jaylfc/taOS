#!/usr/bin/env python3
"""taos-shelld -- the task switcher behind the nav bar's recents button.

Jay (2026-09-29): a nav bar and task switcher where apps run as standalone
fullscreen windows "we can close and switch between", taOS apps and native
Linux apps alike. Only the compositor sees every window, so this reads sway's
tree -- the one list that includes a native app a web page never could.

Serves the switcher page and three calls, on LOOPBACK only:
  GET  /           the switcher (a card per open app)
  GET  /windows    the open app windows: con_id, app_id, title, workspace
  POST /focus      {"con_id": N}  switch to that window
  POST /close      {"con_id": N}  close it

Runs in the sway session as taos (exec_always), so it has SWAYSOCK and
needs no privilege: every action is a swaymsg the session owner could type.
POSTs must carry X-taOS-Shell: 1 and come from the switcher's own origin, so a
web page in some other window cannot close the user's apps with a form or a
no-cors fetch (the same reasoning as taOS's X-taOS-Console, #3204).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = int(os.environ.get("TAOS_SHELLD_PORT", "6973"))
ORIGIN = os.environ.get("TAOS_SHELLD_ORIGIN", "http://shell.taos:%d" % PORT)

#: Windows that are not "apps" to switch between: the kiosk (taOS home and
#: the lock screen, reached with the home button) and this switcher.
HIDDEN_APP_IDS = ("chrome-127.0.0.1__", "chrome-shell.taos__")


def swaymsg(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["swaymsg", *args], capture_output=True, text=True, timeout=5)


def _walk(node, workspace=None):
    if node.get("type") == "workspace":
        workspace = node.get("name")
    if node.get("app_id") or node.get("window_properties"):
        yield node, workspace
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        yield from _walk(child, workspace)


def label_for(app_id: str, title: str) -> str:
    """A readable name: taOS apps carry it in their hostname alias
    (chrome-camera.taos__-Default -> Camera); others use the window title."""
    if app_id.startswith("chrome-") and ".taos__" in app_id:
        return app_id[len("chrome-"):app_id.index(".taos__")].replace("-", " ").title()
    return title or app_id or "App"


def list_windows(tree: dict) -> list[dict]:
    out = []
    for con, ws in _walk(tree):
        app_id = con.get("app_id") or (con.get("window_properties") or {}).get("class") or ""
        if any(app_id.startswith(h) for h in HIDDEN_APP_IDS):
            continue
        if ws == "__i3_scratch":
            continue
        out.append({
            "con_id": con["id"],
            "app_id": app_id,
            "title": con.get("name") or "",
            "label": label_for(app_id, con.get("name") or ""),
            "workspace": ws,
            "focused": bool(con.get("focused")),
        })
    return out


def current_windows() -> list[dict]:
    raw = swaymsg("-t", "get_tree", "-r").stdout
    return list_windows(json.loads(raw)) if raw else []


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Recents</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { margin: 0; height: 100%; background: #050507; color: rgba(255,255,255,.92);
  font-family: -apple-system, "Inter", "Noto Sans", system-ui, sans-serif; }
header { display: flex; align-items: center; justify-content: space-between;
  padding: 20px 20px 8px; }
h1 { font-size: 22px; font-weight: 600; margin: 0; }
#clear { background: rgba(255,255,255,.08); color: rgba(255,255,255,.82); border: 0;
  border-radius: 999px; padding: 8px 14px; font-size: 13px; font-weight: 600; }
#grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; padding: 12px 16px 24px; }
.card { position: relative; background: rgba(30,30,34,.92); border-radius: 22px;
  padding: 16px 14px 14px; aspect-ratio: 3 / 4; display: flex; flex-direction: column;
  justify-content: space-between; overflow: hidden; border: 0; color: inherit;
  text-align: left; touch-action: pan-x; transition: transform .22s cubic-bezier(.2,.8,.2,1), opacity .22s; }
.card[data-current="1"] { box-shadow: 0 0 0 2px #4c9aff inset; }
.mark { width: 52px; height: 52px; border-radius: 15px; display: grid; place-items: center;
  font-size: 22px; font-weight: 700; color: #fff; background: var(--hue, #4c9aff); }
.name { font-size: 15px; font-weight: 600; margin-top: auto; }
.sub { font-size: 12px; color: rgba(255,255,255,.45); margin-top: 3px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.x { position: absolute; top: 10px; right: 10px; width: 30px; height: 30px; border-radius: 50%;
  border: 0; background: rgba(255,255,255,.10); color: rgba(255,255,255,.82); font-size: 16px; }
#empty { text-align: center; color: rgba(255,255,255,.45); margin-top: 38vh; font-size: 15px; }
</style></head>
<body>
<header><h1>Recents</h1><button id="clear" type="button" hidden>Close all</button></header>
<div id="grid"></div>
<div id="empty" hidden>No open apps</div>
<script>
(function () {
  "use strict";
  var grid = document.getElementById("grid");
  var empty = document.getElementById("empty");
  var clear = document.getElementById("clear");
  function hue(s) { var h = 0; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return "hsl(" + h + " 62% 48%)"; }
  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json",
      "X-taOS-Shell": "1" }, body: JSON.stringify(body) });
  }
  function closeCard(card, id) {
    card.style.transform = "translateY(-40%)"; card.style.opacity = "0";
    post("/close", { con_id: id }).finally(function () { setTimeout(load, 200); });
  }
  function render(wins) {
    grid.textContent = "";
    empty.hidden = wins.length > 0; clear.hidden = wins.length === 0;
    wins.forEach(function (w) {
      var card = document.createElement("div"); card.className = "card";
      card.setAttribute("role", "button"); card.tabIndex = 0;
      card.setAttribute("aria-label", "Switch to " + w.label);
      var mark = document.createElement("div"); mark.className = "mark";
      mark.style.setProperty("--hue", hue(w.app_id)); mark.textContent = (w.label[0] || "?").toUpperCase();
      var name = document.createElement("div"); name.className = "name"; name.textContent = w.label;
      var sub = document.createElement("div"); sub.className = "sub"; sub.textContent = w.title;
      var x = document.createElement("button"); x.className = "x"; x.type = "button";
      x.textContent = "✕"; x.setAttribute("aria-label", "Close " + w.label);
      x.addEventListener("click", function (e) { e.stopPropagation(); closeCard(card, w.con_id); });
      card.addEventListener("click", function () { post("/focus", { con_id: w.con_id }); });
      // Swipe up to close, as on a phone.
      var y0 = null;
      card.addEventListener("touchstart", function (e) { y0 = e.touches[0].clientY; }, { passive: true });
      card.addEventListener("touchmove", function (e) {
        if (y0 === null) return; var dy = Math.min(0, e.touches[0].clientY - y0);
        card.style.transition = "none"; card.style.transform = "translateY(" + dy + "px)";
        card.style.opacity = String(1 + dy / 300);
      }, { passive: true });
      card.addEventListener("touchend", function (e) {
        var dy = (e.changedTouches[0].clientY - (y0 === null ? 0 : y0)); y0 = null;
        card.style.transition = "";
        if (dy < -90) { closeCard(card, w.con_id); } else { card.style.transform = ""; card.style.opacity = ""; }
      });
      card.appendChild(mark); card.appendChild(x); card.appendChild(name); card.appendChild(sub);
      grid.appendChild(card);
    });
  }
  function load() {
    fetch("/windows").then(function (r) { return r.json(); }).then(render).catch(function () {});
  }
  clear.addEventListener("click", function () {
    fetch("/windows").then(function (r) { return r.json(); }).then(function (wins) {
      return Promise.all(wins.map(function (w) { return post("/close", { con_id: w.con_id }); }));
    }).finally(function () { setTimeout(load, 250); });
  });
  // Refresh whenever the switcher is shown again (the recents button focuses
  // this window rather than reloading it).
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });
  window.addEventListener("focus", load);
  load();
  setInterval(load, 3000);
})();
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, body: bytes, ctype: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(json.dumps(obj).encode(), "application/json", status)

    def do_GET(self):
        if self.path == "/":
            return self._send(PAGE.encode(), "text/html; charset=utf-8")
        if self.path == "/windows":
            return self._json(current_windows())
        return self._json({"error": "not found"}, 404)

    def _post_allowed(self) -> bool:
        if self.headers.get("X-taOS-Shell") != "1":
            return False
        origin = self.headers.get("Origin")
        return origin in (None, ORIGIN)

    def do_POST(self):
        if self.path not in ("/focus", "/close"):
            return self._json({"error": "not found"}, 404)
        if not self._post_allowed():
            return self._json({"error": "forbidden"}, 403)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
            con_id = int(body["con_id"])
        except (ValueError, KeyError, TypeError):
            return self._json({"error": "bad request"}, 400)
        # Only a window the switcher would list: never the kiosk, never an
        # arbitrary container id.
        if con_id not in {w["con_id"] for w in current_windows()}:
            return self._json({"error": "no such app"}, 404)
        verb = "focus" if self.path == "/focus" else "kill"
        res = swaymsg("[con_id=%d] %s" % (con_id, verb))
        return self._json({"ok": res.returncode == 0}, 200 if res.returncode == 0 else 502)


def replace_previous():
    run = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    pidfile = os.path.join(run, "taos-shelld.pid")
    try:
        old = int(open(pidfile).read().strip())
        if old != os.getpid() and b"taos-shelld" in open("/proc/%d/cmdline" % old, "rb").read():
            os.kill(old, 15)
    except (OSError, ValueError):
        pass
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))


def main():
    replace_previous()
    import time
    for _ in range(20):   # the replaced copy may still hold the port for a moment
        try:
            srv = ThreadingHTTPServer((HOST, PORT), Handler)
            break
        except OSError:
            time.sleep(0.25)
    else:
        sys.exit("taos-shelld: port %d stayed busy" % PORT)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
