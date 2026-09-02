import secrets

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.notifications import NotificationStore
from taos_test_csrf import csrf_event_hooks


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
class TestNotificationStoreUserScope:
    async def test_list_returns_own_and_broadcast(self, notif_store):
        await notif_store.add("a", "a msg", user_id="u1")
        await notif_store.add("b", "b msg", user_id="u2")
        await notif_store.add("c", "c msg")
        items = await notif_store.list(user_id="u1")
        titles = {i["title"] for i in items}
        assert titles == {"a", "c"}

    async def test_list_excludes_other_users(self, notif_store):
        await notif_store.add("a", "a msg", user_id="u1")
        await notif_store.add("b", "b msg", user_id="u2")
        items = await notif_store.list(user_id="u1")
        assert all(i["user_id"] != "u2" for i in items)

    async def test_list_archived_returns_own_only(self, notif_store):
        await notif_store.add("a", "a msg", user_id="u1")
        await notif_store.add("b", "b msg", user_id="u2")
        await notif_store.add("c", "c msg")
        items = await notif_store.list()
        await notif_store.archive(items[0]["id"], user_id="u1")
        await notif_store.archive(items[1]["id"], user_id="u2")
        history = await notif_store.list_archived(user_id="u1")
        titles = {h["title"] for h in history}
        assert titles == {"a"}

    async def test_unread_count_counts_own_and_broadcast(self, notif_store):
        await notif_store.add("a", "a msg", user_id="u1")
        await notif_store.add("b", "b msg", user_id="u2")
        await notif_store.add("c", "c msg")
        assert await notif_store.unread_count(user_id="u1") == 2

    async def test_none_user_id_returns_unfiltered(self, notif_store):
        await notif_store.add("a", "a msg", user_id="u1")
        await notif_store.add("b", "b msg", user_id="u2")
        items = await notif_store.list(user_id=None)
        assert len(items) == 2

    async def test_mark_read_scoped_to_user(self, notif_store):
        await notif_store.add("a", "a msg", user_id="u1")
        await notif_store.add("b", "b msg", user_id="u2")
        u1_items = await notif_store.list(user_id="u1")
        u2_items = await notif_store.list(user_id="u2")
        u1_id = u1_items[0]["id"]
        u2_id = u2_items[0]["id"]
        affected = await notif_store.mark_read(u2_id, user_id="u1")
        assert affected == 0
        assert (await notif_store.list(user_id="u2"))[0]["read"] is False
        affected = await notif_store.mark_read(u1_id, user_id="u1")
        assert affected == 1
        assert (await notif_store.list(user_id="u1"))[0]["read"] is True

    async def test_archive_scoped_to_user(self, notif_store):
        await notif_store.add("a", "a msg", user_id="u1")
        await notif_store.add("b", "b msg", user_id="u2")
        u1_items = await notif_store.list(user_id="u1")
        u2_items = await notif_store.list(user_id="u2")
        u1_id = u1_items[0]["id"]
        u2_id = u2_items[0]["id"]
        affected = await notif_store.archive(u2_id, user_id="u1")
        assert affected == 0
        assert len(await notif_store.list_archived(user_id="u2")) == 0
        affected = await notif_store.archive(u1_id, user_id="u1")
        assert affected == 1
        assert len(await notif_store.list_archived(user_id="u1")) == 1


@pytest.mark.asyncio
class TestNotificationRoutesUserScope:
    @pytest_asyncio.fixture
    async def two_user_app(self, tmp_path):
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

    async def _alice_client(self, app, alice_token):
        transport = ASGITransport(app=app)
        return AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": alice_token},
            event_hooks=csrf_event_hooks(),
        )

    async def _bob_client(self, app, bob_token):
        transport = ASGITransport(app=app)
        return AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": bob_token},
            event_hooks=csrf_event_hooks(),
        )

    async def test_list_excludes_other_user(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("alice-notif", "for alice", user_id=alice_id)
        await store.add("bob-notif", "for bob", user_id=bob_id)
        await store.add("broadcast", "for everyone")
        async with await self._alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications")
            assert resp.status_code == 200
            data = resp.json()
            titles = {i["title"] for i in data}
            assert "alice-notif" in titles
            assert "bob-notif" not in titles
            assert "broadcast" in titles

    async def test_archived_excludes_other_user(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("alice-notif", "for alice", user_id=alice_id)
        await store.add("bob-notif", "for bob", user_id=bob_id)
        alice_items = await store.list(user_id=alice_id)
        bob_items = await store.list(user_id=bob_id)
        await store.archive(alice_items[0]["id"], user_id=alice_id)
        await store.archive(bob_items[0]["id"], user_id=bob_id)
        async with await self._alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/archived")
            assert resp.status_code == 200
            data = resp.json()
            titles = {i["title"] for i in data}
            assert "alice-notif" in titles
            assert "bob-notif" not in titles

    async def test_count_excludes_other_user(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("alice-notif", "for alice", user_id=alice_id)
        await store.add("bob-notif", "for bob", user_id=bob_id)
        async with await self._alice_client(app, alice_token) as c:
            resp = await c.get("/api/notifications/count")
            assert resp.status_code == 200
            assert "1" in resp.text

    async def test_mark_read_other_user_returns_404(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("bob-notif", "for bob", user_id=bob_id)
        bob_items = await store.list(user_id=bob_id)
        bob_notif_id = bob_items[0]["id"]
        async with await self._alice_client(app, alice_token) as c:
            resp = await c.post(f"/api/notifications/{bob_notif_id}/read")
            assert resp.status_code == 404
            assert (await store.list(user_id=bob_id))[0]["read"] is False

    async def test_archive_other_user_returns_404(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("bob-notif", "for bob", user_id=bob_id)
        bob_items = await store.list(user_id=bob_id)
        bob_notif_id = bob_items[0]["id"]
        async with await self._alice_client(app, alice_token) as c:
            resp = await c.post(f"/api/notifications/{bob_notif_id}/archive")
            assert resp.status_code == 404
            assert len(await store.list_archived(user_id=bob_id)) == 0

    async def test_mark_own_notification_succeeds(self, two_user_app):
        app, alice_id, alice_token, bob_id, bob_token = two_user_app
        store = app.state.notifications
        await store.add("alice-notif", "for alice", user_id=alice_id)
        alice_items = await store.list(user_id=alice_id)
        alice_notif_id = alice_items[0]["id"]
        async with await self._alice_client(app, alice_token) as c:
            resp = await c.post(f"/api/notifications/{alice_notif_id}/read")
            assert resp.status_code == 200
            assert (await store.list(user_id=alice_id))[0]["read"] is True
