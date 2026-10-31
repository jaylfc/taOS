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


def _admin_id(app) -> str:
    """Primary admin user id (mirrors device_pair_requests._admin_user_id)."""
    users = app.state.auth.list_users()
    for u in users:
        if u.get("is_admin"):
            return u.get("id") or ""
    return ""


async def _unstamped_decision(app, from_agent, project_id, type_, metadata, options=None):
    """A gate decision persisted WITHOUT the server provenance marker (a legacy
    row, or a raiser that forgot to stamp).  The public create path refuses
    these kinds outright, so the handler guard is proven through the store."""
    decision = await app.state.decision_store.create(
        from_agent=from_agent,
        question="Can I add a note to the board?",
        type=type_,
        options=options or [],
        project_id=project_id,
        metadata=metadata,
    )
    return decision["id"]


# ---------------------------------------------------------------------------
# Server-owned kinds (Jay ruling 2026-09-02): the public create path refuses a
# privileged metadata.kind with HTTP 400, whoever the caller is.
# ---------------------------------------------------------------------------

SERVER_OWNED_KINDS = (
    "execution_gate",
    "delegation_gate",
    "device_pairing",
    "app_grant",
    "project_create",
    "collab_delegation_gate",
)


@pytest.mark.asyncio
async def test_api_created_gate_metadata_mints_nothing(client):
    """The card's exact path: an agent with a genuine decisions_write grant
    posts a harmless-looking question carrying collab_delegation_gate metadata.
    The create is refused, so there is nothing for the owner to approve."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    body = {
        "from_agent": "ignored-for-agent-callers",
        "question": "Can I add a note to the board?",
        "type": "approve_deny",
        "project_id": pid,
        "metadata": {"kind": "collab_delegation_gate",
                     "contact_id": "contact-1",
                     "project_id": pid,
                     "granted_scopes": ["a2a_send"],
                     "elevated_scopes": ["canvas_write"]},
    }
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=body)
    assert resp.status_code == 400, (
        f"expected 400 for a server-owned kind, got {resp.status_code}: {resp.text}"
    )
    rows = await app.state.decision_store.list(project_id=pid)
    assert rows == [], f"expected 0 decisions, found {len(rows)}"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", SERVER_OWNED_KINDS)
async def test_agent_post_with_server_owned_kind_is_rejected(client, kind):
    app = client._transport.app
    pid = await _new_project(client)
    _cid, token = await _mint_agent(app, pid, ("decisions_write",))

    body = _decision_body(project_id=pid, metadata={"kind": kind})
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=body)
    assert resp.status_code == 400, resp.text
    assert await app.state.decision_store.list(project_id=pid) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", SERVER_OWNED_KINDS)
async def test_session_post_with_server_owned_kind_is_rejected(client, kind):
    """A human session is an API caller too: only internal raisers may set a
    server-owned kind."""
    app = client._transport.app
    body = _decision_body(question=f"session {kind}", metadata={"kind": kind})
    resp = await client.post("/api/decisions", json=body)
    assert resp.status_code == 400, resp.text
    rows = await app.state.decision_store.list()
    assert [r for r in rows if r["question"] == f"session {kind}"] == []


@pytest.mark.asyncio
async def test_api_post_with_ordinary_kind_still_created(client):
    """Control: a non-privileged metadata.kind is still accepted and echoed."""
    body = _decision_body(metadata={"kind": "decision", "ref": "x"})
    resp = await client.post("/api/decisions", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["metadata"] == {"kind": "decision", "ref": "x"}


# ---------------------------------------------------------------------------
# execution_gate -- primary proven pair (jaylfc: "execution_gate is the cheapest")
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unstamped_execution_gate_metadata_mints_nothing(client):
    """A card carrying execution_gate metadata without server provenance must
    mint no execution grant when a human approves it."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, _token = await _mint_agent(app, pid, ("decisions_write",))

    did = await _unstamped_decision(
        app, cid, pid, "approve_deny",
        {"kind": "execution_gate", "agent_name": cid,
         "action_class": "test-exec", "tool": "test"},
    )

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
async def test_unstamped_app_grant_metadata_mints_nothing(client):
    """A card carrying app_grant metadata without server provenance must write
    nothing to the app_grants ledger when a human approves it."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, _token = await _mint_agent(app, pid, ("decisions_write",))

    did = await _unstamped_decision(
        app, cid, pid, "multi_select",
        {"kind": "app_grant", "app_id": "testapp", "capabilities": ["net"]},
        options=[{"label": "Network access", "value": "net"}],
    )

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


# ---------------------------------------------------------------------------
# delegation_gate — third guarded handler (jayvfc finding 2: no test at all)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unstamped_delegation_gate_metadata_mints_nothing(client):
    """A card carrying delegation_gate metadata without server provenance must
    mint no delegate grant (and complete no delegation) when a human approves
    it."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, _token = await _mint_agent(app, pid, ("decisions_write",))

    did = await _unstamped_decision(
        app, cid, pid, "approve_deny",
        {"kind": "delegation_gate", "from_agent": cid,
         "to_agent": "to-agent", "task_title": "handoff", "note": ""},
    )

    resp = await client.post(f"/api/decisions/{did}/answer", json={"value": "approve"})
    assert resp.status_code == 200, resp.text

    policies = app.state.execution_policies
    assert await policies.has_live_grant(cid, "delegate") is False


