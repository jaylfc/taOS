#!/usr/bin/env python3
"""The foundation's fake controller, extended for the Notifications and Decisions
apps. It lives in this branch's test directory; native/testing is not touched.

Adds, on top of native/testing/fake_controller.py:
  * notifications seeded with pinned consent rows, a sign-in row and plain rows
    (POST /__fake/config {"seed": true});
  * GET /api/agents/scope-vocabulary, GET /api/projects, POST /api/projects;
  * GET /api/agents/auth-requests and /scope-requests (pending);
  * the consent decision routes, answered like the controller (approve/deny of an
    access request, of a scope request, the sign-in code), recording the path and
    the exact JSON body of every POST so a test can assert what was sent;
  * decisions: list by status, answer (with the controller's own {"error": ...}
    refusals), note, history.
GET /__fake/calls returns the recorded POSTs: [{"path", "body", "has_body"}].
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "testing"))
import fake_controller as fc  # noqa: E402

PROJECT_SCOPES = ["project_tasks", "files_write", "canvas_write"]
VALID_SCOPES = PROJECT_SCOPES + ["memory_read", "cluster_read", "notifications_write"]


def now():
    return time.time()


def seed():
    t = now()
    fc.state["notifications"] = [
        dict(id=1, timestamp=t - 30, level="info", title="Access request",
             message="agent-alpha is requesting memory_read, project_tasks on proj-x", read=False,
             source="auth_requests", pinned=True, user_id=None,
             data=dict(request_id="req-1", identity_claim="agent-alpha", framework="openclaw",
                       requested_scopes=["memory_read", "project_tasks"], project_id="p1",
                       human_duration="for 1 hour")),
        dict(id=2, timestamp=t - 90, level="info", title="Scope request",
             message="agent-beta is requesting files_write on proj-y", read=False,
             source="agent_scope_requests", pinned=True, user_id=None,
             data=dict(request_id="sreq-1", canonical_id="agent-beta",
                       requested_scopes=["files_write", "memory_read"], project_id="p2")),
        dict(id=3, timestamp=t - 120, level="warning", title="Sign in needed",
             message="Claude needs a sign-in", read=False, source="auth_broker", user_id=None,
             data=dict(request_id="br-1", url="https://claude.ai/login?code=x", agent="agent-gamma", prompt="")),
        dict(id=4, timestamp=t - 300, level="success", title="App installed", message="Notes is ready",
             read=False, source="app.installed", user_id=None, data=None),
        dict(id=5, timestamp=t - 4000, level="error", title="Backend down", message="llama.cpp stopped",
             read=True, source="backend.down", user_id=None, data=None),
        dict(id=6, timestamp=t - 90000, level="info", title="Update available", message="taOS 1.2",
             read=True, source="system.update", user_id=None, data=None),
    ]
    fc.state["auth_requests"] = [
        dict(id="req-1", identity_claim="agent-alpha", framework="openclaw",
             requested_scopes=["memory_read", "project_tasks"], reason="needs memory and tasks",
             project_id="p1", status="pending", human_duration="for 1 hour"),
        dict(id="req-2", identity_claim="agent-delta", framework="hermes", requested_scopes=["cluster_read"],
             reason=None, project_id=None, status="pending", human_duration=""),
    ]
    fc.state["scope_requests"] = [
        dict(id="sreq-1", canonical_id="agent-beta", agent_display_name="Beta",
             requested_scopes=["files_write", "memory_read"], project_id="p2", reason="needs files",
             status="pending", created_ts=t - 90),
    ]
    fc.state["projects"] = [dict(id="p1", name="Alpha", slug="alpha"), dict(id="p2", name="Beta", slug="beta")]
    fc.state["decisions"] = [
        dict(id="d-1", from_agent="agent-alpha", project_id="p1", question="Which database?", type="single_select",
             options=[dict(label="Postgres", value="pg", recommended=True, rationale="Already running"),
                      dict(label="SQLite", value="sqlite")],
             context="Pick one for the new service.", priority="blocking", status="pending", answer=None,
             created_at=t - 60, notes=[], metadata={}, parent_decision_id=None),
        dict(id="d-2", from_agent="agent-beta", project_id=None, question="Which regions?", type="multi_select",
             options=[dict(label="EU", value="eu"), dict(label="US", value="us")], context=None,
             priority="normal", status="pending", answer=None, created_at=t - 600, notes=[], metadata={},
             parent_decision_id=None),
        dict(id="d-3", from_agent="agent-gamma", project_id=None, question="Run the migration?",
             type="approve_deny", options=[], context=None, priority="normal", status="pending", answer=None,
             created_at=t - 900, notes=[dict(id="n1", text="Backup done", author="agent-gamma", source="agent",
                                              created_at=t - 800)],
             metadata=dict(kind="execution_gate", agent_name="agent-gamma", action_class="write", tool="shell"),
             parent_decision_id=None),
        dict(id="d-4", from_agent="agent-delta", project_id=None, question="Describe the rollout plan",
             type="free_text", options=[], context=None, priority="normal", status="pending", answer=None,
             created_at=t - 1200, notes=[], metadata={}, parent_decision_id=None),
        dict(id="d-5", from_agent="agent-alpha", project_id=None, question="Old question", type="single_select",
             options=[dict(label="Yes", value="yes"), dict(label="No", value="no")], context=None,
             priority="normal", status="answered", answer=dict(value="yes", answered_by="jay", answered_at=t - 5000,
                                                              source="web", other_value=None, note="ok"),
             created_at=t - 9000, notes=[], metadata={}, parent_decision_id="d-0"),
        dict(id="d-6", from_agent="agent-beta", project_id=None, question="Withdrawn question", type="free_text",
             options=[], context=None, priority="normal", status="withdrawn", answer=None, created_at=t - 9500,
             notes=[], metadata={}, parent_decision_id=None, withdraw_reason="No longer needed",
             withdrawn_at=t - 9400, withdrawn_by="agent-beta"),
    ]
    fc.state["calls"] = []


class H(fc.H):
    def record(self, path):
        n = int(self.headers.get("Content-Length") or 0)
        fc.state.setdefault("calls", []).append(dict(path=path, body=self.body(), has_body=n > 0))

    def push(self, obj):
        for q in list(fc.state["sse_queues"]):
            q.put(("data", obj))

    def r_GET(self, path, q):
        st = fc.state
        if path == "/__fake/calls":
            return self.send_json(st.get("calls", []))
        if path == "/api/agents/scope-vocabulary":
            return self.send_json(dict(valid_scopes=VALID_SCOPES, project_scopes=PROJECT_SCOPES))
        if path == "/api/projects":
            return self.send_json(dict(items=st.get("projects", [])))
        if path == "/api/agents/auth-requests":
            return self.send_json(dict(requests=[r for r in st.get("auth_requests", []) if r["status"] == "pending"]))
        if path == "/api/agents/scope-requests":
            return self.send_json(dict(requests=[r for r in st.get("scope_requests", []) if r["status"] == "pending"]))
        if path == "/api/decisions":
            status = q.get("status")
            rows = [d for d in st["decisions"] if not status or d["status"] == status]
            return self.send_json(dict(items=rows))
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "decisions"] and len(parts) == 4 and parts[3] == "history":
            return self.send_json(dict(items=[dict(id="d-0", from_agent="agent-alpha", question="First draft",
                                                   status="superseded", type="single_select", options=[],
                                                   created_at=now() - 20000, answer=None, notes=[])]))
        return super().r_GET(path, q)

    def r_POST(self, path, q):
        st = fc.state
        parts = path.strip("/").split("/")
        if path == "/api/projects":
            self.record(path)
            b = self.body()
            p = dict(id="p%d" % (len(st["projects"]) + 1), name=b.get("name", ""), slug=b.get("slug", ""))
            st["projects"].append(p)
            return self.send_json(p, 201)
        if parts[:3] == ["api", "agents", "auth-requests"] and len(parts) == 5 and parts[4] in ("approve", "deny"):
            self.record(path)
            req = next((r for r in st["auth_requests"] if r["id"] == parts[3]), None)
            if not req or req["status"] != "pending":
                return self.send_json({"detail": "request was decided concurrently; check current status"}, 409)
            req["status"] = "approved" if parts[4] == "approve" else "denied"
            self.retire("auth_requests", parts[3])
            out = {"ok": True}
            if parts[4] == "approve":
                out["lead_assigned"] = bool(self.body().get("is_lead"))
            return self.send_json(out)
        if parts[:3] == ["api", "agents", "registry"] and len(parts) == 7 and parts[4] == "scope-requests":
            self.record(path)
            req = next((r for r in st["scope_requests"] if r["id"] == parts[5]), None)
            if not req or req["status"] != "pending":
                return self.send_json({"detail": "request was decided concurrently; check current status"}, 409)
            if parts[6] == "deny" and not self.body().get("reason"):
                return self.send_json({"detail": "reason required"}, 422)
            req["status"] = "approved" if parts[6] == "approve" else "denied"
            self.retire("agent_scope_requests", parts[5])
            return self.send_json({"ok": True})
        if parts[:3] == ["api", "auth-broker", "requests"] and len(parts) == 5 and parts[4] == "code":
            self.record(path)
            if self.body().get("code") == "bad":
                return self.send_json({"detail": "code rejected"}, 400)
            self.retire("auth_broker", parts[3])
            return self.send_json({"ok": True})
        if parts[:2] == ["api", "decisions"] and len(parts) == 4 and parts[3] in ("answer", "note"):
            self.record(path)
            d = next((x for x in st["decisions"] if x["id"] == parts[2]), None)
            if not d:
                return self.send_json({"error": "not found"}, 404)
            b = self.body()
            if parts[3] == "note":
                if not str(b.get("text", "")).strip():
                    return self.send_json({"error": "text must not be empty"}, 400)
                d["notes"].append(dict(id="n%d" % (len(d["notes"]) + 1), text=b["text"], author="jay", source="native",
                                       created_at=now()))
                self.push({"kind": "decision.note", "id": d["id"]})
                return self.send_json(d)
            if d["status"] != "pending":
                return self.send_json({"error": "already answered or not pending"}, 409)
            v = b.get("value")
            if isinstance(v, list) and b.get("other_value") is None and not v:
                return self.send_json({"error": "answer requires at least one option or other_value"}, 400)
            d["status"] = "answered"
            d["answer"] = dict(value=v, answered_by="jay", answered_at=now(), source=b.get("source"),
                               other_value=b.get("other_value"), note=b.get("note"))
            self.push({"kind": "decision.answered", "id": d["id"]})
            return self.send_json(d)
        return super().r_POST(path, q)

    def retire(self, source, request_id):
        st = fc.state
        st["notifications"] = [n for n in st["notifications"]
                               if not (n.get("source") == source and (n.get("data") or {}).get("request_id") == request_id)]
        self.push({"kind": "notification.added", "id": "retire"})


_orig_control = fc.H.control


def control(self, verb, path):
    if path == "/__fake/calls":
        return self.send_json(fc.state.get("calls", []))
    body = self.body()
    if body.get("seed"):
        with fc.lock:
            seed()
        self.send_json({"ok": True})
        return
    if "mutate_decision" in body:
        m = body["mutate_decision"]
        with fc.lock:
            for d in fc.state["decisions"]:
                if d["id"] == m.get("id"):
                    d.update({k: v for k, v in m.items() if k != "id"})
        self.send_json({"ok": True})   # no event: the app's card is now stale
        return
    if "add_decision" in body:
        with fc.lock:
            fc.state["decisions"].insert(0, body["add_decision"])
        self.push({"kind": "decision.answered", "id": body["add_decision"].get("id")})
        self.send_json({"ok": True})
        return
    return _orig_control(self, verb, path)


H.control = control


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
