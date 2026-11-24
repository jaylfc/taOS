"""Red tests for per-person desktop preferences.

These tests demonstrate the current bugs before the fix is applied.
Run with: uv run --group dev pytest tests/test_desktop_settings_per_person.py -v
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.desktop_settings import DesktopSettingsStore
from taos_test_csrf import csrf_event_hooks


def _add_member_user(app, username: str = "member", password: str = "memberpass1") -> str:
    """Inject a non-admin user into the auth store and return their user_id."""
    auth = app.state.auth
    invite_code = auth.add_user_invite(username, invited_by_username="admin")
    auth.complete_invite(
        username=username,
        invite_code=invite_code,
        full_name="Member User",
        email=f"{username}@test.local",
        password=password,
    )
    record = auth.find_user(username)
    return record["id"]


def _init_stores_and_admin(app) -> str:
    """Set up admin user, return admin user_id. Idempotent."""
    if not app.state.auth.is_configured():
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    return record["id"] if record else ""


@pytest_asyncio.fixture
async def app(tmp_path):
    """Create app with initialized desktop_settings store."""
    app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
    await app.state.desktop_settings.init()
    _init_stores_and_admin(app)
    app.state._startup_complete = True
    yield app
    await app.state.desktop_settings.close()


@pytest_asyncio.fixture
async def admin_client(app):
    """Authenticated admin client."""
    admin_uid = _init_stores_and_admin(app)
    token = app.state.auth.create_session(user_id=admin_uid, long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        c._test_uid = admin_uid
        c._test_app = app
        yield c


@pytest_asyncio.fixture
async def member_client(app, username="member"):
    """Authenticated non-admin member client."""
    member_uid = _add_member_user(app, username=username, password="memberpass1")
    token = app.state.auth.create_session(user_id=member_uid, long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        c._test_uid = member_uid
        c._test_app = app
        yield c


@pytest_asyncio.fixture
async def two_member_clients(app):
    """Two separate non-admin member clients (alice and bob)."""
    alice_uid = _add_member_user(app, username="alice", password="alicepass1")
    bob_uid = _add_member_user(app, username="bob", password="bobspass1")
    alice_token = app.state.auth.create_session(user_id=alice_uid, long_lived=True)
    bob_token = app.state.auth.create_session(user_id=bob_uid, long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        cookies={"taos_session": alice_token},
        event_hooks=csrf_event_hooks(),
    ) as alice_c:
        async with AsyncClient(
            transport=transport, base_url="http://test",
            cookies={"taos_session": bob_token},
            event_hooks=csrf_event_hooks(),
        ) as bob_c:
            alice_c._test_uid = alice_uid
            alice_c._test_app = app
            bob_c._test_uid = bob_uid
            bob_c._test_app = app
            yield alice_c, bob_c


class TestPerPersonPreferences:
    """Tests for per-person desktop preferences (should fail before fix)."""

    @pytest.mark.asyncio
    async def test_new_member_gets_code_defaults_not_admin_global(self, app, admin_client, member_client):
        """A new member should get shipped code defaults, NOT the admin's global preferences.
        
        Bug: Currently the global "user" scope is used as a fallback for all users,
        so a new member inherits the admin's customizations.
        """
        # Admin sets a custom wallpaper
        await admin_client.put("/api/desktop/settings", json={"wallpaper": "custom-wallpaper"})
        
        # Verify admin has the custom wallpaper
        admin_settings = await admin_client.get("/api/desktop/settings")
        assert admin_settings.json()["wallpaper"] == "custom-wallpaper"
        
        # New member should get code default wallpaper ("graphite"), NOT admin's custom
        member_settings = await member_client.get("/api/desktop/settings")
        assert member_settings.json()["wallpaper"] == "graphite", (
            f"New member should get code default 'graphite', got '{member_settings.json()['wallpaper']}'"
        )

    @pytest.mark.asyncio
    async def test_migration_moves_global_to_owner(self, app, tmp_path):
        """Migration should move existing global 'user' row to owner's user_id.
        
        This test simulates an existing data dir with the old schema (global "user" row).
        After migration, the owner should keep their settings, and the global row
        should no longer be read for anyone.
        """
        # Simulate pre-migration state: global "user" row with custom settings
        store = DesktopSettingsStore(tmp_path / "data" / "desktop.db")
        await store.init()
        owner_uid = _init_stores_and_admin(app)
        
        # Write to the old global scope
        await store.update_settings("user", {"wallpaper": "owner-wallpaper", "mode": "light"})
        await store.close()
        
        # Re-initialize store and run migration (simulating app startup)
        store2 = DesktopSettingsStore(tmp_path / "data" / "desktop.db")
        await store2.init()
        await store2.migrate_legacy_global_to_owner(owner_uid)
        
        # Owner should now have their settings under their own user_id
        owner_settings = await store2.get_settings(owner_uid)
        assert owner_settings["wallpaper"] == "owner-wallpaper", (
            f"Owner should keep their wallpaper after migration, got '{owner_settings['wallpaper']}'"
        )
        assert owner_settings["mode"] == "light"
        
        # Global "user" scope should be empty/clean (not read for anyone)
        global_settings = await store2.get_settings("user")
        # The global scope should return code defaults, not the migrated data
        assert global_settings["wallpaper"] == "graphite"
        assert global_settings["mode"] == "dark"
        
        await store2.close()

    @pytest.mark.asyncio
    async def test_member_write_isolation(self, app, two_member_clients):
        """Member A's writes should not affect Member B or the owner.
        
        Bug: Currently all users read/write the global "user" scope.
        """
        alice, bob = two_member_clients
        
        # Alice sets her wallpaper
        await alice.put("/api/desktop/settings", json={"wallpaper": "member-a-wallpaper"})
        
        # Alice should see her wallpaper
        alice_settings = await alice.get("/api/desktop/settings")
        assert alice_settings.json()["wallpaper"] == "member-a-wallpaper"
        
        # Bob should see code defaults, NOT Alice's wallpaper
        bob_settings = await bob.get("/api/desktop/settings")
        assert bob_settings.json()["wallpaper"] == "graphite", (
            f"Bob should get code default 'graphite', got '{bob_settings.json()['wallpaper']}'"
        )

    @pytest.mark.asyncio
    async def test_local_token_refused_on_put(self, app, tmp_path):
        """Local token and agent tokens should be refused (403) on PUT endpoints.
        
        Currently PUT endpoints use `require_admin` which accepts local tokens.
        They should only accept signed-in session users (any member).
        """
        admin_uid = _init_stores_and_admin(app)
        local_token = app.state.auth.get_local_token()
        
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {local_token}"},
        ) as client:
            # Local token should be refused on PUT /api/desktop/settings
            resp = await client.put("/api/desktop/settings", json={"wallpaper": "hacked"})
            assert resp.status_code == 403, (
                f"Local token should be refused (403), got {resp.status_code}"
            )
            
            # Local token should be refused on PUT /api/desktop/dock
            resp = await client.put("/api/desktop/dock", json={"pinned": ["hacked"]})
            assert resp.status_code == 403
            
            # Local token should be refused on PUT /api/preferences/{ns}
            resp = await client.put("/api/preferences/test", json={"key": "value"})
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_agent_token_refused_on_put(self, app):
        """An agent (local) token may read but must be refused (403) on PUT endpoints.

        Writes to per-user desktop preferences stay session-only: an agent token
        authenticates as ``via == "local_token"``, which is not a session.
        """
        _init_stores_and_admin(app)
        agent_token = app.state.auth.mint_agent_local_token("test-agent")
        
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {agent_token}"},
        ) as client:
            resp = await client.put("/api/desktop/settings", json={"wallpaper": "hacked"})
            assert resp.status_code == 403, (
                f"Agent token should be refused (403) on PUT, got {resp.status_code}"
            )

    @pytest.mark.asyncio
    async def test_get_returns_callers_merged_view(self, app, member_client):
        """GET endpoints should return the caller's merged view (code defaults < user row)."""
        # Member has no saved settings yet
        settings = await member_client.get("/api/desktop/settings")
        data = settings.json()
        assert data["wallpaper"] == "graphite"
        assert data["mode"] == "dark"
        assert data["dock"]["pinned"] == ["messages", "agents", "files", "store", "settings"]
        
        # Member saves a partial update (only wallpaper)
        await member_client.put("/api/desktop/settings", json={"wallpaper": "aurora"})
        
        # GET should return merged view: user's wallpaper + code defaults for rest
        settings = await member_client.get("/api/desktop/settings")
        data = settings.json()
        assert data["wallpaper"] == "aurora"  # user's choice
        assert data["mode"] == "dark"  # code default
        assert data["dock"]["position"] == "bottom"  # code default

    @pytest.mark.asyncio
    async def test_preferences_per_person(self, app, two_member_clients):
        """Namespaced preferences should be per-person."""
        alice, bob = two_member_clients
        
        # Alice saves a preference
        await alice.put("/api/preferences/testns", json={"alice_key": "alice_value"})
        
        # Alice should see her preference
        alice_pref = await alice.get("/api/preferences/testns")
        assert alice_pref.json() == {"alice_key": "alice_value"}
        
        # Bob should see empty dict (code default fallback)
        bob_pref = await bob.get("/api/preferences/testns")
        assert bob_pref.json() == {}

    @pytest.mark.asyncio
    async def test_dock_per_person(self, app, two_member_clients):
        """Dock settings should be per-person."""
        alice, bob = two_member_clients
        
        # Alice customizes her dock
        await alice.put("/api/desktop/dock", json={"pinned": ["alice-app"], "position": "left"})
        
        # Alice should see her dock
        alice_dock = await alice.get("/api/desktop/dock")
        assert alice_dock.json()["pinned"] == ["alice-app"]
        assert alice_dock.json()["position"] == "left"
        
        # Bob should see code defaults
        bob_dock = await bob.get("/api/desktop/dock")
        assert bob_dock.json()["pinned"] == ["messages", "agents", "files", "store", "settings"]
        assert bob_dock.json()["position"] == "bottom"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])