@pytest.mark.asyncio
async def test_server_raised_delegation_gate_still_delegates_on_approval(client):
    """The legitimate internal path (delegation-gate raiser) stamps provenance
    and MUST still complete the delegation + mint the delegate grant."""
    app = client._transport.app
    pid = await _new_project(client)

    decision = await app.state.decision_store.create(
        from_agent="from-agent",
        question="Agent from-agent wants to delegate to to-agent",
        type="approve_deny",
        priority="blocking",
        project_id=pid,
        metadata={
            SERVER_RAISED_KEY: True,
            "kind": "delegation_gate",
            "from_agent": "from-agent",
            "to_agent": "to-agent",
            "task_title": "handoff",
            "note": "",
        },
    )

    resp = await client.post(
        f"/api/decisions/{decision['id']}/answer", json={"value": "approve"}
    )
    assert resp.status_code == 200, resp.text

    policies = app.state.execution_policies
    assert await policies.has_live_grant("from-agent", "delegate") is True


# ---------------------------------------------------------------------------
# device_pairing — fourth guarded handler (jayvfc #2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unstamped_device_pairing_metadata_mints_nothing(client, app):
    """A card carrying device_pairing metadata without server provenance must
    mint no device and leave the pair request pending when a human approves
    it."""
    # A real pending pair request so the control can prove the mint in the
    # counterpart test; this row must stay pending because the guard refuses.
    req = await app.state.device_pair_requests.create(
        platform="ios", display_name="My Phone"
    )
    pair_request_id = req["id"]

    pid = await _new_project(client)
    cid, _token = await _mint_agent(app, pid, ("decisions_write",))

    did = await _unstamped_decision(
        app, cid, pid, "approve_deny",
        {"kind": "device_pairing", "pair_request_id": pair_request_id},
    )

    resp = await client.post(f"/api/decisions/{did}/answer", json={"value": "approve"})
    assert resp.status_code == 200, resp.text

    still = await app.state.device_pair_requests.get(pair_request_id)
    assert still["status"] == "pending"
    # No device was minted for the deciding user.
    rec = await app.state.decision_store.get(did)
    assert await app.state.device_store.list_for_user(rec["user_id"]) == []


@pytest.mark.asyncio
async def test_server_raised_device_pairing_still_mints_on_approval(client, app):
    """The legitimate device-pairing raiser stamps provenance and MUST still
    mint the device + mark the pair request accepted on approval."""
    req = await app.state.device_pair_requests.create(
        platform="ios", display_name="My Phone"
    )
    pair_request_id = req["id"]

    admin = _admin_id(app)
    decision = await app.state.decision_store.create(
        from_agent="@taOSc",
        question="taOSc on 'My Phone' wants to connect. Allow?",
        type="approve_deny",
        priority="blocking",
        user_id=admin,
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

    decided = await app.state.device_pair_requests.get(pair_request_id)
    assert decided["status"] == "accepted"
    devices = await app.state.device_store.list_for_user(decision["user_id"])
    assert len(devices) == 1
