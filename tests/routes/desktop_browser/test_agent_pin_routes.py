"""Tests for /api/desktop/browser/pins HTTP CRUD endpoints."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock


def _get_user_id(app):
    """Resolve the authed admin user id from the app state."""
    auth_mgr = app.state.auth
    record = auth_mgr.find_user("admin")
    return record["id"] if record else "test-admin"


def _make_auth_client(app, tmp_data_dir):
    """Create a second authenticated async client for multi-user isolation tests."""
    from httpx import ASGITransport, AsyncClient
    auth_mgr = app.state.auth
    # Invite a second user
    if auth_mgr.find_user("user_b") is None:
        invite_code = auth_mgr.add_user_invite("user_b", "admin")
        auth_mgr.complete_invite("user_b", invite_code, "user_b", "", "pass_b")
    record = auth_mgr.find_user("user_b")
    token = auth_mgr.create_session(user_id=record["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


def _add_agent(app, agent_id: str):
    """Inject a test agent into app.state.config.agents."""
    app.state.config.agents.append({
        "id": agent_id,
        "name": agent_id,
        "host": "127.0.0.1",
        "qmd_index": "test",
        "color": "#000000",
    })


# ---------------------------------------------------------------------------
# Test 7: 401 on no auth (must come before authenticated tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAgentPinAuth:
    async def test_get_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get(
                "/api/desktop/browser/pins",
                params={"profile_id": "personal", "tab_id": "t1"},
            )
            assert resp.status_code == 401

    async def test_post_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.post(
                "/api/desktop/browser/pins",
                json={"profile_id": "personal", "tab_id": "t1", "agent_id": "a1"},
            )
            assert resp.status_code == 401

    async def test_delete_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.delete(
                "/api/desktop/browser/pins",
                params={"profile_id": "personal", "tab_id": "t1", "agent_id": "a1"},
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test 1: GET happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestListPins:
    async def test_get_returns_empty_for_new_tab(self, client):
        resp = await client.get(
            "/api/desktop/browser/pins",
            params={"profile_id": "personal", "tab_id": "t1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"pins": []}

    async def test_get_returns_pinned_agents(self, client, app):
        _add_agent(app, "agent-a")
        await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-a"},
        )
        resp = await client.get(
            "/api/desktop/browser/pins",
            params={"profile_id": "p1", "tab_id": "t1"},
        )
        assert resp.status_code == 200
        pins = resp.json()["pins"]
        assert len(pins) == 1
        assert pins[0]["agent_id"] == "agent-a"

    # Test 2: GET multi-user isolation
    async def test_get_multi_user_isolation(self, client, app, tmp_path):
        # Seed a pin directly into the store for a different user
        store = app.state.browser_store
        await store.add_pin(
            user_id="other-user", profile_id="p1", tab_id="t1", agent_id="agent-secret",
        )

        # Authed user (admin) should get empty list
        resp = await client.get(
            "/api/desktop/browser/pins",
            params={"profile_id": "p1", "tab_id": "t1"},
        )
        assert resp.status_code == 200
        assert resp.json()["pins"] == []


# ---------------------------------------------------------------------------
# Test 3: POST happy path
# Test 4: POST idempotent
# Test 5: POST 404 on unknown agent
# Test 6: POST 400 on max-4
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPinAgent:
    async def test_post_pins_agent_returns_pinned_true(self, client, app):
        _add_agent(app, "agent-1")
        resp = await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-1"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"pinned": True}

    async def test_post_happy_path_capability_grant_exists(self, client, app):
        _add_agent(app, "agent-cap")
        user_id = _get_user_id(app)
        await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-cap"},
        )
        store = app.state.browser_store
        has_cap = await store.check_capability(
            user_id=user_id, profile_id="p1", agent_id="agent-cap",
            host="anything.io", permission="read_dom",
        )
        assert has_cap is True

    async def test_post_happy_path_list_shows_pin(self, client, app):
        _add_agent(app, "agent-list")
        await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t2", "agent_id": "agent-list"},
        )
        resp = await client.get(
            "/api/desktop/browser/pins",
            params={"profile_id": "p1", "tab_id": "t2"},
        )
        pins = resp.json()["pins"]
        assert any(p["agent_id"] == "agent-list" for p in pins)

    async def test_post_idempotent_returns_pinned_false(self, client, app):
        _add_agent(app, "agent-idem")
        await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-idem"},
        )
        resp = await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-idem"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"pinned": False}

    async def test_post_unknown_agent_returns_404(self, client):
        resp = await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "no-such-agent"},
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "agent not found"}

    async def test_post_max_4_pins_per_tab(self, client, app):
        for i in range(4):
            aid = f"agent-max-{i}"
            _add_agent(app, aid)
            r = await client.post(
                "/api/desktop/browser/pins",
                json={"profile_id": "p1", "tab_id": "t-max", "agent_id": aid},
            )
            assert r.status_code == 200, f"pin {i} failed: {r.json()}"

        # 5th agent: must fail
        fifth = "agent-max-5th"
        _add_agent(app, fifth)
        resp = await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t-max", "agent_id": fifth},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "max 4" in body.get("error", "")


# ---------------------------------------------------------------------------
# Test 8: DELETE happy path
# Test 9: DELETE on missing pin (204)
# Test 10: DELETE multi-user isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestUnpinAgent:
    async def test_delete_returns_204_and_removes_pin(self, client, app):
        _add_agent(app, "agent-del")
        await client.post(
            "/api/desktop/browser/pins",
            json={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-del"},
        )
        resp = await client.delete(
            "/api/desktop/browser/pins",
            params={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-del"},
        )
        assert resp.status_code == 204

        # Subsequent GET should show the pin gone
        list_resp = await client.get(
            "/api/desktop/browser/pins",
            params={"profile_id": "p1", "tab_id": "t1"},
        )
        pins = list_resp.json()["pins"]
        assert not any(p["agent_id"] == "agent-del" for p in pins)

    async def test_delete_missing_pin_returns_204(self, client):
        resp = await client.delete(
            "/api/desktop/browser/pins",
            params={"profile_id": "p1", "tab_id": "t1", "agent_id": "never-pinned"},
        )
        assert resp.status_code == 204

    async def test_delete_multi_user_isolation(self, client, app, tmp_path):
        # Seed a pin for user A (the authed admin) via store directly
        user_a_id = _get_user_id(app)
        store = app.state.browser_store
        await store.add_pin(
            user_id=user_a_id, profile_id="p1", tab_id="t1", agent_id="agent-isolated",
        )

        # User B tries to delete it — result is 204 but pin must still be there for A
        async with _make_auth_client(app, tmp_path) as b_client:
            resp = await b_client.delete(
                "/api/desktop/browser/pins",
                params={"profile_id": "p1", "tab_id": "t1", "agent_id": "agent-isolated"},
            )
            assert resp.status_code == 204

        # User A's pin still exists
        pins = await store.list_pins_for_tab(
            user_id=user_a_id, profile_id="p1", tab_id="t1",
        )
        assert any(p["agent_id"] == "agent-isolated" for p in pins)


# ---------------------------------------------------------------------------
# Test 11: pin_agent service layer — direct unit tests
# ---------------------------------------------------------------------------

class TestPinAgentService:
    @pytest.mark.asyncio
    async def test_agent_not_found_error_raised(self):
        from tinyagentos.routes.desktop_browser.agent_pin import (
            pin_agent, AgentNotFoundError,
        )

        store = AsyncMock()

        async def agent_not_there(aid: str) -> bool:
            return False

        with pytest.raises(AgentNotFoundError):
            await pin_agent(
                store,
                user_id="u1", profile_id="p1", tab_id="t1", agent_id="x",
                agent_exists=agent_not_there,
            )

    @pytest.mark.asyncio
    async def test_too_many_pins_error_raised(self):
        from tinyagentos.routes.desktop_browser.agent_pin import (
            pin_agent, TooManyPinsError, MAX_PINS_PER_TAB,
        )

        store = AsyncMock()
        store.count_pins_for_tab.return_value = MAX_PINS_PER_TAB

        async def agent_exists(aid: str) -> bool:
            return True

        with pytest.raises(TooManyPinsError):
            await pin_agent(
                store,
                user_id="u1", profile_id="p1", tab_id="t1", agent_id="x",
                agent_exists=agent_exists,
            )

    @pytest.mark.asyncio
    async def test_pin_agent_auto_grants_read_dom(self):
        from tinyagentos.routes.desktop_browser.agent_pin import pin_agent

        store = AsyncMock()
        store.count_pins_for_tab.return_value = 0
        store.add_pin.return_value = True

        async def agent_exists(aid: str) -> bool:
            return True

        result = await pin_agent(
            store,
            user_id="u1", profile_id="p1", tab_id="t1", agent_id="x",
            agent_exists=agent_exists,
        )

        assert result is True
        store.add_capability.assert_called_once_with(
            user_id="u1", profile_id="p1", agent_id="x",
            host_pattern="*", permissions="read_dom",
        )

    @pytest.mark.asyncio
    async def test_pin_agent_idempotent_no_extra_capability_grant(self):
        from tinyagentos.routes.desktop_browser.agent_pin import pin_agent

        store = AsyncMock()
        store.count_pins_for_tab.return_value = 0
        store.add_pin.return_value = False  # already pinned

        async def agent_exists(aid: str) -> bool:
            return True

        result = await pin_agent(
            store,
            user_id="u1", profile_id="p1", tab_id="t1", agent_id="x",
            agent_exists=agent_exists,
        )

        assert result is False
        store.add_capability.assert_not_called()

    @pytest.mark.asyncio
    async def test_unpin_agent_returns_true_on_hit(self):
        from tinyagentos.routes.desktop_browser.agent_pin import unpin_agent

        store = AsyncMock()
        store.delete_pin.return_value = True

        result = await unpin_agent(
            store, user_id="u1", profile_id="p1", tab_id="t1", agent_id="x",
        )

        assert result is True
        store.delete_pin.assert_called_once_with(
            user_id="u1", profile_id="p1", tab_id="t1", agent_id="x",
        )
