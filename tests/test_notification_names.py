"""Actionable notifications name things, not raw ids (tsk-i7ghpa).

A notification is often the only surface a human decides from, so its text must
name the project, task, agent and user the way the reader knows them, instead
of prj-/tsk-/user ids that need a lookup the reader cannot perform.
"""
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def registry(client):
    store = client._transport.app.state.agent_registry
    opened = store._db is None
    if opened:
        await store.init()
    yield store
    if opened:
        await store.close()


def _admin_id(client) -> str:
    return client._transport.app.state.auth.find_user("admin")["id"]


async def _project_and_task(client):
    pid = (await client.post("/api/projects", json={"name": "taOS", "slug": "taos"})).json()["id"]
    task = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "Fix login"})).json()
    return pid, task


async def _messages(client, source):
    notifs = (await client.get("/api/notifications")).json()
    return [n["message"] for n in notifs if n["source"] == source]


@pytest.mark.asyncio
async def test_task_closed_names_task_project_and_user(client):
    pid, task = await _project_and_task(client)
    uid = _admin_id(client)

    resp = await client.post(
        f"/api/projects/{pid}/tasks/{task['id']}/close",
        json={"closed_by": uid, "reason": "done"},
    )
    assert resp.status_code == 200, resp.text

    [msg] = await _messages(client, "task.closed")
    assert msg == '"Fix login" closed by Test Admin on taOS'
    assert task["id"] not in msg and uid not in msg and pid not in msg


@pytest.mark.asyncio
async def test_task_claimed_names_agent_by_handle(client, registry):
    pid, task = await _project_and_task(client)
    agent = await registry.register(framework="claude-code", display_name="Dev", handle="@dev-lead")

    resp = await client.post(
        f"/api/projects/{pid}/tasks/{task['id']}/claim",
        json={"claimer_id": agent["canonical_id"]},
    )
    assert resp.status_code == 200, resp.text

    [msg] = await _messages(client, "task.claimed")
    assert msg == '"Fix login" claimed by @dev-lead on taOS'


@pytest.mark.asyncio
async def test_task_reopened_names_task_and_user(client):
    pid, task = await _project_and_task(client)
    uid = _admin_id(client)
    await client.post(
        f"/api/projects/{pid}/tasks/{task['id']}/close",
        json={"closed_by": uid, "reason": "done"},
    )

    resp = await client.post(
        f"/api/projects/{pid}/tasks/{task['id']}/reopen", json={"reopened_by": uid}
    )
    assert resp.status_code == 200, resp.text

    [msg] = await _messages(client, "task.reopened")
    assert msg == '"Fix login" reopened by Test Admin on taOS'


@pytest.mark.asyncio
async def test_unknown_actor_falls_back_to_its_id(client):
    pid, task = await _project_and_task(client)

    await client.post(
        f"/api/projects/{pid}/tasks/{task['id']}/claim", json={"claimer_id": "agent-1"}
    )

    [msg] = await _messages(client, "task.claimed")
    assert msg == '"Fix login" claimed by agent-1 on taOS'


@pytest.mark.asyncio
async def test_access_request_names_the_requested_project(client, monkeypatch, tmp_path):
    from tinyagentos.agent_grants_store import AgentGrantsStore
    from tinyagentos.agent_registry_store import AgentRegistryStore, load_or_create_signing_keypair
    from tinyagentos.auth_requests_store import AuthRequestsStore

    state = client._transport.app.state
    registry = AgentRegistryStore(tmp_path / "reg.db")
    await registry.init()
    auth_store = AuthRequestsStore(tmp_path / "auth.db")
    await auth_store.init()
    grants = AgentGrantsStore(tmp_path / "grants.db")
    await grants.init()
    monkeypatch.setattr(state, "agent_registry", registry)
    monkeypatch.setattr(state, "auth_requests", auth_store)
    monkeypatch.setattr(state, "agent_grants", grants)
    monkeypatch.setattr(
        state, "agent_registry_keypair", load_or_create_signing_keypair(tmp_path / "keys")
    )
    pid, _task = await _project_and_task(client)

    resp = await client.post(
        "/api/agents/auth-requests",
        json={
            "identity_claim": "taos-dev",
            "framework": "claude-code",
            "requested_scopes": ["project_tasks"],
            "project_id": pid,
        },
    )
    assert resp.status_code == 200, resp.text
    request_id = resp.json()["request_id"]

    [notif] = [
        n for n in await state.notifications.list()
        if (n.get("data") or {}).get("request_id") == request_id
    ]
    assert notif["message"] == "taos-dev is requesting project_tasks on taOS"
    # The id still rides in data so the approval surface can preselect it.
    assert notif["data"]["project_id"] == pid

    await registry.close()
    await auth_store.close()
    await grants.close()


@pytest.mark.asyncio
async def test_decision_from_agent_names_agent_by_handle(client, registry):
    agent = await registry.register(framework="claude-code", display_name="Dev", handle="@dev-lead")

    resp = await client.post(
        "/api/decisions",
        json={"from_agent": agent["canonical_id"], "question": "Ship it?", "type": "approve_deny"},
    )
    assert resp.status_code in (200, 201), resp.text

    [msg] = await _messages(client, "decisions")
    assert msg == "@dev-lead needs a decision: Ship it?"


@pytest.mark.asyncio
async def test_invite_access_request_names_project_and_carries_its_id(client):
    import asyncio
    from types import SimpleNamespace

    from tinyagentos.routes.project_invites import _notify_pending_invite

    pid, _task = await _project_and_task(client)
    added = []

    class _Notifs:
        async def add(self, **kw):
            added.append(kw)

    state = SimpleNamespace(
        notifications=_Notifs(), project_store=client._transport.app.state.project_store
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    _notify_pending_invite(
        request,
        {
            "id": "ar-1",
            "identity_claim": "taos-dev",
            "framework": "claude-code",
            "requested_scopes": ["project_tasks"],
            "project_id": pid,
        },
    )
    for _ in range(50):
        if added:
            break
        await asyncio.sleep(0.01)

    [kw] = added
    assert kw["message"] == "taos-dev is requesting project_tasks on taOS"
    assert kw["data"]["project_id"] == pid
