"""Integration tests for auto-update preferences via the preferences API.

Tests that Settings > Updates toggles (PUT /api/preferences/auto-update)
correctly reach the AutoUpdateService's read path.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.auto_update import AutoUpdateService, DEFAULT_PREFS
from taos_test_csrf import csrf_event_hooks


def _init_admin(app) -> str:
    """Set up admin user, return admin user_id."""
    if not app.state.auth.is_configured():
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    return record["id"] if record else ""


@pytest_asyncio.fixture
async def app(tmp_path):
    """Create app with initialized desktop_settings store."""
    app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
    await app.state.desktop_settings.init()
    _init_admin(app)
    app.state._startup_complete = True
    yield app
    await app.state.desktop_settings.close()


@pytest_asyncio.fixture
async def admin_client(app):
    """Authenticated admin client."""
    admin_uid = _init_admin(app)
    token = app.state.auth.create_session(user_id=admin_uid, long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c


@pytest.mark.asyncio
class TestAutoUpdatePreferencesIntegration:
    """Test that auto-update preference API changes reach the updater."""

    async def test_put_preferences_auto_update_reaches_updater(self, app, admin_client):
        """PUT /api/preferences/auto-update writes to global scope; AutoUpdateService reads it."""
        # 1. Write auto-update prefs via the preferences API (admin only for system namespace)
        new_prefs = {"check_enabled": False, "update_ping_enabled": False}
        resp = await admin_client.put("/api/preferences/auto-update", json=new_prefs)
        assert resp.status_code == 200, f"PUT failed: {resp.text}"

        # 2. Read back via the preferences API to confirm persistence
        resp = await admin_client.get("/api/preferences/auto-update")
        assert resp.status_code == 200
        assert resp.json() == new_prefs

        # 3. Verify AutoUpdateService reads the same values from the store
        svc = AutoUpdateService(
            project_dir=None,
            notif_store=AsyncMock(),
            settings_store=app.state.desktop_settings,
            app_state=None,
        )
        read_prefs = await svc._get_prefs()
        assert read_prefs["check_enabled"] is False
        assert read_prefs["update_ping_enabled"] is False

    async def test_non_admin_cannot_write_auto_update_prefs(self, app, tmp_path):
        """Non-admin member gets 403 when writing auto-update prefs (system namespace)."""
        from tinyagentos.auth_context import CurrentUser
        from tinyagentos.auth import AuthManager

        # Create a non-admin member
        auth = app.state.auth
        invite_code = auth.add_user_invite("member", invited_by_username="admin")
        auth.complete_invite(
            username="member",
            invite_code=invite_code,
            full_name="Member User",
            email="member@test.local",
            password="memberpass1",
        )
        member_record = auth.find_user("member")
        member_uid = member_record["id"]

        member_token = app.state.auth.create_session(user_id=member_uid, long_lived=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": member_token},
            event_hooks=csrf_event_hooks(),
        ) as member_client:
            # Non-admin should get 403 on PUT to system namespace
            resp = await member_client.put("/api/preferences/auto-update", json={"check_enabled": False})
            assert resp.status_code == 403, f"Non-admin should be forbidden, got {resp.status_code}"

    async def test_update_status_endpoint_reflects_preferences(self, app, admin_client):
        """GET /api/settings/update-status reads auto-update prefs from global scope."""
        # Set prefs via preferences API
        await admin_client.put("/api/preferences/auto-update", json={"check_enabled": False})

        # Check update-status endpoint
        resp = await admin_client.get("/api/settings/update-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auto_check"] is False, "update-status should reflect the preference"

    async def test_auto_update_service_reads_global_scope(self, app, tmp_path):
        """AutoUpdateService reads auto-update prefs from global 'user' scope directly."""
        # Write directly to store using global scope (bypassing API)
        await app.state.desktop_settings.save_preference("user", "auto-update", {"check_enabled": False})

        # Service should read the global scope value
        svc = AutoUpdateService(
            project_dir=None,
            notif_store=AsyncMock(),
            settings_store=app.state.desktop_settings,
            app_state=None,
        )
        prefs = await svc._get_prefs()
        assert prefs["check_enabled"] is False


from unittest.mock import AsyncMock

if __name__ == "__main__":
    pytest.main([__file__, "-v"])