"""Tests for the fork-free OpenClaw ACP runtime driver."""
from __future__ import annotations

import pytest

from tinyagentos.openclaw_acp_runtime import (
    OPENCLAW_SESSION_KEY,
    container_name,
    drive_turn,
)


class _FakeAdapter:
    def __init__(self, cfg, sink):
        self.cfg = cfg
        self.sink = sink
        self.calls = []

    async def spawn(self):
        self.calls.append("spawn")

    async def initialize(self):
        self.calls.append("initialize")
        return {"protocolVersion": 1}

    async def new_session(self, *a, **k):
        self.calls.append("new_session")
        return "sid-1"

    async def prompt(self, sid, text, trace_id=None):
        self.calls.append(("prompt", sid, text, trace_id))
        await self.sink({"kind": "delta", "trace_id": trace_id, "content": "hi"})
        await self.sink({"kind": "final", "trace_id": trace_id, "content": "hi"})
        return "end_turn"

    async def close(self):
        self.calls.append("close")


def test_container_name():
    assert container_name("kilotest") == "taos-agent-kilotest"


@pytest.mark.asyncio
async def test_drive_turn_binds_session_and_streams_replies():
    captured = []

    async def record_reply(slug, body):
        captured.append((slug, body))

    holder = {}

    def factory(cfg, sink):
        holder["a"] = _FakeAdapter(cfg, sink)
        return holder["a"]

    stop = await drive_turn(
        slug="kilotest", text="hello", trace_id="t1",
        record_reply=record_reply, adapter_factory=factory,
    )
    assert stop == "end_turn"
    a = holder["a"]
    # ACP bound to the agent session + launched via incus exec into the container.
    assert a.cfg.session_key == OPENCLAW_SESSION_KEY
    assert a.cfg.command == ["incus", "exec", "taos-agent-kilotest", "--", "openclaw", "acp"]
    # Full lifecycle ran (and the transport was closed).
    assert {"spawn", "initialize", "new_session", "close"} <= set(
        c if isinstance(c, str) else c[0] for c in a.calls
    )
    # Replies streamed to record_reply tagged with the agent slug.
    assert [b["kind"] for _, b in captured] == ["delta", "final"]
    assert all(s == "kilotest" for s, _ in captured)


@pytest.mark.asyncio
async def test_drive_turn_emits_error_on_transport_failure():
    captured = []

    async def record_reply(slug, body):
        captured.append((slug, body))

    class _Boom(_FakeAdapter):
        async def initialize(self):
            raise RuntimeError("transport down")

    stop = await drive_turn(
        slug="x", text="t", trace_id="t1",
        record_reply=record_reply, adapter_factory=lambda cfg, sink: _Boom(cfg, sink),
    )
    assert stop == "error"
    assert any(b["kind"] == "error" for _, b in captured)
