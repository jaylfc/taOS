"""Tests for tinyagentos.routes.desktop_browser.push.send()."""
from __future__ import annotations

import asyncio
import requests
from unittest.mock import patch

import pytest
import pytest_asyncio

from tinyagentos.routes.desktop_browser import push as push_module
from tinyagentos.routes.desktop_browser.push import _RateLimiter, send
from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair
from tinyagentos.routes.desktop_browser.store import BrowserStore
from pywebpush import WebPushException


# ---------------------------------------------------------------------------
# Module-level: generate a real VAPID keypair once for all tests.
# We need a real PEM because pywebpush parses the private key; only the PEM
# is actually used by push.send() (public key isn't passed to pywebpush).
# ---------------------------------------------------------------------------

import pathlib
import tempfile

_VAPID_TMPDIR = tempfile.mkdtemp()
_vapid_pub, _vapid_priv = load_or_create_vapid_keypair(pathlib.Path(_VAPID_TMPDIR))
FAKE_VAPID = (_vapid_pub, _vapid_priv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENDPOINT_A = "https://push.example.com/sub/device_a"
_ENDPOINT_B = "https://push.example.com/sub/device_b"
_PAYLOAD = {"title": "Test", "body": "Hello"}


def _make_response(status_code: int) -> requests.Response:
    """Construct a requests.Response with the given status code."""
    r = requests.Response()
    r.status_code = status_code
    r.reason = str(status_code)
    return r


def _raise_410(subscription_info, data, vapid_private_key, vapid_claims, timeout=None):
    r = _make_response(410)
    raise WebPushException("Push failed: 410", response=r)


def _raise_429(subscription_info, data, vapid_private_key, vapid_claims, timeout=None):
    r = _make_response(429)
    raise WebPushException("Push failed: 429", response=r)


def _raise_503(subscription_info, data, vapid_private_key, vapid_claims, timeout=None):
    r = _make_response(503)
    raise WebPushException("Push failed: 503", response=r)


def _return_ok(subscription_info, data, vapid_private_key, vapid_claims, timeout=None):
    return _make_response(201)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store(tmp_path):
    s = BrowserStore(tmp_path / "browser.sqlite3")
    await s.init()
    yield s
    await s.close()


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset the module-level rate limiter before and after every test."""
    push_module._rate_limiter = _RateLimiter()
    yield
    push_module._rate_limiter = _RateLimiter()


# ---------------------------------------------------------------------------
# 1. Happy path: 201 → sent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_201_sent(store):
    await store.upsert_push_subscription(
        "user1", "device_a",
        endpoint=_ENDPOINT_A,
        p256dh_key="p256dh_fake",
        auth_key="auth_fake",
    )

    with patch("tinyagentos.routes.desktop_browser.push._sync_send", side_effect=_return_ok):
        result = await send("user1", _PAYLOAD, store=store, vapid=FAKE_VAPID)

    assert result == {"sent": 1, "failed": 0, "removed": 0}

    # Subscription must still exist
    subs = await store.list_push_subscriptions("user1")
    assert len(subs) == 1


# ---------------------------------------------------------------------------
# 2. 410 Gone: subscription deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_410_gone_subscription_deleted(store):
    await store.upsert_push_subscription(
        "user1", "device_a",
        endpoint=_ENDPOINT_A,
        p256dh_key="p256dh_fake",
        auth_key="auth_fake",
    )

    with patch("tinyagentos.routes.desktop_browser.push._sync_send", side_effect=_raise_410):
        result = await send("user1", _PAYLOAD, store=store, vapid=FAKE_VAPID)

    assert result == {"sent": 0, "failed": 0, "removed": 1}

    # Subscription must be gone
    subs = await store.list_push_subscriptions("user1")
    assert subs == []


# ---------------------------------------------------------------------------
# 3. 429: counted as failed, subscription kept
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_429_counted_as_failed_sub_kept(store):
    await store.upsert_push_subscription(
        "user1", "device_a",
        endpoint=_ENDPOINT_A,
        p256dh_key="p256dh_fake",
        auth_key="auth_fake",
    )

    with patch("tinyagentos.routes.desktop_browser.push._sync_send", side_effect=_raise_429):
        result = await send("user1", _PAYLOAD, store=store, vapid=FAKE_VAPID)

    assert result == {"sent": 0, "failed": 1, "removed": 0}

    # Subscription must still be present
    subs = await store.list_push_subscriptions("user1")
    assert len(subs) == 1


# ---------------------------------------------------------------------------
# 4. 5xx: counted as failed, subscription kept
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_503_counted_as_failed_sub_kept(store):
    await store.upsert_push_subscription(
        "user1", "device_a",
        endpoint=_ENDPOINT_A,
        p256dh_key="p256dh_fake",
        auth_key="auth_fake",
    )

    with patch("tinyagentos.routes.desktop_browser.push._sync_send", side_effect=_raise_503):
        result = await send("user1", _PAYLOAD, store=store, vapid=FAKE_VAPID)

    assert result == {"sent": 0, "failed": 1, "removed": 0}

    subs = await store.list_push_subscriptions("user1")
    assert len(subs) == 1


# ---------------------------------------------------------------------------
# 5. Rate limit kicks in
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_kicks_in(store):
    await store.upsert_push_subscription(
        "user1", "device_a",
        endpoint=_ENDPOINT_A,
        p256dh_key="p256dh_fake",
        auth_key="auth_fake",
    )

    # Install a tight limiter: 2 per window
    push_module._rate_limiter = _RateLimiter(limit=2, window_seconds=60)

    call_count = 0

    def _ok_and_count(subscription_info, data, vapid_private_key, vapid_claims, timeout=None):
        nonlocal call_count
        call_count += 1
        return _make_response(201)

    with patch("tinyagentos.routes.desktop_browser.push._sync_send", side_effect=_ok_and_count):
        r1 = await send("user1", _PAYLOAD, store=store, vapid=FAKE_VAPID)
        r2 = await send("user1", _PAYLOAD, store=store, vapid=FAKE_VAPID)
        r3 = await send("user1", _PAYLOAD, store=store, vapid=FAKE_VAPID)

    assert r1 == {"sent": 1, "failed": 0, "removed": 0}
    assert r2 == {"sent": 1, "failed": 0, "removed": 0}
    # Third call must be denied by rate limiter; no upstream call
    assert r3 == {"sent": 0, "failed": 1, "removed": 0}
    assert call_count == 2, f"Expected exactly 2 upstream calls, got {call_count}"


# ---------------------------------------------------------------------------
# 6. Device filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_device_filter(store):
    await store.upsert_push_subscription(
        "user1", "device_a",
        endpoint=_ENDPOINT_A,
        p256dh_key="p256dh_a",
        auth_key="auth_a",
    )
    await store.upsert_push_subscription(
        "user1", "device_b",
        endpoint=_ENDPOINT_B,
        p256dh_key="p256dh_b",
        auth_key="auth_b",
    )

    call_count = 0

    def _ok_and_count(subscription_info, data, vapid_private_key, vapid_claims, timeout=None):
        nonlocal call_count
        call_count += 1
        return _make_response(201)

    with patch("tinyagentos.routes.desktop_browser.push._sync_send", side_effect=_ok_and_count):
        result = await send(
            "user1", _PAYLOAD,
            devices=["device_a"],
            store=store,
            vapid=FAKE_VAPID,
        )

    assert result == {"sent": 1, "failed": 0, "removed": 0}
    assert call_count == 1, f"Expected exactly 1 upstream call (device filter), got {call_count}"


# ---------------------------------------------------------------------------
# 7. Multi-sub partial failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_sub_partial_failure(store):
    await store.upsert_push_subscription(
        "user1", "device_a",
        endpoint=_ENDPOINT_A,
        p256dh_key="p256dh_a",
        auth_key="auth_a",
    )
    await store.upsert_push_subscription(
        "user1", "device_b",
        endpoint=_ENDPOINT_B,
        p256dh_key="p256dh_b",
        auth_key="auth_b",
    )

    def _dispatch(subscription_info, data, vapid_private_key, vapid_claims, timeout=None):
        endpoint = subscription_info["endpoint"]
        if endpoint == _ENDPOINT_A:
            return _make_response(201)
        # _ENDPOINT_B → 410
        r = _make_response(410)
        raise WebPushException("Push failed: 410", response=r)

    with patch("tinyagentos.routes.desktop_browser.push._sync_send", side_effect=_dispatch):
        result = await send("user1", _PAYLOAD, store=store, vapid=FAKE_VAPID)

    assert result == {"sent": 1, "failed": 0, "removed": 1}

    # 410 sub (device_b / _ENDPOINT_B) must be gone
    subs = await store.list_push_subscriptions("user1")
    assert len(subs) == 1
    assert subs[0]["device_id"] == "device_a"

    # 201 sub (device_a) must still be present
    assert subs[0]["endpoint"] == _ENDPOINT_A


# ---------------------------------------------------------------------------
# 8. Timeout: counted as failed, subscription kept
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_timeout_counted_as_failed(store):
    await store.upsert_push_subscription("user_a", "device_a", "https://example.com/push", "p", "a")

    # Patch _send_one's executor call site by patching _sync_send to raise
    # — wait_for catches the exception type and increments failed.
    with patch.object(push_module, "_sync_send", side_effect=asyncio.TimeoutError()):
        result = await send(
            "user_a", {"title": "x"}, store=store, vapid=FAKE_VAPID,
        )
    assert result == {"sent": 0, "failed": 1, "removed": 0}
    rows = await store.list_push_subscriptions("user_a")
    assert len(rows) == 1  # not deleted on timeout
