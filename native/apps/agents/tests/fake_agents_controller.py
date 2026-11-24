#!/usr/bin/env python3
"""The foundation's fake controller, extended for the Agents app (stdlib only).

Adds, on top of native/testing/fake_controller.py:
  GET  /api/agents, /api/agents/containers, /api/agents/registry,
       /api/agents/<name>/trace, /api/taos-agent/config, /api/cluster/workers,
       /auth/me (is_admin follows the `admin` control);
  POST /api/agents/<name>/{start,stop,restart,pause,resume}: changes the
       container the way the controller does, records the call, and answers
       with a configured outcome when one is set (403, 409, 500, a stop that
       did not stop).
Controls (POST /__fake/config): {"agents": "seed"|"empty"}, {"admin": bool},
{"outcome": {"<name>/<action>": {"status": 403, "body": {...}}}},
{"agent_delay_ms": N} (lifecycle answers this late).
GET /__fake/calls returns the recorded lifecycle POSTs: [{"path"}].
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "testing"))
import fake_controller as fc  # noqa: E402


def agent(name, display="", framework="openclaw", model="qwen3-8b", status="running", paused=False,
          remote="", owner="u-alex", vectors=0):
    return dict(name=name, display_name=display, host="10.0.0.%d" % (10 + abs(hash(name)) % 80), color="#3b82f6",
                emoji="", status=status, vectors=vectors, framework=framework, model=model, paused=paused,
                registry_canonical_id="%s-20261101-100000" % name, remote=remote, _owner=owner,
                llm_key="secret-never-shown")


def seed():
    t = time.time()
    fc.state["admin"] = False
    fc.state["agents_calls"] = []
    fc.state["outcome"] = {}
    fc.state["agent_delay_ms"] = 0
    fc.state["list_fail"] = 0
    fc.state["agent_rows"] = [
        agent("researcher", "Researcher", "openclaw", "qwen3-8b", vectors=1204),
        agent("scribe", "Scribe", "hermes", "gemma-3", paused=True, remote="pixel", owner="u-amy"),
        agent("builder", "Builder", "opencode", "", status="stopped", remote="ghost-box"),
        agent("analyst", "Analyst", "openclaw", "qwen3-8b", status="error"),
        agent("courier", "Courier", "hermes", "gemma-3", remote="pixel", owner="u-amy"),
    ]
    fc.state["containers"] = {
        "researcher": "Running", "scribe": "Frozen", "builder": "Stopped", "analyst": "Error", "courier": "Running",
    }
    fc.state["workers"] = [
        dict(name="pixel", status="online", kind="phone", tier_id="arm-phone", last_heartbeat=t),
        dict(name="ghost-box", status="offline", kind="worker"),
    ]
    fc.state["trace"] = [
        dict(id="e2", kind="chat.reply", created_at=t - 120, model="qwen3-8b", error=None),
        dict(id="e1", kind="tool.call", created_at=t - 900, model="qwen3-8b", error="timeout"),
    ]


def public(a):
    return {k: v for k, v in a.items() if not k.startswith("_") and k != "llm_key"}


class H(fc.H):
    def record(self, path):
        fc.state.setdefault("agents_calls", []).append(dict(path=path))

    def r_GET(self, path, q):
        st = fc.state
        if path == "/__fake/calls":
            return self.send_json(st.get("agents_calls", []))
        if path == "/auth/me" and self.headers.get("Authorization", "") == "":
            return self.send_json({"user": {"id": "usr_1", "username": "alex", "is_admin": bool(st.get("admin"))}})
        if path == "/api/agents" and st.get("list_fail"):
            return self.send_json({"detail": "forbidden"}, st["list_fail"])
        if path == "/api/agents":
            return self.send_json([public(a) for a in st["agent_rows"]])
        if path == "/api/agents/containers":
            return self.send_json([
                dict(name="taos-agent-" + n, agent_name=n, status=s, ip="10.1.0.5" if s == "Running" else None,
                     memory_mb=512.0 if s == "Running" else None, cpu_cores=2)
                for n, s in st["containers"].items() if any(a["name"] == n for a in st["agent_rows"])])
        if path == "/api/agents/registry":
            return self.send_json([dict(canonical_id=a["registry_canonical_id"], user_id=a["_owner"], status="active")
                                   for a in st["agent_rows"]])
        if path == "/api/cluster/workers":
            return self.send_json(st["workers"])
        if path == "/api/taos-agent/config":
            return self.send_json(dict(model="qwen3-8b", permitted_models=["qwen3-8b"], persona="", framework="opencode",
                                       key_masked="sk-...abcd", system=True))
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "agents"] and len(parts) == 4 and parts[3] == "trace":
            return self.send_json(dict(agent_name=parts[2], schema_version=1, events=st["trace"]))
        if parts[:2] == ["api", "agents"] and len(parts) == 3 and parts[2] not in ("containers", "registry", "archived"):
            a = next((x for x in st["agent_rows"] if x["name"] == parts[2]), None)
            if a is None:
                return self.send_json({"error": "Agent '%s' not found" % parts[2]}, 404)
            return self.send_json(public(a))
        return super().r_GET(path, q)

    def r_POST(self, path, q):
        st = fc.state
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "agents"] and len(parts) == 4 and parts[3] in ("start", "stop", "restart", "pause", "resume"):
            name, action = parts[2], parts[3]
            self.record(path)
            if st.get("agent_delay_ms"):
                time.sleep(st["agent_delay_ms"] / 1000.0)
            out = st["outcome"].get("%s/%s" % (name, action))
            if out:
                return self.send_json(out.get("body", {}), out.get("status", 200))
            a = next((x for x in st["agent_rows"] if x["name"] == name), None)
            if a is None:
                return self.send_json({"error": "Agent '%s' not found" % name}, 404)
            if action == "pause" and st["containers"].get(name) != "Running":
                return self.send_json({"error": "Agent '%s' is not running (container is %s)" % (name, st["containers"].get(name))}, 409)
            new = dict(start="Running", restart="Running", stop="Stopped", pause="Frozen", resume="Running")[action]
            st["containers"][name] = new
            if action == "pause":
                a["paused"] = True
            if action in ("resume", "start"):
                a["paused"] = False
            a["status"] = "stopped" if new == "Stopped" else "running"
            if action == "stop":
                return self.send_json(dict(prepare_report={}, stop_result=dict(success=True, output=""), force_killed=False))
            return self.send_json(dict(success=True))
        return super().r_POST(path, q)


_orig_control = fc.H.control


def control(self, verb, path):
    if path == "/__fake/calls":
        return self.send_json(fc.state.get("agents_calls", []))
    body = self.body()
    handled = False
    with fc.lock:
        if body.get("agents") == "seed":
            seed(); handled = True
        elif body.get("agents") == "empty":
            seed(); fc.state["agent_rows"] = []; fc.state["containers"] = {}; handled = True
        for k in ("admin", "outcome", "agent_delay_ms", "list_fail"):
            if k in body:
                fc.state[k] = body[k]; handled = True
        if "set_container" in body:
            fc.state["containers"][body["set_container"]["name"]] = body["set_container"]["status"]; handled = True
    return _orig_control(self, verb, path)


H.control = control

_orig_fx = fc.fx


def fx(name):
    raw = _orig_fx(name)
    if name == "auth_login.json":
        import json
        d = json.loads(raw)
        d["user"]["is_admin"] = bool(fc.state.get("admin"))
        return json.dumps(d)
    return raw


fc.fx = fx


def main():
    import argparse
    from http.server import ThreadingHTTPServer
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    fc.reset()
    seed()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    srv.daemon_threads = True
    print("LISTENING %d" % srv.server_address[1], flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
