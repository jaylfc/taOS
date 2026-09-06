"""Tests for /api/desktop/browser/suggest — address-bar autocomplete."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
class TestSuggestAuth:
    async def test_unauthenticated_returns_401(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/desktop/browser/suggest",
                params={"profile_id": "personal", "q": "any"},
            )
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestSuggestParams:
    async def test_missing_profile_returns_422(self, client):
        resp = await client.get(
            "/api/desktop/browser/suggest",
            params={"q": "any"},
        )
        assert resp.status_code == 422

    async def test_empty_query_returns_empty_list(self, client):
        resp = await client.get(
            "/api/desktop/browser/suggest",
            params={"profile_id": "personal", "q": ""},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"suggestions": []}


@pytest.mark.asyncio
class TestSuggestSources:
    async def _get_user_id(self, app) -> str:
        from tinyagentos.auth import AuthManager
        auth_mgr: AuthManager = app.state.auth
        users = auth_mgr._read_users().get("users", [])
        return users[0]["id"] if users else "test-admin"

    async def test_history_match_appears(self, client, app):
        store = app.state.browser_store
        user_id = await self._get_user_id(app)

        await store.add_history(
            user_id=user_id, profile_id="personal",
            url="https://example.com/article",
            title="Example Article",
            visited_at=1700000000,
        )

        resp = await client.get(
            "/api/desktop/browser/suggest",
            params={"profile_id": "personal", "q": "exam"},
        )
        assert resp.status_code == 200
        suggestions = resp.json()["suggestions"]
        assert any(
            s["url"] == "https://example.com/article"
            and s["source"] == "history"
            for s in suggestions
        )

    async def test_bookmark_match_appears(self, client, app):
        store = app.state.browser_store
        user_id = await self._get_user_id(app)

        await store.add_bookmark(
            user_id=user_id, profile_id="personal",
            bookmark_id="bm-1",
            url="https://wikipedia.org/wiki/Cookie",
            title="Cookie - Wikipedia",
            folder_path="/",
            created_at=1700000000,
        )

        resp = await client.get(
            "/api/desktop/browser/suggest",
            params={"profile_id": "personal", "q": "cookie"},
        )
        suggestions = resp.json()["suggestions"]
        assert any(
            s["url"] == "https://wikipedia.org/wiki/Cookie"
            and s["source"] == "bookmark"
            for s in suggestions
        )

    async def test_at_prefix_returns_empty_for_now(self, client):
        # @<agent> prefix is reserved for PR 6 agent suggestions.
        # PR 4 just no-ops it.
        resp = await client.get(
            "/api/desktop/browser/suggest",
            params={"profile_id": "personal", "q": "@john"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"suggestions": []}

    async def test_limit_caps_results(self, client, app):
        store = app.state.browser_store
        user_id = await self._get_user_id(app)

        # Insert 15 matching history entries
        for i in range(15):
            await store.add_history(
                user_id=user_id, profile_id="personal",
                url=f"https://example.com/page-{i}",
                title=f"Page {i}",
                visited_at=1700000000 + i,
            )

        resp = await client.get(
            "/api/desktop/browser/suggest",
            params={"profile_id": "personal", "q": "example", "limit": 5},
        )
        suggestions = resp.json()["suggestions"]
        assert len(suggestions) == 5


@pytest.mark.asyncio
class TestSuggestIsolation:
    async def test_does_not_leak_other_users_history(self, client, app):
        store = app.state.browser_store
        # Add history for an UNRELATED user
        await store.add_history(
            user_id="other-user", profile_id="personal",
            url="https://secret-thing.example.com/",
            title="Secret",
            visited_at=1700000000,
        )

        # Query as the conftest client's authed user
        resp = await client.get(
            "/api/desktop/browser/suggest",
            params={"profile_id": "personal", "q": "secret"},
        )
        suggestions = resp.json()["suggestions"]
        assert not any(
            "secret-thing" in s["url"] for s in suggestions
        )
