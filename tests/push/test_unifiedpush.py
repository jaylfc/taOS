import json

import httpx
import pytest

from tinyagentos.push import (
    NullUnifiedPushSender,
    UnifiedPushSender,
    build_unifiedpush_payload,
    send_device_push,
)
from tinyagentos.push.unifiedpush import HttpUnifiedPushSender
from tinyagentos.notifications_push import send_device_push as send_device_push_broadcast


def test_build_payload_basic():
    payload = build_unifiedpush_payload(title="Hi", body="there")
    assert payload["title"] == "Hi"
    assert payload["body"] == "there"
    assert "actions" not in payload
    assert "data" not in payload


def test_build_payload_with_data():
    payload = build_unifiedpush_payload(title="Hi", body="there", data={"url": "/foo"})
    assert payload["data"] == {"url": "/foo"}


def test_build_payload_with_actions():
    actions = [{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}]
    payload = build_unifiedpush_payload(title="Decide", body="now", actions=actions)
    assert payload["actions"] == actions


@pytest.mark.asyncio
async def test_null_sender_returns_false():
    assert await NullUnifiedPushSender().send("https://example.com/endpoint", {}) is False


@pytest.mark.asyncio
async def test_http_sender_posts_json():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content) if req.content else {}
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = HttpUnifiedPushSender(client=client)
    payload = build_unifiedpush_payload(title="T", body="B", actions=[{"id": "a", "label": "A"}])
    ok = await sender.send("https://example.com/endpoint", payload)
    assert ok is True
    assert seen["url"] == "https://example.com/endpoint"
    assert seen["headers"]["content-type"] == "application/json"
    assert seen["body"]["title"] == "T"
    assert seen["body"]["body"] == "B"
    assert seen["body"]["actions"] == [{"id": "a", "label": "A"}]
    await client.aclose()


@pytest.mark.asyncio
async def test_http_sender_self_creates_and_closes_client():
    sender = HttpUnifiedPushSender()
    assert sender._client.is_closed is False
    await sender.aclose()
    assert sender._client.is_closed is True


@pytest.mark.asyncio
async def test_http_sender_does_not_close_injected_client():
    injected = httpx.AsyncClient()
    sender = HttpUnifiedPushSender(client=injected)
    await sender.aclose()
    assert injected.is_closed is False
    await injected.aclose()


@pytest.mark.asyncio
async def test_http_sender_handles_http_error():
    async def _fail(*args, **kwargs):
        raise httpx.HTTPError("boom")

    client = httpx.AsyncClient()
    sender = HttpUnifiedPushSender(client=client)
    original_post = client.post
    client.post = _fail
    ok = await sender.send("https://example.com/endpoint", {})
    assert ok is False
    client.post = original_post
    await client.aclose()


@pytest.mark.asyncio
async def test_send_device_push_single_device_dispatches_by_platform():
    class FakeApns:
        sent = []

        async def send(self, push_token, payload, *, topic=None):
            FakeApns.sent.append(("apns", push_token, payload))
            return True

        async def aclose(self):
            pass

    class FakeUP:
        sent = []

        async def send(self, push_token, payload):
            FakeUP.sent.append(("up", push_token, payload))
            return True

        async def aclose(self):
            pass

    ok = await send_device_push(
        {"platform": "ios", "push_token": "apns-tok"},
        {"title": "Hi", "body": "there"},
        apns_sender=FakeApns(),
        up_sender=FakeUP(),
    )
    assert ok is True
    assert FakeApns.sent[0][1] == "apns-tok"

    FakeApns.sent.clear()
    FakeUP.sent.clear()
    ok = await send_device_push(
        {"platform": "android", "push_token": "https://up.example.com/e"},
        {"title": "Hi", "body": "there"},
        apns_sender=FakeApns(),
        up_sender=FakeUP(),
    )
    assert ok is True
    assert FakeUP.sent[0][1] == "https://up.example.com/e"


