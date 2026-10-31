import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.notifications import NotificationStore
from taos_test_csrf import csrf_event_hooks


def _row_by_title(items: list[dict], title: str) -> dict:
    for item in items:
        if item["title"] == title:
            return item
    raise AssertionError(f"no notification titled {title!r}")


def _id_by_title(items: list[dict], title: str) -> int:
    return _row_by_title(items, title)["id"]


def _make_config(tmp_path) -> dict:
    return {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }


@pytest_asyncio.fixture
async def notif_store(tmp_path):
    store = NotificationStore(tmp_path / "notifications.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestNotificationStoreHistory:
    async def test_search_returns_own_and_broadcast(self, notif_store):
        await notif_store.add("a", "a msg", user_id="u1")
        await notif_store.add("b", "b msg", user_id="u2")
        await notif_store.add("c", "c msg")
        items = await notif_store.search(user_id="u1")
        titles = {i["title"] for i in items}
        assert titles == {"a", "c"}

    async def test_search_excludes_other_users(self, notif_store):
        await notif_store.add("a", "a msg", user_id="u1")
        await notif_store.add("b", "b msg", user_id="u2")
        items = await notif_store.search(user_id="u1")
        assert all(i["user_id"] != "u2" for i in items)

    async def test_search_q_matches_title_and_message(self, notif_store):
        await notif_store.add("foo", "bar baz", user_id="u1")
        await notif_store.add("hello", "world", user_id="u1")
        items = await notif_store.search(user_id="u1", q="foo")
        assert len(items) == 1
        assert items[0]["title"] == "foo"
        items = await notif_store.search(user_id="u1", q="world")
        assert len(items) == 1
        assert items[0]["title"] == "hello"

    async def test_search_q_literal_percent_matches_only_percent_rows(self, notif_store):
        await notif_store.add("has percent", "100% done", user_id="u1")
        await notif_store.add("no percent", "fully done", user_id="u1")
        items = await notif_store.search(user_id="u1", q="%")
        titles = {i["title"] for i in items}
        assert titles == {"has percent"}

    async def test_search_source_filter(self, notif_store):
        await notif_store.add("a", "msg", source="src1", user_id="u1")
        await notif_store.add("b", "msg", source="src2", user_id="u1")
        await notif_store.add("c", "msg", source="src1", user_id="u1")
        items = await notif_store.search(user_id="u1", sources=["src1"])
        titles = {i["title"] for i in items}
        assert titles == {"a", "c"}

    async def test_search_level_filter(self, notif_store):
        await notif_store.add("a", "msg", level="info", user_id="u1")
        await notif_store.add("b", "msg", level="warning", user_id="u1")
        await notif_store.add("c", "msg", level="error", user_id="u1")
        items = await notif_store.search(user_id="u1", levels=["warning", "error"])
        titles = {i["title"] for i in items}
        assert titles == {"b", "c"}

    async def test_search_since_until(self, notif_store):
        import time
        ts1 = int(time.time()) - 10000
        ts2 = int(time.time()) - 5000
        ts3 = int(time.time())
        await notif_store.add("old", "msg", user_id="u1")
        await notif_store.add("mid", "msg", user_id="u1")
        await notif_store.add("new", "msg", user_id="u1")
        # Manually set timestamps for precise test
        async with notif_store._db.execute(
            "UPDATE notifications SET timestamp = ? WHERE title = ?", (ts1, "old")
        ):
            pass
        async with notif_store._db.execute(
            "UPDATE notifications SET timestamp = ? WHERE title = ?", (ts2, "mid")
        ):
            pass
        async with notif_store._db.execute(
            "UPDATE notifications SET timestamp = ? WHERE title = ?", (ts3, "new")
        ):
            pass
        await notif_store._db.commit()

        items = await notif_store.search(user_id="u1", since=ts2 - 1, until=ts3 + 1)
        titles = {i["title"] for i in items}
        assert "mid" in titles
        assert "new" in titles
        assert "old" not in titles

    async def test_search_state_unread(self, notif_store):
        await notif_store.add("unread1", "msg", user_id="u1")
        await notif_store.add("unread2", "msg", user_id="u1")
        await notif_store.add("read1", "msg", user_id="u1")
        items = await notif_store.list(user_id="u1")
        read_id = _id_by_title(items, "read1")
        await notif_store.mark_read(read_id, user_id="u1")
        items = await notif_store.search(user_id="u1", state="unread")
        titles = {i["title"] for i in items}
        assert titles == {"unread1", "unread2"}

    async def test_search_state_read(self, notif_store):
        await notif_store.add("unread1", "msg", user_id="u1")
        await notif_store.add("read1", "msg", user_id="u1")
        items = await notif_store.list(user_id="u1")
        read_id = _id_by_title(items, "read1")
        await notif_store.mark_read(read_id, user_id="u1")
        items = await notif_store.search(user_id="u1", state="read")
        titles = {i["title"] for i in items}
        assert titles == {"read1"}

    async def test_search_state_read_includes_marked_read_broadcast(self, notif_store):
        await notif_store.add("broadcast-read", "for everyone")
        broadcast_id = _id_by_title(await notif_store.list(), "broadcast-read")
        await notif_store.mark_read(broadcast_id, user_id="u1")
        items = await notif_store.search(user_id="u1", state="read")
        titles = {i["title"] for i in items}
        assert "broadcast-read" in titles

    async def test_search_state_archived(self, notif_store):
        await notif_store.add("active1", "msg", user_id="u1")
        await notif_store.add("archived1", "msg", user_id="u1")
        items = await notif_store.list(user_id="u1")
        archived_id = _id_by_title(items, "archived1")
        await notif_store.archive(archived_id, user_id="u1")
        items = await notif_store.search(user_id="u1", state="archived")
        titles = {i["title"] for i in items}
        assert titles == {"archived1"}

    async def test_search_state_active(self, notif_store):
        await notif_store.add("active1", "msg", user_id="u1")
        await notif_store.add("archived1", "msg", user_id="u1")
        items = await notif_store.list(user_id="u1")
        archived_id = _id_by_title(items, "archived1")
        await notif_store.archive(archived_id, user_id="u1")
        items = await notif_store.search(user_id="u1", state="active")
        titles = {i["title"] for i in items}
        assert titles == {"active1"}

    async def test_search_state_all(self, notif_store):
        await notif_store.add("active1", "msg", user_id="u1")
        await notif_store.add("archived1", "msg", user_id="u1")
        items = await notif_store.list(user_id="u1")
        archived_id = _id_by_title(items, "archived1")
        await notif_store.archive(archived_id, user_id="u1")
        items = await notif_store.search(user_id="u1", state="all")
        titles = {i["title"] for i in items}
        assert titles == {"active1", "archived1"}

    async def test_search_broadcast_archive_per_user(self, notif_store):
        await notif_store.add("broadcast", "for everyone")
        await notif_store.add("a", "a msg", user_id="u1")
        broadcast_id = _id_by_title(await notif_store.list(), "broadcast")
        await notif_store.archive(broadcast_id, user_id="u1")
        u1_archived = await notif_store.search(user_id="u1", state="archived")
        assert any(h["title"] == "broadcast" for h in u1_archived)
        u2_archived = await notif_store.search(user_id="u2", state="archived")
        assert not any(h["title"] == "broadcast" for h in u2_archived)

    async def test_search_paging_walks_all_no_duplicates_no_gaps(self, notif_store):
        # Create 120 archived rows
        for i in range(120):
            await notif_store.add(f"notif-{i:03d}", f"msg {i}", user_id="u1")
        items = await notif_store.list(user_id="u1", limit=200)
        for item in items:
            await notif_store.archive(item["id"], user_id="u1")

        all_items = []
        before = None
        while True:
            page = await notif_store.search(user_id="u1", state="archived", before=before, limit=20)
            if not page:
                break
            all_items.extend(page)
            # Get cursor from last item
            last = page[-1]
            before = f"{last['timestamp']}:{last['id']}"

        assert len(all_items) == 120
        # No duplicates
        ids = [i["id"] for i in all_items]
        assert len(set(ids)) == 120
        # Ordered correctly (newest first)
        timestamps = [i["timestamp"] for i in all_items]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_search_limit_clamped(self, notif_store):
        for i in range(10):
            await notif_store.add(f"n{i}", "msg", user_id="u1")
        items = await notif_store.search(user_id="u1", limit=500)
        assert len(items) <= 200
        items = await notif_store.search(user_id="u1", limit=0)
        assert len(items) <= 200

    async def test_history_sources_distinct_sorted(self, notif_store):
        await notif_store.add("a", "msg", source="src1", user_id="u1")
        await notif_store.add("b", "msg", source="src2", user_id="u1")
        await notif_store.add("c", "msg", source="src1", user_id="u1")
        await notif_store.add("d", "msg", source="src3", user_id="u2")
        sources = await notif_store.history_sources(user_id="u1")
        assert sources == ["src1", "src2"]


@pytest_asyncio.fixture
async def two_user_app(tmp_path):
    config = _make_config(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)

    notif_store = app.state.notifications
    if notif_store._db is not None:
        await notif_store.close()
    await notif_store.init()

    auth = app.state.auth
    auth.setup_user("alice", "Alice", "", "alicepass123")
    alice_rec = auth.find_user("alice")
    alice_token = auth.create_session(user_id=alice_rec["id"], long_lived=True)

    bob_invite = auth.add_user_invite("bob", "alice")
    auth.complete_invite("bob", bob_invite, "Bob", "", "bobpass123")
    bob_rec = auth.find_user("bob")
    bob_token = auth.create_session(user_id=bob_rec["id"], long_lived=True)

    app.state._startup_complete = True

    return app, alice_rec["id"], alice_token, bob_rec["id"], bob_token


def _alice_client(app, alice_token):
    transport = ASGITransport(app=app)
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": alice_token},
        event_hooks=csrf_event_hooks(),
    )


