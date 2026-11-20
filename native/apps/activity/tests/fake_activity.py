#!/usr/bin/env python3
"""The foundation's fake controller plus the Activity routes it lacks.

Imports native/testing/fake_controller.py (left unchanged) and wraps its handler:
  * GET /auth/me                    -> is_admin follows the `admin` switch
  * GET /api/activity               -> a moving CPU/net/disk reading; 3 GPUs when `gpus` > 1
  * GET /api/scheduler/tasks        -> honours ?limit=
  * GET /api/activity/models        -> history, newest first, filters worker/model/event, limit
  * GET /api/activity/models/stream -> SSE, session cookie ONLY (a bearer gets 401, like the real route)
  * POST /api/system/ai-stack/restart -> 403 unless `admin`, else a configurable body
Control (no auth): POST /__fake/act {admin, gpus, workers, restart_body, restart_status,
fail_activity, add_event, drop_stream, reset_events}; GET /__fake/act/stats.
Run: fake_activity.py <path to fake_controller.py>; prints `LISTENING <port>`.
"""
import importlib.util
import json
import os
import queue
import random
import select
import sys
import threading
import time

base = sys.argv[1] if len(sys.argv) > 1 else os.environ["FAKE_BASE_PY"]
spec = importlib.util.spec_from_file_location("fake_controller", base)
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)
H = fc.H
lock = threading.Lock()

A = {}
open_streams = {"n": 0}   # survives reset: handlers of earlier tests are still closing


def reset_act():
    A.clear()
    A.update(
        admin=True, gpus=1, workers="fixture", restart_body=None, restart_status=200,
        fail_activity=False, history_delay_ms=0, tick=0, restarts=0, history_calls=0, stream_open=0, stream_total=0,
        activity_calls=0, tasks_limits=[], drop_epoch=0, queues=[], bearer_rejected=0,
        last_history_query={}, last_stream_query={}, seq=3,
        events=[
            dict(seq=1, ts=time.time() - 120, event="model.load", model="qwen3:4b", worker="controller", backend="ollama",
                 duration_ms=1200, tokens_in=None, tokens_out=None, token_rate=None, reason=None, detail={}),
            dict(seq=2, ts=time.time() - 60, event="request.finish", model="qwen3:4b", worker="controller", backend="ollama",
                 duration_ms=2500, tokens_in=12, tokens_out=84, token_rate=33.6, reason=None, detail={}),
            dict(seq=3, ts=time.time() - 30, event="model.evict", model="bge-m3", worker="pc", backend="ollama",
                 duration_ms=None, tokens_in=None, tokens_out=None, token_rate=None, reason="vram", detail={}),
        ],
    )


reset_act()
WORKERS_FULL = json.loads(fc.fx("workers.json"))
WORKERS_FULL[0]["last_heartbeat"] = None  # set per request: fresh
WORKERS_REDUCED = [{"name": "pc", "status": "online", "tier_id": "x86-cuda-12gb", "kind": "worker"},
                   {"name": "nas", "status": "stale", "tier_id": None, "kind": "worker"}]


def gpus(n):
    if n <= 1:
        return None
    return [
        {"type": "nvidia", "model": "NVIDIA GeForce RTX 3090", "load": {"load_percent": 41, "freq_hz": 1695000000},
         "vram_percent": 62.5, "vram_used_mb": 15360, "vram_total_mb": 24576},
        {"type": "nvidia", "model": "NVIDIA GeForce RTX 3060", "load": {"load_percent": 7, "freq_hz": 1320000000},
         "vram_percent": 18.2, "vram_used_mb": 2234, "vram_total_mb": 12288},
        {"type": "intel", "model": "Intel UHD Graphics 770", "load": None,
         "vram_percent": None, "vram_used_mb": None, "vram_total_mb": None},
    ][:n]