@pytest.mark.asyncio
async def test_send_device_push_broadcast_dispatches_by_platform():
    class FakeApns:
        sent = []

        async def send(self, push_token, payload, *, topic=None):
            FakeApns.sent.append(("apns", push_token, payload))
            return True

        async def aclose(self):
            pass

    class FakeUP:
        sent = []

        async def send(self, push_token, payload):
            FakeUP.sent.append(("up", push_token, payload))
            return True

        async def aclose(self):
            pass

    devices = [
        {"device_id": "d1", "platform": "ios", "push_token": "apns-tok", "user_id": "u1"},
        {"device_id": "d2", "platform": "android", "push_token": "https://up.example.com/e2", "user_id": "u1"},
        {"device_id": "d3", "platform": "watchos", "push_token": "apns-watch", "user_id": "u1"},
    ]

    class FakeStore:
        async def list_for_user(self, user_id):
            return devices

    row = {
        "id": 1,
        "title": "Decide",
        "message": "deploy?",
        "source": "decisions",
        "user_id": "u1",
        "data": {"decision_type": "approve_deny", "options": []},
    }
    result = await send_device_push_broadcast(
        row,
        device_store=FakeStore(),
        apns_sender=FakeApns(),
        up_sender=FakeUP(),
    )
    assert result["sent"] == 3
    assert FakeApns.sent[0][0] == "apns"
    assert FakeApns.sent[0][1] == "apns-tok"
    assert FakeUP.sent[0][0] == "up"
    assert FakeUP.sent[0][1] == "https://up.example.com/e2"
    assert FakeApns.sent[1][0] == "apns"
    assert FakeApns.sent[1][1] == "apns-watch"


@pytest.mark.asyncio
async def test_send_device_push_broadcast_skips_unknown_platform():
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
            return [{"device_id": "d1", "platform": "unknown", "push_token": "tok", "user_id": "u1"}]

    row = {"id": 1, "title": "Hi", "message": "", "source": "system", "user_id": "u1", "data": {}}
    result = await send_device_push_broadcast(
        row,
        device_store=FakeStore(),
        apns_sender=FakeApns(),
        up_sender=FakeUP(),
    )
    assert result["skipped"] == 1
    assert result["sent"] == 0


