"""Tests for OS-level PWA web-push: store, best-effort send, add() wiring, routes."""
from __future__ import annotations

import asyncio
import json
import pathlib
import tempfile
from unittest.mock import patch

import pytest
import pytest_asyncio
import requests
from pywebpush import WebPushException

from tinyagentos.notifications import NotificationStore
from tinyagentos.notifications_push import NotificationPushStore, send_web_push, send_device_push
from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair
from taos_test_csrf import csrf_event_hooks

# A real VAPID keypair once for the whole module - pywebpush parses the PEM,
# but every send is mocked so no network call is ever made.
_VAPID_TMPDIR = tempfile.mkdtemp()
FAKE_VAPID = load_or_create_vapid_keypair(pathlib.Path(_VAPID_TMPDIR), filename="notif_vapid.pem")

_ENDPOINT_A = "https://push.example.com/sub/device_a"
_ENDPOINT_B = "https://push.example.com/sub/device_b"


def _response(status_code: int) -> requests.Response:
    r = requests.Response()
    r.status_code = status_code
    r.reason = str(status_code)
    return r


@pytest_asyncio.fixture
async def push_store(tmp_path):
    store = NotificationPushStore(tmp_path / "notif_push.db")
    await store.init()
    yield store
    await store.close()


async def _seed(store, endpoint, user_id="u1"):
    await store.upsert(user_id=user_id, endpoint=endpoint, p256dh="p256dh_key", auth="auth_key")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestNotificationPushStore:
    async def test_upsert_and_list_all(self, push_store):
        await _seed(push_store, _ENDPOINT_A)
        await _seed(push_store, _ENDPOINT_B)
        subs = await push_store.list_all()
        assert {s["endpoint"] for s in subs} == {_ENDPOINT_A, _ENDPOINT_B}
        assert subs[0]["p256dh"] == "p256dh_key"
        assert subs[0]["auth"] == "auth_key"

    async def test_upsert_is_idempotent_on_endpoint(self, push_store):
        await _seed(push_store, _ENDPOINT_A)
        first = (await push_store.list_all())[0]["created_at"]
        # Re-subscribe same endpoint with new keys - one row, created_at kept.
        await push_store.upsert(user_id="u1", endpoint=_ENDPOINT_A, p256dh="new_p", auth="new_a")
        subs = await push_store.list_all()
        assert len(subs) == 1
        assert subs[0]["p256dh"] == "new_p"
        assert subs[0]["created_at"] == first

    async def test_list_for_user_scopes_and_hides_secrets(self, push_store):
        await _seed(push_store, _ENDPOINT_A, user_id="u1")
        await _seed(push_store, _ENDPOINT_B, user_id="u2")
        rows = await push_store.list_for_user("u1")
        assert len(rows) == 1
        assert rows[0]["endpoint"] == _ENDPOINT_A
        assert "p256dh" not in rows[0] and "auth" not in rows[0]

    async def test_delete_by_endpoint(self, push_store):
        await _seed(push_store, _ENDPOINT_A)
        assert await push_store.delete_by_endpoint(_ENDPOINT_A) == 1
        assert await push_store.list_all() == []
        assert await push_store.delete_by_endpoint(_ENDPOINT_A) == 0

    async def test_upsert_never_changes_owner(self, push_store):
        # user_a owns the endpoint; user_b re-subscribing the same endpoint must
        # NOT hijack the row or overwrite its keys.
        await _seed(push_store, _ENDPOINT_A, user_id="user_a")
        await push_store.upsert(
            user_id="user_b", endpoint=_ENDPOINT_A, p256dh="hijack_p", auth="hijack_a"
        )
        subs = await push_store.list_all()
        assert len(subs) == 1
        assert subs[0]["user_id"] == "user_a"
        assert subs[0]["p256dh"] == "p256dh_key"  # unchanged
        # The real owner can still refresh their own keys.
        await push_store.upsert(
            user_id="user_a", endpoint=_ENDPOINT_A, p256dh="new_p", auth="new_a"
        )
        subs = await push_store.list_all()
        assert subs[0]["p256dh"] == "new_p"

    async def test_delete_for_user_is_ownership_scoped(self, push_store):
        await _seed(push_store, _ENDPOINT_A, user_id="user_a")
        # user_b cannot delete user_a's endpoint.
        assert await push_store.delete_for_user("user_b", _ENDPOINT_A) == 0
        assert len(await push_store.list_all()) == 1
        # The owner can.
        assert await push_store.delete_for_user("user_a", _ENDPOINT_A) == 1
        assert await push_store.list_all() == []

    async def test_upsert_rejects_empty_fields(self, push_store):
        with pytest.raises(ValueError):
            await push_store.upsert(user_id="", endpoint=_ENDPOINT_A, p256dh="p", auth="a")
        with pytest.raises(ValueError):
            await push_store.upsert(user_id="u1", endpoint="", p256dh="p", auth="a")


