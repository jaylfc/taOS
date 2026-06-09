"""Tests for the agent-registry governance lifecycle layer (PR 1).

Covers:
  - Store: set_status() valid transitions
  - Store: set_status() invalid transitions raise ValueError
  - Store: revoke() sets status=revoked + revoked_at
  - Store: external-selfjoin born pending; taos-deployed born active
  - Store: migration backfills revoked_at rows to status=revoked
  - Store: list_inactive() + list_all(status=...)
  - Routes: approve / reject / suspend / reactivate — happy paths
  - Routes: 404 on unknown canonical_id
  - Routes: 409 on invalid transition
  - Routes: /inactive shape + admin-only (member 403)
  - Routes: route-ordering (/inactive not matched as /{canonical_id})
  - Routes: ?status= filter
  - Routes: non-admin forbidden on lifecycle transitions
  - Audit: governance event recorded on transition
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import AgentRegistryStore


# ---------------------------------------------------------------------------
# Store-level tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSetStatus:
    async def _make_store(self, db_path):
        store = AgentRegistryStore(db_path)
        await store.init()
        return store

    # -- Valid transitions ---------------------------------------------------

    async def test_pending_to_active(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f", origin="external-selfjoin")
            assert rec["status"] == "pending"
            updated = await store.set_status(rec["canonical_id"], "active", actor="admin-1")
            assert updated["status"] == "active"
        finally:
            await store.close()

    async def test_pending_to_rejected(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f", origin="external-selfjoin")
            updated = await store.set_status(rec["canonical_id"], "rejected")
            assert updated["status"] == "rejected"
        finally:
            await store.close()

    async def test_active_to_suspended(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f")
            assert rec["status"] == "active"
            updated = await store.set_status(rec["canonical_id"], "suspended")
            assert updated["status"] == "suspended"
        finally:
            await store.close()

    async def test_suspended_to_active(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f")
            await store.set_status(rec["canonical_id"], "suspended")
            updated = await store.set_status(rec["canonical_id"], "active")
            assert updated["status"] == "active"
        finally:
            await store.close()

    async def test_active_to_revoked(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f")
            updated = await store.set_status(rec["canonical_id"], "revoked")
            assert updated["status"] == "revoked"
        finally:
            await store.close()

    async def test_suspended_to_revoked(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f")
            await store.set_status(rec["canonical_id"], "suspended")
            updated = await store.set_status(rec["canonical_id"], "revoked")
            assert updated["status"] == "revoked"
        finally:
            await store.close()

    async def test_pending_to_revoked(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f", origin="external-selfjoin")
            updated = await store.set_status(rec["canonical_id"], "revoked")
            assert updated["status"] == "revoked"
        finally:
            await store.close()

    async def test_rejected_to_pending(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f", origin="external-selfjoin")
            await store.set_status(rec["canonical_id"], "rejected")
            updated = await store.set_status(rec["canonical_id"], "pending")
            assert updated["status"] == "pending"
        finally:
            await store.close()

    async def test_rejected_to_active(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f", origin="external-selfjoin")
            await store.set_status(rec["canonical_id"], "rejected")
            updated = await store.set_status(rec["canonical_id"], "active")
            assert updated["status"] == "active"
        finally:
            await store.close()

    async def test_rejected_to_revoked(self, tmp_path):
        """A rejected agent can still be revoked (any non-terminal → revoked)."""
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f", origin="external-selfjoin")
            await store.set_status(rec["canonical_id"], "rejected")
            updated = await store.set_status(rec["canonical_id"], "revoked")
            assert updated["status"] == "revoked"
            assert updated["revoked_at"] is not None
        finally:
            await store.close()

    # -- Revoke sets revoked_at ---------------------------------------------

    async def test_set_status_revoked_sets_revoked_at(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f")
            updated = await store.set_status(rec["canonical_id"], "revoked")
            assert updated["status"] == "revoked"
            assert updated["revoked_at"] is not None
        finally:
            await store.close()

    # -- Invalid transitions raise ValueError --------------------------------

    @pytest.mark.parametrize("from_s,to_s", [
        ("active",    "pending"),
        ("active",    "rejected"),
        ("suspended", "pending"),
        ("suspended", "rejected"),
        ("revoked",   "active"),    # terminal — no transition out
        ("revoked",   "suspended"),
        ("rejected",  "suspended"),
        ("active",    "active"),    # same-state is not a valid transition
    ])
    async def test_invalid_transition_raises(self, tmp_path, from_s, to_s):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            # Create in 'active' state and manually force to from_s if needed.
            rec = await store.register(framework="f")
            if from_s != "active":
                await store._db.execute(
                    "UPDATE agent_registry SET status = ? WHERE canonical_id = ?",
                    (from_s, rec["canonical_id"]),
                )
                await store._db.commit()
            with pytest.raises(ValueError):
                await store.set_status(rec["canonical_id"], to_s)
        finally:
            await store.close()

    async def test_unknown_canonical_id_raises_key_error(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            with pytest.raises(KeyError):
                await store.set_status("no-such-id", "active")
        finally:
            await store.close()

    # -- Origin-based initial status ----------------------------------------

    async def test_external_selfjoin_born_pending(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f", origin="external-selfjoin")
            assert rec["status"] == "pending"
        finally:
            await store.close()

    async def test_taos_deployed_born_active(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f", origin="taos-deployed")
            assert rec["status"] == "active"
        finally:
            await store.close()

    async def test_default_origin_born_active(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f")
            assert rec["status"] == "active"
        finally:
            await store.close()

    # -- Migration: existing revoked_at rows get status=revoked -------------

    async def test_migration_backfills_revoked_at_rows(self, tmp_path):
        """Simulate a pre-migration DB (no status column) and verify backfill."""
        import aiosqlite
        from tinyagentos.db_migrations import apply_wal_pragmas_async

        db_path = tmp_path / "old.db"

        # Create a bare DB without the status column (old schema).
        async with aiosqlite.connect(str(db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            await apply_wal_pragmas_async(conn)
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS agent_registry (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_id TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    framework    TEXT NOT NULL DEFAULT '',
                    user_id      TEXT NOT NULL DEFAULT '',
                    origin       TEXT NOT NULL DEFAULT 'taos-deployed',
                    handle       TEXT NOT NULL DEFAULT '',
                    role         TEXT,
                    capabilities TEXT NOT NULL DEFAULT '[]',
                    created_ts   TEXT NOT NULL,
                    revoked_at   TEXT
                );
            """)
            # Insert two rows: one active, one already-revoked.
            await conn.execute(
                "INSERT INTO agent_registry (canonical_id, framework, created_ts) "
                "VALUES ('active-agent', 'f', '2026-01-01T00:00:00')"
            )
            await conn.execute(
                "INSERT INTO agent_registry (canonical_id, framework, created_ts, revoked_at) "
                "VALUES ('revoked-agent', 'f', '2026-01-01T00:00:00', '2026-01-02T00:00:00')"
            )
            await conn.commit()

        # Open through the store — migration should add column + backfill.
        store = AgentRegistryStore(db_path)
        await store.init()
        try:
            active = await store.get("active-agent")
            revoked = await store.get("revoked-agent")
            assert active["status"] == "active"
            assert revoked["status"] == "revoked"
        finally:
            await store.close()

    # -- list_inactive + list_all(status=) ----------------------------------

    async def test_list_inactive_returns_non_active(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec_a = await store.register(framework="f")           # active
            rec_p = await store.register(framework="f", origin="external-selfjoin")  # pending
            rec_s = await store.register(framework="f")
            await store.set_status(rec_s["canonical_id"], "suspended")

            inactive = await store.list_inactive()
            cids = {e["canonical_id"] for e in inactive}
            assert rec_p["canonical_id"] in cids
            assert rec_s["canonical_id"] in cids
            assert rec_a["canonical_id"] not in cids
        finally:
            await store.close()

    async def test_list_inactive_shape(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="f", origin="external-selfjoin")
            inactive = await store.list_inactive()
            assert len(inactive) == 1
            entry = inactive[0]
            assert "canonical_id" in entry
            assert "status" in entry
            assert entry["status"] == "pending"
        finally:
            await store.close()

    async def test_list_all_status_filter(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            await store.register(framework="f")                     # active
            await store.register(framework="f", origin="external-selfjoin")  # pending
            pending = await store.list_all(status="pending")
            assert len(pending) == 1
            assert pending[0]["status"] == "pending"
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# Route-level tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def gov_client(app, tmp_data_dir):
    """Async HTTP client with admin session + all required stores init'd."""
    registry_store = app.state.agent_registry
    if registry_store._db is None:
        await registry_store.init()

    metrics = app.state.metrics
    if metrics._db is None:
        await metrics.init()

    # Set up a trace_registry so audit events can be written.
    from pathlib import Path
    from tinyagentos.trace_store import TraceStoreRegistry
    trace_registry = TraceStoreRegistry(Path(tmp_data_dir))
    app.state.trace_registry = trace_registry

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
        yield c, uid

    await registry_store.close()
    await metrics.close()
    await trace_registry.close_all()


@pytest_asyncio.fixture
async def gov_member_client(app, tmp_data_dir):
    """Async HTTP client with a non-admin member session."""
    registry_store = app.state.agent_registry
    if registry_store._db is None:
        await registry_store.init()

    metrics = app.state.metrics
    if metrics._db is None:
        await metrics.init()

    # Create admin first, then create a member via the invite flow.
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    admin_record = app.state.auth.find_user("admin")
    assert admin_record is not None

    invite_code = app.state.auth.add_user_invite("member", "admin")
    app.state.auth.complete_invite(
        username="member",
        invite_code=invite_code,
        full_name="Test Member",
        email="",
        password="testpass1",
    )
    member_record = app.state.auth.find_user("member")
    assert member_record is not None
    assert not member_record.get("is_admin")

    member_token = app.state.auth.create_session(
        user_id=member_record["id"], long_lived=True
    )
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": member_token},
    ) as c:
        yield c

    await registry_store.close()
    await metrics.close()


@pytest.mark.asyncio
class TestLifecycleRoutes:

    async def _register(self, client, origin="taos-deployed"):
        resp = await client.post(
            "/api/agents/registry/register",
            json={"framework": "test", "display_name": "Test Agent", "origin": origin},
        )
        assert resp.status_code == 200
        return resp.json()["canonical_id"]

    # -- approve -------------------------------------------------------------

    async def test_approve_pending_returns_active(self, gov_client):
        client, _uid = gov_client
        cid = await self._register(client, origin="external-selfjoin")
        resp = await client.post(f"/api/agents/registry/{cid}/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    async def test_approve_unknown_returns_404(self, gov_client):
        client, _uid = gov_client
        resp = await client.post("/api/agents/registry/no-such-id/approve")
        assert resp.status_code == 404

    async def test_approve_active_returns_409(self, gov_client):
        """Approving an already-active agent is an invalid transition."""
        client, _uid = gov_client
        cid = await self._register(client)  # taos-deployed → active
        resp = await client.post(f"/api/agents/registry/{cid}/approve")
        assert resp.status_code == 409

    # -- reject --------------------------------------------------------------

    async def test_reject_pending_returns_rejected(self, gov_client):
        client, _uid = gov_client
        cid = await self._register(client, origin="external-selfjoin")
        resp = await client.post(f"/api/agents/registry/{cid}/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    async def test_reject_active_returns_409(self, gov_client):
        client, _uid = gov_client
        cid = await self._register(client)
        resp = await client.post(f"/api/agents/registry/{cid}/reject")
        assert resp.status_code == 409

    # -- suspend -------------------------------------------------------------

    async def test_suspend_active_returns_suspended(self, gov_client):
        client, _uid = gov_client
        cid = await self._register(client)
        resp = await client.post(f"/api/agents/registry/{cid}/suspend")
        assert resp.status_code == 200
        assert resp.json()["status"] == "suspended"

    async def test_suspend_pending_returns_409(self, gov_client):
        client, _uid = gov_client
        cid = await self._register(client, origin="external-selfjoin")
        resp = await client.post(f"/api/agents/registry/{cid}/suspend")
        assert resp.status_code == 409

    async def test_suspend_unknown_returns_404(self, gov_client):
        client, _uid = gov_client
        resp = await client.post("/api/agents/registry/no-such-id/suspend")
        assert resp.status_code == 404

    # -- reactivate ----------------------------------------------------------

    async def test_reactivate_suspended_returns_active(self, gov_client):
        client, _uid = gov_client
        cid = await self._register(client)
        await client.post(f"/api/agents/registry/{cid}/suspend")
        resp = await client.post(f"/api/agents/registry/{cid}/reactivate")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    async def test_reactivate_active_returns_409(self, gov_client):
        client, _uid = gov_client
        cid = await self._register(client)
        resp = await client.post(f"/api/agents/registry/{cid}/reactivate")
        assert resp.status_code == 409

    # -- non-admin forbidden -------------------------------------------------

    async def test_approve_non_admin_forbidden(self, gov_member_client):
        resp = await gov_member_client.post("/api/agents/registry/any-id/approve")
        assert resp.status_code == 403

    async def test_reject_non_admin_forbidden(self, gov_member_client):
        resp = await gov_member_client.post("/api/agents/registry/any-id/reject")
        assert resp.status_code == 403

    async def test_suspend_non_admin_forbidden(self, gov_member_client):
        resp = await gov_member_client.post("/api/agents/registry/any-id/suspend")
        assert resp.status_code == 403

    async def test_reactivate_non_admin_forbidden(self, gov_member_client):
        resp = await gov_member_client.post("/api/agents/registry/any-id/reactivate")
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestInactiveRoute:

    async def _admin_register(self, client, origin="taos-deployed"):
        resp = await client.post(
            "/api/agents/registry/register",
            json={"framework": "test", "display_name": "Test Agent", "origin": origin},
        )
        assert resp.status_code == 200
        return resp.json()["canonical_id"]

    async def test_inactive_returns_non_active_entries(self, gov_client):
        client, _uid = gov_client
        cid_active = await self._admin_register(client)
        cid_pending = await self._admin_register(client, origin="external-selfjoin")

        resp = await client.get("/api/agents/registry/inactive")
        assert resp.status_code == 200
        data = resp.json()
        assert "inactive" in data
        cids = {e["canonical_id"] for e in data["inactive"]}
        assert cid_pending in cids
        assert cid_active not in cids

    async def test_inactive_entry_shape(self, gov_client):
        client, _uid = gov_client
        await self._admin_register(client, origin="external-selfjoin")
        resp = await client.get("/api/agents/registry/inactive")
        assert resp.status_code == 200
        entries = resp.json()["inactive"]
        assert len(entries) >= 1
        entry = entries[0]
        assert "canonical_id" in entry
        assert "status" in entry

    async def test_inactive_admin_only(self, gov_member_client):
        resp = await gov_member_client.get("/api/agents/registry/inactive")
        assert resp.status_code == 403

    async def test_inactive_not_matched_as_canonical_id(self, gov_client):
        """GET /inactive must not be routed to the /{canonical_id} handler."""
        client, _uid = gov_client
        resp = await client.get("/api/agents/registry/inactive")
        # gov_client is an admin session, so this MUST be 200 (proves /inactive
        # beat the /{canonical_id} route). Accepting 403 would let an auth
        # regression slip through without proving the routing.
        assert resp.status_code == 200
        assert "inactive" in resp.json()


@pytest.mark.asyncio
class TestStatusFilter:

    async def test_list_status_pending_filter(self, gov_client):
        client, _uid = gov_client
        cid_active = await client.post(
            "/api/agents/registry/register",
            json={"framework": "f", "display_name": "Active Agent"},
        )
        cid_pending_resp = await client.post(
            "/api/agents/registry/register",
            json={"framework": "f", "display_name": "Pending Agent", "origin": "external-selfjoin"},
        )
        assert cid_active.status_code == 200
        assert cid_pending_resp.status_code == 200
        cid_pending = cid_pending_resp.json()["canonical_id"]

        resp = await client.get("/api/agents/registry?status=pending")
        assert resp.status_code == 200
        records = resp.json()
        assert isinstance(records, list)
        cids = {r["canonical_id"] for r in records}
        assert cid_pending in cids
        assert cid_active.json()["canonical_id"] not in cids

    async def test_list_no_filter_returns_all(self, gov_client):
        client, _uid = gov_client
        await client.post(
            "/api/agents/registry/register",
            json={"framework": "f", "display_name": "A1"},
        )
        await client.post(
            "/api/agents/registry/register",
            json={"framework": "f", "display_name": "A2", "origin": "external-selfjoin"},
        )
        resp = await client.get("/api/agents/registry")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2


@pytest.mark.asyncio
class TestAuditEvent:

    async def test_audit_event_recorded_on_approve(self, gov_client, tmp_data_dir):
        """Approving an agent must write a governance audit event to trace_store."""
        client, _uid = gov_client
        # Register a pending agent.
        resp = await client.post(
            "/api/agents/registry/register",
            json={"framework": "f", "display_name": "Audit Target", "origin": "external-selfjoin"},
        )
        cid = resp.json()["canonical_id"]

        # Approve it.
        await client.post(f"/api/agents/registry/{cid}/approve")

        # Read the governance audit events from the trace store.
        from pathlib import Path
        from tinyagentos.trace_store import AgentTraceStore
        ts = AgentTraceStore(Path(tmp_data_dir), "taos-governance")
        events = await ts.list(kind="governance", limit=50)
        governance_events = [
            e for e in events
            if isinstance(e.get("payload"), dict)
            and e["payload"].get("canonical_id") == cid
        ]
        assert len(governance_events) >= 1
        ev = governance_events[0]
        payload = ev["payload"]
        assert payload["action"] == "approve"
        assert payload["before_status"] == "pending"
        assert payload["after_status"] == "active"
        await ts.close()

    async def test_audit_event_recorded_on_suspend(self, gov_client, tmp_data_dir):
        client, _uid = gov_client
        resp = await client.post(
            "/api/agents/registry/register",
            json={"framework": "f", "display_name": "Suspend Audit"},
        )
        cid = resp.json()["canonical_id"]
        await client.post(f"/api/agents/registry/{cid}/suspend")

        from pathlib import Path
        from tinyagentos.trace_store import AgentTraceStore
        ts = AgentTraceStore(Path(tmp_data_dir), "taos-governance")
        events = await ts.list(kind="governance", limit=50)
        ev = next(
            (e for e in events
             if isinstance(e.get("payload"), dict)
             and e["payload"].get("canonical_id") == cid),
            None,
        )
        assert ev is not None
        assert ev["payload"]["action"] == "suspend"
        await ts.close()
