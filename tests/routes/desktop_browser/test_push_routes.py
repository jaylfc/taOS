"""Tests for /api/desktop/browser/push HTTP endpoints."""
from __future__ import annotations

import base64

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirrors bookmark/site_permission tests)
# ---------------------------------------------------------------------------


def _get_user_id(app):
    """Resolve the authed admin user id from the app state."""
    auth_mgr = app.state.auth
    record = auth_mgr.find_user("admin")
    return record["id"] if record else "test-admin"


def _make_auth_client(app, tmp_data_dir):
    """Create an authenticated async client for a second user (user_b)."""
    from httpx import ASGITransport, AsyncClient

    auth_mgr = app.state.auth
    if auth_mgr.find_user("user_b") is None:
        invite_code = auth_mgr.add_user_invite("user_b", "admin")
        auth_mgr.complete_invite("user_b", invite_code, "user_b", "", "pass_b_ok")
    record = auth_mgr.find_user("user_b")
    token = auth_mgr.create_session(user_id=record["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


_VALID_SUB = {
    "device_id": "device-abc",
    "endpoint": "https://push.example.com/send/abc123",
    "p256dh_key": "BNcRdreALRFXTkOOUHK1EtK2wtwe5HJnRKJE4NVe5kU",
    "auth_key": "tBHItJI5svbpez7KI4CCXg",
    "user_agent": "Mozilla/5.0",
}


# ---------------------------------------------------------------------------
# GET /vapid-public-key — public, no auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestVapidPublicKey:
    async def test_returns_200_without_auth(self, app):
        """vapid-public-key must be reachable with no session cookie."""
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get("/api/desktop/browser/push/vapid-public-key")
            assert resp.status_code == 200
            body = resp.json()
            assert "public_key" in body
            # Verify it's valid uncompressed P-256 point: 65 bytes when decoded
            pub_bytes = base64.urlsafe_b64decode(
                body["public_key"] + "=" * (-len(body["public_key"]) % 4)
            )
            assert len(pub_bytes) == 65

    async def test_same_key_on_second_call(self, app):
        """Cache test: both calls return the identical public key."""
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r1 = await c.get("/api/desktop/browser/push/vapid-public-key")
            r2 = await c.get("/api/desktop/browser/push/vapid-public-key")
        assert r1.json()["public_key"] == r2.json()["public_key"]


# ---------------------------------------------------------------------------
# Auth (401) tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPushAuth:
    async def test_subscribe_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.post("/api/desktop/browser/push/subscribe", json=_VALID_SUB)
            assert resp.status_code == 401

    async def test_list_subscriptions_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get("/api/desktop/browser/push/subscriptions")
            assert resp.status_code == 401

    async def test_delete_subscription_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.delete(
                "/api/desktop/browser/push/subscriptions/some-device"
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /subscribe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSubscribePush:
    async def test_subscribe_happy_path(self, client):
        resp = await client.post(
            "/api/desktop/browser/push/subscribe", json=_VALID_SUB
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_subscribe_appears_in_list(self, client):
        await client.post(
            "/api/desktop/browser/push/subscribe", json=_VALID_SUB
        )
        list_resp = await client.get("/api/desktop/browser/push/subscriptions")
        assert list_resp.status_code == 200
        subs = list_resp.json()["subscriptions"]
        assert len(subs) == 1
        assert subs[0]["device_id"] == _VALID_SUB["device_id"]
        assert subs[0]["endpoint"] == _VALID_SUB["endpoint"]

    async def test_subscribe_missing_required_field_returns_422(self, client):
        # Missing p256dh_key
        bad = {k: v for k, v in _VALID_SUB.items() if k != "p256dh_key"}
        resp = await client.post("/api/desktop/browser/push/subscribe", json=bad)
        assert resp.status_code == 422

    async def test_subscribe_empty_device_id_returns_422(self, client):
        bad = {**_VALID_SUB, "device_id": ""}
        resp = await client.post("/api/desktop/browser/push/subscribe", json=bad)
        assert resp.status_code == 422

    async def test_subscribe_non_https_endpoint_returns_422(self, client):
        bad = {**_VALID_SUB, "endpoint": "http://push.example.com/send/abc"}
        resp = await client.post("/api/desktop/browser/push/subscribe", json=bad)
        assert resp.status_code == 422

    async def test_subscribe_javascript_uri_endpoint_returns_422(self, client):
        bad = {**_VALID_SUB, "endpoint": "javascript:alert(1)"}
        resp = await client.post("/api/desktop/browser/push/subscribe", json=bad)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /subscriptions — secrets must be stripped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListPushSubscriptions:
    async def test_list_empty_initially(self, client):
        resp = await client.get("/api/desktop/browser/push/subscriptions")
        assert resp.status_code == 200
        assert resp.json() == {"subscriptions": []}

    async def test_list_does_not_include_p256dh_key(self, client):
        await client.post("/api/desktop/browser/push/subscribe", json=_VALID_SUB)
        resp = await client.get("/api/desktop/browser/push/subscriptions")
        subs = resp.json()["subscriptions"]
        assert len(subs) == 1
        assert "p256dh_key" not in subs[0], "p256dh_key must never be returned"

    async def test_list_does_not_include_auth_key(self, client):
        await client.post("/api/desktop/browser/push/subscribe", json=_VALID_SUB)
        resp = await client.get("/api/desktop/browser/push/subscriptions")
        subs = resp.json()["subscriptions"]
        assert len(subs) == 1
        assert "auth_key" not in subs[0], "auth_key must never be returned"

    async def test_list_includes_expected_safe_fields(self, client):
        await client.post("/api/desktop/browser/push/subscribe", json=_VALID_SUB)
        resp = await client.get("/api/desktop/browser/push/subscriptions")
        sub = resp.json()["subscriptions"][0]
        assert "device_id" in sub
        assert "endpoint" in sub
        assert "user_agent" in sub
        assert "created_at" in sub
        assert "last_seen_at" in sub

    async def test_list_multi_user_isolation(self, client, app):
        """User A's subscription must not appear in the current user's list."""
        store = app.state.browser_store
        await store.upsert_push_subscription(
            user_id="other-user",
            device_id="other-device",
            endpoint="https://push.example.com/other",
            p256dh_key="otherkey",
            auth_key="otherauth",
        )
        resp = await client.get("/api/desktop/browser/push/subscriptions")
        assert resp.status_code == 200
        subs = resp.json()["subscriptions"]
        assert all(s["device_id"] != "other-device" for s in subs)


# ---------------------------------------------------------------------------
# DELETE /subscriptions/{device_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDeletePushSubscription:
    async def test_delete_happy_path(self, client):
        await client.post("/api/desktop/browser/push/subscribe", json=_VALID_SUB)
        resp = await client.delete(
            f"/api/desktop/browser/push/subscriptions/{_VALID_SUB['device_id']}"
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    async def test_delete_removes_from_list(self, client):
        await client.post("/api/desktop/browser/push/subscribe", json=_VALID_SUB)
        await client.delete(
            f"/api/desktop/browser/push/subscriptions/{_VALID_SUB['device_id']}"
        )
        list_resp = await client.get("/api/desktop/browser/push/subscriptions")
        assert list_resp.json()["subscriptions"] == []

    async def test_delete_non_existent_returns_ok_false(self, client):
        resp = await client.delete(
            "/api/desktop/browser/push/subscriptions/does-not-exist"
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": False}

    async def test_delete_multi_user_isolation(self, client, app, tmp_path):
        """User B deleting device_id 'X' must not remove user A's 'X'."""
        user_a_id = _get_user_id(app)
        store = app.state.browser_store
        await store.upsert_push_subscription(
            user_id=user_a_id,
            device_id="shared-device-id",
            endpoint="https://push.example.com/a",
            p256dh_key="akey",
            auth_key="aauth",
        )

        # User B deletes the same device_id — should not affect user A
        async with _make_auth_client(app, tmp_path) as b_client:
            resp = await b_client.delete(
                "/api/desktop/browser/push/subscriptions/shared-device-id"
            )
            assert resp.status_code == 200

        # User A's subscription must still exist
        remaining = await store.list_push_subscriptions(user_id=user_a_id)
        assert any(s["device_id"] == "shared-device-id" for s in remaining)
