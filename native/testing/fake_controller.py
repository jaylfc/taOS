#!/usr/bin/env python3
"""A small fake taOS controller for the native client tests (stdlib only).

Serves the routes the first native apps use, from the fixtures of
native/taos-client/tests/fixtures: auth, notifications (list, read, read-all,
archive, clear via archive), the OS event stream (SSE), decisions (list, get,
answer), the dashboard reads and models reads.

It enforces what the real controller enforces and the clients must get right:
  * a request needs the `taos_session` cookie or `Authorization: Bearer <token>`;
  * POST/PUT/PATCH/DELETE with a cookie session need `X-CSRF-Token` equal to
    the `csrf_token` cookie;
  * a session is bound to the first User-Agent that used it (a different one
    gets 401), like AuthManager.validate_session.

Test controls (no auth), all under /__fake/:
  GET  /__fake/stats    -> {sse_open, sse_total, requests, ...}
  POST /__fake/config   -> {delay_ms?, delay_path?, heartbeat_ms?, fail?, expire?,
                            drop_sse?, push?, bearer?, reset?}
        delay_ms/delay_path: sleep before answering paths that start with delay_path
        fail: {"path": "/api/decisions", "status": 500, "message": "...", "times": 1}
        expire: true  -> every existing session becomes invalid (401 next)
        drop_sse: true -> close every open SSE connection now
        push: {"kind": "notification.added", ...} -> send to every open SSE
        burst: N      -> push N numbered events quickly
        add_notification: {...} -> insert a row (newest first) and push notification.added
        bearer: "tok" -> accept this bearer token
        me_cookie_only: true -> /auth/me ignores a bearer (the controller today)
        snapshot_delay_ms: GET /api/notifications answers with its snapshot this late
        sse_delay_ms: the SSE connection is accepted this late
Run: fake_controller.py [--port 0]; prints `LISTENING <port>` on stdout.
"""
import argparse
import http.cookies
import json
import os
import queue
import secrets
import select
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "taos-client", "tests", "fixtures")
USER, PASSWORD = "alex", "hunter2-secret-pw"

lock = threading.Lock()
state = {}
# Survive reset(): connections opened before a reset are still open (and
# will close later), so these must keep counting across it.
live = {"sse_open": 0, "drop_epoch": 0}


def reset():
    with lock:
        state.clear()
        state.update(
            sessions={},  # token -> {csrf, ua}
            bearer="local-token-123",
            delay_ms=0,
            delay_path="/",
            heartbeat_ms=10000,
            fail=[],
            sse_total=0,
            requests=0,
            sse_queues=[],
            seq=0,
            notifications=json.loads(fx("notifications_list.json")),
            decisions=json.loads(fx("decisions_list.json"))["items"],
            seen_uas=set(),
            me_cookie_only=False,
            snapshot_delay_ms=0,
            sse_delay_ms=0,
            os_ids={},      # client id -> row id (POST /api/notifications/os is idempotent on it)
            os_posts=0,     # every POST that reached the handler, duplicates included
        )


def fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "fake-taos"

    def log_message(self, fmt, *a):
        if os.environ.get("FAKE_LOG"):
            sys.stderr.write(fmt % a + "\n")

    # ---- plumbing -----------------------------------------------------
    def send_json(self, obj, status=200, headers=()):
        body = json.dumps(obj).encode() if not isinstance(obj, (bytes, str)) else (
            obj if isinstance(obj, bytes) else obj.encode())
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        return self._body

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            self._body = json.loads(raw) if raw else {}
        except ValueError:
            self._body = {}

    def cookies(self):
        c = http.cookies.SimpleCookie()
        c.load(self.headers.get("Cookie", ""))
        return {k: m.value for k, m in c.items()}

    def authed(self, mutating):
        """Return None when fine, else (status, message)."""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            if auth[7:] == state["bearer"]:
                return None
            return 401, "Invalid bearer token"
        ck = self.cookies()
        tok = ck.get("taos_session")
        with lock:
            s = state["sessions"].get(tok)
            ua = self.headers.get("User-Agent", "")
            if not s:
                return 401, "Not authenticated"
            if s["ua"] is None:
                s["ua"] = ua
            elif s["ua"] != ua:
                return 401, "Not authenticated"
            csrf = s["csrf"]
        if mutating and (self.headers.get("X-CSRF-Token") != csrf or ck.get("csrf_token") != csrf):
            return 403, "CSRF token missing or invalid"
        return None

    def route(self, verb):
        self.read_body()  # always drain it: an unread body corrupts keep-alive
        path, _, query = self.path.partition("?")
        q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        with lock:
            state["requests"] += 1
            delay = state["delay_ms"] if path.startswith(state["delay_path"]) else 0
            fail = None
            for f in state["fail"]:
                if path.startswith(f["path"]) and f.get("times", 1) > 0:
                    f["times"] = f.get("times", 1) - 1
                    fail = f
                    break
        if path.startswith("/__fake/"):
            return self.control(verb, path)
        if delay:
            time.sleep(delay / 1000.0)
        if fail:
            return self.send_json({"detail": fail.get("message", "injected failure")}, fail["status"])
        mutating = verb != "GET"
        if path == "/auth/login" and verb == "POST":
            return self.login()
        if path == "/auth/me" and state["me_cookie_only"] and not self.cookies().get("taos_session"):
            # `me_cookie_only` reproduces the controller as of 2026-11 (routes/auth.py
            # auth_me reads only the cookie). Default is the wanted behaviour: a valid
            # bearer is accepted and answered with the local identity.
            return self.send_json({"error": "not authenticated"}, 401)
        err = self.authed(mutating)
        if err:
            return self.send_json({"detail": err[1]}, err[0])
        getattr(self, "r_" + verb)(path, q)

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_DELETE(self):
        self.route("DELETE")

    do_PUT = do_PATCH = do_DELETE

    # ---- control ------------------------------------------------------
    def control(self, verb, path):
        if path == "/__fake/stats":
            with lock:
                return self.send_json({"sse_open": live["sse_open"], "sse_total": state["sse_total"], "requests": state["requests"],
                                       "os_posts": state["os_posts"],
                                       "os_rows": [r for r in state["notifications"] if str(r.get("source", "")).startswith("os:")]})
        b = self.body()
        with lock:
            if b.get("reset"):
                pass
        if b.get("reset"):
            reset()
        with lock:
            for k in ("delay_ms", "delay_path", "heartbeat_ms", "bearer", "me_cookie_only",
                      "snapshot_delay_ms", "sse_delay_ms"):
                if k in b:
                    state[k] = b[k]
            if "fail" in b:
                state["fail"] = [b["fail"]] if b["fail"] else []
            if b.get("expire"):
                state["sessions"].clear()
            if b.get("drop_sse"):
                live["drop_epoch"] += 1
            qs = list(state["sse_queues"])
        if "add_notification" in b:
            with lock:
                row = dict(id=max([r["id"] for r in state["notifications"]] + [0]) + 1,
                           timestamp=time.time(), level="info", title="Added", message="", read=False,
                           source="test", data=None, user_id=None)
                row.update(b["add_notification"])
                state["notifications"].insert(0, row)
            for q in qs:
                q.put(("data", {"kind": "notification.added", "id": "n%d" % row["id"]}))
        if "push" in b:
            for q in qs:
                q.put(("data", b["push"]))
        for i in range(int(b.get("burst", 0))):
            for q in qs:
                q.put(("data", {"kind": "notification.added", "id": "b%d" % i, "n": i}))
        self.send_json({"ok": True})

    # ---- auth ---------------------------------------------------------
    def login(self):
        b = self.body()
        if b.get("password") != PASSWORD or b.get("username", USER) != USER:
            return self.send_json({"detail": "Invalid username or password"}, 401)
        tok, csrf = secrets.token_hex(16), secrets.token_hex(8)
        with lock:
            state["sessions"][tok] = {"csrf": csrf, "ua": self.headers.get("User-Agent", "")}
        self.send_json(fx("auth_login.json"), 200, [
            ("Set-Cookie", "taos_session=%s; HttpOnly; Path=/; SameSite=strict; Max-Age=2592000" % tok),
            ("Set-Cookie", "csrf_token=%s; Path=/; SameSite=strict" % csrf)])

    # ---- routes -------------------------------------------------------
    def r_GET(self, path, q):
        simple = {
            "/auth/me": "auth_me.json", "/api/activity": "activity.json",
            "/api/models": "models_catalog.json", "/api/models/loaded": "loaded_models.json",
            "/api/models/downloads": "downloads_all.json", "/api/providers": "providers.json",
            "/api/scheduler/stats": "scheduler_stats.json", "/api/scheduler/tasks": "scheduler_tasks.json",
            "/api/cluster/workers": "workers.json",
        }
        if path == "/auth/me" and self.headers.get("Authorization", "").startswith("Bearer "):
            return self.send_json({"user": {"id": "local", "username": "local", "is_admin": True, "via": "local_token"}})
        if path in simple:
            return self.send_json(fx(simple[path]))
        if path == "/api/notifications":
            rows = list(state["notifications"])
            if q.get("unread_only") == "true":
                rows = [r for r in rows if not r.get("read")]
            if state["snapshot_delay_ms"]:
                time.sleep(state["snapshot_delay_ms"] / 1000.0)  # the answer is a stale snapshot
            return self.send_json(rows)
        if path == "/api/decisions":
            return self.send_json({"items": state["decisions"]})
        if path.startswith("/api/decisions/"):
            for d in state["decisions"]:
                if d["id"] == path.rsplit("/", 1)[1]:
                    return self.send_json(d)
            return self.send_json({"error": "decision not found"}, 404)
        if path == "/api/os/events":
            return self.sse()
        self.send_json({"detail": "Not Found"}, 404)

    def r_POST(self, path, q):
        parts = path.strip("/").split("/")
        if path == "/auth/logout":
            # The controller revokes the session server side (routes/auth.py logout).
            with lock:
                state["sessions"].pop(self.cookies().get("taos_session"), None)
            return self.send_json({"ok": True})
        if path == "/api/notifications/os":
            # The OS (freedesktop) notification ingest: one row per client id, retries answer duplicate.
            b = self.body()
            cid = b.get("id")
            if not isinstance(cid, str) or not cid or not isinstance(b.get("summary"), str):
                return self.send_json({"error": "invalid"}, 422)
            with lock:
                state["os_posts"] += 1
                if cid in state["os_ids"]:
                    return self.send_json({"id": state["os_ids"][cid], "duplicate": True})
                rid = max([r["id"] for r in state["notifications"]] + [0]) + 1
                state["os_ids"][cid] = rid
                state["notifications"].insert(0, dict(
                    id=rid, timestamp=b.get("posted_at") or time.time(), level="warning" if b.get("urgency") == "critical" else "info",
                    title=b["summary"], message=b.get("body", ""), read=False,
                    source="os:" + (b.get("desktop_entry") or b.get("app_name", "app")).lower(),
                    data={"os": {"client_id": cid}}, user_id="local"))
                qs = list(state["sse_queues"])
            for q2 in qs:
                q2.put(("data", {"kind": "notification.added", "id": "n%d" % rid}))
            return self.send_json({"id": rid, "duplicate": False})
        if path == "/api/notifications/read-all":
            n = 0
            for r in state["notifications"]:
                if not r["read"]:
                    r["read"] = True
                    n += 1
            return self.send_json({"ok": True, "marked": n})
        if parts[:2] == ["api", "notifications"] and len(parts) == 4:
            row = next((r for r in state["notifications"] if str(r["id"]) == parts[2]), None)
            if not row:
                return self.send_json({"error": "not found"}, 404)
            if parts[3] == "read":
                row["read"] = True
                return self.send_json({"ok": True})
            if parts[3] == "archive":
                if row.get("pinned"):
                    row["read"] = True
                    return self.send_json({"ok": True, "kept": "pending-request"})
                state["notifications"].remove(row)
                return self.send_json({"ok": True})
        if parts[:2] == ["api", "decisions"] and len(parts) == 4 and parts[3] == "answer":
            d = next((x for x in state["decisions"] if x["id"] == parts[2]), None)
            if not d:
                return self.send_json({"error": "decision not found"}, 404)
            if d["status"] != "pending":
                return self.send_json({"error": "already answered or not pending"}, 409)
            d["status"] = "answered"
            d["answer"] = {"value": self.body().get("value"), "answered_by": USER}
            return self.send_json(d)
        self.send_json({"detail": "Not Found"}, 404)

    # ---- SSE ----------------------------------------------------------
    def sse(self):
        if state["sse_delay_ms"]:
            time.sleep(state["sse_delay_ms"] / 1000.0)  # connection not yet registered: events sent now are lost
        q = queue.Queue()
        with lock:
            live["sse_open"] += 1
            state["sse_total"] += 1
            state["sse_queues"].append(q)
            epoch = live["drop_epoch"]
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            n = 0
            last_hb = time.time()
            while True:
                with lock:
                    if live["drop_epoch"] != epoch:
                        return
                    hb = state["heartbeat_ms"] / 1000.0
                try:
                    kind, obj = q.get(timeout=0.02)
                    n += 1
                    self.wfile.write(("id: %d\ndata: %s\n\n" % (n, json.dumps(obj))).encode())
                    self.wfile.flush()
                except queue.Empty:
                    pass
                if time.time() - last_hb >= hb:
                    last_hb = time.time()
                    self.wfile.write(('data: {"kind": "events.heartbeat", "ts": %f}\n\n' % time.time()).encode())
                    self.wfile.flush()
                r, _, _ = select.select([self.connection], [], [], 0)
                if r and self.connection.recv(1, 2) == b"":  # MSG_PEEK
                    return
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            with lock:
                live["sse_open"] -= 1
                if q in state["sse_queues"]:
                    state["sse_queues"].remove(q)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    reset()
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print("LISTENING %d" % srv.server_address[1], flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
