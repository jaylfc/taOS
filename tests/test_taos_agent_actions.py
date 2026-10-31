"""Tests for the taOS Agent action gate (tsk-gc6fbi).

Spec section 3.1: every tier-B operation is a two-step API. The agent creates
a pending action; the user confirms or declines it; the executing path
REFUSES anything not confirmed with a 409. A per-kind ``auto`` policy skips
the confirm card and records ``confirmed_by=auto``.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.events import EventBus
from taos_test_csrf import csrf_event_hooks


@pytest.fixture
def tmp_data_dir(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    return tmp_path


@pytest.fixture
def app(tmp_data_dir):
    return create_app(data_dir=tmp_data_dir)


@pytest.fixture
def executed(app):
    """Register a recording executor for the ``test.write`` kind."""
    calls: list[dict] = []

    async def _executor(action: dict) -> dict:
        calls.append(action["payload"])
        return {"ok": True}

    executors = getattr(app.state, "taos_agent_action_executors", None)
    if executors is None:
        executors = app.state.taos_agent_action_executors = {}
    executors["test.write"] = _executor
    return calls


@pytest_asyncio.fixture
async def client(app):
    stores = [
        s for s in (app.state.desktop_settings, getattr(app.state, "taos_agent_actions", None))
        if s is not None
    ]
    for store in stores:
        if store._db is not None:
            await store.close()
        await store.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    # The event bus is lifespan-owned; this client bypasses the lifespan.
    app.state.event_bus = EventBus()
    app.state._startup_complete = True
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        c.uid = uid
        yield c
    for store in stores:
        await store.close()
    await app.state.http_client.aclose()


async def _create(client, kind="test.write", payload=None):
    resp = await client.post(
        "/api/taos-agent/actions",
        json={"kind": kind, "summary": "Open a pull request", "payload": payload or {"n": 1}},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_create_returns_pending(client, executed):
    body = await _create(client)
    assert body["state"] == "pending"
    assert body["action_id"]


@pytest.mark.asyncio
async def test_create_publishes_chat_card_event(client, app, executed):
    queue = await app.state.event_bus.subscribe(f"user:{client.uid}")
    body = await _create(client)
    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event.kind == "taos_agent.action_pending"
    assert event.payload["action_id"] == body["action_id"]
    assert event.payload["summary"] == "Open a pull request"


@pytest.mark.asyncio
async def test_execute_unconfirmed_is_409(client, executed):
    body = await _create(client)
    resp = await client.post(f"/api/taos-agent/actions/{body['action_id']}/execute")
    assert resp.status_code == 409
    assert executed == []


@pytest.mark.asyncio
async def test_confirm_then_execute_runs_once(client, executed):
    body = await _create(client, payload={"n": 7})
    aid = body["action_id"]
    resp = await client.post(f"/api/taos-agent/actions/{aid}/confirm")
    assert resp.status_code == 200
    assert resp.json()["state"] == "confirmed"
    assert resp.json()["confirmed_by"] == "user"

    first = await client.post(f"/api/taos-agent/actions/{aid}/execute")
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "executed"
    second = await client.post(f"/api/taos-agent/actions/{aid}/execute")
    assert second.status_code == 409
    assert executed == [{"n": 7}]


@pytest.mark.asyncio
async def test_auto_policy_skips_card_and_records_auto(client, app, executed):
    resp = await client.put(
        "/api/taos-agent/actions/policy", json={"kind": "test.write", "policy": "auto"}
    )
    assert resp.status_code == 200, resp.text
    prefs = await app.state.desktop_settings.get_preference(client.uid, "taos_agent")
    assert prefs["action_policy"]["test.write"] == "auto"

    queue = await app.state.event_bus.subscribe(f"user:{client.uid}")
    body = await _create(client)
    assert body["state"] == "confirmed"
    assert body["confirmed_by"] == "auto"
    assert queue.empty()

    resp = await client.post(f"/api/taos-agent/actions/{body['action_id']}/execute")
    assert resp.status_code == 200
    assert executed == [{"n": 1}]


@pytest.mark.asyncio
async def test_auto_policy_is_scoped_to_the_signed_in_user(client, app, executed):
    """One account's ``auto`` policy never skips the card for another account."""
    resp = await client.put(
        "/api/taos-agent/actions/policy", json={"kind": "test.write", "policy": "auto"}
    )
    assert resp.status_code == 200, resp.text

    code = app.state.auth.add_user_invite("bob", "admin")
    app.state.auth.complete_invite("bob", code, "Bob", "", "bobpass123")
    bob = app.state.auth.find_user("bob")
    bob_session = app.state.auth.create_session(user_id=bob["id"], long_lived=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": bob_session},
        event_hooks=csrf_event_hooks(),
    ) as bob_client:
        body = await _create(bob_client)
    assert body["state"] == "pending"
    assert body["confirmed_by"] is None


