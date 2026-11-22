"""Per-agent routes require that the caller owns the agent.

Real app, real AuthMiddleware and CSRF. Two member sessions (alice, bob) each
own one agent; the admin session, the host token, agent-bound local tokens and
an anonymous client are the other callers. For every route group:

  other member 403 (and nothing changed or leaked), owner allowed, admin
  allowed, host token allowed, bound token allowed for its OWN agent only,
  anonymous 401.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks
import tinyagentos.routes.framework as fwmod
import tinyagentos.routes.recycle as recyclemod
from tinyagentos.frameworks import FRAMEWORKS

SECRET = "BOB-CHANNEL-SECRET"


def _add_member(app, name):
    a = app.state.auth
    code = a.add_user_invite(name, invited_by_username="admin")
    a.complete_invite(username=name, invite_code=code, full_name=name,
                      email=f"{name}@t.local", password=f"{name}-pass-123")
    return a.find_user(name)["id"]


def _session(app, uid):
    tok = app.state.auth.create_session(user_id=uid, long_lived=True)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       cookies={"taos_session": tok}, event_hooks=csrf_event_hooks())


def _bearer(app, tok):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {tok}"})


def _anon(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       event_hooks=csrf_event_hooks())


@pytest_asyncio.fixture
async def w(client):
    app = client._transport.app
    admin = app.state.auth.find_user("admin")["id"]
    alice, bob = _add_member(app, "alice"), _add_member(app, "bob")
    reg = app.state.agent_registry
    if reg._db is None:
        await reg.init()
    for name, owner in (("alice-agent", alice), ("bob-agent", bob)):
        rec = await reg.register(framework="openclaw", display_name=name, handle=name, user_id=owner)
        app.state.config.agents.append({
            "name": name, "registry_canonical_id": rec["canonical_id"],
            "framework": "openclaw", "framework_update_status": "idle"})
    app.state._startup_complete = True
    return {"app": app, "alice": alice, "bob": bob, "admin": admin}


def _clients(w):
    app = w["app"]
    return {
        "alice": _session(app, w["alice"]), "bob": _session(app, w["bob"]),
        "admin": _session(app, w["admin"]),
        "host": _bearer(app, app.state.auth.get_local_token()),
        "alice_tok": _bearer(app, app.state.auth.mint_agent_local_token("alice-agent")),
        "bob_tok": _bearer(app, app.state.auth.mint_agent_local_token("bob-agent")),
        "anon": _anon(app),
    }


# ---------------------------------------------------------------- channels

@pytest.mark.asyncio
async def test_channels(w):
    app, c = w["app"], _clients(w)
    store = app.state.channels
    await store.add("bob-agent", "web-chat", {"token": SECRET})
    await store.add("alice-agent", "web-chat", {"token": "ALICE-OK"})
    bob_ch = (await store.list_for_agent("bob-agent"))[0]["id"]

    # other member: refused, nothing leaks, nothing changes
    r = await c["alice"].get("/api/channels/agent/bob-agent")
    assert r.status_code == 403 and SECRET not in r.text
    r = await c["alice"].get("/api/channels")
    assert r.status_code == 200 and SECRET not in r.text
    assert {x["agent_name"] for x in r.json()} == {"alice-agent"}
    r = await c["alice"].post("/api/channels", json={"agent_name": "bob-agent", "type": "web-chat",
                                                     "config": {"token": "ALICE-OVERWRITE"}})
    assert r.status_code == 403
    assert (await store.list_for_agent("bob-agent"))[0]["config"]["token"] == SECRET
    assert (await c["alice"].post(f"/api/channels/{bob_ch}/toggle", json={"enabled": False})).status_code == 403
    assert (await store.get(bob_ch))["enabled"] is True
    assert (await c["alice"].post("/api/channels/99999/toggle", json={"enabled": False})).status_code == 403
    assert (await c["alice"].delete("/api/channels/bob-agent/web-chat")).status_code == 403
    assert await store.list_for_agent("bob-agent")

    # owner, admin, host: allowed
    for who in ("bob", "admin", "host"):
        r = await c[who].get("/api/channels/agent/bob-agent")
        assert r.status_code == 200 and SECRET in r.text, who
    assert any(x["agent_name"] == "bob-agent" for x in (await c["bob"].get("/api/channels")).json())
    assert {"alice-agent", "bob-agent"} <= {x["agent_name"] for x in (await c["admin"].get("/api/channels")).json()}
    assert (await c["bob"].post(f"/api/channels/{bob_ch}/toggle", json={"enabled": False})).status_code == 200

    # bound token: own agent only
    assert (await c["bob_tok"].get("/api/channels/agent/bob-agent")).status_code == 200
    r = await c["alice_tok"].get("/api/channels/agent/bob-agent")
    assert r.status_code == 403 and SECRET not in r.text
    assert (await c["alice_tok"].post("/api/channels", json={"agent_name": "bob-agent", "type": "web-chat"})).status_code == 403
    assert {x["agent_name"] for x in (await c["alice_tok"].get("/api/channels")).json()} == {"alice-agent"}

    assert (await c["anon"].get("/api/channels/agent/bob-agent")).status_code == 401
    # owner removes
    assert (await c["bob"].delete("/api/channels/bob-agent/web-chat")).status_code == 200


# ---------------------------------------------------------------- debugger

@pytest.mark.asyncio
async def test_debugger(w):
    c = _clients(w)
    for method, path in (("get", "/agent/bob-agent/debug"), ("get", "/agent/bob-agent/debug/status"),
                         ("post", "/agent/bob-agent/debug/step"), ("post", "/agent/bob-agent/debug/continue"),
                         ("post", "/agent/bob-agent/debug/clear"), ("get", "/agent/bob-agent/debug/events")):
        r = await getattr(c["alice"], method)(path)
        assert r.status_code == 403, path
        assert (await getattr(c["alice_tok"], method)(path)).status_code == 403, path
        assert (await getattr(c["anon"], method)(path)).status_code == 401, path
    assert (await c["alice"].post("/agent/bob-agent/debug/trace", json={"type": "log"})).status_code == 403
    for who in ("bob", "admin", "host", "bob_tok"):
        assert (await c[who].get("/agent/bob-agent/debug/status")).status_code == 200, who
    # an agent pushes its own trace events with its bound token
    r = await c["bob_tok"].post("/agent/bob-agent/debug/trace", json={"type": "log", "data": {}})
    assert r.status_code == 200


# ------------------------------------------------------------------- trace

@pytest.mark.asyncio
async def test_trace(w):
    from tinyagentos.trace_store import TraceStoreRegistry
    app, c = w["app"], _clients(w)
    if app.state.trace_registry is None:
        app.state.trace_registry = TraceStoreRegistry(app.state.data_dir)
    store = await app.state.trace_registry.get("bob-agent")
    await store.record("message_in", content="BOB-TRACE")
    for path in ("/api/agents/bob-agent/trace", "/api/agents/bob-agent/otel-spans"):
        assert (await c["alice"].get(path)).status_code == 403, path
        assert (await c["alice_tok"].get(path)).status_code == 403, path
        assert (await c["anon"].get(path)).status_code == 401, path
        for who in ("bob", "admin", "host", "bob_tok"):
            assert (await c[who].get(path)).status_code == 200, (who, path)
    assert len((await c["bob"].get("/api/agents/bob-agent/trace")).json()["events"]) == 1
    # system slugs (not agents) are the install owner's: governance and cluster audit rows
    for slug in ("taos-governance", "taos-cluster-tasks"):
        assert (await c["alice"].get(f"/api/agents/{slug}/trace")).status_code == 403, slug
        assert (await c["admin"].get(f"/api/agents/{slug}/trace")).status_code == 200, slug
    # recording into another agent's trace
    body = {"agent_name": "bob-agent", "kind": "message_in", "payload": {}}
    assert (await c["alice"].post("/api/trace", json=body)).status_code == 403
    assert len((await store.list())) == 1
    assert (await c["bob"].post("/api/trace", json=body)).status_code == 200
    # a bound token is pinned to its own agent whatever the body says
    r = await c["alice_tok"].post("/api/trace", json=body)
    assert r.status_code == 200 and r.json()["agent_name"] == "alice-agent"


# ----------------------------------------------------------------- recycle

@pytest.mark.asyncio
async def test_recycle(w, monkeypatch):
    c = _clients(w)
    async def fake_list(container):
        return "ok", [{"id": "x", "original_path": f"/{container}/f", "deleted_at": "2026-01-01 00:00:00"}]
    monkeypatch.setattr(recyclemod, "_list_for_agent", fake_list)
    assert (await c["alice"].get("/api/agents/bob-agent/recycle")).status_code == 403
    assert (await c["alice"].post("/api/agents/bob-agent/recycle/restore", json={"original_path": "/a"})).status_code == 403
    assert (await c["alice"].delete("/api/agents/bob-agent/recycle/YWJj")).status_code == 403
    assert (await c["alice_tok"].get("/api/agents/bob-agent/recycle")).status_code == 403
    assert (await c["anon"].get("/api/agents/bob-agent/recycle")).status_code == 401
    for who in ("bob", "admin", "host", "bob_tok"):
        assert (await c[who].get("/api/agents/bob-agent/recycle")).status_code == 200, who
    # owner reaches the handler (container offline here is not an authorisation refusal)
    assert (await c["bob"].post("/api/agents/bob-agent/recycle/restore", json={"original_path": "/a"})).status_code != 403
    # aggregated view only lists agents the caller may act on
    got = {i["agent_name"] for i in (await c["alice"].get("/api/recycle")).json()["items"]}
    assert got == {"alice-agent"}
    got = {i["agent_name"] for i in (await c["admin"].get("/api/recycle")).json()["items"]}
    assert {"alice-agent", "bob-agent"} <= got


# ------------------------------------------------------------------ skills

@pytest.mark.asyncio
async def test_skills(w):
    app, c = w["app"], _clients(w)
    if app.state.skills._db is None:
        await app.state.skills.init()
    sid = (await c["admin"].get("/api/skills")).json()
    sid = (sid["skills"] if isinstance(sid, dict) else sid)[0]["id"]
    assert (await c["alice"].get("/api/agents/bob-agent/skills")).status_code == 403
    assert (await c["alice"].post("/api/agents/bob-agent/skills", json={"skill_id": sid})).status_code == 403
    assert (await c["alice_tok"].post("/api/agents/bob-agent/skills", json={"skill_id": sid})).status_code == 403
    assert (await c["anon"].get("/api/agents/bob-agent/skills")).status_code == 401
    assert (await c["bob"].get("/api/agents/bob-agent/skills")).json()["skills"] == []
    assert (await c["bob"].post("/api/agents/bob-agent/skills", json={"skill_id": sid})).status_code == 200
    assert (await c["alice"].delete(f"/api/agents/bob-agent/skills/{sid}")).status_code == 403
    assert len((await c["bob"].get("/api/agents/bob-agent/skills")).json()["skills"]) == 1
    for who in ("admin", "host", "bob_tok"):
        assert (await c[who].get("/api/agents/bob-agent/skills")).status_code == 200, who
    assert (await c["bob"].delete(f"/api/agents/bob-agent/skills/{sid}")).status_code == 200


# --------------------------------------------------------------- framework

@pytest.mark.asyncio
async def test_framework_update(w, monkeypatch):
    app, c = w["app"], _clients(w)
    app.state.latest_framework_versions = {"openclaw": {"tag": "v9.9.9", "sha": "x"}}
    monkeypatch.setitem(FRAMEWORKS["openclaw"], "release_source", {"type": "github"})
    m = AsyncMock()
    monkeypatch.setattr(fwmod._runner, "start_update", m)
    for who in ("alice", "bob", "alice_tok", "bob_tok"):
        r = await c[who].post("/api/agents/bob-agent/framework/update", json={})
        assert r.status_code == 403, who
    assert (await c["anon"].post("/api/agents/bob-agent/framework/update", json={})).status_code == 401
    assert not m.called
    for who in ("admin", "host"):
        for a in app.state.config.agents:
            a["framework_update_status"] = "idle"
        r = await c[who].post("/api/agents/bob-agent/framework/update", json={})
        assert r.status_code == 202, who


# ------------------------------------------------------------------ broker

@pytest.mark.asyncio
async def test_broker(w):
    app, c = w["app"], _clients(w)
    broker = app.state.broker
    pid = (await c["admin"].get("/api/broker/providers")).json()[0]["id"]
    bob_g = await broker.request_access(agent_identity="bob-agent", provider_id=pid, scopes=[], reason="x")
    alice_g = await broker.request_access(agent_identity="alice-agent", provider_id=pid, scopes=[], reason="x")

    # listing: only grants for agents the caller may act on
    ids = lambda r: {g["id"] for g in r.json()}
    assert ids(await c["alice"].get("/api/broker/grants")) == {alice_g}
    assert ids(await c["bob"].get("/api/broker/grants")) == {bob_g}
    assert ids(await c["admin"].get("/api/broker/grants")) == {bob_g, alice_g}
    assert ids(await c["host"].get("/api/broker/grants")) == {bob_g, alice_g}
    assert ids(await c["bob_tok"].get("/api/broker/grants")) == {bob_g}
    assert (await c["alice"].get("/api/broker/grants", params={"agent_identity": "bob-agent"})).status_code == 403
    assert (await c["alice_tok"].get("/api/broker/grants", params={"agent_identity": "bob-agent"})).status_code == 403
    assert (await c["anon"].get("/api/broker/grants")).status_code == 401

    # requesting for somebody else's agent is refused; for your own it works
    body = {"provider_id": pid, "agent_identity": "bob-agent"}
    assert (await c["alice"].post("/api/broker/request", json=body)).status_code == 403
    assert (await c["alice_tok"].post("/api/broker/request", json=body)).status_code == 403
    assert (await c["bob_tok"].post("/api/broker/request", json=body)).status_code == 200
    assert (await c["bob"].post("/api/broker/request", json=body)).status_code == 200

    # consent: install owner only. Not another member, not even the agent's owner,
    # and never the agent's own token (it maps to the admin user).
    for who in ("alice", "bob", "bob_tok", "alice_tok"):
        r = await c[who].post(f"/api/broker/grants/{bob_g}/approve", json={"window_seconds": 60})
        assert r.status_code == 403, who
        assert (await c[who].post(f"/api/broker/grants/{bob_g}/deny")).status_code == 403, who
    assert (await broker.store.get_grant(bob_g))["status"] == "pending"
    assert (await c["admin"].post(f"/api/broker/grants/{bob_g}/approve", json={"window_seconds": 60})).status_code == 200
    assert (await c["host"].post(f"/api/broker/grants/{alice_g}/deny")).status_code == 200

    # revoke: install owner or the agent's owner; not another member, not an agent token
    assert (await c["alice"].post(f"/api/broker/grants/{bob_g}/revoke")).status_code == 403
    assert (await c["bob_tok"].post(f"/api/broker/grants/{bob_g}/revoke")).status_code == 403
    assert (await broker.store.get_grant(bob_g))["status"] == "active"
    assert (await c["bob"].post(f"/api/broker/grants/{bob_g}/revoke")).status_code == 200


# ------------------------------------------------------------ import embed

@pytest.mark.asyncio
async def test_import_embed(w):
    app, c = w["app"], _clients(w)
    app.state.qmd_client.base_url = "http://qmd.test"
    calls = []
    def fake(req):
        calls.append(str(req.url)); return httpx.Response(200, json={})
    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    mem = app.state.agent_memory_dir
    up = await c["alice"].post("/api/import/upload", files={"file": ("n.md", b"INJECTED", "text/markdown")})
    assert up.status_code == 200
    for name in ("bob-agent", "ghost-agent"):
        assert not (mem / name).exists() or name == "bob-agent"
        r = await c["alice"].post("/api/import/embed", json={"agent_name": name, "files": ["n.md"]})
        assert r.status_code == 403, name
    assert not (mem / "ghost-agent").exists()
    assert (await c["alice_tok"].post("/api/import/embed", json={"agent_name": "bob-agent", "files": ["n.md"]})).status_code == 403
    assert (await c["anon"].post("/api/import/embed", json={"agent_name": "bob-agent", "files": ["n.md"]})).status_code == 401
    assert calls == []
    for who in ("bob", "admin", "host"):
        r = await c[who].post("/api/import/embed", json={"agent_name": "bob-agent", "files": ["n.md"]})
        assert r.status_code == 200, who
    assert len(calls) == 3
    assert (await c["alice"].post("/api/import/embed", json={"agent_name": "alice-agent", "files": ["n.md"]})).status_code == 200


@pytest.mark.asyncio
async def test_trace_post_with_deployed_agent_registry_token(w):
    """A deployed agent posts its own trace with its registry JWT (no session user)."""
    from tinyagentos.agent_registry_store import mint_registry_token
    from tinyagentos.trace_store import TraceStoreRegistry
    app = w["app"]
    if app.state.trace_registry is None:
        app.state.trace_registry = TraceStoreRegistry(app.state.data_dir)
    cid = next(a["registry_canonical_id"] for a in app.state.config.agents if a["name"] == "bob-agent")
    priv, _pub = app.state.agent_registry_keypair
    tok = mint_registry_token(cid, priv, user_id=w["bob"], framework="openclaw")
    async with _bearer(app, tok) as agent:
        r = await agent.post("/api/trace", json={"agent_name": "alice-agent", "kind": "message_in", "payload": {}})
    # pinned to its own agent whatever the body says
    assert r.status_code == 200 and r.json()["agent_name"] == "bob-agent"
