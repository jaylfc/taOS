import time

import aiosqlite
import pytest
import pytest_asyncio

from tinyagentos.notifications import NotificationStore


@pytest_asyncio.fixture
async def notif_store(tmp_path):
    store = NotificationStore(tmp_path / "notifications.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestNotificationStore:
    async def test_add_and_list(self, notif_store):
        await notif_store.add("Test title", "Test message", level="info", source="test")
        items = await notif_store.list()
        assert len(items) == 1
        assert items[0]["title"] == "Test title"
        assert items[0]["message"] == "Test message"
        assert items[0]["level"] == "info"
        assert items[0]["source"] == "test"
        assert items[0]["read"] is False

    async def test_unread_count(self, notif_store):
        assert await notif_store.unread_count() == 0
        await notif_store.add("A", "a")
        await notif_store.add("B", "b")
        assert await notif_store.unread_count() == 2

    async def test_mark_read(self, notif_store):
        await notif_store.add("A", "a")
        items = await notif_store.list()
        notif_id = items[0]["id"]
        await notif_store.mark_read(notif_id)
        assert await notif_store.unread_count() == 0
        items = await notif_store.list()
        assert items[0]["read"] is True

    async def test_mark_all_read(self, notif_store):
        await notif_store.add("A", "a")
        await notif_store.add("B", "b")
        await notif_store.add("C", "c")
        assert await notif_store.unread_count() == 3
        await notif_store.mark_all_read()
        assert await notif_store.unread_count() == 0

    async def test_cleanup(self, notif_store):
        # Insert an old notification directly
        old_ts = int(time.time()) - (31 * 86400)
        await notif_store._db.execute(
            "INSERT INTO notifications (timestamp, level, title, message, source) VALUES (?, ?, ?, ?, ?)",
            (old_ts, "info", "Old", "old message", "test"),
        )
        await notif_store._db.commit()
        await notif_store.add("New", "new message")
        deleted = await notif_store.cleanup(max_age_days=30)
        assert deleted == 1
        items = await notif_store.list()
        assert len(items) == 1
        assert items[0]["title"] == "New"

    async def test_cleanup_preserves_archived(self, notif_store):
        # An old but archived (dismissed) notification is durable history and
        # must survive the age-based GC.
        old_ts = int(time.time()) - (31 * 86400)
        await notif_store._db.execute(
            "INSERT INTO notifications (timestamp, level, title, message, source, archived)"
            " VALUES (?, ?, ?, ?, ?, 1)",
            (old_ts, "info", "OldDismissed", "kept", "test"),
        )
        await notif_store._db.commit()
        deleted = await notif_store.cleanup(max_age_days=30)
        assert deleted == 0
        history = await notif_store.list_archived()
        assert [h["title"] for h in history] == ["OldDismissed"]

    async def test_list_unread_only(self, notif_store):
        await notif_store.add("A", "a")
        await notif_store.add("B", "b")
        items = await notif_store.list()
        await notif_store.mark_read(items[0]["id"])
        unread = await notif_store.list(unread_only=True)
        assert len(unread) == 1

    async def test_archive_hides_from_active_list(self, notif_store):
        await notif_store.add("A", "a")
        await notif_store.add("B", "b")
        items = await notif_store.list()
        await notif_store.archive(items[0]["id"])
        active = await notif_store.list()
        assert len(active) == 1
        assert items[0]["id"] not in [i["id"] for i in active]

    async def test_archived_appears_in_history(self, notif_store):
        await notif_store.add("A", "a")
        items = await notif_store.list()
        await notif_store.archive(items[0]["id"])
        history = await notif_store.list_archived()
        assert len(history) == 1
        assert history[0]["id"] == items[0]["id"]

    async def test_archive_excluded_from_unread_count(self, notif_store):
        await notif_store.add("A", "a")
        await notif_store.add("B", "b")
        assert await notif_store.unread_count() == 2
        items = await notif_store.list()
        await notif_store.archive(items[0]["id"])
        # Dismissed notification no longer counts toward the badge.
        assert await notif_store.unread_count() == 1

    async def test_list_limit(self, notif_store):
        for i in range(10):
            await notif_store.add(f"N{i}", f"msg{i}")
        items = await notif_store.list(limit=3)
        assert len(items) == 3

    async def test_data_payload_round_trips(self, notif_store):
        payload = {"request_id": "req-1", "requested_scopes": ["memory_read"]}
        await notif_store.add("Access request", "owl wants in", source="auth_requests", data=payload)
        items = await notif_store.list()
        assert items[0]["data"] == payload

    async def test_data_defaults_to_none(self, notif_store):
        await notif_store.add("Plain", "no payload")
        items = await notif_store.list()
        assert items[0]["data"] is None

    async def test_archive_by_source_ref(self, notif_store):
        await notif_store.add(
            "Access request", "owl wants in", source="auth_requests",
            data={"request_id": "req-1", "requested_scopes": []},
        )
        await notif_store.add(
            "Other request", "cat wants in", source="auth_requests",
            data={"request_id": "req-2", "requested_scopes": []},
        )
        n = await notif_store.archive_by_source_ref("auth_requests", "req-1")
        assert n == 1
        active_ids = {(i["data"] or {}).get("request_id") for i in await notif_store.list()}
        assert "req-1" not in active_ids
        assert "req-2" in active_ids
        # Resolving moves it into History (archived, not deleted) AND marks it
        # read — acting on a notification both reads and archives it (#62).
        history = await notif_store.list_archived()
        archived = next(h for h in history if (h["data"] or {}).get("request_id") == "req-1")
        assert archived["read"] is True
        # Idempotent: archiving again matches nothing.
        assert await notif_store.archive_by_source_ref("auth_requests", "req-1") == 0


@pytest.mark.asyncio
async def test_data_column_migration_on_legacy_db(tmp_path):
    # Simulate a pre-`data` database (the column did not exist at first ship).
    db_path = tmp_path / "legacy.db"
    legacy = await aiosqlite.connect(str(db_path))
    await legacy.execute(
        "CREATE TABLE notifications ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL,"
        " level TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL,"
        " read INTEGER NOT NULL DEFAULT 0, source TEXT,"
        " archived INTEGER NOT NULL DEFAULT 0)"
    )
    await legacy.commit()
    await legacy.close()

    # Opening the store must add the column without a destructive migration.
    store = NotificationStore(db_path)
    await store.init()
    try:
        await store.add("After upgrade", "ok", source="auth_requests", data={"request_id": "r1"})
        items = await store.list()
        assert items[0]["data"] == {"request_id": "r1"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_notification_api_count(client):
    store = client._transport.app.state.notifications
    await store.add("Test", "test msg")
    resp = await client.get("/api/notifications/count")
    assert resp.status_code == 200
    assert "1" in resp.text


@pytest.mark.asyncio
async def test_notification_api_list(client):
    store = client._transport.app.state.notifications
    await store.add("Hello", "world")
    resp = await client.get("/api/notifications")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Hello"


@pytest.mark.asyncio
async def test_notification_api_mark_read(client):
    store = client._transport.app.state.notifications
    await store.add("Hello", "world")
    items = await store.list()
    notif_id = items[0]["id"]
    resp = await client.post(f"/api/notifications/{notif_id}/read")
    assert resp.status_code == 200
    assert await store.unread_count() == 0


@pytest.mark.asyncio
async def test_notification_api_read_all(client):
    store = client._transport.app.state.notifications
    await store.add("A", "a")
    await store.add("B", "b")
    resp = await client.post("/api/notifications/read-all")
    assert resp.status_code == 200
    assert await store.unread_count() == 0


@pytest.mark.asyncio
async def test_notification_api_archive(client):
    store = client._transport.app.state.notifications
    await store.add("Dismiss me", "bye")
    notif_id = (await store.list())[0]["id"]
    resp = await client.post(f"/api/notifications/{notif_id}/archive")
    assert resp.status_code == 200
    # Gone from the active feed, present in history.
    assert (await store.list()) == []
    history = await store.list_archived()
    assert len(history) == 1
    assert history[0]["id"] == notif_id


@pytest.mark.asyncio
async def test_notification_api_archived_history(client):
    store = client._transport.app.state.notifications
    await store.add("Kept", "k")
    notif_id = (await store.list())[0]["id"]
    await client.post(f"/api/notifications/{notif_id}/archive")
    resp = await client.get("/api/notifications/archived")
    assert resp.status_code == 200
    data = resp.json()
    assert [d["title"] for d in data] == ["Kept"]
