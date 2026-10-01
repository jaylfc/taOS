import json
import logging
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
async def test_mint_rejects_unknown_scopes(client, app):
    """Mint must reject scopes not in the closed vocabulary (#1993)."""
    pid = await _create_project(client)
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": ["a2a_send", "garbage_scope"], "approval_mode": "auto"},
    )
    assert resp.status_code == 400, resp.text
    data = resp.json()
    assert "garbage_scope" in data["error"]


@pytest.mark.asyncio
async def test_mint_accepts_known_scopes(client, app):
    """Known scopes that are not project-scoped should mint just fine."""
    pid = await _create_project(client)
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": ["a2a_send", "a2a_receive"], "approval_mode": "auto"},
    )
    assert resp.status_code == 200, resp.text


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


@pytest.mark.asyncio
async def test_revoke_expired_returns_204(client, app):
    pid = await _create_project(client)
    mint_resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto"},
    )
    iid = mint_resp.json()["invite_id"]
    store = app.state.project_invites
    await store._db.execute(
        "UPDATE project_invites SET expires_ts = 1 WHERE invite_id = ?", (iid,)
    )
    await store._db.commit()
    resp = await client.delete(f"/api/projects/{pid}/invites/{iid}")
    assert resp.status_code == 204, resp.text


@pytest.mark.asyncio
async def test_revoke_already_revoked_returns_409(client, app):
    pid = await _create_project(client)
    mint_resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto"},
    )
    iid = mint_resp.json()["invite_id"]
    first = await client.delete(f"/api/projects/{pid}/invites/{iid}")
    assert first.status_code == 204, first.text
    second = await client.delete(f"/api/projects/{pid}/invites/{iid}")
    assert second.status_code == 409, second.text


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


