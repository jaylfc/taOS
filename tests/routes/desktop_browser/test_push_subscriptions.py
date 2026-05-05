"""Tests for BrowserStore push_subscriptions methods."""
from __future__ import annotations

import time

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def store(tmp_path):
    from tinyagentos.routes.desktop_browser.store import BrowserStore

    s = BrowserStore(tmp_path / "browser.sqlite3")
    await s.init()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sub(endpoint="https://push.example.com/sub1", p256dh="key1", auth="auth1"):
    return dict(endpoint=endpoint, p256dh_key=p256dh, auth_key=auth)


# ---------------------------------------------------------------------------
# 1. Upsert insert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_insert_basic(store):
    """Insert a fresh subscription and verify all fields are returned."""
    before = int(time.time())
    await store.upsert_push_subscription(
        "user_a", "device_1",
        endpoint="https://push.example.com/sub1",
        p256dh_key="p256dhkey==",
        auth_key="authkey==",
        user_agent="Mozilla/5.0",
    )
    after = int(time.time())

    rows = await store.list_push_subscriptions("user_a")
    assert len(rows) == 1
    row = rows[0]
    assert row["device_id"] == "device_1"
    assert row["endpoint"] == "https://push.example.com/sub1"
    assert row["p256dh_key"] == "p256dhkey=="
    assert row["auth_key"] == "authkey=="
    assert row["user_agent"] == "Mozilla/5.0"
    assert before <= row["created_at"] <= after
    assert before <= row["last_seen_at"] <= after


# ---------------------------------------------------------------------------
# 2. Upsert replace preserves created_at
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_replace_preserves_created_at(store):
    """Re-upserting same (user, device) updates endpoint/last_seen_at but keeps created_at."""
    await store.upsert_push_subscription(
        "user_a", "device_1",
        endpoint="https://push.example.com/old",
        p256dh_key="k1",
        auth_key="a1",
    )
    rows = await store.list_push_subscriptions("user_a")
    original_created_at = rows[0]["created_at"]
    original_last_seen = rows[0]["last_seen_at"]

    # Advance wall clock by at least 1 second so last_seen_at changes
    time.sleep(1)

    await store.upsert_push_subscription(
        "user_a", "device_1",
        endpoint="https://push.example.com/new",
        p256dh_key="k2",
        auth_key="a2",
    )

    rows = await store.list_push_subscriptions("user_a")
    assert len(rows) == 1
    row = rows[0]
    assert row["created_at"] == original_created_at, "created_at must not change on upsert"
    assert row["last_seen_at"] > original_last_seen, "last_seen_at must advance"
    assert row["endpoint"] == "https://push.example.com/new"


# ---------------------------------------------------------------------------
# 3. Multi-user isolation on list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_isolation_by_user(store):
    """list_push_subscriptions returns only the requesting user's rows."""
    await store.upsert_push_subscription(
        "user_a", "device_x",
        endpoint="https://push.example.com/a",
        p256dh_key="ka",
        auth_key="aa",
    )
    await store.upsert_push_subscription(
        "user_b", "device_x",
        endpoint="https://push.example.com/b",
        p256dh_key="kb",
        auth_key="ab",
    )

    rows_a = await store.list_push_subscriptions("user_a")
    assert len(rows_a) == 1
    assert rows_a[0]["endpoint"] == "https://push.example.com/a"

    rows_b = await store.list_push_subscriptions("user_b")
    assert len(rows_b) == 1
    assert rows_b[0]["endpoint"] == "https://push.example.com/b"


# ---------------------------------------------------------------------------
# 4. Multi-user isolation on delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_isolation_by_user(store):
    """delete_push_subscription for user_a must not touch user_b's identical device_id."""
    await store.upsert_push_subscription(
        "user_a", "device_x",
        endpoint="https://push.example.com/a",
        p256dh_key="ka",
        auth_key="aa",
    )
    await store.upsert_push_subscription(
        "user_b", "device_x",
        endpoint="https://push.example.com/b",
        p256dh_key="kb",
        auth_key="ab",
    )

    deleted = await store.delete_push_subscription("user_a", "device_x")
    assert deleted is True

    # user_a's row is gone
    assert await store.list_push_subscriptions("user_a") == []
    # user_b's row is untouched
    rows_b = await store.list_push_subscriptions("user_b")
    assert len(rows_b) == 1
    assert rows_b[0]["device_id"] == "device_x"