def activity_doc():
    A["tick"] += 1
    k = A["tick"]
    doc = json.loads(fc.fx("activity.json"))
    doc.pop("surprise", None)
    cores = [{"core": i, "load_percent": round(20 + 40 * abs(((k + i * 3) % 14) - 7) / 7, 1), "freq_khz": 1800000 + i * 100000,
              "governor": "schedutil"} for i in range(8)]
    doc["cpu"] = {"cores": cores, "load_avg": [0.5, 0.4, 0.3],
                  "overall_percent": round(sum(c["load_percent"] for c in cores) / 8, 1)}
    doc["memory"].update(percent=round(30 + (k % 9), 1), used_mb=4800 + 100 * (k % 9), swap_total_mb=2048,
                         swap_used_mb=256, swap_percent=12.5)
    doc["npu"] = {"cores": [{"core": i, "load_percent": (k * 7 + i * 20) % 90} for i in range(3)],
                  "freq_hz": 1000000000, "type": "rknpu", "tops": 6}
    doc["thermal"] = [{"name": "soc", "temp_c": 48.5 + (k % 6)}, {"name": "npu", "temp_c": 61.0}, {"name": "gpu", "temp_c": 74.2}]
    doc["zram"] = [{"device": "zram0", "orig_mb": 2000, "compr_mb": 700, "ratio": 2.86}]
    doc["network"] = [{"name": "eth0", "rx_bps": 1200000 + 200000 * (k % 5), "tx_bps": 90000 + 10000 * (k % 4), "rx_total": 1, "tx_total": 1}]
    doc["disk"]["io_rate"] = {"read_bps": 3000000 * (k % 4), "write_bps": 500000 * (k % 6)}
    doc["processes"] = [{"pid": 4242 + i, "name": n, "user": "root" if i == 0 else "taos", "rss_mb": 900 - 100 * i,
                         "cpu_percent": 55.0 - 9 * i} for i, n in enumerate(["rkllama", "python3", "qmd", "uvicorn"])]
    g = gpus(A["gpus"])
    if g:
        doc["gpus"] = g
    else:
        doc["gpu"] = {"load": {"load_percent": 23, "freq_hz": 800000000}, "vram_percent": 40.0, "vram_used_mb": 1600,
                      "vram_total_mb": 4096, "type": "nvidia"}
        doc["hardware"]["gpu"] = {"type": "nvidia", "model": "NVIDIA GeForce GTX 1050 Ti", "vram_mb": 4096}
    return doc


def filtered(q):
    out = A["events"]
    for key in ("worker", "model", "event"):
        if q.get(key):
            out = [e for e in out if e.get(key) == q[key]]
    return sorted(out, key=lambda e: -e["seq"])


def push_event(ev):
    with lock:
        A["seq"] += 1
        row = dict(seq=A["seq"], ts=time.time(), event="model.load", model="m", worker="controller", backend="ollama",
                   duration_ms=None, tokens_in=None, tokens_out=None, token_rate=None, reason=None, detail={})
        row.update(ev)
        row["seq"] = ev.get("seq", A["seq"]) if ev.get("seq") else row["seq"]
        A["events"].append(row)
        qs = list(A["queues"])
    for q, flt in qs:
        if all(not flt.get(k) or flt[k] == row.get(k) for k in ("worker", "model", "event")):
            q.put(row)
    return row


orig_control, orig_get, orig_post = H.control, H.r_GET, H.r_POST


def control(self, verb, path):
    if path == "/__fake/act/stats":
        with lock:
            keys = ("restarts", "history_calls", "stream_total", "activity_calls", "bearer_rejected")
            d = {k: A[k] for k in keys}
            d["stream_open"] = open_streams["n"]
            d["tasks_limits"] = list(A["tasks_limits"])
            d["history_query"] = A["last_history_query"]
            d["stream_query"] = A["last_stream_query"]
        return self.send_json(d)
    if path == "/__fake/act":
        b = self.body()
        if b.get("reset"):
            reset_act()
        for k in ("admin", "gpus", "workers", "restart_body", "restart_status", "fail_activity", "history_delay_ms"):
            if k in b:
                A[k] = b[k]
        if "add_event" in b:
            push_event(b["add_event"])
        if b.get("drop_stream"):
            with lock:
                A["drop_epoch"] += 1
        if b.get("reset_events"):
            A["events"] = []
        return self.send_json({"ok": True})
    return orig_control(self, verb, path)