def _bob_client(app, bob_token):
    transport = ASGITransport(app=app)
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": bob_token},
        event_hooks=csrf_event_hooks(),
    )


@pytest.mark.asyncio
class TestNotificationHistoryRoutes:
    async def test_history_returns_items_and_cursor(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("alice-notif", "for alice", user_id=alice_id)
        await store.add("bob-notif", "for bob", user_id=bob_id)
        await store.add("broadcast", "for everyone")
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history")
            assert resp.status_code == 200
            data = resp.json()
            assert "items" in data
            assert "next_before" in data
            titles = {i["title"] for i in data["items"]}
            assert "alice-notif" in titles
            assert "bob-notif" not in titles
            assert "broadcast" in titles

    async def test_history_sources(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("a", "msg", source="src1", user_id=alice_id)
        await store.add("b", "msg", source="src2", user_id=alice_id)
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history/sources")
            assert resp.status_code == 200
            data = resp.json()
            assert "sources" in data
            assert data["sources"] == ["src1", "src2"]

    async def test_history_q_filter(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("foo bar", "msg", user_id=alice_id)
        await store.add("hello world", "msg", user_id=alice_id)
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history", params={"q": "foo"})
            assert resp.status_code == 200
            items = resp.json()["items"]
            assert len(items) == 1
            assert items[0]["title"] == "foo bar"

    async def test_history_source_filter_multi(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("a", "msg", source="src1", user_id=alice_id)
        await store.add("b", "msg", source="src2", user_id=alice_id)
        await store.add("c", "msg", source="src3", user_id=alice_id)
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history", params={"source": ["src1", "src2"]})
            assert resp.status_code == 200
            items = resp.json()["items"]
            sources = {i["source"] for i in items}
            assert sources == {"src1", "src2"}

    async def test_history_level_filter_multi(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("a", "msg", level="info", user_id=alice_id)
        await store.add("b", "msg", level="warning", user_id=alice_id)
        await store.add("c", "msg", level="error", user_id=alice_id)
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history", params={"level": ["warning", "error"]})
            assert resp.status_code == 200
            items = resp.json()["items"]
            levels = {i["level"] for i in items}
            assert levels == {"warning", "error"}

    async def test_history_since_until(self, two_user_app):
        import time
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        ts1 = int(time.time()) - 10000
        ts2 = int(time.time()) - 5000
        ts3 = int(time.time())
        await store.add("old", "msg", user_id=alice_id)
        await store.add("mid", "msg", user_id=alice_id)
        await store.add("new", "msg", user_id=alice_id)
        async with store._db.execute(
            "UPDATE notifications SET timestamp = ? WHERE title = ?", (ts1, "old")
        ):
            pass
        async with store._db.execute(
            "UPDATE notifications SET timestamp = ? WHERE title = ?", (ts2, "mid")
        ):
            pass
        async with store._db.execute(
            "UPDATE notifications SET timestamp = ? WHERE title = ?", (ts3, "new")
        ):
            pass
        await store._db.commit()

        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history", params={"since": ts2 - 1, "until": ts3 + 1})
            assert resp.status_code == 200
            items = resp.json()["items"]
            titles = {i["title"] for i in items}
            assert "mid" in titles
            assert "new" in titles
            assert "old" not in titles

    async def test_history_state_unread(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("unread1", "msg", user_id=alice_id)
        await store.add("read1", "msg", user_id=alice_id)
        items = await store.list(user_id=alice_id)
        read_id = _id_by_title(items, "read1")
        await store.mark_read(read_id, user_id=alice_id)
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history", params={"state": "unread"})
            assert resp.status_code == 200
            items = resp.json()["items"]
            titles = {i["title"] for i in items}
            assert titles == {"unread1"}

    async def test_history_state_archived(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("active1", "msg", user_id=alice_id)
        await store.add("archived1", "msg", user_id=alice_id)
        items = await store.list(user_id=alice_id)
        archived_id = _id_by_title(items, "archived1")
        await store.archive(archived_id, user_id=alice_id)
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history", params={"state": "archived"})
            assert resp.status_code == 200
            items = resp.json()["items"]
            titles = {i["title"] for i in items}
            assert titles == {"archived1"}

    async def test_history_invalid_state_returns_400(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history", params={"state": "bogus"})
            assert resp.status_code == 400

    async def test_history_malformed_cursor_returns_400(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history", params={"before": "not-a-cursor"})
            assert resp.status_code == 400
            resp = await c.get("/api/notifications/history", params={"before": "123"})
            assert resp.status_code == 400
            resp = await c.get("/api/notifications/history", params={"before": "123:abc"})
            assert resp.status_code == 400

    async def test_history_paging_walks_all(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        for i in range(50):
            await store.add(f"notif-{i:02d}", f"msg {i}", user_id=alice_id)
        items = await store.list(user_id=alice_id, limit=200)
        for item in items:
            await store.archive(item["id"], user_id=alice_id)

        all_items = []
        before = None
        async with _alice_client(app, alice_token) as c:
            while True:
                params = {"state": "archived", "limit": 10}
                if before:
                    params["before"] = before
                resp = await c.get("/api/notifications/history", params=params)
                assert resp.status_code == 200
                data = resp.json()
                if not data["items"]:
                    break
                all_items.extend(data["items"])
                if data["next_before"] is None:
                    break
                before = data["next_before"]

        assert len(all_items) == 50
        ids = [i["id"] for i in all_items]
        assert len(set(ids)) == 50

    async def test_history_user_a_never_sees_user_b_rows(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("alice-only", "msg", user_id=alice_id)
        await store.add("bob-only", "msg", user_id=bob_id)
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history")
            assert resp.status_code == 200
            items = resp.json()["items"]
            titles = {i["title"] for i in items}
            assert "alice-only" in titles
            assert "bob-only" not in titles

    async def test_history_state_read_includes_marked_read_broadcast(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("broadcast-read", "for everyone")
        broadcast_id = _id_by_title(await store.list(), "broadcast-read")
        await store.mark_read(broadcast_id, user_id=alice_id)
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history", params={"state": "read"})
            assert resp.status_code == 200
            titles = {i["title"] for i in resp.json()["items"]}
            assert "broadcast-read" in titles

    async def test_history_limit_clamp_keeps_paging(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        for i in range(201):
            await store.add(f"notif-{i:03d}", f"msg {i}", user_id=alice_id)
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history", params={"limit": 500})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["items"]) == 200
            assert data["next_before"] is not None

    async def test_history_both_see_broadcast(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("broadcast", "for everyone")
        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history")
            assert resp.status_code == 200
            titles = {i["title"] for i in resp.json()["items"]}
            assert "broadcast" in titles
        async with _bob_client(app, bob_token) as c:
            resp = await c.get("/api/notifications/history")
            assert resp.status_code == 200
            titles = {i["title"] for i in resp.json()["items"]}
            assert "broadcast" in titles

    async def test_history_broadcast_archive_a_does_not_archive_for_b(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("broadcast", "for everyone")
        items = await store.list()
        broadcast_id = _id_by_title(items, "broadcast")
        await store.archive(broadcast_id, user_id=alice_id)

        async with _alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/history", params={"state": "archived"})
            assert resp.status_code == 200
            titles = {i["title"] for i in resp.json()["items"]}
            assert "broadcast" in titles

        async with _bob_client(app, bob_token) as c:
            resp = await c.get("/api/notifications/history", params={"state": "archived"})
            assert resp.status_code == 200
            titles = {i["title"] for i in resp.json()["items"]}
            assert "broadcast" not in titles

            # But bob still sees it in active
            resp = await c.get("/api/notifications/history", params={"state": "active"})
            assert resp.status_code == 200
            titles = {i["title"] for i in resp.json()["items"]}
            assert "broadcast" in titles