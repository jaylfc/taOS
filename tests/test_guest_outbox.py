"""Guest outbox: persisted guest-bound bridge events and Last-Event-ID resume (tsk-ph6mwg)."""
from __future__ import annotations

import json
import re
import time

import pytest
import pytest_asyncio

from tinyagentos.bridge_session import BridgeSessionRegistry
from tinyagentos.chat.guest_outbox import GuestOutbox

from test_agent_chat_session_routes import EVENTS, _agent, _bare, _hdr, chat_app  # noqa: F401


@pytest_asyncio.fixture
async def outbox(tmp_path):
    store = GuestOutbox(tmp_path / "guest_outbox.db")
    await store.init()
    yield store
    await store.close()


def _registry(outbox, local=()):
    reg = BridgeSessionRegistry()
    reg._guest_outbox = outbox
    reg._is_guest = lambda slug: slug not in local
    return reg


def _ids(text):
    return [int(m) for m in re.findall(r"^id: (\d+)$", text, re.M)]


def _texts(text):
    return [json.loads(m)["text"] for m in re.findall(r"^data: (.*)$", text, re.M)
            if "text" in m]


def _stop_at_tick(registry):
    """Make the live subscription end at the first keepalive frame."""
    real = registry.subscribe

    async def limited(slug):
        async for frame in real(slug):
            if frame.startswith("event: tick"):
                return
            yield frame

    registry.subscribe = limited


async def _tick(registry, slug):
    registry._sessions[slug].queue.put_nowait({"event": "tick", "data": {}})


@pytest.mark.asyncio
class TestStore:
    async def test_append_since_ordering_and_boundary(self, outbox):
        a = await outbox.append("g", {"text": "1"})
        b = await outbox.append("g", {"text": "2"})
        c = await outbox.append("g", {"text": "3"})
        await outbox.append("other", {"text": "x"})
        assert a < b < c
        rows = await outbox.since("g", a)
        assert [r[0] for r in rows] == [b, c]
        assert [r[1]["text"] for r in rows] == ["2", "3"]
        assert await outbox.since("g", c) == []

    async def test_undelivered_and_mark_delivered(self, outbox):
        a = await outbox.append("g", {"text": "1"})
        b = await outbox.append("g", {"text": "2"})
        await outbox.mark_delivered(a)
        assert [r[0] for r in await outbox.undelivered("g")] == [b]

    async def test_prune_window(self, outbox):
        old = await outbox.append("g", {"text": "old"})
        new = await outbox.append("g", {"text": "new"})
        await outbox._db.execute(
            "UPDATE guest_outbox SET created_at = ? WHERE id = ?",
            (time.time() - 8 * 86400, old),
        )
        await outbox._db.commit()
        await outbox.prune()
        assert [r[0] for r in await outbox.since("g", 0)] == [new]


@pytest.mark.asyncio
class TestEnqueue:
    async def test_guest_message_persisted_with_outbox_id(self, outbox):
        reg = _registry(outbox)
        await reg.enqueue_user_message("guest-a", {"id": "m1", "text": "hi"})
        item = reg._sessions["guest-a"].queue.get_nowait()
        rows = await outbox.undelivered("guest-a")
        assert item["outbox_id"] == rows[0][0]
        assert rows[0][1]["text"] == "hi"

    async def test_config_agent_not_persisted(self, outbox):
        reg = _registry(outbox, local=("alice",))
        await reg.enqueue_user_message("alice", {"id": "m1", "text": "hi"})
        item = reg._sessions["alice"].queue.get_nowait()
        assert "outbox_id" not in item
        assert await outbox.since("alice", 0) == []

    async def test_no_outbox_attached_is_unchanged(self):
        reg = BridgeSessionRegistry()
        await reg.enqueue_user_message("alice", {"id": "m1", "text": "hi"})
        assert "outbox_id" not in reg._sessions["alice"].queue.get_nowait()


