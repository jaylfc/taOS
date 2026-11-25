"""Every conversation writer also ingests its turn through taOSmd's ingest.

taOSmd's answer_context rebuilds a turn's session window from the vector rows
``ingest`` writes.  These tests drive the real writers (the chat route, the
WebSocket, route_message, record_reply) and check what taOSmd's real store
(in process, forced onto the CPU-only ``lite-pi`` recipe) can then recall.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import arm_test_client

from tinyagentos.app import create_app

taosmd_api = pytest.importorskip("taosmd.api")
if not hasattr(taosmd_api, "answer_context") and not hasattr(
        __import__("taosmd"), "answer_context"):  # pragma: no cover
    pytest.skip("taosmd without answer_context", allow_module_level=True)


class _Resp:
    def __init__(self, status=200):
        self.status_code = status


class InProcessTaosmd:
    """http_client stand-in: POST /ingest runs taOSmd's real ingest (the same
    body handling as its HTTP server) against a real store in a temp dir."""

    def __init__(self, data_dir, fail=0):
        self.data_dir = data_dir
        self.fail = fail
        self.calls = []

    async def post(self, url, json=None, timeout=None):
        import taosmd.api as tm
        self.calls.append(json)
        if self.fail:
            self.fail -= 1
            return _Resp(503)
        fields = {k: json[k] for k in ("role", "session_id", "turn_index", "session_date")
                  if json.get(k) is not None}
        transcript = {"role": "unknown", "content": json["text"], **fields} if fields else json["text"]
        await tm.ingest(transcript, agent=json["agent"], data_dir=self.data_dir)
        return _Resp(200)


@pytest.fixture
def taosmd(tmp_path, monkeypatch):
    import taosmd.recipes as recipes
    from taosmd.vector_memory import VectorMemory


    async def _embed(self, text, task=None):  # no embedding model on CI: bag of words
        vec = [0.0] * 64
        for w in "".join(c.lower() if c.isalnum() else " " for c in text).split():
            vec[hash(w) % 64] += 1.0
        return vec

    monkeypatch.setattr(VectorMemory, "embed", _embed)
    dd = tmp_path / "taosmd"
    dd.mkdir()
    return dd, InProcessTaosmd(dd), recipes


def _lite_pi(taosmd_, *agents):
    from taosmd.agents import ensure_agent
    dd, _, recipes = taosmd_
    for a in agents:
        ensure_agent(a, data_dir=dd)
        r = recipes.apply_recipe(a, "lite-pi", data_dir=dd)
        assert r.id == "lite-pi"
        assert recipes.resolve_recipe(a, data_dir=dd).id == "lite-pi"


def _agents(app, *names):
    for n in names:
        app.state.config.agents.append({"name": n, "host": "127.0.0.1"})


async def _dm(app, agent, user_id):
    ch = await app.state.chat_channels.create_channel(
        name=f"dm-{agent}", type="dm", created_by="user", members=[user_id, agent])
    return ch["id"]


def _admin(app):
    return app.state.auth.find_user("admin")["id"]


def _host(app):
    tok = app.state.auth.get_local_token()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {tok}"})


async def _say(app, ch, text, author, author_type):
    async with _host(app) as c:
        r = await c.post("/api/chat/messages", json={
            "channel_id": ch, "author_id": author, "author_type": author_type, "content": text})
    assert r.status_code == 200, r.text
    return r.json()


async def _settle(app):
    for _ in range(300):
        pending = [t for t in __import__("tinyagentos.routes.chat", fromlist=["x"])._background_tasks]
        if not pending:
            return
        await asyncio.sleep(0.01)


def _worker(app, sender_client, **kw):
    from tinyagentos.turn_ingest import TurnIngestWorker, http_sender
    return TurnIngestWorker(
        app.state.turn_ingest, http_sender(lambda: sender_client, lambda: "http://taosmd"),
        rate_per_sec=0, **kw)


@pytest.mark.asyncio
async def test_lite_pi_recall_finds_turn_with_neighbours_and_isolates_agents(client, taosmd):
    import taosmd.api as tm  # noqa: F401
    from taosmd import service
    dd, shim, _ = taosmd
    app = client._transport.app
    _agents(app, "agent-a", "agent-b")
    _lite_pi(taosmd, "agent-a", "agent-b")
    me = _admin(app)
    ch_a, ch_b = await _dm(app, "agent-a", me), await _dm(app, "agent-b", me)
    script_a = [
        ("user", me, "I am planning a trip to Lisbon in May"),
        ("agent", "agent-a", "Lisbon in May is lovely, book the tram pass early"),
        ("user", me, "My quokka named Biscuit needs a sitter while I travel"),
        ("agent", "agent-a", "I can remind you to find a quokka sitter for Biscuit"),
        ("user", me, "Also remind me to renew my passport"),
    ]
    for t, who, text in script_a:
        await _say(app, ch_a, text, who, t)
    await _say(app, ch_b, "Secret zebra recipe is saffron rice", me, "user")
    await _settle(app)

    assert (await app.state.turn_ingest.counts())["pending"] > 0  # chat path only queued
    await _worker(app, shim).drain_once()
    assert (await app.state.turn_ingest.counts())["pending"] == 0

    ctx = await service.answer_context("quokka sitter Biscuit", agent="agent-a",
                                  budget_tokens=2000, data_dir=dd)
    text = ctx["context"]
    assert "quokka named Biscuit" in text
    # the session window: neighbours either side, in order, with roles
    assert "Lisbon in May is lovely" in text and "renew my passport" in text
    assert text.index("Lisbon in May is lovely") < text.index("quokka named Biscuit") \
        < text.index("renew my passport")
    assert "user:" in text and "assistant:" in text
    assert ctx["included"][0]["session_id"] == ch_a
    assert [t["turn_index"] for t in ctx["included"][0]["turns"]] == [0, 1, 2, 3, 4]

    # agent B never sees agent A's turns, and A never sees B's
    ctx_b = await service.answer_context("quokka sitter Biscuit Lisbon", agent="agent-b",
                                    budget_tokens=2000, data_dir=dd)
    assert "quokka" not in ctx_b["context"] and "Lisbon" not in ctx_b["context"]
    ctx_a2 = await service.answer_context("zebra saffron", agent="agent-a",
                                     budget_tokens=2000, data_dir=dd)
    assert "zebra" not in ctx_a2["context"]


@pytest.mark.asyncio
async def test_person_shelf_gets_their_own_turns_only(client, taosmd):
    import taosmd.api as tm

    from tinyagentos.user_memory import user_namespace
    dd, shim, _ = taosmd
    app = client._transport.app
    store = app.state.user_memory
    if store._db is None:
        await store.init()
    _agents(app, "agent-a")
    me = _admin(app)
    ch = await _dm(app, "agent-a", me)
    await _say(app, ch, "my locker code is amber", me, "user")
    await _say(app, ch, "noted, amber", "agent-a", "agent")
    await _settle(app)
    await _worker(app, shim).drain_once()
    mine = await tm.search("amber", agent=user_namespace(me), mode="bm25", data_dir=dd)
    assert [h["text"] for h in mine] == ["my locker code is amber"]  # not the agent's reply
    await store.close()


@pytest.mark.asyncio
async def test_failed_send_is_retried_kept_and_logged_never_doubled(client, taosmd, caplog):
    dd, shim, _ = taosmd
    shim.fail = 2
    app = client._transport.app
    _agents(app, "agent-a")
    ch = await _dm(app, "agent-a", _admin(app))
    await _say(app, ch, "one single turn about walrus", _admin(app), "user")
    await _settle(app)
    q = app.state.turn_ingest
    w = _worker(app, shim)
    with caplog.at_level(logging.WARNING):
        assert await w.drain_once() == 0  # 503: kept, attempt recorded
    assert any("failed" in r.getMessage() for r in caplog.records)
    row = await (await q._db.execute("SELECT state, attempts, last_error, next_attempt_at FROM turn_ingest")).fetchone()
    assert row[0] == "pending" and row[1] == 1 and "503" in row[2]
    await q._db.execute("UPDATE turn_ingest SET next_attempt_at = 0")  # backoff elapsed
    await q._db.commit()
    assert await w.drain_once() == 0  # second 503
    await q._db.execute("UPDATE turn_ingest SET next_attempt_at = 0")
    await q._db.commit()
    assert await w.drain_once() == 1  # delivered
    assert await w.drain_once() == 0  # never sent again
    ok = [c for c in shim.calls if c["agent"] == "agent-a"]
    # every attempt, including the retries, carries the same source_id: the
    # turn's queue key (<turn id>|<shelf>)
    assert {c["source_id"] for c in ok} == {
        (await (await q._db.execute("SELECT key FROM turn_ingest WHERE shelf = 'agent-a'")).fetchone())[0]}
    assert ok[0]["source_id"].startswith("chat:") and ok[0]["source_id"].endswith("|agent-a")
    assert len(ok) == 3 and (await q.counts())["done"] >= 1
    import taosmd.api as tm
    hits = await tm.search("walrus", agent="agent-a", mode="bm25", data_dir=dd)
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_exhausted_turn_is_marked_failed_loudly_and_can_be_requeued(client, taosmd, caplog):
    dd, shim, _ = taosmd
    shim.fail = 10_000
    app = client._transport.app
    _agents(app, "agent-a")
    ch = await _dm(app, "agent-a", _admin(app))
    await _say(app, ch, "hello there", _admin(app), "user")
    await _settle(app)
    q, w = app.state.turn_ingest, _worker(app, shim)
    with caplog.at_level(logging.ERROR):
        for _ in range(12):
            await q._db.execute("UPDATE turn_ingest SET next_attempt_at = 0")
            await q._db.commit()
            await w.drain_once()
    counts = await q.counts()
    assert counts["failed"] >= 1 and counts["pending"] == 0
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    shim.fail = 0
    assert await q.requeue_failed() >= 1
    assert await w.drain_once() >= 1


@pytest.mark.asyncio
async def test_chat_post_does_not_wait_for_taosmd(client, taosmd):
    dd, shim, _ = taosmd
    app = client._transport.app
    _agents(app, "agent-a")

    class _Hang:
        async def post(self, *a, **k):
            await asyncio.sleep(3600)

        async def aclose(self):
            pass

    app.state.http_client = _Hang()
    ch = await _dm(app, "agent-a", _admin(app))
    t0 = time.monotonic()
    await _say(app, ch, "does not block", _admin(app), "user")
    assert time.monotonic() - t0 < 5
    await _settle(app)
    assert (await app.state.turn_ingest.counts())["pending"] >= 1


@pytest.mark.asyncio
async def test_backfill_is_idempotent_resumable_and_matches_live_keys(client, taosmd, tmp_path):
    import taosmd.api as tm

    from tinyagentos.turn_backfill import backfill
    dd, shim, _ = taosmd
    app = client._transport.app
    _agents(app, "agent-a")
    me = _admin(app)
    ch = await _dm(app, "agent-a", me)
    for i in range(5):  # written straight to the store: the pre-upgrade history
        await app.state.chat_messages.send_message(
            channel_id=ch, author_id=me if i % 2 == 0 else "agent-a",
            author_type="user" if i % 2 == 0 else "agent", content=f"old turn {i} about okapi")
    data_dir = app.state.config_path.parent
    q = app.state.turn_ingest
    # the live path queues one new turn first
    live = await _say(app, ch, "a live turn about okapi", me, "user")
    await _settle(app)
    first = await backfill(data_dir, app.state.config, q, rate=0, limit=3)
    assert first["queued"] == 3 and first["stopped"] == "limit"
    rest = await backfill(data_dir, app.state.config, q, rate=0)  # resumes from the cursor
    again = await backfill(data_dir, app.state.config, q, rate=0)
    assert again["queued"] == 0
    await _worker(app, shim).drain_once()
    while (await q.counts())["pending"]:
        await _worker(app, shim).drain_once()
    hits = await tm.search("okapi", agent="agent-a", mode="bm25", limit=20, data_dir=dd)
    assert len(hits) == 6  # five old + the live one, each exactly once
    keys = [r[0] for r in await (await q._db.execute("SELECT key FROM turn_ingest")).fetchall()]
    assert len(keys) == len(set(keys)) and f"chat:{live['id']}|agent-a" in keys
    assert rest["stopped"] == "complete"


# ------------------------------------------------------- other writers

def _ws_app(tmp_path):
    (tmp_path / "config.yaml").write_text(yaml.dump({
        "server": {"host": "127.0.0.1", "port": 6969}, "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [{"name": "agent-a", "host": "127.0.0.1"}],
        "metrics": {"poll_interval": 30, "retention_days": 30}}))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    return app


def _queued(tmp_path):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "turn_ingest.db")
    try:
        return {k: (shelf, json.loads(p)) for k, shelf, p in conn.execute(
            "SELECT key, shelf, payload FROM turn_ingest")}
    finally:
        conn.close()


def test_websocket_turn_is_queued_with_session_fields(tmp_path):
    from tinyagentos.user_memory import user_namespace
    app = _ws_app(tmp_path)
    rec = app.state.auth.find_user("admin")
    uid = rec["id"]
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    asyncio.run(app.state.chat_channels.init())
    asyncio.run(app.state.chat_messages.init())
    ch = asyncio.run(app.state.chat_channels.create_channel(
        name="d", type="dm", created_by="user", members=[uid, "agent-a"]))["id"]
    with TestClient(app, raise_server_exceptions=False) as tc:
        tc.cookies.set("taos_session", token)
        arm_test_client(tc)
        app.state.taosmd_url = "http://127.0.0.1:9"  # nothing listens: rows stay queued
        with tc.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "join", "channel_id": ch}))
            for text in ("first words", "second words"):
                ws.send_text(json.dumps({"type": "message", "channel_id": ch, "content": text}))
                while True:  # other frames (an agent placeholder) may arrive first
                    frame = json.loads(ws.receive_text())
                    if frame.get("type") == "message" and frame.get("content") == text:
                        break
        for _ in range(100):
            queued = _queued(tmp_path)
            if len(queued) >= 4:
                break
            time.sleep(0.05)
    queued = _queued(tmp_path)
    by_shelf = {}
    for shelf, turn in queued.values():
        by_shelf.setdefault(shelf, []).append(turn)
    assert set(by_shelf) == {"agent-a", user_namespace(uid)}
    for turns in by_shelf.values():
        turns.sort(key=lambda t: t["turn_index"])
        assert [t["content"] for t in turns] == ["first words", "second words"]
        assert [t["turn_index"] for t in turns] == [0, 1]
        assert all(t["role"] == "user" and t["session_id"] == ch and len(t["session_date"]) == 10
                   for t in turns)


@pytest.mark.asyncio
async def test_channel_hub_turns_are_queued_for_the_agents_shelf(tmp_path, monkeypatch):
    import tinyagentos.channel_hub.router as rmod
    from tinyagentos.channel_hub.message import IncomingMessage
    from tinyagentos.channel_hub.router import MessageRouter
    from tinyagentos.session_turns import SessionTurnStore
    from tinyagentos.turn_ingest import TurnIngestQueue

    st = SessionTurnStore(tmp_path / "t.db")
    await st.init()
    q = TurnIngestQueue(tmp_path / "q.db")
    await q.init()
    r = MessageRouter()
    r.register_adapter("atlas", 9100)
    r.set_session_turns(st)
    r.set_turn_ingest(q)  # no archive wired: the turn still reaches taOSmd

    class _R:
        def raise_for_status(self): ...
        def json(self): return {"content": "pong"}

    class _C:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _R()

    monkeypatch.setattr(rmod.httpx, "AsyncClient", _C)
    inc = IncomingMessage(id="m1", from_id="u9", from_name="U", platform="telegram",
                          channel_id="c1", channel_name="c", text="ping", timestamp=1_700_000_000.0)
    await r.route_message("atlas", inc)
    await r.route_message("atlas", inc)  # a replay of the same inbound id
    rows = {k: json.loads(p) for k, p in await (await q._db.execute(
        "SELECT key, payload FROM turn_ingest")).fetchall()}
    inbound = [t for t in rows.values() if t["role"] == "user"]
    outbound = [t for t in rows.values() if t["role"] == "assistant"]
    assert len(inbound) == 1  # same message id: queued once
    assert len(outbound) == 2 and [t["turn_index"] for t in outbound] == [1, 3]
    assert inbound[0]["session_id"] == "telegram:c1" and inbound[0]["session_date"] == "2023-11-14"
    await st.close()
    await q.close()


@pytest.mark.asyncio
async def test_bridge_final_reply_is_queued_for_the_agents_shelf(tmp_path):
    from tinyagentos.bridge_session import BridgeSessionRegistry
    from tinyagentos.session_turns import SessionTurnStore
    from tinyagentos.turn_ingest import TurnIngestQueue

    class _TS:
        async def record(self, kind, **f): return {}

    class _TR:
        async def get(self, slug): return _TS()

    st = SessionTurnStore(tmp_path / "t.db")
    await st.init()
    q = TurnIngestQueue(tmp_path / "q.db")
    await q.init()
    reg = BridgeSessionRegistry(trace_registry=_TR(), archive=AsyncMock(),
                                session_turns=st, turn_ingest=q)
    body = {"kind": "final", "trace_id": "t", "content": "the answer", "id": "r1",
            "channel_id": "chan"}
    await reg.record_reply("atlas", body)
    await reg.record_reply("atlas", body)  # replay of the same reply id
    rows = await (await q._db.execute("SELECT shelf, payload FROM turn_ingest")).fetchall()
    assert len(rows) == 1 and rows[0][0] == "atlas"
    turn = json.loads(rows[0][1])
    assert turn["role"] == "assistant" and turn["content"] == "the answer"
    assert turn["session_id"] == "chan" and turn["turn_index"] == 0
    await st.close()
    await q.close()