@pytest.mark.asyncio
async def test_decline_records_row_and_never_executes(client, app, executed):
    body = await _create(client)
    aid = body["action_id"]
    resp = await client.post(f"/api/taos-agent/actions/{aid}/decline")
    assert resp.status_code == 200
    assert resp.json()["state"] == "declined"

    row = await app.state.taos_agent_actions.get(aid)
    assert row["state"] == "declined"
    assert row["confirmed_by"] == "declined"

    resp = await client.post(f"/api/taos-agent/actions/{aid}/execute")
    assert resp.status_code == 409
    # A declined action can no longer be confirmed either.
    resp = await client.post(f"/api/taos-agent/actions/{aid}/confirm")
    assert resp.status_code == 409
    assert executed == []


@pytest.mark.asyncio
async def test_confirm_requires_a_human_session(client, app, executed):
    """The agent's own credential can create an action but never confirm it."""
    body = await _create(client)
    token = app.state.auth.mint_agent_local_token("test-agent")
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bare:
        for verb in ("confirm", "decline"):
            resp = await bare.post(
                f"/api/taos-agent/actions/{body['action_id']}/{verb}", headers=headers
            )
            assert resp.status_code == 403, (verb, resp.text)
        resp = await bare.put(
            "/api/taos-agent/actions/policy",
            json={"kind": "test.write", "policy": "auto"},
            headers=headers,
        )
        assert resp.status_code == 403
    row = await app.state.taos_agent_actions.get(body["action_id"])
    assert row["state"] == "pending"


@pytest.mark.asyncio
async def test_unknown_action_is_404(client, executed):
    resp = await client.post("/api/taos-agent/actions/nope/confirm")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bad_policy_value_is_422(client):
    resp = await client.put(
        "/api/taos-agent/actions/policy", json={"kind": "test.write", "policy": "always"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_action_is_executing_while_executor_runs(client, app):
    """The row reads ``executing`` during the write and ``executed`` only after it."""
    seen: list[str] = []

    async def _executor(action: dict) -> dict:
        row = await app.state.taos_agent_actions.get(action["id"])
        seen.append(row["state"])
        return {"ok": True}

    app.state.taos_agent_action_executors["test.slow"] = _executor
    body = await _create(client, kind="test.slow")
    aid = body["action_id"]
    await client.post(f"/api/taos-agent/actions/{aid}/confirm")
    resp = await client.post(f"/api/taos-agent/actions/{aid}/execute")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "executed"
    assert seen == ["executing"]
    row = await app.state.taos_agent_actions.get(aid)
    assert row["state"] == "executed"


@pytest.mark.asyncio
async def test_interrupted_execution_is_never_replayed(client, app, executed):
    """A claim whose executor never finished stays ``executing``; execute is 409."""
    body = await _create(client)
    aid = body["action_id"]
    await client.post(f"/api/taos-agent/actions/{aid}/confirm")
    # The process stops right after the claim, before the executor returns.
    claimed = await app.state.taos_agent_actions.claim_for_execution(aid)
    assert claimed["state"] == "executing"
    resp = await client.post(f"/api/taos-agent/actions/{aid}/execute")
    assert resp.status_code == 409
    assert executed == []
    row = await app.state.taos_agent_actions.get(aid)
    assert row["state"] == "executing"
    assert row["executed_at"] is None


@pytest.mark.asyncio
async def test_pending_actions_can_be_listed_by_their_owner(client, app, executed):
    """A missed confirm card is recoverable: the owner can list pending actions."""
    first = await _create(client)
    second = await _create(client)
    await client.post(f"/api/taos-agent/actions/{second['action_id']}/decline")

    resp = await client.get("/api/taos-agent/actions", params={"state": "pending"})
    assert resp.status_code == 200, resp.text
    ids = [a["action_id"] for a in resp.json()["actions"]]
    assert ids == [first["action_id"]]

    code = app.state.auth.add_user_invite("bob", "admin")
    app.state.auth.complete_invite("bob", code, "Bob", "", "bobpass123")
    bob = app.state.auth.find_user("bob")
    bob_session = app.state.auth.create_session(user_id=bob["id"], long_lived=True)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": bob_session},
        event_hooks=csrf_event_hooks(),
    ) as bob_client:
        resp = await bob_client.get("/api/taos-agent/actions", params={"state": "pending"})
    assert resp.status_code == 200
    assert resp.json()["actions"] == []