# ---------------------------------------------------------------------------
# 5. Delete returns False when no row matched
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_returns_false_on_miss(store):
    result = await store.delete_push_subscription("user_a", "nonexistent_device")
    assert result is False


# ---------------------------------------------------------------------------
# 6. Delete by endpoint cleans across users
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_by_endpoint_cross_user(store):
    """delete_push_subscription_by_endpoint removes matching rows for all users."""
    shared_endpoint = "https://push.example.com/shared"

    await store.upsert_push_subscription(
        "user_a", "device_a",
        endpoint=shared_endpoint,
        p256dh_key="ka",
        auth_key="aa",
    )
    await store.upsert_push_subscription(
        "user_b", "device_b",
        endpoint=shared_endpoint,
        p256dh_key="kb",
        auth_key="ab",
    )

    deleted = await store.delete_push_subscription_by_endpoint(shared_endpoint)
    assert deleted == 2

    # Both users' rows are gone
    assert await store.list_push_subscriptions("user_a") == []
    assert await store.list_push_subscriptions("user_b") == []


# ---------------------------------------------------------------------------
# 7. List ordering — most recent last_seen_at first
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_ordering_by_last_seen_at_desc(store):
    """list_push_subscriptions returns rows newest-first by last_seen_at."""
    await store.upsert_push_subscription(
        "user_a", "device_old",
        endpoint="https://push.example.com/old",
        p256dh_key="ko",
        auth_key="ao",
    )

    time.sleep(1)

    await store.upsert_push_subscription(
        "user_a", "device_new",
        endpoint="https://push.example.com/new",
        p256dh_key="kn",
        auth_key="an",
    )

    rows = await store.list_push_subscriptions("user_a")
    assert len(rows) == 2
    assert rows[0]["device_id"] == "device_new", "most recently seen device must be first"
    assert rows[1]["device_id"] == "device_old"
    assert rows[0]["last_seen_at"] >= rows[1]["last_seen_at"]


# ---------------------------------------------------------------------------
# 8. Endpoint dedup — same endpoint, different device_id, same user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_endpoint_dedup_same_user(store):
    """Re-subscribing with a new device_id but the same endpoint removes the old row."""
    shared_endpoint = "https://push.example.com/dedup-test"

    # First subscription: device_x with endpoint E.
    await store.upsert_push_subscription(
        "user_a", "device_x",
        endpoint=shared_endpoint,
        p256dh_key="k1",
        auth_key="a1",
    )

    # Second subscription: device_y with same endpoint E — old row must be removed.
    await store.upsert_push_subscription(
        "user_a", "device_y",
        endpoint=shared_endpoint,
        p256dh_key="k2",
        auth_key="a2",
    )

    rows = await store.list_push_subscriptions("user_a")
    assert len(rows) == 1, "duplicate endpoint rows must be removed"
    assert rows[0]["device_id"] == "device_y"
    assert rows[0]["endpoint"] == shared_endpoint


@pytest.mark.asyncio
async def test_endpoint_dedup_does_not_affect_other_users(store):
    """Dedup within user_a must not remove user_b's row with the same endpoint."""
    shared_endpoint = "https://push.example.com/cross-user-dedup"

    # user_b subscribes with device_b.
    await store.upsert_push_subscription(
        "user_b", "device_b",
        endpoint=shared_endpoint,
        p256dh_key="kb",
        auth_key="ab",
    )

    # user_a subscribes with device_x, then device_y (same endpoint).
    await store.upsert_push_subscription(
        "user_a", "device_x",
        endpoint=shared_endpoint,
        p256dh_key="k1",
        auth_key="a1",
    )
    await store.upsert_push_subscription(
        "user_a", "device_y",
        endpoint=shared_endpoint,
        p256dh_key="k2",
        auth_key="a2",
    )

    # user_a ends up with exactly one row (device_y).
    rows_a = await store.list_push_subscriptions("user_a")
    assert len(rows_a) == 1
    assert rows_a[0]["device_id"] == "device_y"

    # user_b's row is untouched.
    rows_b = await store.list_push_subscriptions("user_b")
    assert len(rows_b) == 1
    assert rows_b[0]["device_id"] == "device_b"
