"""POST /api/notifications/os: the OS (freedesktop) notification ingest.

Everything goes through the real app and the real AuthMiddleware: a session
cookie per person, the local token, and the loopback/forwarded checks.
"""
import json
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks
from tinyagentos.app import create_app
from tinyagentos.notifications import NotificationStore

URL = "/api/notifications/os"


def _body(**kw) -> dict:
    b = {
        "id": "n-0001",
        "app_name": "Thunderbird",
        "desktop_entry": "thunderbird",
        "summary": "New mail",
        "body": "From Alex: lunch?",
        "urgency": "normal",
        "icon": "mail-unread",
        "actions": [{"key": "default", "label": "Open"}],
        "expire_timeout_ms": 5000,
    }
    b.update(kw)
    return b


@pytest_asyncio.fixture
async def world(tmp_path):
    (tmp_path / "config.yaml").write_text(yaml.dump({
        "server": {"host": "0.0.0.0", "port": 6969}, "backends": [],
        "qmd": {"url": "http://localhost:7832"}, "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    store = app.state.notifications
    if store._db is not None:
        await store.close()
    await store.init()
    auth = app.state.auth
    auth.setup_user("alice", "Alice", "", "alicepass123")
    alice = auth.find_user("alice")
    invite = auth.add_user_invite("bob", "alice")
    auth.complete_invite("bob", invite, "Bob", "", "bobpass123")
    bob = auth.find_user("bob")
    app.state._startup_complete = True
    return {
        "app": app,
        "alice": alice["id"], "alice_tok": auth.create_session(user_id=alice["id"], long_lived=True),
        "bob": bob["id"], "bob_tok": auth.create_session(user_id=bob["id"], long_lived=True),
        "local_token": auth.get_local_token(),
    }


def _client(app, token=None, *, host="127.0.0.1", bearer=None):
    kw = {}
    if token:
        kw["cookies"] = {"taos_session": token}
        kw["event_hooks"] = csrf_event_hooks()
    if bearer:
        kw["headers"] = {"Authorization": f"Bearer {bearer}"}
    return AsyncClient(transport=ASGITransport(app=app, client=(host, 4321)),
                       base_url="http://test", **kw)


async def _rows(c):
    return (await c.get("/api/notifications")).json()


@pytest.mark.asyncio
async def test_accepted_and_stored_once(world):
    async with _client(world["app"], world["alice_tok"]) as c:
        r = await c.post(URL, json=_body())
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["duplicate"] is False and isinstance(out["id"], int)
        rows = await _rows(c)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == out["id"]
    assert row["title"] == "New mail" and row["message"] == "From Alex: lunch?"
    assert row["source"] == "os:thunderbird"
    assert row["user_id"] == world["alice"]
    assert row["level"] == "info" and row["read"] is False
    assert row["data"]["os"]["client_id"] == "n-0001"
    assert row["data"]["os"]["icon"] == "mail-unread"
    assert row["data"]["os"]["actions"] == [{"key": "default", "label": "Open"}]


@pytest.mark.asyncio
async def test_retry_with_same_id_stores_once(world):
    async with _client(world["app"], world["alice_tok"]) as c:
        first = (await c.post(URL, json=_body())).json()
        second = await c.post(URL, json=_body())
        assert second.status_code == 200
        assert second.json() == {"id": first["id"], "duplicate": True}
        # even after the person dismissed it, a late retry must not resurrect it
        assert (await c.post(f"/api/notifications/{first['id']}/archive")).status_code == 200
        third = (await c.post(URL, json=_body())).json()
        assert third["id"] == first["id"] and third["duplicate"] is True
        assert await _rows(c) == []
        hist = (await c.get("/api/notifications/history", params={"state": "all"})).json()["items"]
    assert len([h for h in hist if h["source"].startswith("os:")]) == 1


@pytest.mark.asyncio
async def test_concurrent_retries_store_once(world):
    import asyncio
    async with _client(world["app"], world["alice_tok"]) as c:
        rs = await asyncio.gather(*[c.post(URL, json=_body(id="race")) for _ in range(8)])
        assert all(r.status_code == 200 for r in rs)
        assert len({r.json()["id"] for r in rs}) == 1
        assert len(await _rows(c)) == 1


@pytest.mark.asyncio
async def test_another_person_cannot_post_as_or_read_yours(world):
    app = world["app"]
    async with _client(app, world["alice_tok"]) as a, _client(app, world["bob_tok"]) as b:
        mine = (await a.post(URL, json=_body(summary="alice secret"))).json()
        # bob cannot name a recipient
        r = await b.post(URL, json=_body(id="x1", user_id=world["alice"]))
        assert r.status_code == 422
        r = await b.post(URL, json=_body(id="x2", summary="forged"))
        assert r.status_code == 200
        # bob reads none of alice's rows; alice none of bob's
        assert [x["title"] for x in await _rows(b)] == ["forged"]
        assert [x["title"] for x in await _rows(a)] == ["alice secret"]
        # bob cannot touch alice's row through the existing routes either
        assert (await b.post(f"/api/notifications/{mine['id']}/archive")).status_code == 404
        # the same client id is a different notification for a different person
        again = (await b.post(URL, json=_body(id="n-0001", summary="bob own"))).json()
        assert again["duplicate"] is False and again["id"] != mine["id"]
        assert len(await _rows(a)) == 1


@pytest.mark.asyncio
async def test_requires_a_person(world):
    async with _client(world["app"]) as anon:
        assert (await anon.post(URL, json=_body())).status_code == 401
    async with _client(world["app"], bearer="not-a-token") as bad:
        assert (await bad.post(URL, json=_body())).status_code == 401


@pytest.mark.asyncio
async def test_local_token_posts_as_the_primary_person(world):
    async with _client(world["app"], bearer=world["local_token"]) as c:
        r = await c.post(URL, json=_body(id="lt"))
        assert r.status_code == 200, r.text
        rows = await _rows(c)
    assert [x["user_id"] for x in rows] == [world["alice"]]


@pytest.mark.asyncio
async def test_remote_or_forwarded_callers_refused(world):
    async with _client(world["app"], world["alice_tok"], host="203.0.113.7") as c:
        assert (await c.post(URL, json=_body())).status_code == 403
    async with _client(world["app"], world["alice_tok"]) as c:
        r = await c.post(URL, json=_body(), headers={"X-Forwarded-For": "203.0.113.7"})
        assert r.status_code == 403
        assert await _rows(c) == []
        assert (await c.post(URL, json=_body())).status_code == 200  # control


@pytest.mark.asyncio
@pytest.mark.parametrize("patch,status", [
    ({"summary": "s" * 257}, 422),
    ({"body": "b" * 2049}, 422),
    ({"app_name": "a" * 129}, 422),
    ({"icon": "i" * 257}, 422),
    ({"id": ""}, 422),
    ({"id": "x" * 65}, 422),
    ({"id": "has space"}, 422),
    ({"urgency": "urgent"}, 422),
    ({"actions": [{"key": "k", "label": "l"}] * 9}, 422),
    ({"actions": [{"key": "k"}]}, 422),
    ({"expire_timeout_ms": -2}, 422),
    ({"surprise": 1}, 422),
    ({"summary": 5}, 422),
])
async def test_invalid_fields_refused_and_nothing_stored(world, patch, status):
    async with _client(world["app"], world["alice_tok"]) as c:
        r = await c.post(URL, json=_body(**patch))
        assert r.status_code == status, r.text
        assert await _rows(c) == []
        assert (await c.post(URL, json=_body())).status_code == 200  # control


@pytest.mark.asyncio
async def test_oversized_and_malformed_bodies_refused(world):
    async with _client(world["app"], world["alice_tok"]) as c:
        big = json.dumps(_body(body="b" * 20000))
        r = await c.post(URL, content=big, headers={"Content-Type": "application/json"})
        assert r.status_code == 413
        r = await c.post(URL, content=b"{not json", headers={"Content-Type": "application/json"})
        assert r.status_code == 400
        r = await c.post(URL, content=b"[1,2]", headers={"Content-Type": "application/json"})
        assert r.status_code == 422
        r = await c.post(URL, content=b"\xff\xfe", headers={"Content-Type": "application/json"})
        assert r.status_code == 400
        assert await _rows(c) == []


@pytest.mark.asyncio
async def test_rate_limit_per_person(world):
    app = world["app"]
    async with _client(app, world["alice_tok"]) as a, _client(app, world["bob_tok"]) as b:
        codes = [(await a.post(URL, json=_body(id=f"r{i}"))).status_code for i in range(125)]
        assert codes[:120] == [200] * 120
        assert set(codes[120:]) == {429}
        r = await a.post(URL, json=_body(id="late"))
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
        assert r.json()["error"] == "rate_limited"
        # one person's flood does not starve another
        assert (await b.post(URL, json=_body(id="b1"))).status_code == 200
        # a retry of an already stored id was never lost to the limiter earlier
        assert (await a.post(URL, json=_body(id="r0"))).status_code == 429


@pytest.mark.asyncio
async def test_urgency_level_and_late_timestamp(world):
    now = int(time.time())
    async with _client(world["app"], world["alice_tok"]) as c:
        await c.post(URL, json=_body(id="c1", urgency="critical", posted_at=now - 600))
        await c.post(URL, json=_body(id="c2", urgency="low", posted_at=now + 99999))
        await c.post(URL, json=_body(id="c3", posted_at=now - 10 * 86400 * 30))
        rows = {r["data"]["os"]["client_id"]: r for r in await _rows(c)}
    assert rows["c1"]["level"] == "warning" and rows["c1"]["timestamp"] == now - 600
    assert rows["c2"]["level"] == "info" and abs(rows["c2"]["timestamp"] - now) < 120
    assert abs(rows["c3"]["timestamp"] - (now - 30 * 86400)) < 120


@pytest.mark.asyncio
async def test_replaces_updates_the_row_in_place(world):
    async with _client(world["app"], world["alice_tok"]) as c:
        a = (await c.post(URL, json=_body(id="p1", summary="Downloading 10%"))).json()
        await c.post(f"/api/notifications/{a['id']}/read")
        b = (await c.post(URL, json=_body(id="p2", summary="Downloading 90%", replaces="p1"))).json()
        assert b["id"] == a["id"]
        rows = await _rows(c)
        assert len(rows) == 1 and rows[0]["title"] == "Downloading 90%" and rows[0]["read"] is False
        # retrying the replacing id is still a no-op
        assert (await c.post(URL, json=_body(id="p2", summary="Downloading 90%", replaces="p1"))).json()["duplicate"] is True
        # replacing an id that is not there makes a new row
        n = (await c.post(URL, json=_body(id="p3", replaces="nope"))).json()
        assert n["id"] != a["id"]


@pytest.mark.asyncio
async def test_transient_is_not_stored(world):
    async with _client(world["app"], world["alice_tok"]) as c:
        r = await c.post(URL, json=_body(id="t1", transient=True))
        assert r.status_code == 200 and r.json() == {"id": None, "duplicate": False, "stored": False}
        assert await _rows(c) == []


@pytest.mark.asyncio
async def test_row_appears_on_the_sse_stream(tmp_path):
    """The app's real start-up wires store -> event bus; drive the real SSE handler."""
    import asyncio
    from tinyagentos.routes.event_stream import events_stream
    (tmp_path / "config.yaml").write_text(yaml.dump({
        "server": {"host": "0.0.0.0", "port": 6969}, "backends": [], "agents": [],
        "qmd": {"url": "http://localhost:7832"},
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    async with app.router.lifespan_context(app):
        auth = app.state.auth
        auth.setup_user("alice", "Alice", "", "alicepass123")
        alice = auth.find_user("alice")
        invite = auth.add_user_invite("bob", "alice")
        auth.complete_invite("bob", invite, "Bob", "", "bobpass123")
        bob = auth.find_user("bob")
        tok = auth.create_session(user_id=alice["id"], long_lived=True)
        async with _client(app, tok) as c:
            r = await c.post(URL, json=_body(summary="on the wire"))
            assert r.status_code == 200, r.text
        req = MagicMock()
        req.app = app
        req.is_disconnected = AsyncMock(return_value=False)
        req.state.user_id = alice["id"]
        resp = await events_stream(req)
        chunk = await asyncio.wait_for(resp.body_iterator.__anext__(), 5)
        await resp.body_iterator.aclose()
        text = chunk if isinstance(chunk, str) else chunk.decode()
        payload = json.loads(text.split("data: ", 1)[1].split("\n", 1)[0])
        assert payload["type"] == "notification.added"
        assert payload["payload"]["title"] == "on the wire"
        assert payload["payload"]["source"] == "os:thunderbird"
        # another person's stream carries nothing of it
        req.state.user_id = bob["id"]
        resp = await events_stream(req)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(resp.body_iterator.__anext__(), 0.5)


@pytest.mark.asyncio
async def test_old_database_without_the_id_table_upgrades(tmp_path):
    db = tmp_path / "notifications.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL,"
        " level TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0,"
        " source TEXT, archived INTEGER NOT NULL DEFAULT 0, data TEXT, user_id TEXT)")
    con.execute("INSERT INTO notifications (timestamp, level, title, message, source, user_id)"
                " VALUES (1, 'info', 'old', 'row', 'system', 'u1')")
    con.commit(); con.close()
    store = NotificationStore(db)
    await store.init()
    try:
        rid, created = await store.add_os(user_id="u1", client_id="a", title="t", message="m",
                                          level="info", source="os:x", data={})
        assert created and rid is not None
        assert (await store.add_os(user_id="u1", client_id="a", title="t", message="m",
                                   level="info", source="os:x", data={})) == (rid, False)
        assert {r["title"] for r in await store.list(user_id="u1")} == {"old", "t"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_replace_of_a_removed_row_is_stored_not_lost(world):
    async with _client(world["app"], world["alice_tok"]) as c:
        a = (await c.post(URL, json=_body(id="p1", summary="Downloading 10%"))).json()
        store = world["app"].state.notifications
        # retention removed the row but its id record is still there
        await store._db.execute("DELETE FROM notifications WHERE id = ?", (a["id"],))
        await store._db.commit()
        b = (await c.post(URL, json=_body(id="p2", summary="Downloading 90%", replaces="p1"))).json()
        assert b["id"] is not None and b["duplicate"] is False
        rows = await _rows(c)
        assert [r["title"] for r in rows] == ["Downloading 90%"] and rows[0]["id"] == b["id"]
        # the chain keeps working: the next replace updates that new row in place
        n = (await c.post(URL, json=_body(id="p3", summary="Done", replaces="p1"))).json()
        assert n["id"] == b["id"]
        assert [r["title"] for r in await _rows(c)] == ["Done"]


@pytest.mark.asyncio
async def test_cleanup_prunes_id_records_with_their_rows(world):
    store = world["app"].state.notifications
    async with _client(world["app"], world["alice_tok"]) as c:
        await c.post(URL, json=_body(id="a1"))
        await c.post(URL, json=_body(id="a2"))
        await c.post(f"/api/notifications/1/archive")  # archived rows are kept by retention
    assert await store.cleanup(max_age_days=-1) == 1
    async with store._db.execute("SELECT client_id FROM os_notification_ids") as cur:
        left = [r[0] for r in await cur.fetchall()]
    assert left == ["a1"]


@pytest.mark.asyncio
async def test_replace_respects_a_muted_source(world):
    async with _client(world["app"], world["alice_tok"]) as c:
        a = (await c.post(URL, json=_body(id="m1", summary="before"))).json()
        await world["app"].state.notifications.set_event_muted(world["alice"], "os:thunderbird", True)
        r = (await c.post(URL, json=_body(id="m2", summary="after", replaces="m1"))).json()
        assert r == {"id": None, "duplicate": False, "stored": False, "reason": "muted"}
        assert [x["title"] for x in await _rows(c)] == ["before"] and a["id"]


@pytest.mark.asyncio
async def test_same_id_with_different_content_is_409(world):
    async with _client(world["app"], world["alice_tok"]) as c:
        first = (await c.post(URL, json=_body(id="z1", summary="one"))).json()
        r = await c.post(URL, json=_body(id="z1", summary="two"))
        assert r.status_code == 409 and r.json()["error"] == "id_reused_with_different_content"
        assert [x["title"] for x in await _rows(c)] == ["one"]
        # a true replay (even with a later posted_at) is still a duplicate
        again = await c.post(URL, json=_body(id="z1", summary="one", posted_at=int(time.time()) - 5))
        assert again.status_code == 200 and again.json() == {"id": first["id"], "duplicate": True}


@pytest.mark.asyncio
async def test_database_from_the_earlier_head_gains_the_digest_column(tmp_path):
    # An earlier head created os_notification_ids without `digest`; CREATE TABLE IF NOT EXISTS would
    # leave it that way, and the first POST would fail with "no such column".
    db = tmp_path / "notifications.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL,"
        " level TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0,"
        " source TEXT, archived INTEGER NOT NULL DEFAULT 0, data TEXT, user_id TEXT)")
    con.execute(
        "CREATE TABLE os_notification_ids (user_id TEXT NOT NULL, client_id TEXT NOT NULL,"
        " notification_id INTEGER NOT NULL, PRIMARY KEY (user_id, client_id))")
    con.execute("INSERT INTO notifications (timestamp, level, title, message, source, user_id)"
                " VALUES (1, 'info', 'old', 'row', 'os:x', 'u1')")
    con.execute("INSERT INTO os_notification_ids (user_id, client_id, notification_id) VALUES ('u1', 'old', 1)")
    con.commit(); con.close()
    store = NotificationStore(db)
    await store.init()
    try:
        rid, created = await store.add_os(user_id="u1", client_id="a", title="t", message="m",
                                          level="info", source="os:x", data={}, digest="d1")
        assert created and rid is not None
        # a retry of the same id with the same content is still a duplicate; different content is refused
        assert (await store.add_os(user_id="u1", client_id="a", title="t", message="m",
                                   level="info", source="os:x", data={}, digest="d1")) == (rid, False)
        # the pre-upgrade record keeps working (its digest is the empty default)
        assert (await store.add_os(user_id="u1", client_id="old", title="old", message="row",
                                   level="info", source="os:x", data={}))[1] is False
    finally:
        await store.close()
