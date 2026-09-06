"""Org API routes (#161): GET /api/agents/org, PUT /api/agents/{canonical_id}/org.

Mirrors the gov_client fixture pattern in test_registry_governance_lifecycle.py:
the shared `client` fixture in conftest.py never initialises agent_registry
(it is lifespan-owned), so these routes need their own fixture that inits it.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks


@pytest_asyncio.fixture
async def org_client(app):
    """Async HTTP client with admin session + agent_registry initialised."""
    registry_store = app.state.agent_registry
    if registry_store._db is None:
        await registry_store.init()

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c, uid

    await registry_store.close()


@pytest_asyncio.fixture
async def org_member_client(app):
    """Async HTTP client with a non-admin member session, same registry init."""
    registry_store = app.state.agent_registry
    if registry_store._db is None:
        await registry_store.init()

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    admin_record = app.state.auth.find_user("admin")
    admin_uid = admin_record["id"] if admin_record else ""
    invite_code = app.state.auth.add_user_invite("member", admin_uid)
    app.state.auth.complete_invite("member", invite_code, "Test Member", "", "memberpass123")
    member_record = app.state.auth.find_user("member")
    member_uid = member_record["id"] if member_record else ""
    token = app.state.auth.create_session(user_id=member_uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c, member_uid

    await registry_store.close()


@pytest.mark.asyncio
class TestGetOrgChart:
    async def test_admin_sees_full_tree(self, org_client):
        client, admin_uid = org_client
        manager = await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Manager"},
        )
        manager_id = manager.json()["canonical_id"]
        report = await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Report"},
        )
        report_id = report.json()["canonical_id"]

        put_resp = await client.put(
            f"/api/agents/{report_id}/org", json={"reports_to": manager_id}
        )
        assert put_resp.status_code == 200

        resp = await client.get("/api/agents/org")
        assert resp.status_code == 200
        tree = resp.json()["tree"]
        root = next(n for n in tree if n["canonical_id"] == manager_id)
        assert root["reports"][0]["canonical_id"] == report_id

    async def test_member_forbidden(self, org_member_client):
        client, _uid = org_member_client
        resp = await client.get("/api/agents/org")
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestUpdateOrgFields:
    async def test_set_role_and_title(self, org_client):
        client, _uid = org_client
        reg = await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Worker"},
        )
        cid = reg.json()["canonical_id"]

        resp = await client.put(
            f"/api/agents/{cid}/org", json={"role": "engineer", "title": "Staff Engineer"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "engineer"
        assert body["title"] == "Staff Engineer"

    async def test_empty_body_is_400(self, org_client):
        """An all-None body is a silent no-op write; it must be rejected."""
        client, _uid = org_client
        reg = await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Worker"},
        )
        cid = reg.json()["canonical_id"]
        resp = await client.put(f"/api/agents/{cid}/org", json={})
        assert resp.status_code == 400

    async def test_clear_role_with_empty_string(self, org_client):
        client, _uid = org_client
        reg = await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Worker"},
        )
        cid = reg.json()["canonical_id"]
        await client.put(f"/api/agents/{cid}/org", json={"role": "engineer"})

        resp = await client.put(f"/api/agents/{cid}/org", json={"role": ""})
        assert resp.status_code == 200
        assert resp.json()["role"] is None

    async def test_whitespace_only_title_is_cleared(self, org_client):
        client, _uid = org_client
        reg = await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Worker"},
        )
        cid = reg.json()["canonical_id"]
        await client.put(f"/api/agents/{cid}/org", json={"title": "Staff Engineer"})

        resp = await client.put(f"/api/agents/{cid}/org", json={"title": "   "})
        assert resp.status_code == 200
        assert resp.json()["title"] is None

    async def test_set_reports_to(self, org_client):
        client, _uid = org_client
        manager = (await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Manager"},
        )).json()
        report = (await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Report"},
        )).json()

        resp = await client.put(
            f"/api/agents/{report['canonical_id']}/org",
            json={"reports_to": manager["canonical_id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["reports_to"] == manager["canonical_id"]

    async def test_clear_reports_to_with_empty_string(self, org_client):
        client, _uid = org_client
        manager = (await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Manager"},
        )).json()
        report = (await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Report"},
        )).json()
        await client.put(
            f"/api/agents/{report['canonical_id']}/org",
            json={"reports_to": manager["canonical_id"]},
        )

        resp = await client.put(
            f"/api/agents/{report['canonical_id']}/org", json={"reports_to": ""}
        )
        assert resp.status_code == 200
        assert resp.json()["reports_to"] is None

    async def test_self_report_is_400(self, org_client):
        client, _uid = org_client
        reg = (await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Solo"},
        )).json()
        resp = await client.put(
            f"/api/agents/{reg['canonical_id']}/org",
            json={"reports_to": reg["canonical_id"]},
        )
        assert resp.status_code == 400

    async def test_nonexistent_manager_is_400(self, org_client):
        client, _uid = org_client
        reg = (await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Solo"},
        )).json()
        resp = await client.put(
            f"/api/agents/{reg['canonical_id']}/org",
            json={"reports_to": "no-such-manager-20260101-000000"},
        )
        assert resp.status_code == 400

    async def test_cycle_is_400(self, org_client):
        client, _uid = org_client
        a = (await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "A"},
        )).json()
        b = (await client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "B"},
        )).json()
        await client.put(f"/api/agents/{a['canonical_id']}/org", json={"reports_to": b["canonical_id"]})

        resp = await client.put(
            f"/api/agents/{b['canonical_id']}/org", json={"reports_to": a["canonical_id"]}
        )
        assert resp.status_code == 400

    async def test_not_found_canonical_id_is_404(self, org_client):
        client, _uid = org_client
        resp = await client.put(
            "/api/agents/no-such-agent-20260101-000000/org", json={"role": "x"}
        )
        assert resp.status_code == 404

    async def test_member_cannot_update_others_entry(self, org_client, app):
        """Builds its own member session on top of org_client's admin (rather
        than also depending on org_member_client), since both client fixtures
        call auth.setup_user("admin", ...) on the same underlying `app` and a
        second call raises "a user is already configured"."""
        admin_client, _admin_uid = org_client
        reg = (await admin_client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "AdminOwned"},
        )).json()

        invite_code = app.state.auth.add_user_invite("member", "admin")
        app.state.auth.complete_invite("member", invite_code, "Test Member", "", "memberpass123")
        member = app.state.auth.find_user("member")
        token = app.state.auth.create_session(user_id=member["id"], long_lived=True)

        member_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", cookies={"taos_session": token},
            event_hooks=csrf_event_hooks(),
        )
        try:
            resp = await member_client.put(
                f"/api/agents/{reg['canonical_id']}/org", json={"title": "hijacked"}
            )
        finally:
            await member_client.aclose()
        assert resp.status_code == 404
