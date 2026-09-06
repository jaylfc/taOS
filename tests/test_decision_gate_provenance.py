"""tsk-mul5pa — server-owned kinds: a gate decision whose metadata was supplied
by an API caller must mint nothing on approval.

The confused deputy: an agent holding ``decisions_write`` can POST a card whose
``question`` reads as harmless while its ``metadata.kind`` is a privileged gate
(``execution_gate`` / ``app_grant`` / ...).  The human sees only the question,
approves, and a grant is minted off caller-supplied metadata.

The fix is server-stamped provenance: the public create path strips a
``_server_raised`` marker it can never set, and every ``_apply_*_grant`` refuses
to act when the marker is absent.  Only the internal raisers stamp it.

Each proven pair below:
  - the RED test drives the real callers (agent POST + owner answer) and asserts
    the grant store is empty;
  - the CONTROL stamps provenance via the internal path and asserts the grant
    still lands, so deleting the handler cannot fake the red.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token

# Local literal (not imported from decisions.py) so the red-first proof runs on
# the unfixed tree too, where the constant does not yet exist.  Must match
# decisions.SERVER_RAISED_KEY.
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
# execution_gate — primary proven pair (jaylfc: "execution_gate is the cheapest")
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_created_execution_gate_metadata_mints_nothing(client):
    """An agent-posted card carrying execution_gate metadata must mint no
    execution grant when a human approves it."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    body = _decision_body(
        project_id=pid,
        type="approve_deny",
        metadata={"kind": "execution_gate", "agent_name": cid,
                  "action_class": "test-exec", "tool": "test"},
    )
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=body)
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    # The project owner (human) approves the card.
    resp = await client.post(f"/api/decisions/{did}/answer", json={"value": "approve"})
    assert resp.status_code == 200, resp.text

    policies = getattr(app.state, "execution_policies", None)
    assert policies is not None
    assert await policies.has_live_grant(cid, "test-exec") is False


@pytest.mark.asyncio
async def test_server_raised_execution_gate_still_mints_on_approval(client):
    """The legitimate internal path (execution-gate raiser) stamps provenance
    and MUST still mint the grant on approval."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, _token = await _mint_agent(app, pid, ("decisions_write",))

    decision = await app.state.decision_store.create(
        from_agent=cid,
        question=f"Agent {cid} wants to run test-exec",
        type="approve_deny",
        priority="blocking",
        project_id=pid,
        metadata={
            SERVER_RAISED_KEY: True,
            "kind": "execution_gate",
            "agent_name": cid,
            "action_class": "test-exec",
            "tool": "test",
        },
    )

    resp = await client.post(
        f"/api/decisions/{decision['id']}/answer", json={"value": "approve"}
    )
    assert resp.status_code == 200, resp.text

    policies = app.state.execution_policies
    assert await policies.has_live_grant(cid, "test-exec") is True


# ---------------------------------------------------------------------------
# app_grant — second proven handler (proves the fix covers the class)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_created_app_grant_metadata_mints_nothing(client):
    """An agent-posted card carrying app_grant metadata must write nothing to
    the app_grants ledger when a human approves it."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    body = _decision_body(
        project_id=pid,
        type="multi_select",
        options=[{"label": "Network access", "value": "net"}],
        metadata={"kind": "app_grant", "app_id": "testapp", "capabilities": ["net"]},
    )
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=body)
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    record = await app.state.decision_store.get(did)
    owner_uid = record.get("user_id") or ""

    resp = await client.post(f"/api/decisions/{did}/answer", json={"value": ["net"]})
    assert resp.status_code == 200, resp.text

    grants = getattr(app.state, "app_grants", None)
    assert grants is not None
    assert await grants.granted_capabilities(owner_uid, "testapp") == set()


@pytest.mark.asyncio
async def test_server_raised_app_grant_still_mints_on_approval(client):
    """The legitimate app-grant flow stamps provenance and MUST still write the
    ledger on approval."""
    app = client._transport.app
    pid = await _new_project(client)

    decision = await app.state.decision_store.create(
        from_agent="@taos-app-install",
        question="testapp would like these permissions",
        type="multi_select",
        priority="blocking",
        project_id=pid,
        options=[{"label": "Network access", "value": "net"}],
        metadata={
            SERVER_RAISED_KEY: True,
            "kind": "app_grant",
            "app_id": "testapp",
            "capabilities": ["net"],
        },
    )
    owner_uid = decision.get("user_id") or ""

    resp = await client.post(
        f"/api/decisions/{decision['id']}/answer", json={"value": ["net"]}
    )
    assert resp.status_code == 200, resp.text

    grants = app.state.app_grants
    granted = await grants.granted_capabilities(owner_uid, "testapp")
    assert "net" in granted