@pytest.mark.asyncio
async def test_send_device_push_broadcast_noop_broadcast():
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
    result = await send_device_push_broadcast(
        row,
        device_store=FakeStore(),
        apns_sender=FakeApns(),
        up_sender=FakeUP(),
    )
    assert result == {"sent": 0, "failed": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_send_device_push_broadcast_no_push_token_device_is_skipped():
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
    result = await send_device_push_broadcast(
        row,
        device_store=FakeStore(),
        apns_sender=FakeApns(),
        up_sender=FakeUP(),
    )
    assert result["sent"] == 1
    assert result["skipped"] == 1


@pytest.mark.asyncio
async def test_send_device_push_broadcast_actions_for_decision_types():
    class FakeApns:
        sent = []

        async def send(self, push_token, payload, *, topic=None):
            FakeApns.sent.append(payload)
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
        def __init__(self, devices):
            self._devices = devices

        async def list_for_user(self, user_id):
            return self._devices

    devices_android = [{"device_id": "d1", "platform": "android", "push_token": "https://up.example.com/e", "user_id": "u1"}]
    devices_ios = [{"device_id": "d1", "platform": "ios", "push_token": "apns-tok", "user_id": "u1"}]

    for dtype, expected_actions in [
        ("approve_deny", [{"id": "approve", "label": "Approve"}, {"id": "deny", "label": "Deny"}]),
        ("single_select", [{"id": "opt1", "label": "Option 1"}]),
        ("free_text", [{"id": "quick_reply", "label": "Reply"}]),
    ]:
        FakeApns.sent.clear()
        FakeUP.sent.clear()
        row = {
            "id": 1,
            "title": "Decide",
            "message": "q?",
            "source": "decisions",
            "user_id": "u1",
            "data": {"decision_type": dtype, "options": [{"value": "opt1", "label": "Option 1"}]},
        }
        await send_device_push_broadcast(
            row,
            device_store=FakeStore(devices_android),
            apns_sender=FakeApns(),
            up_sender=FakeUP(),
        )
        assert len(FakeUP.sent) == 1
        assert FakeUP.sent[0]["actions"] == expected_actions

        FakeApns.sent.clear()
        FakeUP.sent.clear()
        await send_device_push_broadcast(
            row,
            device_store=FakeStore(devices_ios),
            apns_sender=FakeApns(),
            up_sender=FakeUP(),
        )
        assert len(FakeApns.sent) == 1


@pytest.mark.asyncio
async def test_send_refuses_cgnat_endpoint():
    """send() passes allow_private=True, which should still block CGNAT (100.64/10)."""
    from unittest.mock import patch

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["called"] = True
        seen["url"] = str(req.url)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    sender = HttpUnifiedPushSender(client=client)
    with patch(
        "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
        side_effect=lambda host, port: [(2, 1, 6, "", ("100.64.0.1", 0))],
    ):
        ok = await sender.send("http://100.64.0.1:7900/a2a/send", {})
        assert ok is False
        assert "called" not in seen
    await client.aclose()


@pytest.mark.asyncio
async def test_send_refuses_rfc1918_endpoint():
    """send() passes allow_private=True, which allows RFC1918 (10.0.0.1) because is_private is skipped."""
    from unittest.mock import patch

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["called"] = True
        seen["url"] = str(req.url)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    sender = HttpUnifiedPushSender(client=client)
    with patch(
        "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
        side_effect=lambda host, port: [(2, 1, 6, "", ("10.0.0.1", 0))],
    ):
        ok = await sender.send("http://10.0.0.1:7900/a2a/send", {})
        assert ok is True
        assert "called" in seen
        assert seen["url"] == "http://10.0.0.1:7900/a2a/send"
    await client.aclose()


@pytest.mark.asyncio
async def test_send_refuses_linklocal_endpoint():
    """send() passes allow_private=True, but link-local (169.254/16) should still be blocked."""
    from unittest.mock import patch

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["called"] = True
        seen["url"] = str(req.url)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    sender = HttpUnifiedPushSender(client=client)
    with patch(
        "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
        side_effect=lambda host, port: [(2, 1, 6, "", ("169.254.0.1", 0))],
    ):
        ok = await sender.send("http://169.254.0.1:7900/a2a/send", {})
        assert ok is False
        assert "called" not in seen
    await client.aclose()


@pytest.mark.asyncio
async def test_send_allows_public_endpoint():
    """Control test: a public endpoint should succeed."""
    from unittest.mock import patch

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["called"] = True
        seen["url"] = str(req.url)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    sender = HttpUnifiedPushSender(client=client)
    with patch(
        "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
        side_effect=lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    ):
        payload = build_unifiedpush_payload(title="Hi", body="there")
        ok = await sender.send("https://example.com/endpoint", payload)
        assert ok is True
        assert "called" in seen
        assert seen["url"] == "https://example.com/endpoint"
    await client.aclose()


@pytest.mark.asyncio
async def test_send_refuses_host_that_resolves_to_cgnat_at_send_time():
    """Send-time re-resolution: a hostname that validated public at registration
    but resolves to CGNAT when the sender runs must not issue a POST."""
    from unittest.mock import patch

    from tinyagentos.routes.desktop_browser.ssrf import validate_url_or_raise

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["called"] = True
        seen["url"] = str(req.url)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    call_count = [0]

    def fake_getaddrinfo(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return [(2, 1, 6, "", ("93.184.216.34", 0))]
        return [(2, 1, 6, "", ("100.64.0.1", 0))]

    sender = HttpUnifiedPushSender(client=client)
    with patch(
        "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
        side_effect=fake_getaddrinfo,
    ):
        validate_url_or_raise("http://rebind.example.com/endpoint", allow_private=True)
        ok = await sender.send("http://rebind.example.com/endpoint", {})
    assert ok is False
    assert "called" not in seen
    await client.aclose()