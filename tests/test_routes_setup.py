"""Tests for /api/setup/status and /api/setup/dismiss routes.

Also covers the account-email path in /auth/setup.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Local fixture: extend conftest.client with desktop_settings init
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(client, app):  # noqa: F811 — intentional shadow of conftest.client
    """Wrap conftest.client and ensure desktop_settings is initialised.

    desktop_settings is initialised in the app lifespan handler which is NOT
    run during testing. All setup-route tests call get/save_preference so
    this must be ready before any request is made.
    """
    ds = app.state.desktop_settings
    if ds._db is None:
        await ds.init()
    yield client
    # ds.close() is handled by conftest teardown (it's in app.state, app-scoped)
    # — no double-close here.


# ---------------------------------------------------------------------------
# Account email tests (Part 1)
# ---------------------------------------------------------------------------

class TestAccountEmail:
    """The /auth/setup route accepts and persists email."""

    @pytest.mark.asyncio
    async def test_setup_stores_email(self, app, tmp_data_dir):
        """setup_user stores the email and public_user exposes it."""
        user = app.state.auth.setup_user("jay", "Jay", "jay@example.com", "pass1234")
        assert user["email"] == "jay@example.com"

    @pytest.mark.asyncio
    async def test_setup_works_without_email(self, app, tmp_data_dir):
        """Email is optional — empty string is fine."""
        user = app.state.auth.setup_user("jay", "Jay", "", "pass1234")
        assert user["email"] == ""

    @pytest.mark.asyncio
    async def test_setup_route_accepts_email(self, app, tmp_data_dir):
        """POST /auth/setup JSON path stores and returns the email field."""
        store = app.state.metrics
        if store._db is not None:
            await store.close()
        await store.init()
        notif_store = app.state.notifications
        if notif_store._db is not None:
            await notif_store.close()
        await notif_store.init()
        await app.state.qmd_client.init()
        secrets_store = app.state.secrets
        if secrets_store._db is not None:
            await secrets_store.close()
        await secrets_store.init()
        scheduler = app.state.scheduler
        if scheduler._db is not None:
            await scheduler.close()
        await scheduler.init()
        channel_store = app.state.channels
        if channel_store._db is not None:
            await channel_store.close()
        await channel_store.init()
        relationship_mgr = app.state.relationships
        if relationship_mgr._db is not None:
            await relationship_mgr.close()
        await relationship_mgr.init()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/auth/setup",
                json={
                    "username": "jay",
                    "full_name": "Jay",
                    "email": "jay@example.com",
                    "password": "pass1234",
                    "auto_login": False,
                },
            )

        await relationship_mgr.close()
        await channel_store.close()
        await scheduler.close()
        await secrets_store.close()
        await notif_store.close()
        await store.close()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["user"]["email"] == "jay@example.com"

    def test_setup_user_email_persists_in_record(self, tmp_path):
        """email survives a read-back from the JSON store."""
        from tinyagentos.auth import AuthManager
        mgr = AuthManager(tmp_path)
        mgr.setup_user("jay", "Jay Doe", "jay@example.com", "pass1234")
        record = mgr.find_user("jay")
        assert record is not None
        assert record.get("email") == "jay@example.com"

    def test_setup_user_email_optional(self, tmp_path):
        """setup_user with empty email does not raise."""
        from tinyagentos.auth import AuthManager
        mgr = AuthManager(tmp_path)
        user = mgr.setup_user("jay", "Jay Doe", "", "pass1234")
        assert user["email"] == ""


# ---------------------------------------------------------------------------
# Setup-status tests (Part 2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSetupStatus:
    async def test_status_default_all_false(self, client, app, tmp_data_dir):
        """Fresh install: has_provider=False, taos_model_set=False, etc."""
        # Clear backends and agents for a clean baseline
        app.state.config.backends.clear()
        app.state.config.agents.clear()

        resp = await client.get("/api/setup/status")
        assert resp.status_code == 200
        data = resp.json()

        assert data["account"] is True
        assert data["has_provider"] is False
        assert data["taos_model_set"] is False
        assert data["has_agent"] is False
        assert data["memory_enabled"] is False
        assert data["dismissed"] is False
        assert data["complete"] is False

    async def test_has_provider_true_when_backend_present(self, client, app):
        """has_provider reflects config.backends non-empty."""
        # The default test config already has test-backend
        app.state.config.backends = [{"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}]
        resp = await client.get("/api/setup/status")
        assert resp.status_code == 200
        assert resp.json()["has_provider"] is True

    async def test_has_provider_false_when_no_backends(self, client, app):
        app.state.config.backends = []
        resp = await client.get("/api/setup/status")
        assert resp.json()["has_provider"] is False

    async def test_taos_model_set_true_when_pref_saved(self, client, app):
        """taos_model_set reads desktop_settings pref:taos_agent.model."""
        store = app.state.desktop_settings
        await store.save_preference("user", "taos_agent", {"model": "some-model"})

        resp = await client.get("/api/setup/status")
        assert resp.json()["taos_model_set"] is True

    async def test_taos_model_set_false_when_no_model(self, client, app):
        """No taos_agent pref → taos_model_set=False."""
        store = app.state.desktop_settings
        await store.save_preference("user", "taos_agent", {})

        resp = await client.get("/api/setup/status")
        assert resp.json()["taos_model_set"] is False

    async def test_has_agent_true_when_agents_configured(self, client, app):
        """has_agent reflects config.agents non-empty."""
        app.state.config.agents = [{"name": "test-agent"}]
        resp = await client.get("/api/setup/status")
        assert resp.json()["has_agent"] is True

    async def test_has_agent_false_when_no_agents(self, client, app):
        app.state.config.agents = []
        resp = await client.get("/api/setup/status")
        assert resp.json()["has_agent"] is False

    async def test_memory_enabled_true_when_taosmd_default_exists(self, client, app, tmp_data_dir):
        """memory_enabled is True when data_dir/taosmd_default.json exists."""
        marker = tmp_data_dir / "taosmd_default.json"
        marker.write_text(
            '{"device_id": "local", "tier_id": "standard", "tier_name": "Standard"}'
        )
        resp = await client.get("/api/setup/status")
        assert resp.json()["memory_enabled"] is True

    async def test_memory_enabled_false_when_no_taosmd_default(self, client, app, tmp_data_dir):
        """memory_enabled is False when taosmd_default.json absent."""
        marker = tmp_data_dir / "taosmd_default.json"
        if marker.exists():
            marker.unlink()

        resp = await client.get("/api/setup/status")
        assert resp.json()["memory_enabled"] is False

    async def test_complete_requires_both_core_steps(self, client, app):
        """complete = has_provider AND taos_model_set."""
        app.state.config.backends = [{"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}]
        store = app.state.desktop_settings
        # Provider set but no model
        await store.save_preference("user", "taos_agent", {})
        resp = await client.get("/api/setup/status")
        assert resp.json()["complete"] is False

        # Both set
        await store.save_preference("user", "taos_agent", {"model": "gpt-4"})
        resp = await client.get("/api/setup/status")
        assert resp.json()["complete"] is True

        # Model set but no provider
        app.state.config.backends = []
        resp = await client.get("/api/setup/status")
        assert resp.json()["complete"] is False

    async def test_dismissed_false_initially(self, client):
        resp = await client.get("/api/setup/status")
        assert resp.json()["dismissed"] is False


# ---------------------------------------------------------------------------
# Dismiss tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSetupDismiss:
    async def test_dismiss_persists_flag(self, client):
        """POST /api/setup/dismiss sets dismissed=True."""
        resp = await client.post("/api/setup/dismiss")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["dismissed"] is True

    async def test_dismiss_reflected_in_status(self, client):
        """After dismiss, GET /api/setup/status returns dismissed=True."""
        await client.post("/api/setup/dismiss")

        resp = await client.get("/api/setup/status")
        assert resp.json()["dismissed"] is True

    async def test_dismiss_idempotent(self, client):
        """Dismissing twice is fine."""
        await client.post("/api/setup/dismiss")
        resp = await client.post("/api/setup/dismiss")
        assert resp.status_code == 200
        assert resp.json()["dismissed"] is True