@pytest.mark.asyncio
class TestGuestStream:
    async def _setup(self, chat_app, outbox, handle="@guest-a"):
        _cid, token = await _agent(chat_app, handle)
        reg = _registry(outbox)
        chat_app.state.bridge_sessions = reg
        chat_app.state.guest_outbox = outbox
        _stop_at_tick(reg)
        return reg, token

    async def test_last_event_id_replays_only_newer_in_order(self, chat_app, outbox):
        reg, token = await self._setup(chat_app, outbox)
        for t in ("1", "2", "3"):
            await reg.enqueue_user_message("guest-a", {"id": "m" + t, "text": t})
        first = (await outbox.since("guest-a", 0))[0][0]
        # drop the in-memory copies: only the outbox remains (as after a restart)
        reg._sessions["guest-a"].queue = __import__("asyncio").Queue()
        await _tick(reg, "guest-a")
        async with _bare(chat_app) as c:
            r = await c.get(EVENTS, headers={**_hdr(token), "Last-Event-ID": str(first)})
        assert _ids(r.text) == [first + 1, first + 2]
        assert _texts(r.text) == ["2", "3"]

    async def test_since_query_param_works(self, chat_app, outbox):
        reg, token = await self._setup(chat_app, outbox)
        for t in ("1", "2"):
            await reg.enqueue_user_message("guest-a", {"id": "m" + t, "text": t})
        first = (await outbox.since("guest-a", 0))[0][0]
        reg._sessions["guest-a"].queue = __import__("asyncio").Queue()
        await _tick(reg, "guest-a")
        async with _bare(chat_app) as c:
            r = await c.get(f"{EVENTS}?since={first}", headers=_hdr(token))
        assert _texts(r.text) == ["2"]

    async def test_non_integer_resume_value_is_ignored(self, chat_app, outbox):
        reg, token = await self._setup(chat_app, outbox)
        await reg.enqueue_user_message("guest-a", {"id": "m1", "text": "1"})
        await _tick(reg, "guest-a")
        async with _bare(chat_app) as c:
            r = await c.get(
                f"{EVENTS}?since=abc", headers={**_hdr(token), "Last-Event-ID": "zzz"}
            )
        assert r.status_code == 200
        assert _texts(r.text) == ["1"]

    async def test_replay_and_queue_do_not_duplicate(self, chat_app, outbox):
        reg, token = await self._setup(chat_app, outbox)
        for t in ("1", "2"):
            await reg.enqueue_user_message("guest-a", {"id": "m" + t, "text": t})
        await _tick(reg, "guest-a")
        async with _bare(chat_app) as c:
            r = await c.get(EVENTS, headers=_hdr(token))
        assert _texts(r.text) == ["1", "2"]
        assert len(_ids(r.text)) == 2

    async def test_delivered_rows_not_replayed_next_connect(self, chat_app, outbox):
        reg, token = await self._setup(chat_app, outbox)
        await reg.enqueue_user_message("guest-a", {"id": "m1", "text": "1"})
        await _tick(reg, "guest-a")
        async with _bare(chat_app) as c:
            await c.get(EVENTS, headers=_hdr(token))
            await _tick(reg, "guest-a")
            r = await c.get(EVENTS, headers=_hdr(token))
        assert _texts(r.text) == []

    async def test_replay_scoped_to_own_slug(self, chat_app, outbox):
        reg, token = await self._setup(chat_app, outbox)
        await reg.enqueue_user_message("guest-b", {"id": "m1", "text": "secret"})
        await reg.enqueue_user_message("guest-a", {"id": "m2", "text": "mine"})
        reg._sessions["guest-a"].queue = __import__("asyncio").Queue()
        await _tick(reg, "guest-a")
        async with _bare(chat_app) as c:
            r = await c.get(f"{EVENTS}?since=0&slug=guest-b", headers=_hdr(token))
        assert "secret" not in r.text
        assert _texts(r.text) == ["mine"]

    async def test_restart_with_no_subscriber_delivers_in_order(
        self, chat_app, outbox, tmp_path
    ):
        old = _registry(outbox)
        await old.enqueue_user_message("guest-a", {"id": "m1", "text": "first"})
        await old.enqueue_user_message("guest-a", {"id": "m2", "text": "second"})
        await outbox.close()
        fresh_outbox = GuestOutbox(tmp_path / "guest_outbox.db")
        await fresh_outbox.init()
        try:
            _cid, token = await _agent(chat_app, "@guest-a")
            fresh = _registry(fresh_outbox)
            chat_app.state.bridge_sessions = fresh
            chat_app.state.guest_outbox = fresh_outbox
            _stop_at_tick(fresh)
            fresh._get_or_create("guest-a")
            await _tick(fresh, "guest-a")
            async with _bare(chat_app) as c:
                r = await c.get(EVENTS, headers=_hdr(token))
            assert _texts(r.text) == ["first", "second"]
            ids = _ids(r.text)
            assert ids == sorted(ids) and len(ids) == 2
        finally:
            await fresh_outbox.close()
            outbox._db = None