# ---------------------------------------------------------------------------
# send_web_push - best-effort fan-out
# ---------------------------------------------------------------------------

_ROW = {"id": 7, "title": "Access request", "message": "agent x wants chat",
        "source": "auth_requests", "data": {"request_id": "r1"}}


@pytest.mark.asyncio
class TestSendWebPush:
    async def test_one_send_per_subscription(self, push_store):
        await _seed(push_store, _ENDPOINT_A)
        await _seed(push_store, _ENDPOINT_B)
        with patch("pywebpush.webpush") as mock:
            result = await send_web_push(_ROW, store=push_store, vapid=FAKE_VAPID)
        assert mock.call_count == 2
        assert result == {"sent": 2, "failed": 0, "removed": 0}

    async def test_payload_maps_notification_fields(self, push_store):
        await _seed(push_store, _ENDPOINT_A)
        with patch("pywebpush.webpush") as mock:
            await send_web_push(_ROW, store=push_store, vapid=FAKE_VAPID)
        import json
        payload = json.loads(mock.call_args.kwargs["data"])
        assert payload["title"] == "Access request"
        assert payload["body"] == "agent x wants chat"
        assert payload["tag"] == "auth_requests:7"
        assert payload["source"] == "auth_requests"
        assert payload["data"]["url"] == "/desktop"
        assert payload["data"]["source"] == "auth_requests"
        assert payload["data"]["id"] == 7

    async def test_payload_includes_target_when_present(self, push_store):
        await _seed(push_store, _ENDPOINT_A)
        row = {**_ROW, "data": {"target": {"kind": "project_file", "project_id": "p1"}}}
        with patch("pywebpush.webpush") as mock:
            await send_web_push(row, store=push_store, vapid=FAKE_VAPID)
        import json
        payload = json.loads(mock.call_args.kwargs["data"])
        assert payload["data"]["target"] == {"kind": "project_file", "project_id": "p1"}

    async def test_uses_row_deep_link_when_present(self, push_store):
        await _seed(push_store, _ENDPOINT_A)
        row = {**_ROW, "data": {"url": "/desktop/decisions"}}
        with patch("pywebpush.webpush") as mock:
            await send_web_push(row, store=push_store, vapid=FAKE_VAPID)
        import json
        payload = json.loads(mock.call_args.kwargs["data"])
        assert payload["data"]["url"] == "/desktop/decisions"

    @pytest.mark.parametrize(
        "bad_url",
        [
            "https://evil.example.com/phish",
            "//evil.example.com/phish",
            "javascript:alert(1)",
            "\\evil",
            "desktop",  # no leading slash
        ],
    )
    async def test_rejects_unsafe_deep_link(self, push_store, bad_url):
        await _seed(push_store, _ENDPOINT_A)
        row = {**_ROW, "data": {"url": bad_url}}
        with patch("pywebpush.webpush") as mock:
            await send_web_push(row, store=push_store, vapid=FAKE_VAPID)
        import json
        payload = json.loads(mock.call_args.kwargs["data"])
        assert payload["data"]["url"] == "/desktop"

    async def test_410_prunes_subscription(self, push_store):
        await _seed(push_store, _ENDPOINT_A)

        def _gone(*a, **k):
            raise WebPushException("gone", response=_response(410))

        with patch("pywebpush.webpush", side_effect=_gone):
            result = await send_web_push(_ROW, store=push_store, vapid=FAKE_VAPID)
        assert result == {"sent": 0, "failed": 0, "removed": 1}
        assert await push_store.list_all() == []

    async def test_404_prunes_subscription(self, push_store):
        await _seed(push_store, _ENDPOINT_A)

        def _missing(*a, **k):
            raise WebPushException("missing", response=_response(404))

        with patch("pywebpush.webpush", side_effect=_missing):
            result = await send_web_push(_ROW, store=push_store, vapid=FAKE_VAPID)
        assert result["removed"] == 1
        assert await push_store.list_all() == []

    async def test_transient_error_keeps_subscription_and_does_not_raise(self, push_store):
        await _seed(push_store, _ENDPOINT_A)

        def _boom(*a, **k):
            raise WebPushException("503", response=_response(503))

        with patch("pywebpush.webpush", side_effect=_boom):
            result = await send_web_push(_ROW, store=push_store, vapid=FAKE_VAPID)
        assert result == {"sent": 0, "failed": 1, "removed": 0}
        assert len(await push_store.list_all()) == 1

    async def test_unexpected_exception_is_swallowed(self, push_store):
        await _seed(push_store, _ENDPOINT_A)
        with patch("pywebpush.webpush", side_effect=RuntimeError("kaboom")):
            result = await send_web_push(_ROW, store=push_store, vapid=FAKE_VAPID)
        assert result["failed"] == 1

    async def test_no_vapid_is_noop(self, push_store):
        await _seed(push_store, _ENDPOINT_A)
        with patch("pywebpush.webpush") as mock:
            result = await send_web_push(_ROW, store=push_store, vapid=None)
        mock.assert_not_called()
        assert result == {"sent": 0, "failed": 0, "removed": 0}

    async def test_no_subscriptions_is_noop(self, push_store):
        with patch("pywebpush.webpush") as mock:
            result = await send_web_push(_ROW, store=push_store, vapid=FAKE_VAPID)
        mock.assert_not_called()
        assert result == {"sent": 0, "failed": 0, "removed": 0}

    async def test_payload_includes_image_when_row_carries_one(self, push_store):
        # tsk-cf7wzc: web push carries the rich attachment on both top-level
        # `image` (Notification API) and inner `data.image` so PWA service workers
        # that only inspect one shape still see it. Non-native clients that
        # ignore `image` still get a valid text notification.
        await _seed(push_store, _ENDPOINT_A)
        row = {
            "id": 9,
            "title": "Deploy",
            "message": "queued",
            "source": "decisions",
            "data": {
                "image": "https://cdn.example.com/run/42.png",
                "decision_type": "approve_deny",
            },
        }
        captured: list[bytes] = []

        def capture(**kwargs):
            captured.append(kwargs["data"].encode("utf-8"))

        with patch("pywebpush.webpush", side_effect=capture):
            await send_web_push(row, store=push_store, vapid=FAKE_VAPID)
        assert len(captured) == 1
        payload = json.loads(captured[0].decode("utf-8"))
        assert payload["image"] == "https://cdn.example.com/run/42.png"
        assert payload["data"]["image"] == "https://cdn.example.com/run/42.png"
        # Non-native clients still see a valid text notification.
        assert payload["title"] == "Deploy"
        assert payload["body"] == "queued"

    async def test_payload_omits_image_when_absent(self, push_store):
        await _seed(push_store, _ENDPOINT_A)
        captured: list[bytes] = []

        def capture(**kwargs):
            captured.append(kwargs["data"].encode("utf-8"))

        with patch("pywebpush.webpush", side_effect=capture):
            await send_web_push(_ROW, store=push_store, vapid=FAKE_VAPID)
        payload = json.loads(captured[0].decode("utf-8"))
        assert "image" not in payload
        assert "image" not in payload["data"]