def r_get(self, path, q):
    if path == "/auth/me" and not self.headers.get("Authorization", "").startswith("Bearer "):
        d = json.loads(fc.fx("auth_me.json"))
        d["user"]["is_admin"] = bool(A["admin"])
        return self.send_json(d)
    if path == "/api/activity":
        A["activity_calls"] += 1
        if A["fail_activity"]:
            return self.send_json({"detail": "activity broken"}, 500)
        return self.send_json(activity_doc())
    if path == "/api/scheduler/tasks":
        lim = int(q.get("limit", 50))
        A["tasks_limits"].append(lim)
        d = json.loads(fc.fx("scheduler_tasks.json"))
        d["tasks"] = [t for t in d["tasks"] if t["status"] != "levitating"]
        d["tasks"] += [{"task_id": "t%d" % i, "capability": "chat", "submitter": "agent-%d" % i, "resource": "npu-rk3588",
                        "status": ["running", "error", "rejected", "complete"][i % 4], "elapsed_seconds": 1.5 + i}
                       for i in range(2, 14)]
        return self.send_json({"tasks": d["tasks"][:lim]})
    if path == "/api/scheduler/stats":
        d = json.loads(fc.fx("scheduler_stats.json"))
        d["resources"][0]["tier"] = 1
        d["resources"][0]["potential_capabilities"] = ["chat", "embedding"]
        return self.send_json(d)
    if path == "/api/cluster/workers":
        if A["workers"] == "reduced":
            return self.send_json(WORKERS_REDUCED)
        w = json.loads(json.dumps(WORKERS_FULL))
        w[0]["last_heartbeat"] = time.time() - 5
        w[0]["hardware"] = {"cpu": {"model": "AMD Ryzen 9 5950X @ 3.4GHz"}, "ram_mb": 65536,
                            "gpu": {"type": "nvidia", "model": "NVIDIA GeForce RTX 3090", "vram_mb": 24576}}
        return self.send_json(w if A["workers"] != "empty" else [])
    if path == "/api/activity/models":
        if self.headers.get("Authorization", "").startswith("Bearer "):
            A["bearer_rejected"] += 1
            return self.send_json({"detail": "Not authenticated"}, 401)
        A["history_calls"] += 1
        A["last_history_query"] = dict(q)
        if A["history_delay_ms"]:
            time.sleep(A["history_delay_ms"] / 1000.0)
        rows = filtered(q)[: int(q.get("limit", 100))]
        return self.send_json({"events": rows, "count": len(rows), "event_types": [
            "model.load", "model.unload", "model.evict", "model.shrink", "model.route", "request.start", "request.finish"]})
    if path == "/api/activity/models/stream":
        if self.headers.get("Authorization", "").startswith("Bearer "):
            A["bearer_rejected"] += 1
            return self.send_json({"detail": "Not authenticated"}, 401)
        return stream(self, q)
    return orig_get(self, path, q)


def stream(self, q):
    qu = queue.Queue()
    with lock:
        open_streams["n"] += 1
        A["stream_total"] += 1
        A["last_stream_query"] = dict(q)
        A["queues"].append((qu, dict(q)))
        epoch = A["drop_epoch"]
    try:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(b": connected\n\n")
        self.wfile.flush()
        last = time.time()
        while True:
            with lock:
                if A["drop_epoch"] != epoch:
                    return
            try:
                row = qu.get(timeout=0.02)
                self.wfile.write(("id: %d\ndata: %s\n\n" % (row["seq"], json.dumps(row))).encode())
                self.wfile.flush()
            except queue.Empty:
                pass
            if time.time() - last > 10:
                last = time.time()
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
            r, _, _ = select.select([self.connection], [], [], 0)
            if r and self.connection.recv(1, 2) == b"":
                return
    except (BrokenPipeError, ConnectionResetError, OSError):
        return
    finally:
        with lock:
            open_streams["n"] -= 1
            A["queues"] = [x for x in A["queues"] if x[0] is not qu]


def r_post(self, path, q):
    if path == "/api/system/ai-stack/restart":
        A["restarts"] += 1
        if not A["admin"]:
            return self.send_json({"detail": "Admin access required"}, 403)
        if A["restart_status"] != 200:
            return self.send_json({"error": "restart blew up"}, A["restart_status"])
        return self.send_json(A["restart_body"] or json.loads(fc.fx("ai_restart.json")))
    return orig_post(self, path, q)


H.control, H.r_GET, H.r_POST = control, r_get, r_post
fc.main.__globals__  # noqa: the base module's main() builds the server around H
sys.argv = [base] + [a for a in sys.argv[2:]]
fc.main()
