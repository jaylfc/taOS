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
    - Abuse cap → 429 (including under a concurrent burst)
    - List pending (admin) → returns pending records
    - List pending (non-admin) → 403
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

from tinyagentos.auth_requests_store import AuthRequestsStore
from tinyagentos.agent_registry_store import mint_registry_token, verify_registry_token
from tinyagentos.base_store import PendingCapExceeded
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
                requested_scopes=["a2a_send", "a2a_receive"],
                reason="need access to memory",
            )
            assert record["status"] == "pending"
            assert record["identity_claim"] == "my-agent"
            assert record["framework"] == "hermes"
            assert record["requested_scopes"] == ["a2a_send", "a2a_receive"]
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
                requested_scopes=["a2a_send"],
            )
            updated = await store.set_decision(
                record["id"],
                "accepted",
                canonical_id="agent-a-20260609-120000",
                token="fake.token.value",
                granted_scopes=["a2a_send"],
                decided_by="admin-user",
            )
            assert updated is not None
            assert updated["status"] == "accepted"
            assert updated["canonical_id"] == "agent-a-20260609-120000"
            assert updated["token"] == "fake.token.value"
            assert updated["granted_scopes"] == ["a2a_send"]
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
                requested_scopes=["a2a_send"],
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
                requested_scopes=["a2a_send"],
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

    async def test_create_enforces_pending_cap_atomically(self, tmp_path):
        """``create(pending_cap=N)`` admits exactly N pending rows under a burst.

        The guard lives in the INSERT itself, so it holds however the caller's
        awaits interleave; a caller that counted first would hand every racer
        the same stale answer. ``pending_cap=None`` keeps the store uncapped
        for the callers that mint one row per freshly-deduped identity.
        """
        store = await self._make_store(tmp_path / "ar.db")
        try:
            results = await asyncio.gather(
                *[
                    store.create(
                        identity_claim="agent-x",
                        framework="hermes",
                        requested_scopes=[],
                        pending_cap=2,
                    )
                    for _ in range(6)
                ],
                return_exceptions=True,
            )
            created = [r for r in results if isinstance(r, dict)]
            refused = [r for r in results if isinstance(r, PendingCapExceeded)]
            assert len(created) == 2, f"expected 2 inserts, got {results}"
            assert len(refused) == 4, f"expected 4 refusals, got {results}"
            assert await store.count_pending_for("agent-x", "hermes") == 2

            # The cap is per (identity_claim, framework), not global.
            other = await store.create(
                identity_claim="agent-x",
                framework="claude",
                requested_scopes=[],
                pending_cap=2,
            )
            assert other["framework"] == "claude"
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
        event_hooks=csrf_event_hooks(),
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
        event_hooks=csrf_event_hooks(),
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
    "requested_scopes": ["a2a_send", "a2a_receive"],
    "reason": "I need access to A2A bus for communication",
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

    async def test_create_carries_requested_project_id_in_notification(self, consent_client):
        """The consent notification must surface the requested project_id so the
        approve screen is never a blank dropdown: an access request bound to a
        project must let the approver see and confirm that project before
        granting (#tsk-flc5sp)."""
        notif_store = consent_client._transport.app.state.notifications
        if notif_store._db is None:
            await notif_store.init()
        try:
            transport = ASGITransport(app=consent_client._transport.app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as bare:
                resp = await bare.post(
                    "/api/agents/auth-requests",
                    json={**_CREATE_BODY, "project_id": "prj-btrdrl"},
                )
            assert resp.status_code == 200
            items = await notif_store.list()
            auth = [i for i in items if i.get("source") == "auth_requests"]
            assert auth, "creating a request should raise an access-request notification"
            payload = auth[0]["data"]
            assert payload["project_id"] == "prj-btrdrl"
            assert payload["request_id"] == resp.json()["request_id"]
            # New: check that duration fields are present (they may be None for unbounded grants)
            assert "duration_secs" in payload
            assert "human_duration" in payload
            assert payload["human_duration"] == "no expiry"
        finally:
            if notif_store._db is not None:
                await notif_store.close()

    async def test_create_with_duration_carries_duration_in_notification(self, consent_client):
        """The consent notification must include human-readable duration when duration_secs is set."""
        notif_store = consent_client._transport.app.state.notifications
        if notif_store._db is None:
            await notif_store.init()
        try:
            transport = ASGITransport(app=consent_client._transport.app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as bare:
                # Create an auth request with a 1-hour duration
                resp = await bare.post(
                    "/api/agents/auth-requests",
                    json={**_CREATE_BODY, "duration_secs": 3600},  # 1 hour
                )
            assert resp.status_code == 200
            items = await notif_store.list()
            auth = [i for i in items if i.get("source") == "auth_requests"]
            assert auth, "creating a request should raise an access-request notification"
            payload = auth[0]["data"]
            # Check that duration_secs is included in the payload
            assert "duration_secs" in payload
            assert payload["duration_secs"] == 3600
            # Check that human_duration is included and human-readable
            assert "human_duration" in payload
            assert payload["human_duration"] == "expires 1 hour after approval"
        finally:
            if notif_store._db is not None:
                await notif_store.close()


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
            json={"granted_scopes": ["a2a_send"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert "canonical_id" in data
        canonical_id = data["canonical_id"]

        # Check grants were written.
        grants = await consent_client._transport.app.state.agent_grants.list_grants(canonical_id)
        assert any(g["scope"] == "a2a_send" for g in grants)

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
            json={"granted_scopes": ["a2a_send"]},
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
            json={"granted_scopes": ["a2a_send"]},
        )
        assert resp.status_code == 403

    async def test_create_with_unknown_scope_returns_400(self, consent_client):
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            resp = await bare.post(
                "/api/agents/auth-requests",
                json={**_CREATE_BODY, "requested_scopes": ["a2a_send", "root_shell"]},
            )
        assert resp.status_code == 400
        assert "root_shell" in resp.json()["detail"]

    async def test_approve_scope_not_requested_returns_400(self, consent_client):
        """The admin can narrow the requested scopes but never widen them."""
        transport = ASGITransport(app=consent_client._transport.app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
            request_id = create_resp.json()["request_id"]

        # _CREATE_BODY requests a2a_send + a2a_receive only.
        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send", "files_write"]},
        )
        assert resp.status_code == 400
        assert "files_write" in resp.json()["detail"]

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
            json={"granted_scopes": ["a2a_send"]},
        )
        # Second approve → 409 (already decided).
        resp2 = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send"]},
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

    async def test_concurrent_creates_cannot_bypass_abuse_cap(self, consent_client):
        """A concurrent burst from one identity must not exceed _PENDING_CAP.

        The route is unauthenticated, so this is the cheapest flood there is:
        with a count-then-insert cap every request in the burst reads the same
        pre-insert count, passes, and inserts.
        """
        from tinyagentos.routes.agent_auth_requests import _PENDING_CAP

        app = consent_client._transport.app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            async def _create():
                return await bare.post(
                    "/api/agents/auth-requests",
                    json={**_CREATE_BODY, "identity_claim": "burst-agent"},
                )

            # No return_exceptions: a server-side crash must fail the test, not
            # shrink the status list into a vacuous pass.
            results = await asyncio.gather(
                *[_create() for _ in range(2 * _PENDING_CAP)]
            )

        statuses = sorted(r.status_code for r in results)
        assert statuses == [200] * _PENDING_CAP + [429] * _PENDING_CAP, (
            f"cap race: expected exactly {_PENDING_CAP} accepted, got {statuses}"
        )
        pending = await app.state.auth_requests.count_pending_for(
            "burst-agent", _CREATE_BODY["framework"]
        )
        assert pending == _PENDING_CAP

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

        approve_body = {"granted_scopes": ["a2a_send"]}
        cookies = dict(consent_client.cookies)

        async def do_approve():
            async with AsyncClient(
                transport=ASGITransport(app=consent_client._transport.app),
                base_url="http://test",
                cookies=cookies,
                event_hooks=csrf_event_hooks(),
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
            json={"granted_scopes": ["a2a_send", "a2a_receive"]},
        )

        resp = await consent_client.get("/api/agents/registry/grants")
        assert resp.status_code == 200
        grants = resp.json()["grants"]
        scopes = {g["scope"] for g in grants}
        assert "a2a_send" in scopes
        assert "a2a_receive" in scopes

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
            json={"granted_scopes": ["a2a_send"]},
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
            json={"granted_scopes": ["a2a_send"]},
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
            json={"granted_scopes": ["a2a_send"]},
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
            json={"granted_scopes": ["a2a_send"], "project_id": "proj-override"},
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
            json={"granted_scopes": ["a2a_send"]},
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

    async def test_approve_reuses_pending_identity_returns_200(self, consent_client):
        """taOS #2114: approving a request whose handle already belongs to a
        PENDING agent must REUSE that agent's canonical_id and return 200.

        This drives the whole flow end-to-end: the requester files the request
        with a real registry token for the pending identity, an admin approves,
        and the existing row is reactivated with exactly one registry row for
        the handle. Before the fix _classify_proof rejected the token because
        the identity was not active, so approval always returned 409.
        """
        app = consent_client._transport.app
        registry = app.state.agent_registry
        transport = ASGITransport(app=app)

        # external-selfjoin identities are born 'pending'; this row is a
        # half-finished earlier approval.
        reg_record = await registry.register(
            framework="hermes",
            display_name="Hermes",
            handle="hermes-reuse-test",
            origin="external-selfjoin",
        )
        original_cid = reg_record["canonical_id"]
        assert reg_record["status"] == "pending"

        # A real proof token for that canonical_id.
        private_pem, _public_pem = app.state.agent_registry_keypair
        proof = mint_registry_token(
            original_cid, private_pem, user_id="test", framework="hermes"
        )

        # Create an auth request for the same handle, proving the identity.
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post(
                "/api/agents/auth-requests",
                json={**_CREATE_BODY, "identity_claim": "hermes-reuse-test"},
                headers={"Authorization": f"Bearer {proof}"},
            )
            assert create_resp.status_code == 200
            assert create_resp.json()["proof_status"] == "accepted"
            request_id = create_resp.json()["request_id"]

        # Approve: must succeed (200) and reuse the pending canonical_id.
        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["canonical_id"] == original_cid

        # The reused agent is now active ...
        agent = await registry.get(original_cid)
        assert agent["status"] == "active"

        # ... and no second identity was minted for the handle.
        rows = [
            r
            for r in await registry.list_all()
            if r.get("handle") == "hermes-reuse-test"
        ]
        assert len(rows) == 1
        assert rows[0]["canonical_id"] == original_cid

        # The approval really completed: polling returns the accepted request
        # and a token for the reused identity.
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            status_resp = await bare.get(f"/api/agents/auth-requests/{request_id}")
        body = status_resp.json()
        assert body["status"] == "accepted"
        assert body["canonical_id"] == original_cid
        assert body["token"]
        # The token itself must authenticate as the reused identity: the
        # adjacent canonical_id field proves nothing about what it grants.
        claims = verify_registry_token(body["token"], _public_pem)
        assert claims["sub"] == original_cid

    async def test_approve_reuses_the_pending_identity_the_proof_names(
        self, consent_client
    ):
        """Two pending rows can share a normalised handle. The lookups find only
        one of them, so a valid token for the other must still pick its own
        identity instead of 409ing forever."""
        app = consent_client._transport.app
        registry = app.state.agent_registry
        transport = ASGITransport(app=app)

        other_spelling = await registry.register(
            framework="hermes",
            display_name="Hermes",
            handle="Hermes-Two-Rows",
            origin="external-selfjoin",
        )
        exact = await registry.register(
            framework="hermes",
            display_name="Hermes",
            handle="hermes-two-rows",
            origin="external-selfjoin",
        )
        assert other_spelling["status"] == exact["status"] == "pending"

        private_pem, _public_pem = app.state.agent_registry_keypair
        proof = mint_registry_token(
            other_spelling["canonical_id"], private_pem, user_id="test", framework="hermes"
        )
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post(
                "/api/agents/auth-requests",
                json={**_CREATE_BODY, "identity_claim": "hermes-two-rows"},
                headers={"Authorization": f"Bearer {proof}"},
            )
        assert create_resp.json()["proof_status"] == "accepted"

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{create_resp.json()['request_id']}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["canonical_id"] == other_spelling["canonical_id"]
        assert (await registry.get(exact["canonical_id"]))["status"] == "pending"

        # Polling must hand back a token whose effective identity is the
        # proof-named row, not the exact-spelling one: the canonical_id
        # field alone proves nothing about what the token authenticates as.
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            status_resp = await bare.get(
                f"/api/agents/auth-requests/{create_resp.json()['request_id']}"
            )
        body = status_resp.json()
        assert body["status"] == "accepted"
        assert body["canonical_id"] == other_spelling["canonical_id"]
        claims = verify_registry_token(body["token"], _public_pem)
        assert claims["sub"] == other_spelling["canonical_id"]

    @staticmethod
    def _deny_wins_the_race(app, monkeypatch):
        """A competing deny lands after the approve has done its writes, just
        before the approve commits its decision."""
        store = app.state.auth_requests
        real = store.set_decision

        async def racing(request_id, decision, **kwargs):
            if decision == "accepted":
                assert await real(request_id, "refused", decided_by="other-admin")
            return await real(request_id, decision, **kwargs)

        monkeypatch.setattr(store, "set_decision", racing)

    async def _pending_proof_request(self, consent_client, handle, **create_overrides):
        app = consent_client._transport.app
        reg = await app.state.agent_registry.register(
            framework="hermes",
            display_name="Hermes",
            handle=handle,
            origin="external-selfjoin",
        )
        private_pem, _public_pem = app.state.agent_registry_keypair
        proof = mint_registry_token(
            reg["canonical_id"], private_pem, user_id="test", framework="hermes"
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            resp = await bare.post(
                "/api/agents/auth-requests",
                json={**_CREATE_BODY, "identity_claim": handle, **create_overrides},
                headers={"Authorization": f"Bearer {proof}"},
            )
        return reg["canonical_id"], resp.json()["request_id"]

    async def test_a_denied_race_leaves_the_reused_identity_pending_with_no_grants(
        self, consent_client, monkeypatch
    ):
        """An admin's deny must not leave usable authorization behind: the loser
        of the decision race undoes its activation and the grants it wrote."""
        app = consent_client._transport.app
        cid, request_id = await self._pending_proof_request(consent_client, "race-reuse")
        self._deny_wins_the_race(app, monkeypatch)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )

        assert resp.status_code == 409, resp.text
        assert (await app.state.agent_registry.get(cid))["status"] == "pending"
        assert await app.state.agent_grants.list_grants(cid) == []
        assert not await app.state.relationships.can_communicate(cid, "taos-instance")
        assert (await app.state.auth_requests.get(request_id))["status"] == "refused"

    async def test_a_denied_race_keeps_grants_the_identity_already_held(
        self, consent_client, monkeypatch
    ):
        app = consent_client._transport.app
        cid, request_id = await self._pending_proof_request(consent_client, "race-prior")
        await app.state.agent_grants.add_grant(cid, "a2a_send", tier="once")
        self._deny_wins_the_race(app, monkeypatch)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send", "a2a_receive"]},
        )

        assert resp.status_code == 409, resp.text
        scopes = [g["scope"] for g in await app.state.agent_grants.list_grants(cid)]
        assert scopes == ["a2a_send"]

    async def test_a_denied_race_removes_a_freshly_minted_identity(
        self, consent_client, monkeypatch
    ):
        app = consent_client._transport.app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post(
                "/api/agents/auth-requests",
                json={**_CREATE_BODY, "identity_claim": "race-fresh"},
            )
        self._deny_wins_the_race(app, monkeypatch)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{create_resp.json()['request_id']}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )

        assert resp.status_code == 409, resp.text
        rows = await app.state.agent_registry.list_all()
        assert [r for r in rows if r.get("handle") == "race-fresh"] == []

    async def test_losing_to_an_approve_of_the_same_identity_undoes_nothing(
        self, consent_client, monkeypatch
    ):
        """The winner holds a token for this identity, so the loser must not
        pull its activation or grants out from under it."""
        app = consent_client._transport.app
        cid, request_id = await self._pending_proof_request(consent_client, "race-same")
        store = app.state.auth_requests
        real = store.set_decision

        async def racing(rid, decision, **kwargs):
            await real(rid, decision, **kwargs)
            return await real(rid, decision, **kwargs)

        monkeypatch.setattr(store, "set_decision", racing)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )

        assert resp.status_code == 409, resp.text
        assert (await app.state.agent_registry.get(cid))["status"] == "active"
        assert [g["scope"] for g in await app.state.agent_grants.list_grants(cid)] == ["a2a_send"]

    async def test_activation_conflict_on_a_reused_identity_is_a_409(
        self, consent_client, monkeypatch
    ):
        """Two approvals can both pick the same pending row. The second activation
        raises a transition conflict, which must not surface as a 500 or delete
        the identity the first approval just activated."""
        app = consent_client._transport.app
        registry = app.state.agent_registry
        cid, request_id = await self._pending_proof_request(consent_client, "race-activate")
        real = registry.set_status

        async def racing(canonical_id, new_status, **kwargs):
            await real(canonical_id, new_status, **kwargs)
            return await real(canonical_id, new_status, **kwargs)

        monkeypatch.setattr(registry, "set_status", racing)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )

        assert resp.status_code == 409, resp.text
        assert await registry.get(cid) is not None

    async def test_approve_non_active_identity_without_proof_returns_409(
        self, consent_client
    ):
        """The genuinely invalid case still 409s: a non-active holder exists but
        the requester presented no proof token for it."""
        app = consent_client._transport.app
        registry = app.state.agent_registry
        transport = ASGITransport(app=app)

        reg_record = await registry.register(
            framework="hermes",
            display_name="Hermes",
            handle="hermes-no-proof-test",
            origin="external-selfjoin",
        )
        original_cid = reg_record["canonical_id"]
        assert reg_record["status"] == "pending"

        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post(
                "/api/agents/auth-requests",
                json={**_CREATE_BODY, "identity_claim": "hermes-no-proof-test"},
            )
            assert create_resp.status_code == 200
            request_id = create_resp.json()["request_id"]

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "registry token" in detail or "belongs to existing agent" in detail

        # No new identity was minted, and the pending row is untouched.
        agent = await registry.get(original_cid)
        assert agent["status"] == "pending"
        rows = [
            r
            for r in await registry.list_all()
            if r.get("handle") == "hermes-no-proof-test"
        ]
        assert len(rows) == 1

    async def test_reserved_name_value_error_is_not_a_concurrent_409(
        self, consent_client, monkeypatch
    ):
        """Review #3789: register() rejecting a name (reserved prefix) must
        surface as that error, not be rewritten into the concurrent-decision
        409 that is reserved for a genuine activation race."""
        app = consent_client._transport.app
        registry = app.state.agent_registry

        async def reserved(*args, **kwargs):
            raise ValueError(
                "cannot register agent: name 'user' resolves to reserved "
                "prefix 'user-'; choose a different name"
            )

        monkeypatch.setattr(registry, "register", reserved)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
        request_id = create_resp.json()["request_id"]
        with pytest.raises(ValueError, match="reserved"):
            await consent_client.post(
                f"/api/agents/auth-requests/{request_id}/approve",
                json={"granted_scopes": ["a2a_send"]},
            )

    async def test_a_failed_grant_write_undoes_the_whole_approval(
        self, consent_client, monkeypatch
    ):
        """Review #3789: if a grant write fails after activation, the approval
        must not be left half-applied: the minted identity is removed and the
        request is still pending, so it can be decided again."""
        app = consent_client._transport.app
        registry = app.state.agent_registry
        grants = app.state.agent_grants

        async def boom(*args, **kwargs):
            raise RuntimeError("grant store unavailable")

        monkeypatch.setattr(grants, "add_grant", boom)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post("/api/agents/auth-requests", json=_CREATE_BODY)
        request_id = create_resp.json()["request_id"]
        with pytest.raises(RuntimeError, match="grant store unavailable"):
            await consent_client.post(
                f"/api/agents/auth-requests/{request_id}/approve",
                json={"granted_scopes": ["a2a_send"]},
            )
        rows = [
            r for r in await registry.list_all() if r.get("handle") == "my-external-agent"
        ]
        assert rows == []
        record = await app.state.auth_requests.get(request_id)
        assert record["status"] == "pending"

    async def test_a_denied_race_restores_the_projects_previous_lead(
        self, consent_client, monkeypatch
    ):
        """Review #3789: approving a new lead overwrites the project's lead
        pointer; if the approval then loses the decision race, the undo must
        restore the previous lead rather than leaving the refused agent (or
        a dangling pointer) as lead."""
        app = consent_client._transport.app
        pstore = app.state.project_store
        if pstore._db is None:
            await pstore.init()
        await pstore.create_project_with_lead(
            name="Lead Race Project",
            slug="lead-race-project",
            created_by="admin",
            member_id="original-lead",
        )
        project = await pstore.get_project_by_slug("lead-race-project")
        project_id = project["id"]
        assert project["lead_member_id"] == "original-lead"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post(
                "/api/agents/auth-requests",
                json={
                    **_CREATE_BODY,
                    "requested_scopes": ["project_tasks"],
                    "project_id": project_id,
                },
            )
        request_id = create_resp.json()["request_id"]
        self._deny_wins_the_race(app, monkeypatch)
        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={
                "granted_scopes": ["project_tasks"],
                "is_lead": True,
                "project_id": project_id,
            },
        )
        assert resp.status_code == 409, resp.text
        after = await pstore.get_project(project_id)
        assert after["lead_member_id"] == "original-lead"
    async def test_a_failed_grant_snapshot_preserves_prior_grants(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 2): when the prior-grants snapshot read fails,
        the undo must not treat the identity as grant-less and revoke grants
        that existed before this approval."""
        app = consent_client._transport.app
        cid, request_id = await self._pending_proof_request(
            consent_client, "snap-fail"
        )
        grants = app.state.agent_grants
        await grants.add_grant(cid, "a2a_send", tier="once")

        real_list = grants.list_grants
        calls = {"n": 0}

        async def flaky(canonical_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("grant store read unavailable")
            return await real_list(canonical_id)

        monkeypatch.setattr(grants, "list_grants", flaky)

        with pytest.raises(RuntimeError, match="grant store read unavailable"):
            await consent_client.post(
                f"/api/agents/auth-requests/{request_id}/approve",
                json={"granted_scopes": ["a2a_send"]},
            )
        scopes = [g["scope"] for g in await real_list(cid)]
        assert scopes == ["a2a_send"]
        assert (await app.state.agent_registry.get(cid))["status"] == "pending"

    async def test_a_denied_race_restores_a_renewed_grants_prior_expiry(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 2): with renew=True the approval replaces a
        pre-existing grant's expires_at; losing the race must put the
        previous expiry back, not leave the renewed one in place."""
        from datetime import datetime, timedelta, timezone

        app = consent_client._transport.app
        registry = app.state.agent_registry
        reg = await registry.register(
            framework="hermes",
            display_name="Hermes",
            handle="renew-race",
            origin="external-selfjoin",
        )
        cid = reg["canonical_id"]
        private_pem, _public_pem = app.state.agent_registry_keypair
        proof = mint_registry_token(
            cid, private_pem, user_id="test", framework="hermes"
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post(
                "/api/agents/auth-requests",
                json={
                    **_CREATE_BODY,
                    "identity_claim": "renew-race",
                    "duration_secs": 30 * 24 * 3600,
                },
                headers={"Authorization": f"Bearer {proof}"},
            )
        request_id = create_resp.json()["request_id"]

        prior_expiry = (
            datetime.now(timezone.utc) + timedelta(days=1)
        ).isoformat()
        await app.state.agent_grants.add_grant(
            cid, "a2a_send", tier="once", expires_at=prior_expiry
        )
        self._deny_wins_the_race(app, monkeypatch)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send"], "renew": True},
        )
        assert resp.status_code == 409, resp.text
        grants = await app.state.agent_grants.list_grants(cid)
        assert [g["scope"] for g in grants] == ["a2a_send"]
        assert grants[0]["expires_at"] == prior_expiry

    async def test_an_unreadable_decision_record_does_not_block_the_undo(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 2): _undo_lost_approval reads the decision
        record to check who won. If that read itself fails, cleanup must
        still run (treat the record as not accepted by us) and the caller
        must see the 409, not the store error."""
        app = consent_client._transport.app
        cid, request_id = await self._pending_proof_request(
            consent_client, "undo-store-fail"
        )
        store = app.state.auth_requests
        real_set_decision = store.set_decision
        real_get = store.get
        state = {"decided": False}

        async def racing(rid, decision, **kwargs):
            if decision == "accepted":
                assert await real_set_decision(
                    rid, "refused", decided_by="other-admin"
                )
                state["decided"] = True
            return await real_set_decision(rid, decision, **kwargs)

        async def broken_get(rid):
            if state["decided"]:
                raise RuntimeError("auth store unavailable")
            return await real_get(rid)

        monkeypatch.setattr(store, "set_decision", racing)
        monkeypatch.setattr(store, "get", broken_get)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )
        assert resp.status_code == 409, resp.text
        assert (await app.state.agent_registry.get(cid))["status"] == "pending"
        assert await app.state.agent_grants.list_grants(cid) == []

    async def test_a_failed_grant_revoke_does_not_skip_the_rest_of_the_undo(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 2): each undo step is independent — a grant
        revoke that raises must not skip the lead restore, the member
        removal or the identity revert."""
        app = consent_client._transport.app
        pstore = app.state.project_store
        if pstore._db is None:
            await pstore.init()
        await pstore.create_project_with_lead(
            name="Undo Steps Project",
            slug="undo-steps-project",
            created_by="admin",
            member_id="original-lead",
        )
        project_id = (await pstore.get_project_by_slug("undo-steps-project"))["id"]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post(
                "/api/agents/auth-requests",
                json={
                    **_CREATE_BODY,
                    "identity_claim": "undo-steps",
                    "requested_scopes": ["project_tasks"],
                    "project_id": project_id,
                },
            )
        request_id = create_resp.json()["request_id"]
        self._deny_wins_the_race(app, monkeypatch)

        async def boom(*args, **kwargs):
            raise RuntimeError("grant store unavailable")

        monkeypatch.setattr(app.state.agent_grants, "revoke_grant", boom)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={
                "granted_scopes": ["project_tasks"],
                "is_lead": True,
                "project_id": project_id,
            },
        )
        assert resp.status_code == 409, resp.text
        after = await pstore.get_project(project_id)
        assert after["lead_member_id"] == "original-lead"
        rows = [
            r
            for r in await app.state.agent_registry.list_all()
            if r.get("handle") == "undo-steps"
        ]
        assert rows == []

    async def test_the_undo_does_not_overwrite_a_lead_set_after_this_approval(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 2): the lead restore is conditional — if
        another approval has since made someone else the project's lead,
        the undo must not clobber that newer lead with the pre-approval
        value."""
        app = consent_client._transport.app
        pstore = app.state.project_store
        if pstore._db is None:
            await pstore.init()
        await pstore.create_project_with_lead(
            name="Lead Clobber Project",
            slug="lead-clobber-project",
            created_by="admin",
            member_id="original-lead",
        )
        project_id = (await pstore.get_project_by_slug("lead-clobber-project"))[
            "id"
        ]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            create_resp = await bare.post(
                "/api/agents/auth-requests",
                json={
                    **_CREATE_BODY,
                    "identity_claim": "lead-clobber",
                    "requested_scopes": ["project_tasks"],
                    "project_id": project_id,
                },
            )
        request_id = create_resp.json()["request_id"]

        store = app.state.auth_requests
        real_set_decision = store.set_decision

        async def racing(rid, decision, **kwargs):
            if decision == "accepted":
                assert await real_set_decision(
                    rid, "refused", decided_by="other-admin"
                )
                # Meanwhile, a different approval installed its own lead.
                await pstore.add_member(
                    project_id=project_id,
                    member_id="other-lead",
                    member_kind="native",
                    role="lead",
                )
                await pstore.set_lead(project_id, "other-lead")
            return await real_set_decision(rid, decision, **kwargs)

        monkeypatch.setattr(store, "set_decision", racing)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={
                "granted_scopes": ["project_tasks"],
                "is_lead": True,
                "project_id": project_id,
            },
        )
        assert resp.status_code == 409, resp.text
        after = await pstore.get_project(project_id)
        assert after["lead_member_id"] == "other-lead"

    async def test_a_rotation_between_check_and_activation_blocks_reuse(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 2): the proof-token cutoff must be re-read at
        activation time. A rotation that lands after the handler's registry
        snapshot was taken supersedes the proof, and the reuse must 409
        instead of activating on a stale snapshot."""
        app = consent_client._transport.app
        registry = app.state.agent_registry
        cid, request_id = await self._pending_proof_request(
            consent_client, "rotate-mid"
        )
        record = await app.state.auth_requests.get(request_id)
        proven_iat = record["proven_token_iat"]
        assert proven_iat
        # The rotation lands after the request was filed.
        await registry.bump_token_min_iat(cid, proven_iat + 3600)

        real_get = registry.get
        calls = {"n": 0}

        async def stale_first_get(canonical_id):
            calls["n"] += 1
            row = await real_get(canonical_id)
            if calls["n"] == 1 and row is not None:
                # The snapshot the approve handler works from was taken
                # before the rotation landed.
                row = {**row, "token_min_iat": 0}
            return row

        monkeypatch.setattr(registry, "get", stale_first_get)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )
        assert resp.status_code == 409, resp.text
        assert "superseded" in resp.json()["detail"]
        assert (await real_get(cid))["status"] == "pending"

    async def test_a_lost_race_does_not_revert_an_identity_another_approval_won(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 2): approval B reused the identity approval A
        activated and B's decision landed. When A then loses its own race,
        A's undo must leave the identity (and B's authorization) alone —
        the guard must look beyond A's own request."""
        app = consent_client._transport.app
        cid, request_a = await self._pending_proof_request(
            consent_client, "shared-identity"
        )
        store = app.state.auth_requests
        record_a = await store.get(request_a)
        request_b = (
            await store.create(
                identity_claim="shared-identity",
                framework="hermes",
                requested_scopes=["a2a_send"],
                proven_canonical_id=cid,
                proven_token_iat=record_a["proven_token_iat"],
            )
        )["id"]

        real_set_decision = store.set_decision

        async def racing(rid, decision, **kwargs):
            if rid == request_a and decision == "accepted":
                # B's approval of the same identity lands first and wins;
                # then A's own record is refused by another admin.
                assert await real_set_decision(
                    request_b,
                    "accepted",
                    canonical_id=cid,
                    token="b-token",
                    granted_scopes=["a2a_send"],
                    decided_by="other-admin",
                )
                assert await real_set_decision(
                    rid, "refused", decided_by="other-admin"
                )
            return await real_set_decision(rid, decision, **kwargs)

        monkeypatch.setattr(store, "set_decision", racing)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_a}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )
        assert resp.status_code == 409, resp.text
        assert (await app.state.agent_registry.get(cid))["status"] == "active"
        scopes = [
            g["scope"] for g in await app.state.agent_grants.list_grants(cid)
        ]
        assert scopes == ["a2a_send"]

    async def test_a_denied_race_restores_a_permanent_grant_bounded_without_renew(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 3): add_grant(renew=False) also rewrites a
        pre-existing grant — a permanent grant gains the approval's bound
        and the row is rewritten at the approval's tier. Losing the race
        must restore the snapshotted expiry (unbounded) and tier for the
        pre-existing key, not only in the renew=True case."""
        app = consent_client._transport.app
        cid, request_id = await self._pending_proof_request(
            consent_client, "perm-race", duration_secs=30 * 24 * 3600
        )
        grants = app.state.agent_grants
        await grants.add_grant(cid, "a2a_send", tier="always-allow")
        self._deny_wins_the_race(app, monkeypatch)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )
        assert resp.status_code == 409, resp.text
        rows = await grants.list_grants(cid)
        assert [g["scope"] for g in rows] == ["a2a_send"]
        assert rows[0]["expires_at"] is None
        assert rows[0]["tier"] == "always-allow"

    async def test_a_denied_race_restores_an_expired_grant_replaced_without_renew(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 3): with renew=False an expired pre-existing
        grant is replaced by the approval's live bound. Losing the race
        must put the original past expiry back — the grant must not come
        back live on a refused request."""
        from datetime import datetime, timedelta, timezone

        app = consent_client._transport.app
        cid, request_id = await self._pending_proof_request(
            consent_client, "expired-race", duration_secs=30 * 24 * 3600
        )
        grants = app.state.agent_grants
        prior_expiry = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        await grants.add_grant(
            cid, "a2a_send", tier="once", expires_at=prior_expiry
        )
        self._deny_wins_the_race(app, monkeypatch)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_id}/approve",
            json={"granted_scopes": ["a2a_send"]},
        )
        assert resp.status_code == 409, resp.text
        rows = await grants.list_grants(cid)
        assert [g["scope"] for g in rows] == ["a2a_send"]
        assert rows[0]["expires_at"] == prior_expiry

    async def test_a_lost_race_to_a_shared_identity_still_undoes_the_losers_writes(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 3): when another approval won the same
        identity, the loser's undo skips only the identity revert. The
        grants the loser wrote that the winner does not also hold, the
        loser's project membership and its lead overwrite are still
        undone."""
        app = consent_client._transport.app
        pstore = app.state.project_store
        if pstore._db is None:
            await pstore.init()
        await pstore.create_project_with_lead(
            name="Shared Identity Project",
            slug="shared-identity-project",
            created_by="admin",
            member_id="original-lead",
        )
        project = await pstore.get_project_by_slug("shared-identity-project")
        project_id = project["id"]

        cid, request_a = await self._pending_proof_request(
            consent_client,
            "shared-cleanup",
            requested_scopes=["a2a_send", "a2a_receive", "project_tasks"],
            project_id=project_id,
        )
        store = app.state.auth_requests
        record_a = await store.get(request_a)
        request_b = (
            await store.create(
                identity_claim="shared-cleanup",
                framework="hermes",
                requested_scopes=["a2a_send"],
                project_id=project_id,
                proven_canonical_id=cid,
                proven_token_iat=record_a["proven_token_iat"],
            )
        )["id"]

        real_set_decision = store.set_decision

        async def racing(rid, decision, **kwargs):
            if rid == request_a and decision == "accepted":
                # B's approval of the same identity lands first and wins,
                # holding only a2a_send; then A's record is refused by
                # another admin and A's own decision call loses.
                assert await real_set_decision(
                    request_b,
                    "accepted",
                    canonical_id=cid,
                    token="b-token",
                    granted_scopes=["a2a_send"],
                    decided_by="other-admin",
                )
                assert await real_set_decision(
                    rid, "refused", decided_by="other-admin"
                )
            return await real_set_decision(rid, decision, **kwargs)

        monkeypatch.setattr(store, "set_decision", racing)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_a}/approve",
            json={
                "granted_scopes": ["a2a_send", "a2a_receive", "project_tasks"],
                "is_lead": True,
                "project_id": project_id,
            },
        )
        assert resp.status_code == 409, resp.text
        # The winner's activation of the shared identity stands...
        assert (await app.state.agent_registry.get(cid))["status"] == "active"
        # ...the grant the winner also holds stays, the loser's other
        # grants are revoked...
        scopes = sorted(
            g["scope"] for g in await app.state.agent_grants.list_grants(cid)
        )
        assert scopes == ["a2a_send"]
        # ...and the loser's membership and lead overwrite are undone.
        assert await pstore.get_member(project_id, cid) is None
        after = await pstore.get_project(project_id)
        assert after["lead_member_id"] == "original-lead"


    async def test_a_lost_race_to_a_same_project_winner_keeps_its_membership(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 4): when the winning request is on the SAME
        project and holds project_tasks, the loser's undo must not remove
        the project membership. The winner is a live member of that
        project on the shared identity; removing the row would leave it
        holding project_tasks with no membership."""
        app = consent_client._transport.app
        pstore = app.state.project_store
        if pstore._db is None:
            await pstore.init()
        await pstore.create_project_with_lead(
            name="Same Project Member",
            slug="same-project-member",
            created_by="admin",
            member_id="original-lead",
        )
        project = await pstore.get_project_by_slug("same-project-member")
        project_id = project["id"]

        cid, request_a = await self._pending_proof_request(
            consent_client,
            "same-project-member",
            requested_scopes=["a2a_send", "a2a_receive", "project_tasks"],
            project_id=project_id,
        )
        store = app.state.auth_requests
        record_a = await store.get(request_a)
        request_b = (
            await store.create(
                identity_claim="same-project-member",
                framework="hermes",
                requested_scopes=["a2a_send", "project_tasks"],
                project_id=project_id,
                proven_canonical_id=cid,
                proven_token_iat=record_a["proven_token_iat"],
            )
        )["id"]

        real_set_decision = store.set_decision

        async def racing(rid, decision, **kwargs):
            if rid == request_a and decision == "accepted":
                # B's approval of the same identity on the same project
                # lands first and wins, holding project_tasks; then A's
                # record is refused and A's own decision call loses.
                assert await real_set_decision(
                    request_b,
                    "accepted",
                    canonical_id=cid,
                    token="tok-b",
                    granted_scopes=["a2a_send", "project_tasks"],
                    decided_by="other-admin",
                )
                assert await real_set_decision(
                    rid, "refused", decided_by="other-admin"
                )
            return await real_set_decision(rid, decision, **kwargs)

        monkeypatch.setattr(store, "set_decision", racing)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_a}/approve",
            json={
                "granted_scopes": ["a2a_send", "a2a_receive", "project_tasks"],
                "project_id": project_id,
            },
        )
        assert resp.status_code == 409, resp.text
        assert (await app.state.agent_registry.get(cid))["status"] == "active"
        scopes = sorted(
            g["scope"] for g in await app.state.agent_grants.list_grants(cid)
        )
        assert scopes == ["a2a_send", "project_tasks"]
        # The winner is a member of the same project on this identity:
        # the loser's undo leaves the membership row in place.
        assert await pstore.get_member(project_id, cid) is not None

    async def test_a_lost_race_to_a_same_project_winner_keeps_the_lead(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 4): same-project winner holding
        project_tasks — the loser's undo must not restore the lead
        pointer either. The pointer names the shared identity, so the
        still-ours guard cannot tell the loser's lead write from the
        winner's; putting the prior lead back would strip the lead
        from the winning member."""
        app = consent_client._transport.app
        pstore = app.state.project_store
        if pstore._db is None:
            await pstore.init()
        await pstore.create_project_with_lead(
            name="Same Project Lead",
            slug="same-project-lead",
            created_by="admin",
            member_id="original-lead",
        )
        project = await pstore.get_project_by_slug("same-project-lead")
        project_id = project["id"]

        cid, request_a = await self._pending_proof_request(
            consent_client,
            "same-project-lead",
            requested_scopes=["a2a_send", "a2a_receive", "project_tasks"],
            project_id=project_id,
        )
        store = app.state.auth_requests
        record_a = await store.get(request_a)
        request_b = (
            await store.create(
                identity_claim="same-project-lead",
                framework="hermes",
                requested_scopes=["a2a_send", "project_tasks"],
                project_id=project_id,
                proven_canonical_id=cid,
                proven_token_iat=record_a["proven_token_iat"],
            )
        )["id"]

        real_set_decision = store.set_decision

        async def racing(rid, decision, **kwargs):
            if rid == request_a and decision == "accepted":
                # B wins the same identity on the same project holding
                # project_tasks; A's record is refused and A loses.
                assert await real_set_decision(
                    request_b,
                    "accepted",
                    canonical_id=cid,
                    token="tok-b",
                    granted_scopes=["a2a_send", "project_tasks"],
                    decided_by="other-admin",
                )
                assert await real_set_decision(
                    rid, "refused", decided_by="other-admin"
                )
            return await real_set_decision(rid, decision, **kwargs)

        monkeypatch.setattr(store, "set_decision", racing)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_a}/approve",
            json={
                "granted_scopes": ["a2a_send", "a2a_receive", "project_tasks"],
                "is_lead": True,
                "project_id": project_id,
            },
        )
        assert resp.status_code == 409, resp.text
        assert (await app.state.agent_registry.get(cid))["status"] == "active"
        # The lead pointer still names the shared identity the winner
        # is live on, and the membership row remains.
        after = await pstore.get_project(project_id)
        assert after["lead_member_id"] == cid
        assert await pstore.get_member(project_id, cid) is not None

    async def test_a_lost_race_to_a_deferred_winner_keeps_its_unbound_grants(
        self, consent_client, monkeypatch
    ):
        """Review #3789 (round 4): a winner that deferred binding wrote
        its grants unbound (project_id=None) even though its record
        names a project. Keying the winner's grants only on the
        record's project_id misses those rows, and the loser's undo
        would revoke the very grant the winner holds."""
        app = consent_client._transport.app
        cid, request_a = await self._pending_proof_request(
            consent_client,
            "deferred-winner",
            requested_scopes=["a2a_send", "a2a_receive"],
            project_id="prj-deferred",
        )
        store = app.state.auth_requests
        record_a = await store.get(request_a)
        request_b = (
            await store.create(
                identity_claim="deferred-winner",
                framework="hermes",
                requested_scopes=["a2a_send"],
                project_id="prj-deferred",
                proven_canonical_id=cid,
                proven_token_iat=record_a["proven_token_iat"],
            )
        )["id"]

        real_set_decision = store.set_decision

        async def racing(rid, decision, **kwargs):
            if rid == request_a and decision == "accepted":
                # B's approval of the same identity lands first and wins.
                # B deferred binding, so its a2a_send grant is the
                # unbound row; then A's record is refused and A loses.
                assert await real_set_decision(
                    request_b,
                    "accepted",
                    canonical_id=cid,
                    token="tok-b",
                    granted_scopes=["a2a_send"],
                    decided_by="other-admin",
                )
                assert await real_set_decision(
                    rid, "refused", decided_by="other-admin"
                )
            return await real_set_decision(rid, decision, **kwargs)

        monkeypatch.setattr(store, "set_decision", racing)

        resp = await consent_client.post(
            f"/api/agents/auth-requests/{request_a}/approve",
            json={
                "granted_scopes": ["a2a_send", "a2a_receive"],
                "defer_binding": True,
            },
        )
        assert resp.status_code == 409, resp.text
        assert (await app.state.agent_registry.get(cid))["status"] == "active"
        # The winner's unbound a2a_send grant survives the loser's undo;
        # the loser's own a2a_receive is revoked.
        scopes = sorted(
            g["scope"] for g in await app.state.agent_grants.list_grants(cid)
        )
        assert scopes == ["a2a_send"]
