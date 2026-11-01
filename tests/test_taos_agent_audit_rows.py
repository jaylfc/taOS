"""Tests for the taOS Agent audit rows (tsk-jku5a5).

Spec section 4: trace_store kinds ``external_write`` and ``local_fix``. The
action gate records the request (pending) and the outcome (executed or
declined) as ``external_write`` rows on the ``taos-agent`` trace; the kind
registry refuses a row that is missing a required field.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.events import EventBus
from tinyagentos.trace_store import AgentTraceStore, TraceStoreRegistry
from taos_test_csrf import csrf_event_hooks

_WRITE_ROW = {
    "target": "github_pr", "action": "Open a pull request", "user_id": "u1",
    "repo": "jaylfc/taOS", "url": "", "diff_sha256": "", "confirmed_by": "user",
    "action_id": "a1", "latency_ms": 0, "state": "executed",
}


# -- kind registry -----------------------------------------------------------

@pytest.mark.asyncio
async def test_registry_rejects_external_write_missing_confirmed_by(tmp_path):
    store = AgentTraceStore(tmp_path, "taos-agent")
    payload = {k: v for k, v in _WRITE_ROW.items() if k != "confirmed_by"}
    with pytest.raises(ValueError, match="confirmed_by"):
        await store.record("external_write", payload=payload)
    await store.close()


@pytest.mark.asyncio
async def test_registry_rejects_unknown_confirmed_by(tmp_path):
    store = AgentTraceStore(tmp_path, "taos-agent")
    with pytest.raises(ValueError, match="confirmed_by"):
        await store.record("external_write", payload={**_WRITE_ROW, "confirmed_by": "agent"})
    await store.close()


@pytest.mark.asyncio
async def test_registry_accepts_complete_rows(tmp_path):
    store = AgentTraceStore(tmp_path, "taos-agent")
    await store.record("external_write", payload=_WRITE_ROW)
    await store.record(
        "local_fix",
        payload={"tool": "restart_service", "target": "qmd", "undo": "none needed", "user_id": "u1"},
    )
    with pytest.raises(ValueError, match="undo"):
        await store.record(
            "local_fix", payload={"tool": "restart_service", "target": "qmd", "user_id": "u1"}
        )
    kinds = sorted(e["kind"] for e in await store.list())
    assert kinds == ["external_write", "local_fix"]
    await store.close()


# -- the action gate writes the rows -----------------------------------------

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


@pytest_asyncio.fixture
async def client(tmp_data_dir):
    app = create_app(data_dir=tmp_data_dir)
    stores = (app.state.desktop_settings, app.state.taos_agent_actions)
    for store in stores:
        if store._db is not None:
            await store.close()
        await store.init()

    async def _executor(action: dict) -> dict:
        return {"url": "https://github.com/jaylfc/taOS/pull/9"}

    app.state.taos_agent_action_executors["github_pr"] = _executor
    # Lifespan-owned; this client bypasses the lifespan.
    app.state.event_bus = EventBus()
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=record["id"], long_lived=True)
    app.state._startup_complete = True
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        c.app = app
        c.uid = record["id"]
        yield c
    await app.state.trace_registry.close_all()
    for store in stores:
        await store.close()
    await app.state.http_client.aclose()


async def _rows(client) -> list[dict]:
    store = await client.app.state.trace_registry.get("taos-agent")
    return [e["payload"] for e in await store.list(kind="external_write")]


async def _create(client) -> str:
    resp = await client.post("/api/taos-agent/actions", json={
        "kind": "github_pr", "summary": "Open a pull request",
        "payload": {"repo": "jaylfc/taOS", "diff_sha256": "abc123"},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["action_id"]


@pytest.mark.asyncio
async def test_create_records_pending_request_row(client):
    aid = await _create(client)
    rows = await _rows(client)
    assert len(rows) == 1
    assert rows[0]["action_id"] == aid
    assert rows[0]["state"] == "pending"
    assert rows[0]["confirmed_by"] == "pending"
    assert rows[0]["user_id"] == client.uid
    assert rows[0]["repo"] == "jaylfc/taOS"
    assert rows[0]["diff_sha256"] == "abc123"


@pytest.mark.asyncio
async def test_confirmed_action_writes_exactly_one_outcome_row(client):
    aid = await _create(client)
    assert (await client.post(f"/api/taos-agent/actions/{aid}/confirm")).status_code == 200
    assert (await client.post(f"/api/taos-agent/actions/{aid}/execute")).status_code == 200
    assert (await client.post(f"/api/taos-agent/actions/{aid}/execute")).status_code == 409

    outcomes = [r for r in await _rows(client) if r["state"] != "pending"]
    assert len(outcomes) == 1
    (row,) = outcomes
    assert row["state"] == "executed"
    assert row["confirmed_by"] == "user"
    assert row["action_id"] == aid
    assert row["target"] == "github_pr"
    assert row["url"] == "https://github.com/jaylfc/taOS/pull/9"
    assert isinstance(row["latency_ms"], int)


@pytest.mark.asyncio
async def test_declined_action_writes_declined_row(client):
    aid = await _create(client)
    assert (await client.post(f"/api/taos-agent/actions/{aid}/decline")).status_code == 200

    outcomes = [r for r in await _rows(client) if r["state"] != "pending"]
    assert len(outcomes) == 1
    assert outcomes[0]["state"] == "declined"
    assert outcomes[0]["confirmed_by"] == "declined"
    assert outcomes[0]["action_id"] == aid


@pytest.mark.asyncio
async def test_auto_action_records_auto(client):
    await client.put("/api/taos-agent/actions/policy", json={"kind": "github_pr", "policy": "auto"})
    aid = await _create(client)
    assert (await client.post(f"/api/taos-agent/actions/{aid}/execute")).status_code == 200
    rows = await _rows(client)
    assert [r["confirmed_by"] for r in rows] == ["auto", "auto"]
    assert sorted(r["state"] for r in rows) == ["confirmed", "executed"]


@pytest.mark.asyncio
async def test_audit_kinds_cannot_be_posted_through_the_trace_api(client):
    """The gate is the only writer: a caller cannot forge an audit row."""
    for kind, payload in (
        ("external_write", _WRITE_ROW),
        ("local_fix", {"tool": "t", "target": "x", "undo": "u", "user_id": "u1"}),
    ):
        resp = await client.post(
            "/api/trace", json={"agent_name": "taos-agent", "kind": kind, "payload": payload}
        )
        assert resp.status_code == 400, (kind, resp.text)
    assert await _rows(client) == []


@pytest.mark.asyncio
async def test_trace_read_shows_only_the_callers_own_audit_rows(client):
    """Another account's external_write and local_fix rows never reach this user."""
    store = await client.app.state.trace_registry.get("taos-agent")
    await store.record("external_write", payload={**_WRITE_ROW, "user_id": "someone-else"})
    await store.record(
        "local_fix",
        payload={"tool": "t", "target": "x", "undo": "u", "user_id": "someone-else"},
    )
    await store.record("message_in", payload={"text": "hello"})
    aid = await _create(client)

    for kind in ("external_write", "local_fix"):
        resp = await client.get(f"/api/agents/taos-agent/trace?kind={kind}&limit=200")
        assert resp.status_code == 200, resp.text
        for event in resp.json()["events"]:
            assert event["payload"]["user_id"] == client.uid, (kind, event["payload"])

    resp = await client.get("/api/agents/taos-agent/trace?kind=external_write")
    assert [e["payload"]["action_id"] for e in resp.json()["events"]] == [aid]
    resp = await client.get("/api/agents/taos-agent/trace")
    kinds = sorted(e["kind"] for e in resp.json()["events"])
    assert kinds == ["external_write", "message_in"]
