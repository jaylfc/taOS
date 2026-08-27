"""Scope-request flow: an EXISTING registry agent asks for MORE scopes on its
own identity, the owner/admin approves, and the grant lands on the SAME
canonical_id (no new identity is minted).

Mirrors the auth-request route tests (tests/test_routes_agent_auth_requests.py):
each test wires fresh registry / grants / scope-request stores + keypair onto
app.state via monkeypatch, so the flow is exercised end to end through the real
routes and middleware.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    mint_registry_token,
)
from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.agent_scope_requests_store import AgentScopeRequestsStore
from taos_test_csrf import csrf_event_hooks


class _Env:
    """Fresh stores + a registered active agent, wired onto app.state."""

    def __init__(self, registry, grants, scope_store, priv, pub, admin_uid):
        self.registry = registry
        self.grants = grants
        self.scope_store = scope_store
        self.priv = priv
        self.pub = pub
        self.admin_uid = admin_uid

    def agent_token(self, canonical_id: str, framework: str = "claude") -> str:
        return mint_registry_token(
            canonical_id, self.priv, user_id=self.admin_uid, framework=framework
        )

    async def close(self):
        await self.registry.close()
        await self.grants.close()
        await self.scope_store.close()


async def _wire(client, monkeypatch, tmp_path, *, owner_uid=None):
    """Build fresh stores, register one ACTIVE agent, monkeypatch app.state."""
    app = client._transport.app
    admin = app.state.auth.find_user("admin")
    admin_uid = admin["id"] if admin else ""
    owner_uid = owner_uid if owner_uid is not None else admin_uid

    registry = AgentRegistryStore(tmp_path / "reg.db")
    await registry.init()
    grants = AgentGrantsStore(tmp_path / "grants.db")
    await grants.init()
    scope_store = AgentScopeRequestsStore(tmp_path / "scope.db")
    await scope_store.init()
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys")

    monkeypatch.setattr(app.state, "agent_registry", registry)
    monkeypatch.setattr(app.state, "agent_grants", grants)
    monkeypatch.setattr(app.state, "agent_scope_requests", scope_store)
    monkeypatch.setattr(app.state, "agent_registry_keypair", (priv, pub))

    env = _Env(registry, grants, scope_store, priv, pub, admin_uid)
    env.owner_uid = owner_uid
    return env


async def _register_active(env, *, handle="@worker", display="worker", framework="claude"):
    rec = await env.registry.register(
        framework=framework,
        display_name=display,
        user_id=env.owner_uid,
        origin="taos-deployed",  # taos-deployed registers active immediately
        handle=handle,
    )
    return rec["canonical_id"]


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_creates_and_approves_grant_on_existing_identity(
    client, monkeypatch, tmp_path
):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        before = len(await env.registry.list_all())

        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests",
            json={"requested_scopes": ["memory_read", "memory_write"], "reason": "need memory"},
        )
        assert resp.status_code == 200, resp.text
        req_id = resp.json()["request_id"]
        assert resp.json()["status"] == "pending"

        # Admin narrows to a subset on approve.
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{req_id}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["canonical_id"] == cid

        # Grant landed on the EXISTING canonical_id, and NO new identity exists.
        grants = await env.grants.list_grants(cid)
        assert {g["scope"] for g in grants} == {"memory_read"}
        assert all(g["project_id"] is None for g in grants)
        assert len(await env.registry.list_all()) == before  # no new identity
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_agent_can_self_request_with_own_token(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        token = env.agent_token(cid)
        app = client._transport.app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                f"/api/agents/registry/{cid}/scope-requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"requested_scopes": ["a2a_send"]},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending"
        assert await env.scope_store.count_pending_for(cid) == 1
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_agent_cannot_request_for_another_identity(client, monkeypatch, tmp_path):
    """Agent A's token must not create a scope request against agent B's id."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid_a = await _register_active(env, handle="@a", display="a")
        cid_b = await _register_active(env, handle="@b", display="b")
        token_a = env.agent_token(cid_a)
        app = client._transport.app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                f"/api/agents/registry/{cid_b}/scope-requests",
                headers={"Authorization": f"Bearer {token_a}"},
                json={"requested_scopes": ["a2a_send"]},
            )
        assert resp.status_code == 404, resp.text
        assert await env.scope_store.count_pending_for(cid_b) == 0
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_unknown_canonical_id_404(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        resp = await client.post(
            "/api/agents/registry/does-not-exist/scope-requests",
            json={"requested_scopes": ["memory_read"]},
        )
        assert resp.status_code == 404, resp.text
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_inactive_canonical_id_404(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        await env.registry.revoke(cid)  # now inactive
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests",
            json={"requested_scopes": ["memory_read"]},
        )
        assert resp.status_code == 404, resp.text
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_requested_scope_outside_vocabulary_400(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests",
            json={"requested_scopes": ["root_everything"]},
        )
        assert resp.status_code == 400, resp.text
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Approve / deny guards
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_granted_must_be_subset_of_requested_400(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["memory_read"]
        )
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
            json={"granted_scopes": ["memory_read", "memory_write"]},
        )
        assert resp.status_code == 400, resp.text
        # Nothing was granted on the rejected approval.
        assert await env.grants.list_grants(cid) == []
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_global_decisions_write_grant_works(client, monkeypatch, tmp_path):
    """decisions_write is global-capable: a null-project grant is allowed and no
    project_id is required."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["decisions_read", "decisions_write"]
        )
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
            json={"granted_scopes": ["decisions_read", "decisions_write"]},
        )
        assert resp.status_code == 200, resp.text
        grants = await env.grants.list_grants(cid)
        assert {g["scope"] for g in grants} == {"decisions_read", "decisions_write"}
        assert all(g["project_id"] is None for g in grants)  # global grants
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_project_scope_requires_project_id_400(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["project_tasks"]
        )
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
            json={"granted_scopes": ["project_tasks"]},
        )
        assert resp.status_code == 400, resp.text
        assert "project_id" in resp.text
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_non_owner_cannot_approve(client, monkeypatch, tmp_path):
    """A non-admin user who does not own the agent cannot approve."""
    app = client._transport.app
    # Create a non-admin second user and its session.
    code = app.state.auth.add_user_invite("bob", "admin")
    app.state.auth.complete_invite("bob", code, "Bob", "", "bobpass123")
    bob = app.state.auth.find_user("bob")
    bob_session = app.state.auth.create_session(user_id=bob["id"], long_lived=True)

    # The agent is owned by the admin, not bob.
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["memory_read"]
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": bob_session},
            event_hooks=csrf_event_hooks(),
        ) as bob_client:
            resp = await bob_client.post(
                f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
                json={"granted_scopes": ["memory_read"]},
            )
        assert resp.status_code == 404, resp.text
        assert await env.grants.list_grants(cid) == []
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_agent_cannot_approve_its_own_request(client, monkeypatch, tmp_path):
    """The agent's own registry token cannot reach the approve endpoint: the
    middleware allowlist only exposes the create path to a registry JWT, so an
    agent token on /approve falls through to the session gate and is rejected."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["memory_read"]
        )
        token = env.agent_token(cid)
        app = client._transport.app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
                headers={"Authorization": f"Bearer {token}"},
                json={"granted_scopes": ["memory_read"]},
            )
        # The middleware allowlist only exposes the create path to a registry JWT;
        # an agent token on /approve is NOT matched (the regex anchors at the
        # scope-requests segment), so it falls through to the session gate and
        # returns 401 (Authentication required), not 403.
        assert resp.status_code == 401, resp.text
        assert await env.grants.list_grants(cid) == []
        # Request is still pending — the agent could not self-approve.
        assert (await env.scope_store.get(rec["id"]))["status"] == "pending"
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_idempotent_reapprove_no_duplicate_grant_or_identity(
    client, monkeypatch, tmp_path
):
    """Approving a scope already granted (via a fresh request) is a no-op on the
    grant table (UNIQUE key) and never creates a second identity."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        before = len(await env.registry.list_all())

        for _ in range(2):
            rec = await env.scope_store.create(
                canonical_id=cid, requested_scopes=["decisions_write"]
            )
            resp = await client.post(
                f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
                json={"granted_scopes": ["decisions_write"]},
            )
            assert resp.status_code == 200, resp.text

        grants = await env.grants.list_grants(cid)
        # Exactly one grant row for (cid, decisions_write, NULL) despite two approvals.
        assert [g["scope"] for g in grants] == ["decisions_write"]
        assert len(await env.registry.list_all()) == before  # still no new identity
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_deny_marks_refused_and_second_deny_conflicts(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["memory_read"]
        )
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/deny",
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "refused"
        assert await env.grants.list_grants(cid) == []

        # A second deny on an already-decided request is a 409.
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/deny",
        )
        assert resp.status_code == 409, resp.text
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_approve_wrong_agent_path_404(client, monkeypatch, tmp_path):
    """A request id that belongs to a different canonical_id is not found under
    this agent's path (no cross-agent approval)."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid_a = await _register_active(env, handle="@a", display="a")
        cid_b = await _register_active(env, handle="@b", display="b")
        rec = await env.scope_store.create(
            canonical_id=cid_a, requested_scopes=["memory_read"]
        )
        resp = await client.post(
            f"/api/agents/registry/{cid_b}/scope-requests/{rec['id']}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 404, resp.text
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_global_scope_ignores_agent_supplied_project_id(client, monkeypatch, tmp_path):
    """A global-capable scope requested with an agent-named project_id must NOT
    bind the grant to that unvalidated project when the operator approves without
    an explicit project_id: it is granted globally (project_id=None). Guards the
    cross-project escalation the old agent-supplied fallback allowed."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        # The agent names a project it should not reach, on the request itself.
        rec = await env.scope_store.create(
            canonical_id=cid,
            requested_scopes=["decisions_write"],
            project_id="prj-victim",
        )
        # Operator approves WITHOUT an explicit body.project_id.
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
            json={"granted_scopes": ["decisions_write"]},
        )
        assert resp.status_code == 200, resp.text
        grants = await env.grants.list_grants(cid)
        assert {g["scope"] for g in grants} == {"decisions_write"}
        # Bound globally, NEVER to the agent-named project.
        assert all(g["project_id"] is None for g in grants)
        assert all(g["project_id"] != "prj-victim" for g in grants)
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_create_authorizes_before_scope_vocab(client, monkeypatch, tmp_path):
    """An unauthorized caller is rejected BEFORE scope-vocabulary validation, so an
    authz failure cannot leak whether a scope name is valid."""
    app = client._transport.app
    code = app.state.auth.add_user_invite("carol", "admin")
    app.state.auth.complete_invite("carol", code, "Carol", "", "carolpass123")
    carol = app.state.auth.find_user("carol")
    carol_session = app.state.auth.create_session(user_id=carol["id"], long_lived=True)

    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)  # owned by admin, not carol
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": carol_session},
            event_hooks=csrf_event_hooks(),
        ) as carol_client:
            resp = await carol_client.post(
                f"/api/agents/registry/{cid}/scope-requests",
                json={"requested_scopes": ["not_a_real_scope"]},
            )
        # Authz runs first -> 403, NOT a 400 vocab error confirming the bad scope.
        assert resp.status_code == 404, resp.text
        assert "not_a_real_scope" not in resp.text
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_agent_cannot_deny_its_own_request(client, monkeypatch, tmp_path):
    """The agent's own registry token cannot reach the deny endpoint: the
    middleware allowlist only exposes the create path to a registry JWT, so an
    agent token on /deny falls through to the session gate and is rejected."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["memory_read"]
        )
        token = env.agent_token(cid)
        app = client._transport.app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/deny",
                headers={"Authorization": f"Bearer {token}"},
            )
        # Same auth model as approve: middleware does not pass a registry JWT
        # through to the deny handler, so it falls to the session gate → 401.
        assert resp.status_code == 401, resp.text
        assert await env.grants.list_grants(cid) == []
        # Request is still pending — the agent could not self-deny.
        assert (await env.scope_store.get(rec["id"]))["status"] == "pending"
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_approved_scope_grant_unlocks_route_e2e(client, monkeypatch, tmp_path):
    """End-to-end: agent requests decisions_write scope, admin approves, then
    the agent's token actually reaches the decisions endpoint with a 200.
    This proves the grant enforcement chain is wired end to end — the scope
    request flow, the grant store, and the middleware + route handler all
    cooperate so the agent can use the granted scope.  Guards against the
    class of bug where a grant appears to land but the enforcement path never
    checks it (regression guard for issue #2095)."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)

        # Step 1: agent self-requests decisions_write
        token = env.agent_token(cid)
        app = client._transport.app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                f"/api/agents/registry/{cid}/scope-requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"requested_scopes": ["decisions_write"]},
            )
        assert resp.status_code == 200, resp.text
        req_id = resp.json()["request_id"]

        # Step 2: admin approves the scope request
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{req_id}/approve",
            json={"granted_scopes": ["decisions_write"]},
        )
        assert resp.status_code == 200, resp.text

        # Step 3: agent uses the granted scope on the decisions POST endpoint
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                "/api/decisions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "from_agent": cid,
                    "question": "Should we deploy?",
                    "type": "approve_deny",
                    "priority": "normal",
                },
            )
        assert resp.status_code == 200, resp.text
        decision = resp.json()
        assert decision.get("from_agent") == cid
        assert decision.get("question") == "Should we deploy?"
    finally:
        await env.close()


def test_project_scope_set_is_a_single_definition():
    """Which scopes require a project binding must be defined exactly once.

    This existed as three parallel copies (two function-local, one module-level).
    Fixing only the module-level one left the auth-request approval path -- the
    path an invite actually takes -- still granting files_* and
    project_tasks_create globally: grants written with project_id=None, which
    check_agent_scope_for_project never matches, so the operator sees a
    successful approval and the agent silently has no access.

    Aliases are checked by identity, not equality, so a re-introduced copy that
    happens to agree today still fails here rather than drifting later.
    """
    from tinyagentos.routes import agent_auth_requests as mod

    assert mod._PROJECT_SCOPES == {
        "project_tasks",
        "project_tasks_create",
        "project_tasks_update",
        "project_lists",
        "project_notes",
        "canvas_read",
        "canvas_write",
        "files_read",
        "files_write",
    }
    assert mod._SCOPE_PROJECT_SCOPES is mod._PROJECT_SCOPES
    assert mod._SCOPE_CANVAS_SCOPES is mod._CANVAS_SCOPES
    assert mod._SCOPE_FILES_SCOPES is mod._FILES_SCOPES

    # No shadowing re-definition anywhere in the module source.
    import inspect
    import re

    src = inspect.getsource(mod)
    for name in ("_CANVAS_SCOPES", "_FILES_SCOPES", "_PROJECT_SCOPES"):
        assigns = re.findall(rf"^\s*{name}\s*=", src, re.MULTILINE)
        assert len(assigns) == 1, f"{name} is assigned {len(assigns)} times, expected 1"


def test_every_project_bound_scope_is_a_valid_scope():
    """The project-scope set must not name a scope the vocabulary rejects.

    A typo here fails open in the confusing direction: the scope never matches
    the needs_project check, so it is granted globally without anyone noticing.
    """
    from tinyagentos.routes.agent_auth_requests import VALID_SCOPES, _PROJECT_SCOPES

    assert _PROJECT_SCOPES <= set(VALID_SCOPES)


# ---------------------------------------------------------------------------
# Existence-hiding: non-owner vs non-existent must be byte-identical
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_scope_request_non_owner_and_nonexistent_identical(
    client, monkeypatch, tmp_path
):
    """An authenticated non-owner and a nonexistent canonical_id must produce
    byte-identical responses on create_scope_request (status + body)."""
    app = client._transport.app
    code = app.state.auth.add_user_invite("carol", "admin")
    app.state.auth.complete_invite("carol", code, "Carol", "", "carpass123")
    carol = app.state.auth.find_user("carol")
    carol_session = app.state.auth.create_session(user_id=carol["id"], long_lived=True)

    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)  # owned by admin, not carol

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": carol_session},
            event_hooks=csrf_event_hooks(),
        ) as carol_client:
            resp_owner = await carol_client.post(
                f"/api/agents/registry/{cid}/scope-requests",
                json={"requested_scopes": ["memory_read"]},
            )
            # Same caller for the nonexistent probe: an admin would 404 on a
            # missing id too, but the contract under test is what ONE
            # unprivileged caller can distinguish.
            resp_nonexistent = await carol_client.post(
                "/api/agents/registry/does-not-exist/scope-requests",
                json={"requested_scopes": ["memory_read"]},
            )

        assert resp_owner.status_code == resp_nonexistent.status_code == 404
        assert resp_owner.content == resp_nonexistent.content
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_approve_scope_request_non_owner_and_nonexistent_identical(
    client, monkeypatch, tmp_path
):
    """An authenticated non-owner and a nonexistent canonical_id must produce
    byte-identical responses on approve_scope_request (status + body)."""
    app = client._transport.app
    code = app.state.auth.add_user_invite("carol", "admin")
    app.state.auth.complete_invite("carol", code, "Carol", "", "carpass123")
    carol = app.state.auth.find_user("carol")
    carol_session = app.state.auth.create_session(user_id=carol["id"], long_lived=True)

    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)  # owned by admin, not carol
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["memory_read"]
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": carol_session},
            event_hooks=csrf_event_hooks(),
        ) as carol_client:
            resp_owner = await carol_client.post(
                f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
                json={"granted_scopes": ["memory_read"]},
            )
            resp_nonexistent = await carol_client.post(
                "/api/agents/registry/does-not-exist/scope-requests/does-not-exist/approve",
                json={"granted_scopes": ["memory_read"]},
            )

        assert resp_owner.status_code == resp_nonexistent.status_code == 404
        assert resp_owner.content == resp_nonexistent.content
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_deny_scope_request_non_owner_and_nonexistent_identical(
    client, monkeypatch, tmp_path
):
    """An authenticated non-owner and a nonexistent canonical_id must produce
    byte-identical responses on deny_scope_request (status + body)."""
    app = client._transport.app
    code = app.state.auth.add_user_invite("carol", "admin")
    app.state.auth.complete_invite("carol", code, "Carol", "", "carpass123")
    carol = app.state.auth.find_user("carol")
    carol_session = app.state.auth.create_session(user_id=carol["id"], long_lived=True)

    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)  # owned by admin, not carol
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["memory_read"]
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": carol_session},
            event_hooks=csrf_event_hooks(),
        ) as carol_client:
            resp_owner = await carol_client.post(
                f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/deny",
            )
            resp_nonexistent = await carol_client.post(
                "/api/agents/registry/does-not-exist/scope-requests/does-not-exist/deny",
            )

        assert resp_owner.status_code == resp_nonexistent.status_code == 404
        assert resp_owner.content == resp_nonexistent.content
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_create_scope_request_inactive_token_no_existence_oracle(
    client, monkeypatch, tmp_path
):
    """A suspended agent's (validly signed) token must get the SAME response
    for an existing target as for a nonexistent one. Before the fix,
    check_agent_identity's 403 surfaced only when the target existed (an
    unknown target 404s first), disclosing existence through the
    credential-error path."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid_target = await _register_active(env, handle="@target", display="target")
        cid_b = await _register_active(env, handle="@suspended", display="suspended")
        token_b = env.agent_token(cid_b)
        await env.registry.set_status(cid_b, "suspended", actor="test")

        app = client._transport.app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp_existing = await bare.post(
                f"/api/agents/registry/{cid_target}/scope-requests",
                headers={"Authorization": f"Bearer {token_b}"},
                json={"requested_scopes": ["a2a_send"]},
            )
            resp_missing = await bare.post(
                "/api/agents/registry/does-not-exist/scope-requests",
                headers={"Authorization": f"Bearer {token_b}"},
                json={"requested_scopes": ["a2a_send"]},
            )

        assert resp_existing.status_code == resp_missing.status_code == 404
        assert resp_existing.content == resp_missing.content
        assert await env.scope_store.count_pending_for(cid_target) == 0
    finally:
        await env.close()
