import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.notifications import NotificationStore


class TestNotificationStore:
    @pytest.mark.asyncio
    async def test_emit_event_stores_notification(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            await store.emit_event("worker.join", "Worker joined", "worker-1 connected", level="info")
            items = await store.list(limit=10)
            assert len(items) == 1
            assert items[0]["title"] == "Worker joined"
            assert items[0]["source"] == "worker.join"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_emit_event_respects_muted_prefs(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            await store.set_event_muted("worker.join", True)
            await store.emit_event("worker.join", "Worker joined", "worker-1 connected")
            items = await store.list(limit=10)
            assert len(items) == 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_event_prefs(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            prefs = await store.get_event_prefs()
            assert isinstance(prefs, list)
            await store.set_event_muted("backend.down", True)
            prefs = await store.get_event_prefs()
            muted = [p for p in prefs if p["event_type"] == "backend.down"]
            assert len(muted) == 1
            assert muted[0]["muted"] is True
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_emit_unmuted_event_passes_through(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            await store.set_event_muted("worker.join", True)
            await store.emit_event("backend.up", "Backend online", "test-backend connected")
            items = await store.list(limit=10)
            assert len(items) == 1
            assert items[0]["title"] == "Backend online"
        finally:
            await store.close()


@pytest.mark.asyncio
class TestNotificationCreateRoutes:
    """Route tests for the admin-gated POST /api/notifications endpoint."""

    async def _member_client(self, app) -> AsyncClient:
        """Cookie'd client for a non-admin member session.

        Reuses the admin created by the ``client`` fixture (add_user_invite
        requires an inviter) to issue an invite, then completes it as a plain
        non-admin user so the admin gate can be exercised.
        """
        auth_mgr = app.state.auth
        invite_code = auth_mgr.add_user_invite("member", "admin")
        auth_mgr.complete_invite(
            "member", invite_code, "Test Member", "", "memberpass1234"
        )
        member = auth_mgr.find_user("member")
        token = auth_mgr.create_session(user_id=member["id"], long_lived=True)
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": token},
        )

    async def test_non_admin_is_forbidden(self, client, app):
        """A non-admin session is rejected with 403 on POST /api/notifications."""
        member_client = await self._member_client(app)
        try:
            resp = await member_client.post(
                "/api/notifications",
                json={"title": "review me", "message": "a doc is ready"},
            )
        finally:
            await member_client.aclose()
        assert resp.status_code == 403

    async def test_admin_create_appears_in_list(self, client):
        """An admin-created notification shows up in GET /api/notifications."""
        resp = await client.post(
            "/api/notifications",
            json={
                "title": "PR ready for review",
                "message": "tinyagentos/routes/notifications.py needs review",
                "level": "info",
                "source": "review-request",
                "data": {"pr": 42},
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        listing = await client.get("/api/notifications")
        assert listing.status_code == 200
        items = listing.json()
        matches = [i for i in items if i["title"] == "PR ready for review"]
        assert len(matches) == 1
        created = matches[0]
        assert created["message"] == "tinyagentos/routes/notifications.py needs review"
        assert created["level"] == "info"
        assert created["source"] == "review-request"
        assert created["data"] == {"pr": 42}
        assert created["read"] is False
