"""tsk-f75p3z: GET /api/decisions and GET /api/notifications must accept the
agent registry JWT instead of rejecting the agent auth mode with a 401.

Measured on the live controller: both list routes answered 200 to a session
cookie and 401 to ``Authorization: Bearer <registry JWT>``, so every agent
that sources its credential (TAOS_TOKEN) was locked out of the
human-in-the-loop channel.  The 401 is asserted explicitly so a future auth
refactor cannot silently reintroduce the rejection.

Contract (narrowest sensible, mirrors _authorize_task_actor's resolution: an
authenticated session wins, otherwise the registry JWT must carry the scope):
  * GET /api/decisions with a registry JWT (decisions_write) lists only the
    decisions THAT agent raised, scoped to its granted projects (the same set
    GET /api/decisions/agent returns).  Never the user's inbox.
  * GET /api/notifications with a registry JWT (notifications_write) lists only
    the notifications THAT agent posted (source ``agent:<canonical_id>``).
    Never the user's other notifications.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


async def _mint_agent(app, project_id, scopes, handle="@taOS-dev"):
    """Register an active agent, grant it *scopes* for *project_id*, return
    (canonical_id, bearer_token). project_id=None grants globally."""
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    for store in (registry, grants):
        if store._db is None:
            await store.init()
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="claude-code",
        display_name="taOS dev",
        allow_reserved=True,
        origin="internal",
        handle=handle,
    )
    cid = rec["canonical_id"]
    if rec.get("status") != "active":
        await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(
        cid, priv, user_id="u", framework="claude-code", project_id=project_id
    )
    return cid, token


def _agent_client(app, token):
    """Cookieless client that authenticates only via the agent bearer token."""
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


# -- decisions ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_decisions_accepts_registry_jwt(client):
    """GET /api/decisions with a decisions_write registry JWT is not a 401 and
    returns only that agent's own decisions, not the user's inbox."""
    app = client._transport.app
    cid, token = await _mint_agent(app, None, ("decisions_write",))

    # A decision the human raised (not by this agent): must stay invisible.
    human = await client.post(
        "/api/decisions",
        json={"from_agent": "@someone-else", "question": "human q", "type": "approve_deny"},
    )
    assert human.status_code == 200, human.text

    async with _agent_client(app, token) as ac:
        created = await ac.post(
            "/api/decisions",
            json={"from_agent": "spoofed", "question": "agent q", "type": "approve_deny"},
        )
        assert created.status_code == 200, created.text
        resp = await ac.get("/api/decisions")

    assert resp.status_code != 401, (
        "registry JWT rejected by GET /api/decisions (tsk-f75p3z regression): " + resp.text
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    ids = {d["id"] for d in items}
    assert created.json()["id"] in ids
    assert human.json()["id"] not in ids
    assert all(d["from_agent"] == cid for d in items)


@pytest.mark.asyncio
async def test_list_decisions_registry_jwt_honours_status_filter(client):
    """The status query param still filters on the agent path."""
    app = client._transport.app
    _cid, token = await _mint_agent(app, None, ("decisions_write",))
    async with _agent_client(app, token) as ac:
        created = await ac.post(
            "/api/decisions",
            json={"from_agent": "x", "question": "q", "type": "approve_deny"},
        )
        assert created.status_code == 200, created.text
        pending = await ac.get("/api/decisions", params={"status": "pending"})
        answered = await ac.get("/api/decisions", params={"status": "answered"})
    assert pending.status_code == 200, pending.text
    assert created.json()["id"] in {d["id"] for d in pending.json()["items"]}
    assert answered.status_code == 200, answered.text
    assert answered.json()["items"] == []


@pytest.mark.asyncio
async def test_list_decisions_registry_jwt_without_scope_is_403(client):
    """A valid agent token lacking decisions_write is refused (403), not given
    the user's decisions."""
    app = client._transport.app
    _cid, token = await _mint_agent(app, None, ("project_tasks",))
    async with _agent_client(app, token) as ac:
        resp = await ac.get("/api/decisions")
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_list_decisions_garbage_bearer_still_401(client):
    """A Bearer that is not a valid registry JWT still 401s."""
    app = client._transport.app
    async with _agent_client(app, "not-a-jwt") as ac:
        resp = await ac.get("/api/decisions")
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_agent_created_decision_records_agent_as_author_and_notifies(client):
    """Acceptance 2 + 4: an agent creating a decision is recorded as the author
    (its canonical_id, not a human account, not the body's from_agent), and the
    create emits a "Decision needed" notification attributed to that agent."""
    app = client._transport.app
    admin_id = app.state.auth.find_user("admin")["id"]
    cid, token = await _mint_agent(app, None, ("decisions_write",))
    async with _agent_client(app, token) as ac:
        resp = await ac.post(
            "/api/decisions",
            json={"from_agent": "spoofed-human", "question": "deploy?", "type": "approve_deny"},
        )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["from_agent"] == cid
    assert d["user_id"] == admin_id  # the decider, not the author

    notifs = await app.state.notifications.list(limit=50)
    hits = [
        n for n in notifs
        if n["source"] == "decisions" and (n.get("data") or {}).get("decision_id") == d["id"]
    ]
    assert len(hits) == 1, notifs
    assert hits[0]["title"] == "Decision needed"
    assert hits[0]["data"]["from_agent"] == cid


