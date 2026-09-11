"""tsk-mul5pa + tsk-lgdxoz — server-owned kinds: a gate decision whose metadata
was supplied by an API caller must mint nothing on approval.  The two handlers
not covered by the original test pair (delegation_gate, device_pairing) are
exercised below alongside a backfill integration test.

Each proven pair:
  - the RED test drives the real callers (API POST + owner answer) and asserts
    the grant/pairing is absent;
  - the CONTROL stamps provenance via the internal path and asserts the grant
    or pairing lands, so deleting the handler cannot fake the red.

The backfill test seeds a pre-#2748 pending gate row (no _server_raised key)
into an already-initialised database, runs the store's _post_init backfill, and
asserts the row is now answerable end-to-end.
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token

SERVER_RAISED_KEY = "_server_raised"


async def _mint_agent(app, project_id, scopes, handle="@taOS-dev"):
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
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _new_project(client, name="alpha", slug="alpha"):
    resp = await client.post("/api/projects", json={"name": name, "slug": slug})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _decision_body(**over):
    body = {"from_agent": "spoofed", "question": "ship it?", "type": "approve_deny"}
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# delegation_gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_created_delegation_gate_metadata_mints_no_grant(client):
    """An agent-posted card carrying delegation_gate metadata must mint no
    delegate grant when a human approves it."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    body = _decision_body(
        project_id=pid,
        metadata={"kind": "delegation_gate", "from_agent": cid,
                  "to_agent": "agent-b", "task_id": "task-1", "task_title": "Task 1"},
    )
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=body)
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    resp = await client.post(f"/api/decisions/{did}/answer", json={"value": "approve"})
    assert resp.status_code == 200, resp.text

    policies = getattr(app.state, "execution_policies", None)
    assert policies is not None
    assert await policies.has_live_grant(cid, "delegate") is False


@pytest.mark.asyncio
async def test_server_raised_delegation_gate_still_mints_on_approval(client):
    """The legitimate delegation-gate flow stamps provenance and MUST still
    mint the delegate grant on approval."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, _token = await _mint_agent(app, pid, ("decisions_write",))

    # Ensure to_agent ("agent-b") exists in the registry for _resolve_agent_id.
    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    to_rec = await registry.register(
        framework="claude-code",
        display_name="agent-b",
        allow_reserved=True,
        origin="internal",
        handle="agent-b",
    )
    if to_rec.get("status") != "active":
        await registry.set_status(to_rec["canonical_id"], "active")

    decision = await app.state.decision_store.create(
        from_agent=cid,
        question=f"Agent {cid} wants to delegate Task 1 to agent-b",
        type="approve_deny",
        priority="blocking",
        project_id=pid,
        metadata={
            SERVER_RAISED_KEY: True,
            "kind": "delegation_gate",
            "from_agent": cid,
            "to_agent": "agent-b",
            "task_title": "Task 1",
            "project_id": pid,
        },
    )

    resp = await client.post(
        f"/api/decisions/{decision['id']}/answer", json={"value": "approve"}
    )
    assert resp.status_code == 200, resp.text

    policies = app.state.execution_policies
    assert await policies.has_live_grant(cid, "delegate") is True


# ---------------------------------------------------------------------------
# device_pairing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_created_device_pairing_metadata_pairs_nothing(client):
    """An agent-posted card carrying device_pairing metadata must mint no
    device when a human approves it."""
    app = client._transport.app
    pair_store = app.state.device_pair_requests
    if pair_store._db is None:
        await pair_store.init()

    pair = await pair_store.create(
        platform="ios",
        display_name="lock-screen",
        verify_code="123456",
    )
    pair_request_id = pair["id"]

    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    body = _decision_body(
        from_agent=cid,
        project_id=pid,
        metadata={"kind": "device_pairing", "pair_request_id": pair_request_id},
    )
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=body)
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    resp = await client.post(f"/api/decisions/{did}/answer", json={"value": "approve"})
    assert resp.status_code == 200, resp.text

    record = await pair_store.get(pair_request_id)
    assert record is not None
    assert record["status"] == "pending"


@pytest.mark.asyncio
async def test_server_raised_device_pairing_still_pairs_on_approval(client):
    """The legitimate device-pairing flow stamps provenance and MUST still
    mint the device on approval."""
    app = client._transport.app
    pair_store = app.state.device_pair_requests
    if pair_store._db is None:
        await pair_store.init()
    device_store = app.state.device_store
    if device_store._db is None:
        await device_store.init()

    verify_code = "654321"
    pair = await pair_store.create(
        platform="ios",
        display_name="lock-screen",
        verify_code=verify_code,
    )
    pair_request_id = pair["id"]

    pid = await _new_project(client)
    cid, _token = await _mint_agent(app, pid, ("decisions_write",))

    admin_uid = app.state.auth.find_user("admin")["id"]

    decision = await app.state.decision_store.create(
        from_agent=cid,
        question=f"Pair ios device (code: {verify_code})",
        type="approve_deny",
        priority="blocking",
        project_id=pid,
        user_id=admin_uid,
        metadata={
            SERVER_RAISED_KEY: True,
            "kind": "device_pairing",
            "pair_request_id": pair_request_id,
        },
    )

    resp = await client.post(
        f"/api/decisions/{decision['id']}/answer", json={"value": "approve"}
    )
    assert resp.status_code == 200, resp.text

    record = await pair_store.get(pair_request_id)
    assert record is not None
    assert record["status"] == "accepted"
    assert record.get("device_id") is not None


# ---------------------------------------------------------------------------
# Backfill integration test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_legacy_pending_gate_decision_still_pairs(client):
    """A pending gate decision created before the _server_raised marker existed
    must still complete its side effect after the store backfill runs."""
    app = client._transport.app
    store = app.state.decision_store
    pair_store = app.state.device_pair_requests
    if pair_store._db is None:
        await pair_store.init()

    verify_code = "999888"
    pair = await pair_store.create(
        platform="ios",
        display_name="lock-screen",
        verify_code=verify_code,
    )
    pair_request_id = pair["id"]

    pid = await _new_project(client)
    cid, _token = await _mint_agent(app, pid, ("decisions_write",))

    admin_uid = app.state.auth.find_user("admin")["id"]

    legacy_metadata = {
        "kind": "device_pairing",
        "pair_request_id": pair_request_id,
    }
    decision = await store.create(
        from_agent=cid,
        question=f"Pair ios device (code: {verify_code})",
        type="approve_deny",
        priority="blocking",
        project_id=pid,
        user_id=admin_uid,
        metadata=legacy_metadata,
    )
    did = decision["id"]

    raw = await (await store._db.execute(
        "SELECT metadata FROM decisions WHERE id = ?", (did,)
    )).fetchone()
    meta_before = json.loads(raw[0])
    assert meta_before.get("_server_raised") is not True

    await store._post_init()

    raw = await (await store._db.execute(
        "SELECT metadata FROM decisions WHERE id = ?", (did,)
    )).fetchone()
    meta_after = json.loads(raw[0])
    assert meta_after.get("_server_raised")

    resp = await client.post(f"/api/decisions/{did}/answer", json={"value": "approve"})
    assert resp.status_code == 200, resp.text

    record = await pair_store.get(pair_request_id)
    assert record is not None
    assert record["status"] == "accepted"
    assert record.get("device_id") is not None