# ---------------------------------------------------------------------------
# NotificationStore.add() wiring - best-effort, off-loop, never breaks add()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAddTriggersPush:
    async def test_add_dispatches_to_push_sender(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            seen: list[dict] = []

            async def sender(row: dict) -> None:
                seen.append(row)

            store.set_push_sender(sender)
            await store.add("Hi", "there", source="auth_requests", data={"request_id": "r1"})
            # Dispatch is a background task - let it run.
            await asyncio.sleep(0)
            assert len(seen) == 1
            assert seen[0]["title"] == "Hi"
            assert seen[0]["source"] == "auth_requests"
            assert seen[0]["data"] == {"request_id": "r1"}
        finally:
            await store.close()

    async def test_failing_sender_does_not_break_add(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            async def boom(row: dict) -> None:
                raise RuntimeError("push blew up")

            store.set_push_sender(boom)
            # add() must succeed and persist the row despite the sender raising.
            await store.add("Still", "works", source="test")
            await asyncio.sleep(0)  # let the failing task run + get swallowed
            items = await store.list()
            assert len(items) == 1
            assert items[0]["title"] == "Still"
        finally:
            await store.close()

    async def test_no_sender_behaves_as_before(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            await store.add("Plain", "notif")
            assert len(await store.list()) == 1
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# Per-user scoping (user_id on add() → per-user push fan-out)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPerUserScoping:
    async def test_add_with_user_id_stores_it(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            await store.add("Hi", "there", user_id="user-1")
            items = await store.list()
            assert len(items) == 1
            assert items[0]["user_id"] == "user-1"
        finally:
            await store.close()

    async def test_add_without_user_id_stores_null(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            await store.add("Broadcast", "to all")
            items = await store.list()
            assert len(items) == 1
            assert items[0]["user_id"] is None
        finally:
            await store.close()

    async def test_add_passes_user_id_to_event_emitter(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            seen: list[dict] = []

            async def emitter(row: dict) -> None:
                seen.append(row)

            store.set_event_emitter(emitter)
            await store.add("Scoped", "msg", user_id="user-a")
            assert len(seen) == 1
            assert seen[0]["user_id"] == "user-a"
        finally:
            await store.close()

    async def test_add_passes_null_user_id_to_event_emitter(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            seen: list[dict] = []

            async def emitter(row: dict) -> None:
                seen.append(row)

            store.set_event_emitter(emitter)
            await store.add("Broadcast", "msg")
            assert len(seen) == 1
            assert seen[0]["user_id"] is None
        finally:
            await store.close()

    async def test_send_web_push_scoped_to_user(self, push_store):
        """When row has user_id, only that user's subscriptions are targeted."""
        import json

        await _seed(push_store, "https://push.example.com/user_a_sub", user_id="user-a")
        await _seed(push_store, "https://push.example.com/user_b_sub", user_id="user-b")

        row = {**_ROW, "user_id": "user-a"}
        with patch("pywebpush.webpush") as mock:
            result = await send_web_push(row, store=push_store, vapid=FAKE_VAPID)
        assert mock.call_count == 1
        assert result["sent"] == 1
        # Verify the targeted endpoint belongs to user-a
        payload = json.loads(mock.call_args.kwargs["data"])
        assert payload["title"] == "Access request"

    async def test_send_web_push_broadcast_when_no_user_id(self, push_store):
        """When row has no user_id (None/absent), fans out to all."""
        await _seed(push_store, "https://push.example.com/user_a_sub", user_id="user-a")
        await _seed(push_store, "https://push.example.com/user_b_sub", user_id="user-b")

        row = {**_ROW}  # no user_id
        with patch("pywebpush.webpush") as mock:
            result = await send_web_push(row, store=push_store, vapid=FAKE_VAPID)
        assert mock.call_count == 2
        assert result["sent"] == 2

    async def test_send_web_push_scoped_no_subscriptions_is_noop(self, push_store):
        """Scoped send to a user with no subscriptions is a no-op."""
        await _seed(push_store, "https://push.example.com/user_a_sub", user_id="user-a")
        row = {**_ROW, "user_id": "user-b"}  # user-b has no subscriptions
        with patch("pywebpush.webpush") as mock:
            result = await send_web_push(row, store=push_store, vapid=FAKE_VAPID)
        mock.assert_not_called()
        assert result == {"sent": 0, "failed": 0, "removed": 0}

    async def test_send_web_push_broadcast_none_user_id(self, push_store):
        """When user_id is explicitly None, fans out to all (broadcast)."""
        await _seed(push_store, "https://push.example.com/user_a_sub", user_id="user-a")
        await _seed(push_store, "https://push.example.com/user_b_sub", user_id="user-b")

        row = {**_ROW, "user_id": None}
        with patch("pywebpush.webpush") as mock:
            result = await send_web_push(row, store=push_store, vapid=FAKE_VAPID)
        assert mock.call_count == 2
        assert result["sent"] == 2


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


def _build_app(tmp_path):
    import yaml
    from tinyagentos.app import create_app

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    return create_app(data_dir=tmp_path)


@pytest_asyncio.fixture
async def route_app(tmp_path):
    app = _build_app(tmp_path)
    # Lifespan-owned objects - init eagerly for ASGITransport (no lifespan run).
    await app.state.notif_push_store.init()
    app.state.notif_vapid_keypair = FAKE_VAPID
    yield app
    await app.state.notif_push_store.close()


def _admin_token(app):
    auth_mgr = app.state.auth
    if not auth_mgr.is_configured():
        auth_mgr.setup_user("admin", "Admin", "", "adminpass1")
    record = auth_mgr.find_user("admin")
    return auth_mgr.create_session(user_id=record["id"], long_lived=True)


def _second_user_token(app):
    """A non-admin user_b, created via the invite flow (admin must exist)."""
    auth_mgr = app.state.auth
    _admin_token(app)  # ensure admin exists first
    if auth_mgr.find_user("user_b") is None:
        code = auth_mgr.add_user_invite("user_b", "admin")
        auth_mgr.complete_invite("user_b", code, "user_b", "", "pass_b_ok1")
    record = auth_mgr.find_user("user_b")
    return auth_mgr.create_session(user_id=record["id"], long_lived=True)


_SUB_BODY = {
    "subscription": {
        "endpoint": "https://push.example.com/send/xyz",
        "keys": {"p256dh": "p256dh_key", "auth": "auth_key"},
    }
}


@pytest.mark.asyncio
class TestPushRoutes:
    async def _client(self, app, authed=True):
        from httpx import ASGITransport, AsyncClient

        cookies = {"taos_session": _admin_token(app)} if authed else {}
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", cookies=cookies,
            event_hooks=csrf_event_hooks(),
        )

    async def test_vapid_public_key_requires_auth(self, route_app):
        async with await self._client(route_app, authed=False) as c:
            resp = await c.get("/api/notifications/push/vapid-public-key")
        assert resp.status_code == 401

    async def test_vapid_public_key_returns_key_for_session(self, route_app):
        async with await self._client(route_app) as c:
            resp = await c.get("/api/notifications/push/vapid-public-key")
        assert resp.status_code == 200
        assert resp.json()["public_key"] == FAKE_VAPID[0]

    async def test_subscribe_then_unsubscribe(self, route_app):
        async with await self._client(route_app) as c:
            r1 = await c.post("/api/notifications/push/subscribe", json=_SUB_BODY)
            assert r1.status_code == 200 and r1.json()["ok"] is True
            subs = await route_app.state.notif_push_store.list_all()
            assert len(subs) == 1
            assert subs[0]["endpoint"] == _SUB_BODY["subscription"]["endpoint"]

            r2 = await c.post(
                "/api/notifications/push/unsubscribe",
                json={"endpoint": _SUB_BODY["subscription"]["endpoint"]},
            )
            assert r2.status_code == 200 and r2.json()["ok"] is True
            assert await route_app.state.notif_push_store.list_all() == []

    async def test_subscribe_rejects_non_https_endpoint(self, route_app):
        bad = {"subscription": {"endpoint": "http://x/y", "keys": {"p256dh": "p", "auth": "a"}}}
        async with await self._client(route_app) as c:
            resp = await c.post("/api/notifications/push/subscribe", json=bad)
        assert resp.status_code == 422

    async def test_user_cannot_unsubscribe_another_users_endpoint(self, route_app):
        from httpx import ASGITransport, AsyncClient

        endpoint = _SUB_BODY["subscription"]["endpoint"]
        # user_a (admin) subscribes.
        async with await self._client(route_app) as c:
            r = await c.post("/api/notifications/push/subscribe", json=_SUB_BODY)
            assert r.status_code == 200

        # user_b tries to unsubscribe user_a's endpoint -> 404, not-owned oracle
        # cannot leak, and user_a's row must survive.
        token_b = _second_user_token(route_app)
        async with AsyncClient(
            transport=ASGITransport(app=route_app),
            base_url="http://test",
            cookies={"taos_session": token_b},
            event_hooks=csrf_event_hooks(),
        ) as cb:
            r = await cb.post(
                "/api/notifications/push/unsubscribe", json={"endpoint": endpoint}
            )
        assert r.status_code == 404
        assert r.json()["ok"] is False
        subs = await route_app.state.notif_push_store.list_all()
        assert len(subs) == 1 and subs[0]["endpoint"] == endpoint

    async def test_unsubscribe_missing_endpoint_returns_404(self, route_app):
        async with await self._client(route_app) as c:
            resp = await c.post(
                "/api/notifications/push/unsubscribe",
                json={"endpoint": "https://push.example.com/never-subscribed"},
            )
        assert resp.status_code == 404


def test_vapid_signing_key_is_accepted_by_pywebpush_vapid():
    """Regression guard for the silent-100%-failure bug: the stored VAPID PEM
    must convert to a key that py_vapid.Vapid.from_string accepts. Passing raw
    PEM here raised 'Could not deserialize key data' on every push send, which
    the per-subscription handler swallowed, disabling all web push invisibly."""
    from pathlib import Path
    import tempfile
    from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair
    from tinyagentos.notifications_push import _vapid_signing_key
    from py_vapid import Vapid01

    with tempfile.TemporaryDirectory() as d:
        _pub, private_pem = load_or_create_vapid_keypair(Path(d), filename="notif_vapid.pem")
        signing_key = _vapid_signing_key(private_pem)
        # The whole point: this must NOT raise. Raw PEM would.
        Vapid01.from_string(private_key=signing_key)


# ---------------------------------------------------------------------------
# Device push dispatch (APNs vs UnifiedPush by platform lookup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSendDevicePush:
    async def test_android_posts_to_unifiedpush_endpoint(self):
        import json

        class FakeUP:
            sent = []

            async def send(self, push_token, payload):
                FakeUP.sent.append((push_token, payload))
                return True

            async def aclose(self):
                pass

        class FakeApns:
            async def send(self, *args, **kwargs):
                return True

            async def aclose(self):
                pass

        class FakeStore:
            async def list_for_user(self, user_id):
                return [
                    {
                        "device_id": "d1",
                        "platform": "android",
                        "push_token": "https://up.example.com/endpoint",
                        "user_id": "u1",
                    }
                ]

        row = {
            "id": 1,
            "title": "Decide",
            "message": "deploy?",
            "source": "decisions",
            "user_id": "u1",
            "data": {"decision_type": "approve_deny", "options": []},
        }
        result = await send_device_push(
            row,
            device_store=FakeStore(),
            apns_sender=FakeApns(),
            up_sender=FakeUP(),
        )
        assert result["sent"] == 1
        assert FakeUP.sent[0][0] == "https://up.example.com/endpoint"
        body = FakeUP.sent[0][1]
        assert body["title"] == "Decide"
        assert body["actions"] == [
            {"id": "approve", "label": "Approve"},
            {"id": "reject", "label": "Reject"},
            {"id": "add_note", "label": "Add note"},
        ]

    async def test_ios_routes_to_apns_unchanged(self):
        class FakeApns:
            sent = []

            async def send(self, push_token, payload, *, topic=None):
                FakeApns.sent.append((push_token, payload))
                return True

            async def aclose(self):
                pass

        class FakeUP:
            async def send(self, *args, **kwargs):
                return True

            async def aclose(self):
                pass

        class FakeStore:
            async def list_for_user(self, user_id):
                return [
                    {
                        "device_id": "d1",
                        "platform": "ios",
                        "push_token": "apns-tok",
                        "user_id": "u1",
                    }
                ]

        row = {
            "id": 1,
            "title": "Hi",
            "message": "there",
            "source": "system",
            "user_id": "u1",
            "data": {},
        }
        result = await send_device_push(
            row,
            device_store=FakeStore(),
            apns_sender=FakeApns(),
            up_sender=FakeUP(),
        )
        assert result["sent"] == 1
        assert FakeApns.sent[0][0] == "apns-tok"

    async def test_broadcast_row_is_noop_for_device_push(self):
        class FakeApns:
            async def send(self, *args, **kwargs):
                return True

            async def aclose(self):
                pass

        class FakeUP:
            async def send(self, *args, **kwargs):
                return True

            async def aclose(self):
                pass

        class FakeStore:
            async def list_for_user(self, user_id):
                return []

        row = {"id": 1, "title": "Broadcast", "message": "", "source": "system", "user_id": None, "data": {}}
        result = await send_device_push(
            row,
            device_store=FakeStore(),
            apns_sender=FakeApns(),
            up_sender=FakeUP(),
        )
        assert result == {"sent": 0, "failed": 0, "skipped": 0}

    async def test_no_push_token_device_is_skipped(self):
        class FakeApns:
            async def send(self, *args, **kwargs):
                return True

            async def aclose(self):
                pass

        class FakeUP:
            async def send(self, *args, **kwargs):
                return True

            async def aclose(self):
                pass

        class FakeStore:
            async def list_for_user(self, user_id):
                return [
                    {"device_id": "d1", "platform": "android", "push_token": "", "user_id": "u1"},
                    {"device_id": "d2", "platform": "ios", "push_token": "tok", "user_id": "u1"},
                ]

        row = {"id": 1, "title": "Hi", "message": "", "source": "system", "user_id": "u1", "data": {}}
        result = await send_device_push(
            row,
            device_store=FakeStore(),
            apns_sender=FakeApns(),
            up_sender=FakeUP(),
        )
        assert result["sent"] == 1
        assert result["skipped"] == 1

    async def test_ios_decision_sets_category_mutable_content_and_actions(self):
        # tsk-cf7wzc: an approve_deny decision must reach iOS with the
        # DECISION_APPROVE_DENY category so the native shell maps it to a
        # UNNotificationCategory with approve / reject / add-note buttons, and
        # mutable-content so the service extension can attach the image and
        # wire the action callback that posts back to Decisions answer.
        class FakeApns:
            sent = []

            async def send(self, push_token, payload, *, topic=None):
                FakeApns.sent.append(payload)
                return True

            async def aclose(self):
                pass

        class FakeUP:
            async def send(self, *args, **kwargs):
                return True

            async def aclose(self):
                pass

        class FakeStore:
            async def list_for_user(self, user_id):
                return [
                    {"device_id": "d1", "platform": "ios", "push_token": "apns-tok", "user_id": "u1"}
                ]

        row = {
            "id": 1,
            "title": "Deploy",
            "message": "approve?",
            "source": "decisions",
            "user_id": "u1",
            "data": {
                "decision_type": "approve_deny",
                "image": "https://cdn.example.com/run/42.png",
            },
        }
        await send_device_push(
            row,
            device_store=FakeStore(),
            apns_sender=FakeApns(),
            up_sender=FakeUP(),
        )
        assert len(FakeApns.sent) == 1
        payload = FakeApns.sent[0]
        assert payload["aps"]["category"] == "DECISION_APPROVE_DENY"
        assert payload["aps"]["mutable-content"] == 1
        assert payload["aps"]["alert"] == {"title": "Deploy", "body": "approve?"}
        # APNs flattens `data` into the top-level payload (per Apple docs), so
        # the iOS shell + service extension read actions / image directly off
        # the root, not under a nested `data` key.
        assert payload["actions"] == [
            {"id": "approve", "label": "Approve"},
            {"id": "reject", "label": "Reject"},
            {"id": "add_note", "label": "Add note"},
        ]
        assert payload["image"] == "https://cdn.example.com/run/42.png"
        assert payload["decision_type"] == "approve_deny"

    async def test_android_decision_carries_image_and_actions(self):
        # tsk-cf7wzc: UnifiedPush distributors get a uniform shape -- top-level
        # `image` for distributors that render attachments, `actions` for
        # button rows, and the inner `data` mirror for clients that only read
        # one of the two.
        class FakeApns:
            async def send(self, *args, **kwargs):
                return True

            async def aclose(self):
                pass

        class FakeUP:
            sent = []

            async def send(self, push_token, payload):
                FakeUP.sent.append(payload)
                return True

            async def aclose(self):
                pass

        class FakeStore:
            async def list_for_user(self, user_id):
                return [
                    {"device_id": "d1", "platform": "android", "push_token": "https://up.example.com/e", "user_id": "u1"}
                ]

        row = {
            "id": 1,
            "title": "Deploy",
            "message": "approve?",
            "source": "decisions",
            "user_id": "u1",
            "data": {
                "decision_type": "approve_deny",
                "image": "https://cdn.example.com/run/42.png",
            },
        }
        await send_device_push(
            row,
            device_store=FakeStore(),
            apns_sender=FakeApns(),
            up_sender=FakeUP(),
        )
        assert len(FakeUP.sent) == 1
        body = FakeUP.sent[0]
        assert body["image"] == "https://cdn.example.com/run/42.png"
        assert body["actions"] == [
            {"id": "approve", "label": "Approve"},
            {"id": "reject", "label": "Reject"},
            {"id": "add_note", "label": "Add note"},
        ]
        assert body["data"]["image"] == "https://cdn.example.com/run/42.png"
        assert body["data"]["actions"] == body["actions"]
