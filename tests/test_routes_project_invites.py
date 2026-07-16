import json
import time

import pytest

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.projects.invite_store import (
    InviteAlreadyRedeemedError,
    InvitePendingCapError,
    InviteRevokedError,
    ProjectInviteStore,
)


async def _create_project(client, slug="invite-proj") -> str:
    resp = await client.post("/api/projects", json={"name": "Invite Proj", "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_mint_returns_invite_id_and_pin(client, app):
    pid = await _create_project(client)
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": ["a2a_send"], "approval_mode": "auto", "check_interval_secs": 1800},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["invite_id"]) == 6
    assert data["invite_id"].isdigit()
    assert len(data["pin"]) == 4
    assert data["pin"].isdigit()
    assert "project_tasks" in data["scopes"]
    assert data["approval_mode"] == "auto"
    assert data["check_interval_secs"] == 1800


@pytest.mark.asyncio
async def test_mint_project_tasks_always_present(client, app):
    pid = await _create_project(client)
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": ["a2a_send"], "approval_mode": "auto"},
    )
    assert resp.status_code == 200
    assert "project_tasks" in resp.json()["scopes"]


@pytest.mark.asyncio
async def test_mint_11th_pending_returns_429(client, app):
    pid = await _create_project(client, slug="invite-cap-proj")
    store = app.state.project_invites
    for i in range(10):
        await store.mint(
            project_id=pid,
            scopes=[],
            approval_mode="auto",
            check_interval_secs=1800,
            created_by="admin",
        )
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto"},
    )
    assert resp.status_code == 429, resp.text


@pytest.mark.asyncio
async def test_list_invites_excludes_pin_hash(client, app):
    pid = await _create_project(client)
    await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto"},
    )
    resp = await client.get(f"/api/projects/{pid}/invites")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert "pin_hash" not in items[0]


@pytest.mark.asyncio
async def test_revoke_returns_204(client, app):
    pid = await _create_project(client)
    mint_resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto"},
    )
    iid = mint_resp.json()["invite_id"]
    resp = await client.delete(f"/api/projects/{pid}/invites/{iid}")
    assert resp.status_code == 204, resp.text


@pytest.mark.asyncio
async def test_non_admin_cannot_mint(client, app):
    pid = await _create_project(client)
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        user_id="non-admin-user", is_admin=False
    )
    try:
        resp = await client.post(
            f"/api/projects/{pid}/invites",
            json={"scopes": [], "approval_mode": "auto"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.asyncio
async def test_revoke_nonexistent_returns_404(client, app):
    pid = await _create_project(client)
    resp = await client.delete(f"/api/projects/{pid}/invites/000000")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Redeem slice (S2): auto + manual approval, handle derivation, bundle, errors
# ---------------------------------------------------------------------------

async def _setup_agent_ecosystem(app, monkeypatch, tmp_path):
    """Eagerly init the agent registry / auth-requests / grants stores + the
    signing keypair on app.state so the redeem path can mint an identity.

    Mirrors the wiring the consent approve route depends on (and what the
    auth-request tests monkeypatch in). Returns nothing; mutates app.state."""
    from tinyagentos.agent_registry_store import (
        AgentRegistryStore,
        load_or_create_signing_keypair,
    )
    from tinyagentos.auth_requests_store import AuthRequestsStore
    from tinyagentos.agent_grants_store import AgentGrantsStore

    registry = AgentRegistryStore(tmp_path / "reg.db")
    await registry.init()
    auth_store = AuthRequestsStore(tmp_path / "auth.db")
    await auth_store.init()
    grants = AgentGrantsStore(tmp_path / "grants.db")
    await grants.init()
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys")

    monkeypatch.setattr(app.state, "agent_registry", registry)
    monkeypatch.setattr(app.state, "auth_requests", auth_store)
    monkeypatch.setattr(app.state, "agent_grants", grants)
    monkeypatch.setattr(app.state, "agent_registry_keypair", (priv, pub))
    return registry, auth_store, grants


async def _mint_invite(client, pid, *, approval_mode="auto", scopes=None, check_interval_secs=1800):
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={
            "scopes": scopes or ["a2a_send"],
            "approval_mode": approval_mode,
            "check_interval_secs": check_interval_secs,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # The mint route does not return the raw pin in all flows? It does (S1).
    return data["invite_id"], data["pin"]


@pytest.mark.asyncio
async def test_redeem_auto_mode_end_to_end(client, app, monkeypatch, tmp_path):
    registry, auth_store, grants = await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    pid = await _create_project(client, slug="redauto")
    iid, pin = await _mint_invite(client, pid, approval_mode="auto", scopes=["a2a_send"])

    resp = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": "claude"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    req_id = body["request_id"]
    handle = body["agent_handle"]
    assert handle == "redauto-claude", handle
    assert body["poll_path"] == f"/api/agents/auth-requests/{req_id}"

    # Poll returns accepted + canonical_id + token.
    poll = await client.get(f"/api/agents/auth-requests/{req_id}")
    assert poll.status_code == 200, poll.text
    pdata = poll.json()
    assert pdata["status"] == "accepted"
    assert pdata["canonical_id"]
    assert pdata["token"]

    # A member row exists with the derived handle.
    members = await app.state.project_store.list_members(pid)
    assert len(members) == 1, members
    assert members[0]["member_id"] == pdata["canonical_id"]


@pytest.mark.asyncio
async def test_redeem_bundle_carries_no_secret(client, app, monkeypatch, tmp_path):
    await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    pid = await _create_project(client, slug="redbundle")
    iid, pin = await _mint_invite(client, pid, approval_mode="auto", scopes=["a2a_send"])

    resp = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": "grok"},
    )
    assert resp.status_code == 200, resp.text
    bundle = resp.json()["bundle"]
    # The bundle must not carry the credential itself: no top-level `token`
    # key, and no JWT-shaped (three dot-separated base64url) string anywhere.
    assert "token" not in bundle
    assert "secret" not in bundle
    blob = json.dumps(bundle)
    assert ".eyJ" not in blob and ".eyJ" not in blob
    # Endpoints + scoped apis present.
    assert "a2a_bus_send" in bundle["apis"]


@pytest.mark.asyncio
async def test_redeem_canvas_scope_advertised_only_when_granted(client, app, monkeypatch, tmp_path):
    await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    pid = await _create_project(client, slug="redcanvas")

    # Without canvas scope: no canvas routes in apis.
    iid, pin = await _mint_invite(client, pid, approval_mode="auto", scopes=["a2a_send"])
    resp = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": "claude"},
    )
    assert resp.status_code == 200, resp.text
    apis = resp.json()["bundle"]["apis"]
    assert "canvas_elements" not in apis
    assert "canvas_element" not in apis

    # With canvas_write scope: the write route appears, but the GET
    # canvas_elements / snapshot (read) routes do NOT (read needs canvas_read).
    iid2, pin2 = await _mint_invite(client, pid, approval_mode="auto", scopes=["canvas_write"])
    resp2 = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid2, "pin": pin2, "harness": "claude", "label": "review"},
    )
    assert resp2.status_code == 200, resp2.text
    apis2 = resp2.json()["bundle"]["apis"]
    assert "canvas_elements" not in apis2
    assert "canvas_snapshot" not in apis2
    assert "canvas_element" in apis2
    # Handle de-duplicated against the first agent (same harness+project,
    # different label so still unique; assert it is well-formed).
    assert resp2.json()["agent_handle"] == "redcanvas-claude-review"


