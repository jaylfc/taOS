"""Tests for the external-agent consent loop (Phase 1).

Covers:
  Store:
    - create → pending record returned
    - set_decision: pending → accepted
    - set_decision: pending → refused
    - set_decision: already decided → returns None (idempotency / conflict guard)
    - list_pending: only pending rows
    - count_pending_for: abuse-cap helper

  Routes:
    - POST create (no auth) → {request_id, status: 'pending'}
    - GET status pending (no auth, no token in response)
    - Admin approve → canonical_id minted + grants written + status accepted
    - GET status accepted → token returned
    - Non-admin approve → 403
    - Approve unknown → 404
    - Approve already-decided → 409
    - Deny → refused
    - Deny already-decided → 409
    - Abuse cap → 429
    - List pending (admin) → returns pending records
    - List pending (non-admin) → 403
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.auth_requests_store import AuthRequestsStore
from tinyagentos.agent_grants_store import AgentGrantsStore


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAuthRequestsStore:

    async def _make_store(self, db_path):
        store = AuthRequestsStore(db_path)
        await store.init()
        return store

    async def test_create_returns_pending_record(self, tmp_path):
        store = await self._make_store(tmp_path / "ar.db")
        try:
            record = await store.create(
                identity_claim="my-agent",
                framework="hermes",
                requested_scopes=["memory_read", "memory_write"],
                reason="need access to memory",
            )
            assert record["status"] == "pending"
            assert record["identity_claim"] == "my-agent"
            assert record["framework"] == "hermes"
            assert record["requested_scopes"] == ["memory_read", "memory_write"]
            assert record["reason"] == "need access to memory"
            assert record["canonical_id"] is None
            assert record["token"] is None
            assert record["granted_scopes"] is None
            assert record["decided_ts"] is None
            assert record["id"] is not None
        finally:
            await store.close()

    async def test_set_decision_accepted(self, tmp_path):
        store = await self._make_store(tmp_path / "ar.db")
        try:
            record = await store.create(
                identity_claim="agent-a",
                framework="openclaw",
                requested_scopes=["memory_read"],
            )
            updated = await store.set_decision(
                record["id"],
                "accepted",
                canonical_id="agent-a-20260609-120000",
                token="fake.token.value",
                granted_scopes=["memory_read"],
                decided_by="admin-user",
            )
            assert updated is not None
            assert updated["status"] == "accepted"
            assert updated["canonical_id"] == "agent-a-20260609-120000"
            assert updated["token"] == "fake.token.value"
            assert updated["granted_scopes"] == ["memory_read"]
            assert updated["decided_by"] == "admin-user"
            assert updated["decided_ts"] is not None
        finally:
            await store.close()

    async def test_set_decision_refused(self, tmp_path):
        store = await self._make_store(tmp_path / "ar.db")
        try:
            record = await store.create(
                identity_claim="agent-b",
                framework="hermes",
                requested_scopes=["memory_read"],
            )
            updated = await store.set_decision(
                record["id"],
                "refused",
                decided_by="admin-user",
            )
            assert updated is not None
            assert updated["status"] == "refused"
            assert updated["canonical_id"] is None
            assert updated["token"] is None
        finally:
            await store.close()

    async def test_set_decision_already_decided_returns_none(self, tmp_path):
        """Second decision on an already-decided request returns None (atomic guard)."""
        store = await self._make_store(tmp_path / "ar.db")
        try:
            record = await store.create(
                identity_claim="agent-c",
                framework="hermes",
                requested_scopes=["memory_read"],
            )
            # First decision succeeds.
            first = await store.set_decision(
                record["id"], "accepted",
                canonical_id="cid", token="tok",
                granted_scopes=[], decided_by="admin",
            )
            assert first is not None
            # Second decision returns None — already decided.
            second = await store.set_decision(
                record["id"], "refused",
                decided_by="admin",
            )
            assert second is None
        finally:
            await store.close()

    async def test_list_pending_only_returns_pending(self, tmp_path):
        store = await self._make_store(tmp_path / "ar.db")
        try:
            r1 = await store.create(identity_claim="a", framework="f", requested_scopes=[])
            r2 = await store.create(identity_claim="b", framework="f", requested_scopes=[])
            r3 = await store.create(identity_claim="c", framework="f", requested_scopes=[])

            # Decide r2.
            await store.set_decision(r2["id"], "accepted",
                                     canonical_id="cid", token="tok",
                                     granted_scopes=[], decided_by="admin")

            pending = await store.list_pending()
            pending_ids = {r["id"] for r in pending}
            assert r1["id"] in pending_ids
            assert r3["id"] in pending_ids
            assert r2["id"] not in pending_ids
        finally:
            await store.close()

    async def test_count_pending_for(self, tmp_path):
        store = await self._make_store(tmp_path / "ar.db")
        try:
            await store.create(identity_claim="agent-x", framework="hermes", requested_scopes=[])
            await store.create(identity_claim="agent-x", framework="hermes", requested_scopes=[])
            await store.create(identity_claim="agent-y", framework="hermes", requested_scopes=[])

            assert await store.count_pending_for("agent-x", "hermes") == 2
            assert await store.count_pending_for("agent-y", "hermes") == 1
            assert await store.count_pending_for("nobody", "hermes") == 0
        finally:
            await store.close()

    async def test_invalid_decision_status_raises(self, tmp_path):
        store = await self._make_store(tmp_path / "ar.db")
        try:
            record = await store.create(
                identity_claim="agent-d", framework="f", requested_scopes=[]
            )
            with pytest.raises(ValueError, match="accepted.*refused"):
                await store.set_decision(record["id"], "pending", decided_by="admin")
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# Route fixture — mirrors registry_client pattern
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def consent_client(app, tmp_data_dir):
    """Async client with auth_requests + agent_grants + agent_registry stores
    initialised, authenticated as admin.  Mirrors registry_client in
    test_agent_registry.py.
    """
    # Init required stores (lifespan not running in tests)
    registry_store = app.state.agent_registry
    if registry_store._db is None:
        await registry_store.init()

    auth_requests = app.state.auth_requests
    if auth_requests._db is None:
        await auth_requests.init()

    agent_grants = app.state.agent_grants
    if agent_grants._db is None:
        await agent_grants.init()

    relationship_mgr = app.state.relationships
    if relationship_mgr._db is None:
        await relationship_mgr.init()

    metrics_store = app.state.metrics
    if metrics_store._db is None:
        await metrics_store.init()

    # Set up admin user + session
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        c._test_admin_uid = uid
        yield c

    await auth_requests.close()
    await agent_grants.close()
    await registry_store.close()
    await relationship_mgr.close()
    await metrics_store.close()


@pytest_asyncio.fixture
async def consent_client_nonadmin(app, tmp_data_dir):
    """Async client authenticated as a non-admin member."""
    registry_store = app.state.agent_registry
    if registry_store._db is None:
        await registry_store.init()

    auth_requests = app.state.auth_requests
    if auth_requests._db is None:
        await auth_requests.init()

    agent_grants = app.state.agent_grants
    if agent_grants._db is None:
        await agent_grants.init()

    relationship_mgr = app.state.relationships
    if relationship_mgr._db is None:
        await relationship_mgr.init()

    metrics_store = app.state.metrics
    if metrics_store._db is None:
        await metrics_store.init()

    # Set up admin first (required by auth manager), then a member via invite.
    auth_mgr = app.state.auth
    auth_mgr.setup_user("admin", "Test Admin", "", "testpass")
    invite_code = auth_mgr.add_user_invite("member", "admin")
    auth_mgr.complete_invite("member", invite_code, "Test Member", "", "memberpass123")
    member_record = auth_mgr.find_user("member")
    member_uid = member_record["id"] if member_record else ""
    member_token = auth_mgr.create_session(user_id=member_uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": member_token},
    ) as c:
        yield c

    await auth_requests.close()
    await agent_grants.close()
    await registry_store.close()
    await relationship_mgr.close()
    await metrics_store.close()


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

_CREATE_BODY = {
    "identity_claim": "my-external-agent",
    "framework": "hermes",
    "requested_scopes": ["memory_read", "memory_write"],
    "reason": "I need access to memory for context",
}


@pytest.mark.asyncio
class TestAuthRequestRoutes:

    async def test_create_no_auth_returns_pending(self, consent_client):
        """POST with no auth must succeed (EXEMPT path) and return pending."""
        # Use a bare client without cookies to confirm no-auth path works.
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as bare:
            resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
        assert resp.status_code == 200
        data = resp.json()
        assert "request_id" in data
        assert data["status"] == "pending"

    async def test_get_status_pending_no_token(self, consent_client):
        """GET status on a pending request returns status but NOT a token."""
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
            request_id = create_resp.json()["request_id"]
            status_resp = await bare.get(f"/api/agents/auth-requests/{request_id}")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["status"] == "pending"
        assert "token" not in data
        assert "canonical_id" not in data

    async def test_approve_mints_identity_and_grants(self, consent_client):
        """Admin approve → canonical_id minted + grants recorded + status accepted."""
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
            request_id = create_resp.json()["request_id"]

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert "canonical_id" in data
        canonical_id = data["canonical_id"]

        # Check grants were written.
        grants = await consent_client._transport.app.state.agent_grants.list_grants(canonical_id)
        assert any(g["scope"] == "memory_read" for g in grants)

        # Approval must ACTIVATE the agent — external-selfjoin lands 'pending';
        # consent-approve transitions it to 'active' so it's not in the bus
        # inactive feed. (Regression guard.)
        reg = await consent_client._transport.app.state.agent_registry.get(canonical_id)
        assert reg is not None and reg["status"] == "active"

        # Token not returned by approve endpoint — only by the status poll.
        assert "token" not in data

    async def test_get_status_accepted_returns_token(self, consent_client):
        """After accept, status poll returns canonical_id and token."""
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
            request_id = create_resp.json()["request_id"]

        await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read"]},
        )

        # Poll status as the external agent would (no auth).
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            status_resp = await bare.get(f"/api/agents/auth-requests/{request_id}")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["status"] == "accepted"
        assert "canonical_id" in data
        assert "token" in data
        # Token must look like a JWT (3 dot-separated parts).
        assert data["token"].count(".") == 2

    async def test_non_admin_approve_returns_403(self, consent_client_nonadmin):
        """Non-admin calling approve must get 403."""
        # First create a request via the nonadmin client (still works — EXEMPT).
        transport = ASGITransport(app=consent_client_nonadmin._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
            request_id = create_resp.json()["request_id"]

        resp = await consent_client_nonadmin.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 403

    async def test_create_with_unknown_scope_returns_400(self, consent_client):
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            resp = await bare.post(
                "/api/agents/auth-requests",
                json={**_CREATE_BODY, "requested_scopes": ["memory_read", "root_shell"]},
            )
        assert resp.status_code == 400
        assert "root_shell" in resp.json()["detail"]

    async def test_approve_scope_not_requested_returns_400(self, consent_client):
        """The admin can narrow the requested scopes but never widen them."""
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
            request_id = create_resp.json()["request_id"]

        # _CREATE_BODY requests memory_read + memory_write only.
        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read", "tools_execute"]},
        )
        assert resp.status_code == 400
        assert "tools_execute" in resp.json()["detail"]

        # The request must still be pending (no side effects from the rejection).
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            status_resp = await bare.get(f"/api/agents/auth-requests/{request_id}")
        assert status_resp.json()["status"] == "pending"

    async def test_approve_unknown_request_returns_404(self, consent_client):
        resp = await consent_client.post(
            "/api/agents/auth-requests/doesnotexist/approve",
            json={"granted_scopes": []},
        )
        assert resp.status_code == 404

    async def test_approve_already_decided_returns_409(self, consent_client):
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
            request_id = create_resp.json()["request_id"]

        # First approve succeeds.
        await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        # Second approve → 409 (already decided).
        resp2 = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp2.status_code == 409

    async def test_deny_returns_refused(self, consent_client):
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
            request_id = create_resp.json()["request_id"]

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/deny"
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "refused"

        # Poll confirms refused — and no token.
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            status_resp = await bare.get(f"/api/agents/auth-requests/{request_id}")
        assert status_resp.json()["status"] == "refused"
        assert "token" not in status_resp.json()

    async def test_deny_already_decided_returns_409(self, consent_client):
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
            request_id = create_resp.json()["request_id"]

        await consent_client.post(f"/api/agents/auth-requests/{request_id}/deny")
        resp2 = await consent_client.post(f"/api/agents/auth-requests/{request_id}/deny")
        assert resp2.status_code == 409

    async def test_abuse_cap_returns_429(self, consent_client):
        """Submitting more than _PENDING_CAP requests from the same identity → 429."""
        from tinyagentos.routes.agent_auth_requests import _PENDING_CAP

        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            for _ in range(_PENDING_CAP):
                r = await bare.post(
                    "/api/agents/auth-requests",
                    json={**_CREATE_BODY, "identity_claim": "flood-agent"},
                )
                assert r.status_code == 200

            # The (_PENDING_CAP + 1)th request must be rate-limited.
            r = await bare.post(
                "/api/agents/auth-requests",
                json={**_CREATE_BODY, "identity_claim": "flood-agent"},
            )
            assert r.status_code == 429

    async def test_list_pending_admin(self, consent_client):
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
            await bare.post("/api/agents/auth-requests",
                            json={**_CREATE_BODY, "identity_claim": "agent-2"})

        resp = await consent_client.get("/api/agents/auth-requests")
        assert resp.status_code == 200
        data = resp.json()
        assert "requests" in data
        assert len(data["requests"]) >= 2

    async def test_list_pending_nonadmin_returns_403(self, consent_client_nonadmin):
        resp = await consent_client_nonadmin.get("/api/agents/auth-requests")
        assert resp.status_code == 403

    async def test_get_status_unknown_returns_404(self, consent_client):
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            resp = await bare.get("/api/agents/auth-requests/no-such-id")
        assert resp.status_code == 404

    async def test_list_unsupported_status_returns_400(self, consent_client):
        resp = await consent_client.get("/api/agents/auth-requests?status=all")
        assert resp.status_code == 400

    async def test_concurrent_approve_only_one_wins(self, consent_client):
        """Two simultaneous approvals of the same request: one 200, one 409."""
        import asyncio
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
        request_id = create_resp.json()["request_id"]

        approve_body = {"granted_scopes": ["memory_read"]}
        cookies = dict(consent_client.cookies)

        async def do_approve():
            async with AsyncClient(
                transport=ASGITransport(app=consent_client._transport.app),
                base_url="http://test",
                cookies=cookies,
            ) as c:
                return await c.post(
                    f"/api/agents/auth-requests/{request_id}/approve",
                    json=approve_body,
                )

        r1, r2 = await asyncio.gather(do_approve(), do_approve())
        codes = sorted([r1.status_code, r2.status_code])
        assert codes == [200, 409], f"expected [200, 409], got {codes}"

    async def test_grants_feed_populated_after_approve(self, consent_client):
        """After approval the grants feed returns the approved scopes."""
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
        request_id = create_resp.json()["request_id"]

        await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read", "memory_write"]},
        )

        resp = await consent_client.get("/api/agents/registry/grants")
        assert resp.status_code == 200
        grants = resp.json()["grants"]
        scopes = {g["scope"] for g in grants}
        assert "memory_read" in scopes
        assert "memory_write" in scopes

    async def test_grants_feed_nonadmin_returns_403(self, consent_client_nonadmin):
        resp = await consent_client_nonadmin.get("/api/agents/registry/grants")
        assert resp.status_code == 403

    async def test_grants_feed_filtered_by_canonical_id(self, consent_client):
        """?canonical_id= filter returns only that agent's grants."""
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            r = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
        request_id = r.json()["request_id"]

        approve_resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        canonical_id = approve_resp.json()["canonical_id"]

        resp = await consent_client.get(
            f"/api/agents/registry/grants?canonical_id={canonical_id}"
        )
        assert resp.status_code == 200
        grants = resp.json()["grants"]
        assert all(g["canonical_id"] == canonical_id for g in grants)

    async def test_approve_lock_is_evicted_after_decision(self, consent_client):
        """After approval the per-request lock entry is removed from the dict."""
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
            request_id = create_resp.json()["request_id"]

        await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read"]},
        )

        # After a terminal decision the lock entry must be absent so the dict
        # does not grow unbounded over the process lifetime.
        app = consent_client._transport.app
        assert getattr(app.state, "_approve_locks", {}).get(request_id) is None

    async def test_project_id_carried_into_jwt_and_grants(self, consent_client):
        """Request with project_id: JWT carries the claim; grant rows carry it too."""
        import base64

        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post(
                "/api/agents/auth-requests",
                json={**_CREATE_BODY, "project_id": "proj-abc"},
            )
        request_id = create_resp.json()["request_id"]

        approve_resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert approve_resp.status_code == 200
        canonical_id = approve_resp.json()["canonical_id"]

        # Retrieve the minted token via the status poll.
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            status_resp = await bare.get(f"/api/agents/auth-requests/{request_id}")
        token = status_resp.json()["token"]

        # Decode the JWT payload (middle part, base64url without padding).
        raw = token.split(".")[1]
        padding = 4 - len(raw) % 4
        if padding != 4:
            raw += "=" * padding
        import json as _json
        jwt_payload = _json.loads(base64.urlsafe_b64decode(raw))
        assert jwt_payload.get("project_id") == "proj-abc"

        # Grant rows must also carry the project binding.
        grants = await consent_client._transport.app.state.agent_grants.list_grants(canonical_id)
        assert all(g["project_id"] == "proj-abc" for g in grants)

    async def test_approve_body_project_id_overrides_request(self, consent_client):
        """ApproveBody.project_id overrides the project_id from the original request."""
        import base64
        import json as _json

        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post(
                "/api/agents/auth-requests",
                json={**_CREATE_BODY, "project_id": "proj-original"},
            )
        request_id = create_resp.json()["request_id"]

        approve_resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read"], "project_id": "proj-override"},
        )
        assert approve_resp.status_code == 200
        canonical_id = approve_resp.json()["canonical_id"]

        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            status_resp = await bare.get(f"/api/agents/auth-requests/{request_id}")
        token = status_resp.json()["token"]

        raw = token.split(".")[1]
        padding = 4 - len(raw) % 4
        if padding != 4:
            raw += "=" * padding
        jwt_payload = _json.loads(base64.urlsafe_b64decode(raw))
        assert jwt_payload.get("project_id") == "proj-override"

        grants = await consent_client._transport.app.state.agent_grants.list_grants(canonical_id)
        assert all(g["project_id"] == "proj-override" for g in grants)

    async def test_no_project_id_omits_claim_and_grant_is_null(self, consent_client):
        """When no project_id is set anywhere, JWT has no project_id key and grants have None."""
        import base64
        import json as _json

        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
        request_id = create_resp.json()["request_id"]

        approve_resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert approve_resp.status_code == 200
        canonical_id = approve_resp.json()["canonical_id"]

        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            status_resp = await bare.get(f"/api/agents/auth-requests/{request_id}")
        token = status_resp.json()["token"]

        raw = token.split(".")[1]
        padding = 4 - len(raw) % 4
        if padding != 4:
            raw += "=" * padding
        jwt_payload = _json.loads(base64.urlsafe_b64decode(raw))
        assert "project_id" not in jwt_payload

        grants = await consent_client._transport.app.state.agent_grants.list_grants(canonical_id)
        assert all(g.get("project_id") is None for g in grants)
