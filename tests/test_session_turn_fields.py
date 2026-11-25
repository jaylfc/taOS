"""Every taOS memory writer carries session_id, turn_index, session_date, turn_owner.

Each test drives the real caller (the chat route, the WebSocket, route_message,
record_reply, the upload route) and reads what reached the archive or the user
memory store.  The counter is shared, atomic and survives a restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import arm_test_client

from tinyagentos.app import create_app

UNKNOWN = "unknown"
FIELDS = ("session_id", "turn_index", "session_date", "turn_owner")


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _host_client(app):
    token = app.state.auth.get_local_token()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _channel(app, name="g"):
    ch = await app.state.chat_channels.create_channel(
        name=name, type="text", created_by="user")
    return ch["id"]


async def _post(app, channel_id, content="hi", author="user-1", author_type="user"):
    async with _host_client(app) as c:
        r = await c.post("/api/chat/messages", json={
            "channel_id": channel_id, "author_id": author,
            "author_type": author_type, "content": content})
    assert r.status_code == 200, r.text
    return r.json()


def _admin_id(app):
    return app.state.auth.find_user("admin")["id"]


def _archived(app):
    return [(c.args[1] if len(c.args) > 1 else c.kwargs["data"])
            for c in app.state.archive.record.call_args_list]


@pytest.fixture
def archive_app(client):
    app = client._transport.app
    app.state.archive = AsyncMock()
    app.state.archive.record = AsyncMock(return_value=1)
    return app


# ---------------------------------------------------------------- chat HTTP

@pytest.mark.asyncio
async def test_http_archive_carries_session_fields(archive_app):
    app = archive_app
    ch = await _channel(app)
    msg = await _post(app, ch, "hello", author="alice")
    (data,) = _archived(app)
    assert data["session_id"] == ch
    assert data["turn_index"] == 0
    # bound to the authenticated principal (the local token's owner), not the body
    assert data["turn_owner"] == _admin_id(app)
    assert data["turn_owner"] != "alice"
    assert data["session_date"] == _day(msg["created_at"])


@pytest.mark.asyncio
async def test_http_turn_index_increments_per_session(archive_app):
    app = archive_app
    a, b = await _channel(app, "a"), await _channel(app, "b")
    await _post(app, a)
    await _post(app, a)
    await _post(app, b)
    await _post(app, a)
    got = [(d["session_id"], d["turn_index"]) for d in _archived(app)]
    assert got == [(a, 0), (a, 1), (b, 0), (a, 2)]


@pytest.mark.asyncio
async def test_concurrent_posts_never_share_an_index(archive_app):
    app = archive_app
    ch = await _channel(app)
    await asyncio.gather(*[_post(app, ch, f"m{i}") for i in range(25)])
    idx = sorted(d["turn_index"] for d in _archived(app))
    assert idx == list(range(25))


@pytest.mark.asyncio
async def test_user_memory_capture_shares_the_archive_turn(archive_app):
    app = archive_app
    um = MagicMock()
    um.get_settings = AsyncMock(return_value={"capture_conversations": True})
    um.save_chunk = AsyncMock(return_value="h")
    app.state.user_memory = um
    ch = await _channel(app)
    await _post(app, ch, "one")
    await _post(app, ch, "two")
    for _ in range(200):
        if um.save_chunk.call_count == 2:
            break
        await asyncio.sleep(0.01)
    metas = [c.kwargs["metadata"] for c in um.save_chunk.call_args_list]
    assert sorted(m["turn_index"] for m in metas) == [0, 1]
    arch = {d["turn_index"] for d in _archived(app)}
    assert arch == {0, 1}
    for m in metas:
        assert m["session_id"] == ch and m["turn_owner"] == _admin_id(app)
        assert len(m["session_date"]) == 10


@pytest.mark.asyncio
async def test_turn_index_survives_restart(archive_app):
    app = archive_app
    ch = await _channel(app)
    await _post(app, ch)
    await _post(app, ch)
    store = app.state.session_turns
    await store.close()
    await store.init()  # restart: new connection on the same file
    await _post(app, ch)
    assert [d["turn_index"] for d in _archived(app)] == [0, 1, 2]


@pytest.mark.asyncio
async def test_session_with_history_before_the_counter_continues_from_it(archive_app):
    """Old data dir: three messages exist, no counter row."""
    app = archive_app
    ch = await _channel(app)
    for i in range(3):
        await app.state.chat_messages.send_message(
            channel_id=ch, author_id="u", author_type="user", content=f"old{i}")
    await _post(app, ch, "new")
    (data,) = _archived(app)
    assert data["turn_index"] == 3


@pytest.mark.asyncio
async def test_unwired_counter_writes_unknown_not_zero(archive_app, caplog):
    app = archive_app
    ch = await _channel(app)
    store = app.state.session_turns
    await store.close()  # allocation will fail
    with caplog.at_level(logging.WARNING):
        await _post(app, ch)
    (data,) = _archived(app)
    assert data["turn_index"] == UNKNOWN
    assert data["turn_index"] != 0
    assert any("turn_index" in r.getMessage() for r in caplog.records)
    await store.init()


# -------------------------------------------------------------- WebSocket

def _ws_app(tmp_path):
    (tmp_path / "config.yaml").write_text(yaml.dump({
        "server": {"host": "0.0.0.0", "port": 6969}, "backends": [],
        "qmd": {"url": "http://localhost:7832"}, "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30}}))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    return app


def test_ws_and_http_share_one_counter_at_the_same_point(tmp_path):
    app = _ws_app(tmp_path)
    rec = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=rec["id"], long_lived=True)
    with TestClient(app, raise_server_exceptions=False) as tc:
        tc.cookies.set("taos_session", token)
        arm_test_client(tc)
        loop = asyncio.new_event_loop()
        try:
            ch = loop.run_until_complete(_channel(app))
        finally:
            loop.close()
        saved = []

        class _UM:
            async def get_settings(self, uid):
                return {"capture_conversations": True}

            async def save_chunk(self, uid, content, title="", collection="", metadata=None):
                saved.append(metadata)
                return "h"

        app.state.user_memory = _UM()
        with tc.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "join", "channel_id": ch}))
            for text in ("a", "b"):
                ws.send_text(json.dumps(
                    {"type": "message", "channel_id": ch, "content": text}))
                frame = json.loads(ws.receive_text())
                assert frame["type"] == "message"
        import time
        for _ in range(50):
            if len(saved) == 2:
                break
            time.sleep(0.02)
    assert sorted(m["turn_index"] for m in saved) == [0, 1]
    for m in saved:
        assert m["session_id"] == ch
        assert m["turn_owner"] == rec["id"]
        assert len(m["session_date"]) == 10 and m["session_date"] != UNKNOWN


def test_lifespan_wires_the_one_counter_into_every_writer(tmp_path):
    app = _ws_app(tmp_path)
    with TestClient(app, raise_server_exceptions=False):
        store = app.state.session_turns
        assert store is not None and store._db is not None
        assert app.state.bridge_sessions._session_turns is store
        assert app.state.channel_hub_router._session_turns is store


# ------------------------------------------------------------ channel hub

@pytest.mark.asyncio
async def test_channel_hub_inbound_and_outbound_carry_fields(tmp_path, monkeypatch):
    import tinyagentos.channel_hub.router as rmod
    from tinyagentos.channel_hub.message import IncomingMessage
    from tinyagentos.channel_hub.router import MessageRouter
    from tinyagentos.session_turns import SessionTurnStore

    store = SessionTurnStore(tmp_path / "t.db")
    await store.init()
    archive = AsyncMock()
    r = MessageRouter()
    r.register_adapter("atlas", 9100)
    r.set_archive(archive)
    r.set_session_turns(store)

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"content": "pong"}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _Resp()

    monkeypatch.setattr(rmod.httpx, "AsyncClient", _Client)
    inc = IncomingMessage(id="m1", from_id="u9", from_name="U", platform="telegram",
                          channel_id="c1", channel_name="c", text="ping",
                          timestamp=1_700_000_000.0)
    await r.route_message("atlas", inc)
    await r.route_message("atlas", inc)
    datas = [c.args[1] for c in archive.record.call_args_list]
    assert [d["direction"] for d in datas] == ["inbound", "outbound", "inbound", "outbound"]
    assert [d["turn_index"] for d in datas] == [0, 1, 2, 3]
    assert all(d["session_id"] == "telegram:c1" for d in datas)
    assert datas[0]["turn_owner"] == "u9" and datas[1]["turn_owner"] == "atlas"
    assert datas[0]["session_date"] == _day(1_700_000_000.0)
    assert datas[1]["session_date"] == _day(__import__("time").time())  # outbound: its own time
    await store.close()


# ----------------------------------------------------------- bridge session

async def _bridge(tmp_path, with_counter=True):
    from tinyagentos.bridge_session import BridgeSessionRegistry
    from tinyagentos.session_turns import SessionTurnStore

    class _TS:
        async def record(self, kind, **f): return {"kind": kind, **f}

    class _TR:
        async def get(self, slug): return _TS()

    store = SessionTurnStore(tmp_path / "b.db")
    await store.init()
    archive = AsyncMock()
    reg = BridgeSessionRegistry(trace_registry=_TR(), archive=archive,
                                session_turns=store if with_counter else None)
    return reg, archive, store


@pytest.mark.asyncio
async def test_bridge_every_event_kind_carries_fields(tmp_path):
    reg, archive, store = await _bridge(tmp_path)
    ch = "chan-1"
    # a user turn already happened in this session (turn 0)
    await store.next_turn(ch)
    await reg.record_reply("atlas", {"kind": "tool_call", "trace_id": "t", "tool": "x", "channel_id": ch})
    await reg.record_reply("atlas", {"kind": "tool_result", "trace_id": "t", "tool": "x", "channel_id": ch})
    await reg.record_reply("atlas", {"kind": "reasoning", "trace_id": "t", "content": "hm", "channel_id": ch})
    await reg.record_reply("atlas", {"kind": "error", "trace_id": "t", "error": "bad", "channel_id": ch})
    await reg.record_reply("atlas", {"kind": "final", "trace_id": "t", "content": "done",
                                     "id": "r1", "channel_id": ch})
    by_kind = {c.kwargs["event_type"]: c.kwargs["data"] for c in archive.record.call_args_list}
    assert set(by_kind) == {"tool_call", "tool_result", "reasoning", "error", "final"}
    for kind in ("tool_call", "tool_result", "reasoning", "error"):
        assert by_kind[kind]["turn_index"] == 0, kind  # events belong to the turn in progress
    assert by_kind["final"]["turn_index"] == 1  # the reply is its own turn
    for kind, d in by_kind.items():
        assert d["session_id"] == ch and d["turn_owner"] == "atlas", kind
        assert d["session_date"] == _day(__import__("time").time()), kind
    await store.close()


@pytest.mark.asyncio
async def test_bridge_final_duplicate_does_not_consume_a_turn(tmp_path):
    reg, archive, store = await _bridge(tmp_path)
    body = {"kind": "final", "trace_id": "t", "content": "x", "id": "r1", "channel_id": "c"}
    await reg.record_reply("atlas", body)
    await reg.record_reply("atlas", body)
    assert archive.record.call_count == 1
    assert await store.current_turn("c") == 0
    await store.close()


@pytest.mark.asyncio
async def test_bridge_unknown_channel_writes_unknown_never_zero(tmp_path, caplog):
    reg, archive, store = await _bridge(tmp_path)
    with caplog.at_level(logging.WARNING):
        await reg.record_reply("atlas", {"kind": "final", "trace_id": "t", "content": "x", "id": "r1"})
    d = archive.record.call_args.kwargs["data"]
    assert d["session_id"] == UNKNOWN and d["turn_index"] == UNKNOWN
    assert d["turn_owner"] == "atlas"
    assert caplog.records
    await store.close()


@pytest.mark.asyncio
async def test_bridge_without_counter_writes_unknown(tmp_path):
    reg, archive, store = await _bridge(tmp_path, with_counter=False)
    await reg.record_reply("atlas", {"kind": "final", "trace_id": "t", "content": "x",
                                     "id": "r1", "channel_id": "c"})
    d = archive.record.call_args.kwargs["data"]
    assert d["session_id"] == "c" and d["turn_index"] == UNKNOWN
    await store.close()


# ------------------------------------------------------ workspace activity

@pytest.mark.asyncio
async def test_workspace_upload_capture_carries_session_fields(client):
    app = client._transport.app
    saved = []

    class _UM:
        async def get_settings(self, uid): return {"capture_files": True}

        async def save_chunk(self, uid, content, title="", collection="", metadata=None):
            saved.append(metadata)
            return "h"

    app.state.user_memory = _UM()
    uid = getattr(app.state.auth.find_user("admin"), "get", lambda k: None)("id")
    for name in ("a.txt", "b.txt"):
        r = await client.post("/api/workspace/files/upload",
                              files={"file": (name, b"data")})
        assert r.status_code == 200, r.text
    for _ in range(200):
        if len(saved) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(saved) == 2
    assert sorted(m["turn_index"] for m in saved) == [0, 1]
    for m in saved:
        assert m["session_id"] == f"files:{uid}"
        assert m["turn_owner"] == uid
        assert len(m["session_date"]) == 10


# ----------------------------------------------------- counter module itself

@pytest.mark.asyncio
async def test_counter_atomic_and_persisted(tmp_path):
    from tinyagentos.session_turns import SessionTurnStore
    s = SessionTurnStore(tmp_path / "c.db")
    await s.init()
    got = await asyncio.gather(*[s.next_turn("s") for _ in range(50)])
    assert sorted(got) == list(range(50))
    await s.close()
    s2 = SessionTurnStore(tmp_path / "c.db")
    await s2.init()
    assert await s2.next_turn("s") == 50
    assert await s2.current_turn("never") is None
    await s2.close()


# ------------------------------------------------- owner binding, partitions

@pytest.mark.asyncio
async def test_body_author_cannot_spoof_turn_owner(archive_app):
    app = archive_app
    ch = await _channel(app)
    await _post(app, ch, "x", author="mallory")
    await _post(app, ch, "y", author="victim-agent", author_type="agent")
    owners = {d["turn_owner"] for d in _archived(app)}
    assert owners == {_admin_id(app)}


def _add_user(app, username):
    users = app.state.auth._read_users()
    rec = {"id": f"id-{username}", "username": username, "full_name": username,
           "email": "", "password_hash": "x", "created_at": 0, "is_admin": False,
           "capabilities": []}
    users["users"].append(rec)
    app.state.auth._write_users(users)
    return rec["id"]


@pytest.mark.asyncio
async def test_two_people_do_not_pool_http_chat_captures(archive_app):
    from taos_test_csrf import csrf_event_hooks
    app = archive_app
    um = MagicMock()
    um.get_settings = AsyncMock(return_value={"capture_conversations": True})
    um.save_chunk = AsyncMock(return_value="h")
    app.state.user_memory = um
    admin = _admin_id(app)
    bob = _add_user(app, "bob")
    ch = await app.state.chat_channels.create_channel(
        name="duo", type="group", created_by="user", members=[admin, bob])
    ch = ch["id"]
    for uid in (admin, bob):
        token = app.state.auth.create_session(user_id=uid, long_lived=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               cookies={"taos_session": token},
                               event_hooks=csrf_event_hooks()) as c:
            r = await c.post("/api/chat/messages", json={
                "channel_id": ch, "author_id": uid, "author_type": "user",
                "content": f"from {uid}"})
            assert r.status_code == 200, r.text
    for _ in range(200):
        if um.save_chunk.call_count == 2:
            break
        await asyncio.sleep(0.01)
    calls = {c.args[0]: c.kwargs["metadata"] for c in um.save_chunk.call_args_list}
    assert set(calls) == {admin, bob}
    for uid, meta in calls.items():
        assert meta["turn_owner"] == uid and meta["session_id"] == ch


@pytest.mark.asyncio
async def test_search_capture_carries_session_fields_under_the_user(archive_app, client):
    app = archive_app
    saved = []

    class _UM:
        async def get_settings(self, uid):
            return {"capture_searches": True}

        async def save_chunk(self, uid, content, title="", collection="", metadata=None):
            saved.append((uid, metadata))
            return "h"

    app.state.user_memory = _UM()
    for q in ("alpha", "beta"):
        await client.get("/api/search", params={"q": q})
    for _ in range(40):
        await asyncio.sleep(0.01)
        if len(saved) == 2:
            break
    admin = _admin_id(app)
    assert len(saved) == 2
    assert {u for u, _ in saved} == {admin}
    assert sorted(m["turn_index"] for _, m in saved) == [0, 1]
    for _, m in saved:
        assert m["session_id"] == f"search:{admin}" and m["turn_owner"] == admin


@pytest.mark.asyncio
async def test_manual_save_session_fields(archive_app, client):
    app = archive_app
    saved = []

    class _UM:
        async def save_chunk(self, uid, content, title, collection, metadata):
            saved.append(metadata)
            return "h"

    app.state.user_memory = _UM()
    ch = await _channel(app)
    await _post(app, ch)  # turn 0 exists in the session
    spoof = {"turn_owner": "mallory", "turn_index": 99}
    await client.post("/api/user-memory/save", json={"content": "c", "metadata": spoof})
    await client.post("/api/user-memory/save", json={"content": "c2", "session_id": ch})
    none_, named = saved
    assert none_["session_id"] == UNKNOWN and none_["turn_index"] == UNKNOWN
    assert none_["turn_owner"] == _admin_id(app)  # not the metadata spoof
    assert named["session_id"] == ch and named["turn_index"] == 0


# ------------------------------------------- per-user partitions, legacy rows

@pytest.mark.asyncio
async def test_owner_browses_legacy_and_new_member_sees_only_own(client):
    from taos_test_csrf import csrf_event_hooks
    app = client._transport.app
    store = app.state.user_memory
    if store._db is None:
        await store.init()
    admin = _admin_id(app)
    bob = _add_user(app, "bob")
    # data written before this change: the shared "user" partition
    await store.save_chunk("user", "legacy note", "legacy", "conversations", {})
    await store.save_chunk(bob, "bob note", "bobs", "conversations", {})

    r = await client.post("/api/user-memory/save", json={"content": "new owner note", "title": "new"})
    assert r.status_code == 200, r.text
    r = await client.post("/api/workspace/files/upload", files={"file": ("f.txt", b"d")})
    assert r.status_code == 200
    for _ in range(200):
        if (await store.get_stats(admin))["collections"].get("files"):
            break
        await asyncio.sleep(0.01)

    owner_titles = {c["title"] for c in (await client.get("/api/user-memory/browse")).json()["chunks"]}
    assert {"legacy", "new", "f.txt"} <= owner_titles
    assert "bobs" not in owner_titles

    # the legacy rows were not copied or rewritten
    cur = await store._db.execute("SELECT COUNT(*) FROM user_memory_chunks WHERE user_id='user'")
    assert (await cur.fetchone())[0] == 1

    token = app.state.auth.create_session(user_id=bob, long_lived=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           cookies={"taos_session": token},
                           event_hooks=csrf_event_hooks()) as c:
        member = (await c.get("/api/user-memory/browse")).json()["chunks"]
        stats = (await c.get("/api/user-memory/stats")).json()
    assert {x["title"] for x in member} == {"bobs"}
    assert stats["total"] == 1
    await store.close()