# ---------------------------------------------------------------------------
# OS-level (project-less) invites: mint + redeem to a chat-available identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_os_mint_returns_pin_and_no_project_tasks(client, app):
    resp = await client.post(
        "/api/agents/invites",
        json={
            "scopes": ["a2a_send"],
            "approval_mode": "auto",
            "check_interval_secs": 1800,
            "display_name": "Scout",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["invite_id"]) == 6 and data["invite_id"].isdigit()
    assert len(data["pin"]) == 4 and data["pin"].isdigit()
    # OS-level invites are not project-bound, so project_tasks is not forced.
    assert data["scopes"] == ["a2a_send"]
    assert data["display_name"] == "Scout"


@pytest.mark.asyncio
async def test_os_mint_non_admin_rejected(client, app):
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        user_id="non-admin-user", is_admin=False
    )
    try:
        resp = await client.post(
            "/api/agents/invites",
            json={"scopes": ["a2a_send"], "approval_mode": "auto"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.asyncio
async def test_os_list_and_revoke(client, app):
    mint = await client.post(
        "/api/agents/invites",
        json={"scopes": ["a2a_send"], "approval_mode": "auto", "display_name": "Scout"},
    )
    iid = mint.json()["invite_id"]

    listing = await client.get("/api/agents/invites")
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["invite_id"] == iid
    assert rows[0]["display_name"] == "Scout"

    revoke = await client.delete(f"/api/agents/invites/{iid}")
    assert revoke.status_code == 204, revoke.text


@pytest.mark.asyncio
async def test_os_revoke_rejects_project_invite(client, app):
    """The OS-level revoke route must not revoke a project-scoped invite."""
    pid = await _create_project(client, slug="os-guard")
    iid, _pin = await _mint_invite(client, pid, approval_mode="auto")
    resp = await client.delete(f"/api/agents/invites/{iid}")
    assert resp.status_code == 404, resp.text


async def _mint_os_invite(client, *, approval_mode="auto", scopes=None, display_name=None):
    resp = await client.post(
        "/api/agents/invites",
        json={
            "scopes": scopes if scopes is not None else ["a2a_send"],
            "approval_mode": approval_mode,
            "check_interval_secs": 1800,
            "display_name": display_name,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return data["invite_id"], data["pin"]


@pytest.mark.asyncio
async def test_os_invite_strips_project_scoped_grants(client, app):
    # An OS-level (project-less) invite must not carry project-bound scopes.
    resp = await client.post(
        "/api/agents/invites",
        json={
            "scopes": ["a2a_send", "project_tasks", "canvas_read", "canvas_write"],
            "approval_mode": "auto",
            "check_interval_secs": 1800,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scopes"] == ["a2a_send"]


@pytest.mark.asyncio
async def test_os_invite_display_name_validation(client, app):
    # Control characters are rejected.
    bad = await client.post(
        "/api/agents/invites",
        json={"scopes": ["a2a_send"], "display_name": "bad\x00name"},
    )
    assert bad.status_code == 422, bad.text
    # All-whitespace collapses to no alias.
    blank = await client.post(
        "/api/agents/invites",
        json={"scopes": ["a2a_send"], "display_name": "   "},
    )
    assert blank.status_code == 200, blank.text
    assert blank.json()["display_name"] is None


@pytest.mark.asyncio
async def test_os_redeem_mints_chat_agent_no_project_grant(client, app, monkeypatch, tmp_path):
    _registry, _auth, grants = await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    iid, pin = await _mint_os_invite(client, scopes=["a2a_send"], display_name="Scout")

    resp = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": "claude"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The handle derives from the alias, not a project slug.
    assert body["agent_handle"] == "scout"
    # The bundle carries no project and no project/task/canvas apis.
    bundle = body["bundle"]
    assert bundle["project"] is None
    assert "tasks_list" not in bundle["apis"]
    assert "canvas_elements" not in bundle["apis"]
    assert "a2a_bus_send" in bundle["apis"]

    req_id = body["request_id"]
    poll = await client.get(f"/api/agents/auth-requests/{req_id}")
    assert poll.status_code == 200, poll.text
    pdata = poll.json()
    assert pdata["status"] == "accepted"
    canonical_id = pdata["canonical_id"]
    assert pdata["token"]

    # No project membership row anywhere.
    projects = await app.state.project_store.list_projects()
    for p in projects:
        assert await app.state.project_store.list_members(p["id"]) == []

    # Grants are GLOBAL (project_id is None), never project-scoped.
    agent_grants = await grants.list_grants(canonical_id)
    assert agent_grants, "expected at least one global grant"
    for g in agent_grants:
        assert g["project_id"] is None
    scopes_granted = {g["scope"] for g in agent_grants}
    assert "a2a_send" in scopes_granted
    assert "project_tasks" not in scopes_granted


@pytest.mark.asyncio
async def test_os_redeem_applies_display_name(client, app, monkeypatch, tmp_path):
    registry, _auth, _grants = await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    iid, pin = await _mint_os_invite(client, scopes=["a2a_send"], display_name="My Cool Agent")

    resp = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": "claude"},
    )
    assert resp.status_code == 200, resp.text
    # Handle is slugified; the display_name preserves the human alias.
    assert resp.json()["agent_handle"] == "my-cool-agent"
    canonical_id = (await client.get(
        f"/api/agents/auth-requests/{resp.json()['request_id']}"
    )).json()["canonical_id"]
    record = await registry.get(canonical_id)
    assert record["display_name"] == "My Cool Agent"


@pytest.mark.asyncio
async def test_os_invite_advert_json_works(client, app):
    """The /i/{invite_id} advert must not 500 for a project-less invite."""
    iid, _pin = await _mint_os_invite(client, scopes=["a2a_send"], display_name="Scout")
    resp = await client.get(f"/i/{iid}", headers={"accept": "application/json"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["redeem"]["path"] == "/api/projects/invites/redeem"


@pytest.mark.asyncio
async def test_os_redeem_falls_back_to_harness_handle(client, app, monkeypatch, tmp_path):
    await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    iid, pin = await _mint_os_invite(client, scopes=["a2a_send"], display_name=None)
    resp = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": "opencode"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent_handle"] == "opencode"


@pytest.mark.asyncio
async def test_invite_info_html_escapes_project_name(client, app):
    # A project name is owner-controlled and is interpolated into the invite
    # advert HTML. It must be HTML-escaped or a name like "<script>..." would
    # execute in the browser of whoever opens the invite link (stored XSS).
    resp = await client.post(
        "/api/projects",
        json={"name": "<script>alert(1)</script>", "slug": "xssproj"},
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["id"]
    iid, pin = await _mint_invite(client, pid, approval_mode="auto")

    resp = await client.get(f"/i/{iid}", headers={"accept": "text/html"})
    assert resp.status_code == 200, resp.text
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in resp.text


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



@pytest.mark.asyncio
async def test_mint_default_ttl_is_one_hour(client, app):
    pid = await _create_project(client)
    before = time.time()
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto"},
    )
    assert resp.status_code == 200, resp.text
    ttl = resp.json()["expires_ts"] - before
    assert 3500 < ttl <= 3610


@pytest.mark.asyncio
async def test_mint_honours_ttl_secs(client, app):
    pid = await _create_project(client)
    before = time.time()
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto", "ttl_secs": 86400},
    )
    assert resp.status_code == 200, resp.text
    ttl = resp.json()["expires_ts"] - before
    assert 86300 < ttl <= 86410


@pytest.mark.asyncio
async def test_mint_rejects_ttl_over_cap(client, app):
    pid = await _create_project(client)
    resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto", "ttl_secs": 999999},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_revoke_redeemed_returns_409(client, app):
    pid = await _create_project(client)
    mint_resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto"},
    )
    body = mint_resp.json()
    store = app.state.project_invites
    await store.redeem(body["invite_id"], body["pin"])
    resp = await client.delete(f"/api/projects/{pid}/invites/{body['invite_id']}")
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_revoke_claimed_returns_409_with_mid_redeem_message(client, app):
    pid = await _create_project(client)
    mint_resp = await client.post(
        f"/api/projects/{pid}/invites",
        json={"scopes": [], "approval_mode": "auto"},
    )
    iid = mint_resp.json()["invite_id"]
    store = app.state.project_invites
    await store._db.execute(
        "UPDATE project_invites SET status = 'claimed' WHERE invite_id = ?", (iid,)
    )
    await store._db.commit()
    resp = await client.delete(f"/api/projects/{pid}/invites/{iid}")
    assert resp.status_code == 409, resp.text
    assert "mid-redeem" in resp.json()["error"]


# ---------------------------------------------------------------------------
# #2002: scope validation at mint (OS-level) + failed auto-approve rollback
# ---------------------------------------------------------------------------
# mint rejects unknown scopes on BOTH endpoints (project + OS-level) with 400.
# A failed auto-approve rolls the invite back to 'pending', refuses the auth
# request it already created (so it doesn't dangle in the consent inbox),
# and the same invite+pin must then redeem on retry.


@pytest.mark.asyncio
async def test_mint_rejects_unknown_scopes_os_level(client, app):
    """The OS-level (/api/agents/invites) mint must reject unknown scopes (#1993)."""
    resp = await client.post(
        "/api/agents/invites",
        json={"scopes": ["a2a_send", "garbage_scope"], "approval_mode": "auto"},
    )
    assert resp.status_code == 400, resp.text
    data = resp.json()
    assert "garbage_scope" in data["error"]


async def _redeem_failed_auto_approve_then_retry(
    client, app, monkeypatch, iid, pin, auth_store, *, harness, expected_handle
):
    """Force the auto-approve step to blow up once, then retry with the real
    helper. Asserts the #2002 rollback invariants:

      * the failed redeem returns 400
      * the invite is restored to 'pending' (not stuck in 'claimed')
      * the auth request created before the failure is 'refused' (dangling)
      * the same invite+pin redeems successfully on retry
      * the retry's auth request is accepted and the invite is 'redeemed'
    """
    import tinyagentos.routes.agent_auth_requests as _aar

    real_approve = _aar.approve_request_record
    captured: dict = {}
    control = {"fail_next": True}

    async def _spy(*args, **kwargs):
        record = kwargs.get("record")
        if record is not None:
            captured["request_id"] = record["id"]
        if control["fail_next"]:
            raise RuntimeError("simulated auto-approve failure")
        return await real_approve(*args, **kwargs)

    monkeypatch.setattr(_aar, "approve_request_record", _spy)

    # 1) The auto-approve fails: the route rolls back the invite and refuses
    #    the auth request it just minted so it cannot linger or be double-approved.
    failed = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": harness},
    )
    assert failed.status_code == 400, failed.text

    pending_row = await app.state.project_invites.get(iid)
    assert pending_row["status"] == "pending", pending_row

    refused_req = await auth_store.get(captured["request_id"])
    assert refused_req is not None, captured
    assert refused_req["status"] == "refused", refused_req

    # 2) Retry: real approve now runs. Same invite+pin must redeem successfully.
    control["fail_next"] = False
    retry = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": harness},
    )
    assert retry.status_code == 200, retry.text
    body = retry.json()
    assert body["agent_handle"] == expected_handle, body

    final_row = await app.state.project_invites.get(iid)
    assert final_row["status"] == "redeemed", final_row
    retry_req = await auth_store.get(body["request_id"])
    assert retry_req["status"] == "accepted", retry_req

    return body


@pytest.mark.asyncio
async def test_redeem_failed_approve_restores_pending_and_refuses_auth(
    client, app, monkeypatch, tmp_path
):
    """A failed auto-approve rolls the project invite back to 'pending' and
    refuses the dangling auth request; the same invite+pin redeems on retry."""
    _registry, auth_store, _grants = await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    pid = await _create_project(client, slug="redfail")
    iid, pin = await _mint_invite(client, pid, approval_mode="auto", scopes=["a2a_send"])
    body = await _redeem_failed_auto_approve_then_retry(
        client, app, monkeypatch, iid, pin, auth_store,
        harness="claude", expected_handle="redfail-claude",
    )
    poll = await client.get(f"/api/agents/auth-requests/{body['request_id']}")
    assert poll.status_code == 200, poll.text
    assert poll.json()["status"] == "accepted"
    members = await app.state.project_store.list_members(pid)
    assert len(members) == 1


@pytest.mark.asyncio
async def test_redeem_failed_approve_os_level_restores_pending(
    client, app, monkeypatch, tmp_path
):
    """The OS-level redeem path must also roll back to 'pending' on a failed
    auto-approve, refuse the dangling auth request, and redeem on retry."""
    _registry, auth_store, _grants = await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    iid, pin = await _mint_os_invite(client, scopes=["a2a_send"], display_name="Scout")
    await _redeem_failed_auto_approve_then_retry(
        client, app, monkeypatch, iid, pin, auth_store,
        harness="claude", expected_handle="scout",
    )


@pytest.mark.asyncio
async def test_redeem_429_carries_retry_after(client):
    """Redeem is unauthenticated, so its 429 is the one an honest client is
    most likely to meet: it must say how long to back off. The limiter runs
    before the invite is even looked up, so a bogus id is enough to trip it."""
    from tinyagentos import auth_middleware

    auth_middleware._rate_limit_hits.clear()
    try:
        last = None
        for _ in range(auth_middleware._INVITE_RATE_MAX_PER_WINDOW + 1):
            last = await client.post(
                "/api/projects/invites/redeem",
                json={"invite_id": "nosuch", "pin": "00000000", "harness": "claude"},
            )
        assert last.status_code == 429, last.text
        retry_after = int(last.headers["retry-after"])
        assert 1 <= retry_after <= int(auth_middleware._INVITE_RATE_WINDOW_SECS)
    finally:
        auth_middleware._rate_limit_hits.clear()


class TestDeriveOsHandleHarnessFallback:
    """CodeRabbit finding on #2798: an unslugifiable display_name with a
    slugifiable label dropped the harness component entirely instead of
    falling back to it, so two different harnesses landed on the identical
    label-only handle."""

    def test_unslugifiable_alias_with_label_still_includes_harness(self):
        from tinyagentos.routes.project_invites import _derive_os_handle

        handle = _derive_os_handle("🎉", "claude", "beta")
        assert handle == "claude-beta"

    def test_different_harnesses_no_longer_collide_on_label_alone(self):
        from tinyagentos.routes.project_invites import _derive_os_handle

        claude_handle = _derive_os_handle("🎉", "claude", "beta")
        gemini_handle = _derive_os_handle("🎉", "gemini", "beta")
        assert claude_handle != gemini_handle


class TestDeriveHandleDedup:
    """#2093: the label already containing the project name caused double-redeem.

    ``_derive_handle`` must not prepend a component that is already present in
    the slugified label."""

    def test_label_starts_with_project_slug(self):
        from tinyagentos.routes.project_invites import _derive_handle

        # taosmobile + claude + taosmobile-dev → taosmobile-claude-dev
        handle = _derive_handle("taosmobile", "claude", "taosmobile-dev")
        assert handle == "taosmobile-claude-dev"

    def test_label_equals_project_slug(self):
        from tinyagentos.routes.project_invites import _derive_handle

        # taosmobile + claude + taosmobile → taosmobile-claude (label stripped)
        handle = _derive_handle("taosmobile", "claude", "taosmobile")
        assert handle == "taosmobile-claude"

    def test_label_starts_with_harness(self):
        from tinyagentos.routes.project_invites import _derive_handle

        # claude + label "claude-code-dev" → must not become "proj-claude-claude-code-dev"
        handle = _derive_handle("myproj", "claude", "claude-code-dev")
        assert handle == "myproj-claude-code-dev"

    def test_label_equals_harness(self):
        from tinyagentos.routes.project_invites import _derive_handle

        # proj + claude + claude -> proj-claude (label stripped)
        handle = _derive_handle("myproj", "claude", "claude")
        assert handle == "myproj-claude"

    def test_label_without_overlap_unchanged(self):
        from tinyagentos.routes.project_invites import _derive_handle

        # No overlap -> label is appended verbatim (slugified).
        handle = _derive_handle("taosmobile", "claude", "review-task")
        assert handle == "taosmobile-claude-review-task"


class TestGrokGuideMarkdown:
    """#tsk-c56jag: grok harness must carry secure-form + routine-poll instructions,
    and non-grok harness text must stay byte-identical."""

    @pytest.mark.asyncio
    async def test_grok_project_guide_contains_secure_form_and_poll(self, client, app, monkeypatch, tmp_path):
        await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
        pid = await _create_project(client, slug="grokproj")
        iid, pin = await _mint_invite(client, pid, approval_mode="auto", scopes=["a2a_send"])

        resp = await client.post(
            "/api/projects/invites/redeem",
            json={"invite_id": iid, "pin": pin, "harness": "grok"},
        )
        assert resp.status_code == 200, resp.text
        guide = resp.json()["bundle"]["guide_markdown"]
        assert "secure form" in guide
        assert "poll" in guide
        assert "every bot on this Grok account" in guide
        assert "1800" in guide

    @pytest.mark.asyncio
    async def test_grok_os_guide_contains_secure_form_and_poll(self, client, app, monkeypatch, tmp_path):
        await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
        iid, pin = await _mint_os_invite(client, scopes=["a2a_send"], display_name="GrokBot")
        resp = await client.post(
            "/api/projects/invites/redeem",
            json={"invite_id": iid, "pin": pin, "harness": "grok"},
        )
        assert resp.status_code == 200, resp.text
        guide = resp.json()["bundle"]["guide_markdown"]
        assert "secure form" in guide
        assert "poll" in guide
        assert "every bot on this Grok account" in guide
        assert "1800" in guide

    @pytest.mark.asyncio
    async def test_claude_project_guide_unchanged(self, client, app, monkeypatch, tmp_path):
        await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
        pid = await _create_project(client, slug="claudeproj")
        iid, pin = await _mint_invite(client, pid, approval_mode="auto", scopes=["a2a_send"])

        resp = await client.post(
            "/api/projects/invites/redeem",
            json={"invite_id": iid, "pin": pin, "harness": "claude"},
        )
        assert resp.status_code == 200, resp.text
        guide = resp.json()["bundle"]["guide_markdown"]
        assert "secure form" not in guide
        assert "every bot on this Grok account" not in guide

    @pytest.mark.asyncio
    async def test_claude_os_guide_unchanged(self, client, app, monkeypatch, tmp_path):
        await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
        iid, pin = await _mint_os_invite(client, scopes=["a2a_send"], display_name="ClaudeBot")
        resp = await client.post(
            "/api/projects/invites/redeem",
            json={"invite_id": iid, "pin": pin, "harness": "claude"},
        )
        assert resp.status_code == 200, resp.text
        guide = resp.json()["bundle"]["guide_markdown"]
        assert "secure form" not in guide
        assert "every bot on this Grok account" not in guide

    @pytest.mark.asyncio
    async def test_grok_guide_does_not_claim_tasks_on_bus(self, client, app, monkeypatch, tmp_path):
        """RED-FIRST: Grok guide should clarify polling is for onboarding only, not tasks."""
        await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
        pid = await _create_project(client, slug="grokred")
        iid, pin = await _mint_invite(client, pid, approval_mode="auto", scopes=["a2a_send"])

        resp = await client.post(
            "/api/projects/invites/redeem",
            json={"invite_id": iid, "pin": pin, "harness": "grok"},
        )
        assert resp.status_code == 200, resp.text
        guide = resp.json()["bundle"]["guide_markdown"]

        # a) Grok guide does NOT contain 'tasks arrive on the A2A bus'
        assert "tasks arrive on the A2A bus" not in guide, f"Guide contains wrong text about tasks on bus:\n{guide}"
        
        # b) Grok guide DOES mention tasks/ready
        assert "tasks/ready" in guide, f"Guide does not mention tasks/ready:\n{guide}"
        
        # c) NON-Grok guide still contains 'reliable delivery floor' - mint a new invite for this
        iid2, pin2 = await _mint_invite(client, pid, approval_mode="auto", scopes=["a2a_send"])
        non_grok_resp = await client.post(
            "/api/projects/invites/redeem",
            json={"invite_id": iid2, "pin": pin2, "harness": "claude"},
        )
        non_grok_guide = non_grok_resp.json()["bundle"]["guide_markdown"]
        assert "reliable delivery floor" in non_grok_guide, f"NON-Grok guide does not contain reliable delivery floor:\n{non_grok_guide}"


class TestBuildControllerDict:
    """_build_controller_dict endpoint bundle contract."""

    @pytest.mark.asyncio
    async def test_https_relay_url_emitted_at_priority_1(self, monkeypatch):
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")
        monkeypatch.delenv("TAOS_CONTROLLER_CALLBACK_HOST", raising=False)

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ):
            result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        relay_eps = [ep for ep in endpoints if ep["kind"] == "relay"]
        assert len(relay_eps) == 1
        assert relay_eps[0]["url"] == "https://relay.example.test"
        assert relay_eps[0]["priority"] == 1
        assert endpoints[0] == relay_eps[0]

    @pytest.mark.asyncio
    async def test_http_relay_url_omitted_with_warning(self, monkeypatch, caplog):
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "http://relay.example.test")
        monkeypatch.delenv("TAOS_CONTROLLER_CALLBACK_HOST", raising=False)

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value=None,
        ):
            with caplog.at_level(
                logging.WARNING, logger="tinyagentos.routes.project_invites"
            ):
                result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        relay_eps = [ep for ep in endpoints if ep["kind"] == "relay"]
        assert len(relay_eps) == 0
        assert any(
            "relay" in r.message.lower() and "not safe" in r.message.lower()
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_public_callback_host_omitted_with_warning(self, monkeypatch, caplog):
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        monkeypatch.setenv("TAOS_CONTROLLER_CALLBACK_HOST", "public.example.com")

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value="public.example.com",
        ):
            with caplog.at_level(
                logging.WARNING, logger="tinyagentos.routes.project_invites"
            ):
                result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        for ep in endpoints:
            assert "public.example.com" not in ep.get("url", "")
        assert any("public.example.com" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_private_callback_host_emitted_as_http(self, monkeypatch):
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.setenv("TAOS_CONTROLLER_CALLBACK_HOST", "192.168.1.1")
        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value="192.168.1.1",
        ):
            result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        assert endpoints[0]["kind"] == "lan"
        assert endpoints[0]["url"] == "http://192.168.1.1:6969"
        assert endpoints[0]["priority"] == 1

    @pytest.mark.asyncio
    async def test_lan_only_bundle_unchanged(self, monkeypatch):
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        monkeypatch.delenv("TAOS_CONTROLLER_CALLBACK_HOST", raising=False)

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips",
            return_value=["192.168.1.1"],
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value=None,
        ):
            result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        assert len(endpoints) == 1
        assert endpoints[0] == {
            "kind": "lan",
            "url": "http://192.168.1.1:6969",
            "priority": 1,
        }

    @pytest.mark.asyncio
    async def test_mesh_bundle_unchanged(self, monkeypatch):
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        monkeypatch.delenv("TAOS_CONTROLLER_CALLBACK_HOST", raising=False)

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": True, "node_ip": "100.64.0.7"},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value=None,
        ):
            result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        assert len(endpoints) == 1
        assert endpoints[0] == {
            "kind": "mesh",
            "url": "http://100.64.0.7:6969",
            "priority": 1,
        }

    # --- RED-FIRST tests for tsk-kdg6e5 (fix-forward #3305) ---

    @pytest.mark.asyncio
    async def test_tailscale_callback_host_emitted_at_priority_1(self, monkeypatch):
        """Tailscale IP (100.64.0.0/10) should be advertised as priority-1 LAN endpoint."""
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        monkeypatch.setenv("TAOS_CONTROLLER_CALLBACK_HOST", "100.78.225.80")

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value="100.78.225.80",
        ):
            result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        lan_eps = [ep for ep in endpoints if ep["kind"] == "lan"]
        assert len(lan_eps) == 1
        assert lan_eps[0]["url"] == "http://100.78.225.80:6969"
        assert lan_eps[0]["priority"] == 1

    @pytest.mark.asyncio
    async def test_hostname_override_local_advertised(self, monkeypatch):
        """Override like 'taos.local' should be advertised over HTTP."""
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        monkeypatch.setenv("TAOS_CONTROLLER_CALLBACK_HOST", "taos.local")

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value="taos.local",
        ):
            result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        lan_eps = [ep for ep in endpoints if ep["kind"] == "lan"]
        assert len(lan_eps) == 1
        assert lan_eps[0]["url"] == "http://taos.local:6969"
        assert lan_eps[0]["priority"] == 1

    @pytest.mark.asyncio
    async def test_hostname_override_ts_net_advertised(self, monkeypatch):
        """Override like 'controller.ts.net' (MagicDNS) should be advertised over HTTP."""
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        monkeypatch.setenv("TAOS_CONTROLLER_CALLBACK_HOST", "controller.ts.net")

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value="controller.ts.net",
        ):
            result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        lan_eps = [ep for ep in endpoints if ep["kind"] == "lan"]
        assert len(lan_eps) == 1
        assert lan_eps[0]["url"] == "http://controller.ts.net:6969"
        assert lan_eps[0]["priority"] == 1

    @pytest.mark.asyncio
    async def test_public_hostname_override_omitted(self, monkeypatch, caplog):
        """Override like 'controller.example.com' (public-looking) should NOT be advertised over HTTP."""
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        monkeypatch.setenv("TAOS_CONTROLLER_CALLBACK_HOST", "controller.example.com")

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value="controller.example.com",
        ):
            with caplog.at_level(
                logging.WARNING, logger="tinyagentos.routes.project_invites"
            ):
                result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        for ep in endpoints:
            assert "controller.example.com" not in ep.get("url", "")
        assert any("controller.example.com" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_full_url_override_lan_dedup(self, monkeypatch):
        """Full URL override 'http://192.168.1.5:6969' with LAN IP 192.168.1.5 should dedup to one endpoint."""
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        monkeypatch.setenv("TAOS_CONTROLLER_CALLBACK_HOST", "http://192.168.1.5:6969")

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips",
            return_value=["192.168.1.5"],
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value="http://192.168.1.5:6969",
        ):
            result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        lan_eps = [ep for ep in endpoints if ep["kind"] == "lan"]
        # Should have exactly ONE endpoint for 192.168.1.5 (deduped)
        assert len(lan_eps) == 1
        assert lan_eps[0]["url"] == "http://192.168.1.5:6969"

    @pytest.mark.asyncio
    async def test_http_relay_url_omitted_https_only(self, monkeypatch, caplog):
        """TAOS_CONTROLLER_RELAY_URL=http://10.0.0.5 should be omitted (relay must be HTTPS)."""
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "http://10.0.0.5")
        monkeypatch.delenv("TAOS_CONTROLLER_CALLBACK_HOST", raising=False)

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value=None,
        ):
            with caplog.at_level(
                logging.WARNING, logger="tinyagentos.routes.project_invites"
            ):
                result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        relay_eps = [ep for ep in endpoints if ep["kind"] == "relay"]
        assert len(relay_eps) == 0
        assert any("relay" in r.message.lower() and "not safe" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_ipv6_ula_callback_host_bracketed_and_advertised(self, monkeypatch):
        """Bare ULA IPv6 override must be bracketed and advertised as a trusted LAN endpoint."""
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        monkeypatch.setenv("TAOS_CONTROLLER_CALLBACK_HOST", "fd7a:115c:a1e0::1")

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value="fd7a:115c:a1e0::1",
        ):
            result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        lan_eps = [ep for ep in endpoints if ep["kind"] == "lan"]
        assert len(lan_eps) == 1
        assert lan_eps[0]["url"] == "http://[fd7a:115c:a1e0::1]:6969"
        assert lan_eps[0]["priority"] == 1
        # No endpoint contains a malformed '::1:' port fragment.
        for ep in endpoints:
            assert "::1:" not in ep.get("url", "")

    @pytest.mark.asyncio
    async def test_ipv6_documentation_callback_host_not_advertised(self, monkeypatch, caplog):
        """Bare documentation-range IPv6 override must NOT be advertised."""
        from tinyagentos.routes.project_invites import _build_controller_dict
        from types import SimpleNamespace
        from unittest.mock import patch

        monkeypatch.delenv("TAOS_CONTROLLER_RELAY_URL", raising=False)
        monkeypatch.setenv("TAOS_CONTROLLER_CALLBACK_HOST", "2001:db8::1")

        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch(
            "tinyagentos.routes.project_invites._enumerate_lan_ips", return_value=[]
        ), patch(
            "tinyagentos.taosnet.mesh.mesh_status",
            return_value={"joined": False},
        ), patch(
            "tinyagentos.routes.agent_deploy.controller_callback_host",
            return_value="2001:db8::1",
        ):
            with caplog.at_level(
                logging.WARNING, logger="tinyagentos.routes.project_invites"
            ):
                result = await _build_controller_dict(req)

        endpoints = result["endpoints"]
        for ep in endpoints:
            assert "2001:db8::1" not in ep.get("url", "")
        assert any("2001:db8::1" in r.message for r in caplog.records)
