"""Follow-up of #3695: the "Decision needed" notification goes to the decider
only, not to every user.

Before this fix both create paths (POST /api/decisions and the
request_decision agent tool) called ``notifications.add`` with no ``user_id``,
so the row was a broadcast: it reached every user's bell, the SSE broadcast
channel and every subscribed web-push device, leaking the question text to
other users on a multi-user box.  The row must carry the decider's user id
(``actor.decider_user_id``: the project owner, the instance admin for an
OS-level decision, or the session user).
"""
import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


async def _mint_agent(app, scopes):
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
        handle="@taOS-dev",
    )
    cid = rec["canonical_id"]
    if rec.get("status") != "active":
        await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=None)
    token = mint_registry_token(cid, priv, user_id="u", framework="claude-code", project_id=None)
    return cid, token


async def _decision_notifications(app, decision_id):
    rows = await app.state.notifications.list(limit=50)
    return [
        n for n in rows
        if n["source"] == "decisions" and (n.get("data") or {}).get("decision_id") == decision_id
    ]


@pytest.mark.asyncio
async def test_agent_decision_notification_is_scoped_to_decider(client):
    app = client._transport.app
    admin_id = app.state.auth.find_user("admin")["id"]
    _cid, token = await _mint_agent(app, ("decisions_write",))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        resp = await ac.post(
            "/api/decisions",
            json={"from_agent": "ignored", "question": "secret plan?", "type": "approve_deny"},
        )
    assert resp.status_code == 200, resp.text
    d = resp.json()

    hits = await _decision_notifications(app, d["id"])
    assert len(hits) == 1, hits
    assert hits[0]["user_id"] == admin_id, "decision notification was broadcast to every user"

    other_bell = await app.state.notifications.list(limit=50, user_id="some-other-user")
    assert all((n.get("data") or {}).get("decision_id") != d["id"] for n in other_bell), other_bell


@pytest.mark.asyncio
async def test_session_decision_notification_is_scoped_to_decider(client):
    app = client._transport.app
    admin_id = app.state.auth.find_user("admin")["id"]
    resp = await client.post(
        "/api/decisions",
        json={"from_agent": "@helper", "question": "pick one", "type": "approve_deny"},
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()

    hits = await _decision_notifications(app, d["id"])
    assert len(hits) == 1, hits
    assert hits[0]["user_id"] == admin_id, "decision notification was broadcast to every user"
    assert hits[0]["user_id"] == d["user_id"]