# -- notifications -----------------------------------------------------------


@pytest.mark.asyncio
async def test_list_notifications_accepts_registry_jwt(client):
    """GET /api/notifications with a notifications_write registry JWT is not a
    401 and returns only the notifications that agent posted."""
    app = client._transport.app
    cid, token = await _mint_agent(app, None, ("notifications_write",))

    # Something already in the user's bell that the agent must not see.
    await app.state.notifications.add(
        title="private", message="user-only", level="info", source="system",
    )

    async with _agent_client(app, token) as ac:
        posted = await ac.post(
            "/api/notifications",
            json={"title": "agent says", "message": "hello", "level": "info"},
        )
        assert posted.status_code == 200, posted.text
        resp = await ac.get("/api/notifications")

    assert resp.status_code != 401, (
        "registry JWT rejected by GET /api/notifications (tsk-f75p3z regression): " + resp.text
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert [n["title"] for n in items] == ["agent says"]
    assert all(n["source"] == f"agent:{cid}" for n in items)


@pytest.mark.asyncio
async def test_list_notifications_registry_jwt_without_scope_is_403(client):
    app = client._transport.app
    _cid, token = await _mint_agent(app, None, ("project_tasks",))
    async with _agent_client(app, token) as ac:
        resp = await ac.get("/api/notifications")
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_list_notifications_session_path_unchanged(client):
    """The session caller still sees its bell (broadcasts included)."""
    app = client._transport.app
    await app.state.notifications.add(
        title="for the user", message="m", level="info", source="system",
    )
    resp = await client.get("/api/notifications")
    assert resp.status_code == 200, resp.text
    assert "for the user" in [n["title"] for n in resp.json()]


@pytest.mark.asyncio
async def test_list_decisions_registry_jwt_project_filter_applies_before_limit(client):
    """With grants on two projects, ?project_id=B finds an older B decision even
    when more than 500 newer decisions sit in project A (the filter is pushed
    into the store query, so the row limit applies after it)."""
    app = client._transport.app
    cid, token = await _mint_agent(app, "proj-a", ("decisions_write",))
    await app.state.agent_grants.add_grant(cid, "decisions_write", project_id="proj-b")
    store = app.state.decision_store
    old_b = await store.create(
        from_agent=cid, question="b q", type="approve_deny", project_id="proj-b"
    )
    for i in range(501):
        await store.create(
            from_agent=cid, question=f"a q {i}", type="approve_deny", project_id="proj-a"
        )
    async with _agent_client(app, token) as ac:
        resp = await ac.get("/api/decisions", params={"project_id": "proj-b"})
        other = await ac.get("/api/decisions", params={"project_id": "proj-c"})
    assert resp.status_code == 200, resp.text
    assert [d["id"] for d in resp.json()["items"]] == [old_b["id"]]
    assert other.status_code == 200, other.text
    assert other.json()["items"] == []