@pytest.mark.asyncio
async def test_redeem_manual_mode_leaves_pending(client, app, monkeypatch, tmp_path):
    await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    pid = await _create_project(client, slug="redmanual")
    iid, pin = await _mint_invite(client, pid, approval_mode="manual", scopes=["a2a_send"])

    resp = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": "opencode"},
    )
    assert resp.status_code == 200, resp.text
    req_id = resp.json()["request_id"]

    poll = await client.get(f"/api/agents/auth-requests/{req_id}")
    assert poll.status_code == 200, poll.text
    assert poll.json()["status"] == "pending"
    # No member row yet (manual approval has not happened).
    members = await app.state.project_store.list_members(pid)
    assert members == []


@pytest.mark.asyncio
async def test_redeem_wrong_pin_returns_403(client, app, monkeypatch, tmp_path):
    await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    pid = await _create_project(client, slug="redwrong")
    iid, pin = await _mint_invite(client, pid, approval_mode="auto", scopes=["a2a_send"])

    resp = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": "0000" if pin != "0000" else "1111", "harness": "claude"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_redeem_second_redeem_returns_409(client, app, monkeypatch, tmp_path):
    await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    pid = await _create_project(client, slug="redtwice")
    iid, pin = await _mint_invite(client, pid, approval_mode="auto", scopes=["a2a_send"])

    first = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": "claude"},
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": "claude"},
    )
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
async def test_invite_info_json_advertises_redeem_contract(client, app):
    pid = await _create_project(client, slug="redinfo")
    iid, pin = await _mint_invite(client, pid, approval_mode="auto")

    resp = await client.get(f"/i/{iid}", headers={"accept": "application/json"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["redeem"]["method"] == "POST"
    assert data["redeem"]["path"] == "/api/projects/invites/redeem"
    assert data["redeem"]["fields"]["invite_id"] == "required"
    assert data["redeem"]["fields"]["pin"] == "required"
    assert data["redeem"]["fields"]["harness"] == "required (your tool: kilo|grok|opencode|claude|aider|...)"
    assert "label" in data["redeem"]["fields"]


@pytest.mark.asyncio
async def test_invite_info_html_for_browser(client, app):
    pid = await _create_project(client, slug="redhtml")
    iid, pin = await _mint_invite(client, pid, approval_mode="auto")

    resp = await client.get(f"/i/{iid}", headers={"accept": "text/html"})
    assert resp.status_code == 200, resp.text
    assert "text/html" in resp.headers["content-type"]
    assert "taOS" in resp.text


@pytest.mark.asyncio
async def test_guide_markdown_contains_required_instructions(client, app, monkeypatch, tmp_path):
    await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    pid = await _create_project(client, slug="redguide")
    iid, pin = await _mint_invite(client, pid, approval_mode="auto", scopes=["a2a_send"])

    resp = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": "claude"},
    )
    assert resp.status_code == 200, resp.text
    guide = resp.json()["bundle"]["guide_markdown"]
    assert "https://github.com/jaylfc/taOS" in guide
    assert "WRITE THIS INTO YOUR OWN PERSISTENT MEMORY" in guide
    # Timed-check instruction mentions the interval value.
    assert "1800" in guide

