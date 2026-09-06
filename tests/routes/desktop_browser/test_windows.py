"""Tests for /api/desktop/browser/windows GET/PUT/DELETE persistence."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
class TestWindowsAuth:
    async def test_get_unauthenticated_returns_401(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/desktop/browser/windows")
        assert resp.status_code == 401

    async def test_put_unauthenticated_returns_401(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.put(
                "/api/desktop/browser/windows",
                json={"windows": []},
            )
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestWindowsCrud:
    async def test_get_returns_empty_for_new_user(self, client):
        resp = await client.get("/api/desktop/browser/windows")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"windows": []}

    async def test_put_then_get_round_trip(self, client):
        payload = {
            "windows": [
                {
                    "window_id": "win-1",
                    "profile_id": "personal",
                    "active_tab_id": "tab-a",
                    "state": '{"tabs":[{"id":"tab-a","url":"http://example.com/"}]}',
                },
                {
                    "window_id": "win-2",
                    "profile_id": "work",
                    "active_tab_id": None,
                    "state": '{"tabs":[]}',
                },
            ]
        }
        put_resp = await client.put(
            "/api/desktop/browser/windows", json=payload,
        )
        assert put_resp.status_code == 200

        get_resp = await client.get("/api/desktop/browser/windows")
        assert get_resp.status_code == 200
        body = get_resp.json()
        # Order may be by updated_at desc; verify by id set
        ids = {w["window_id"] for w in body["windows"]}
        assert ids == {"win-1", "win-2"}

    async def test_put_upserts_existing_window(self, client):
        first = {"windows": [{
            "window_id": "win-1", "profile_id": "personal",
            "active_tab_id": "tab-a", "state": '{"v":1}',
        }]}
        await client.put("/api/desktop/browser/windows", json=first)

        # Same window_id, different state
        second = {"windows": [{
            "window_id": "win-1", "profile_id": "personal",
            "active_tab_id": "tab-b", "state": '{"v":2}',
        }]}
        await client.put("/api/desktop/browser/windows", json=second)

        get_resp = await client.get("/api/desktop/browser/windows")
        windows = get_resp.json()["windows"]
        assert len(windows) == 1
        assert windows[0]["state"] == '{"v":2}'
        assert windows[0]["active_tab_id"] == "tab-b"

    async def test_delete_removes_window(self, client):
        await client.put(
            "/api/desktop/browser/windows",
            json={"windows": [{
                "window_id": "win-x", "profile_id": "personal",
                "active_tab_id": None, "state": "{}",
            }]},
        )

        del_resp = await client.delete(
            "/api/desktop/browser/windows/win-x"
        )
        assert del_resp.status_code == 204

        get_resp = await client.get("/api/desktop/browser/windows")
        assert get_resp.json() == {"windows": []}
