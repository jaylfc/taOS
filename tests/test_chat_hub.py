"""Unit tests for ChatHub — no WebSocket required."""
from __future__ import annotations

import json
import time

import pytest

from tinyagentos.chat.hub import ChatHub


class MockWebSocket:
    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


# ── helpers ───────────────────────────────────────────────────────────────────

def _last(ws: MockWebSocket) -> dict:
    return json.loads(ws.sent[-1])


# ── tests ─────────────────────────────────────────────────────────────────────

def test_connect_sets_presence():
    hub = ChatHub()
    ws = MockWebSocket()
    hub.connect(ws, "alice")
    assert hub._presence["alice"]["status"] == "online"


def test_disconnect_clears_presence():
    hub = ChatHub()
    ws = MockWebSocket()
    hub.connect(ws, "alice")
    hub.disconnect(ws, "alice")
    assert hub._presence["alice"]["status"] == "offline"


def test_disconnect_stays_online_with_multiple_sockets():
    hub = ChatHub()
    ws1 = MockWebSocket()
    ws2 = MockWebSocket()
    hub.connect(ws1, "alice")
    hub.connect(ws2, "alice")
    hub.disconnect(ws1, "alice")
    # Still has ws2, so should remain online
    assert hub._presence["alice"]["status"] == "online"


@pytest.mark.asyncio
async def test_join_and_broadcast():
    hub = ChatHub()
    ws = MockWebSocket()
    hub.connect(ws, "alice")
    hub.join(ws, "ch1")
    await hub.broadcast("ch1", {"type": "ping"})
    assert len(ws.sent) == 1
    assert _last(ws)["type"] == "ping"


@pytest.mark.asyncio
async def test_broadcast_to_multiple():
    hub = ChatHub()
    ws1 = MockWebSocket()
    ws2 = MockWebSocket()
    hub.connect(ws1, "alice")
    hub.connect(ws2, "bob")
    hub.join(ws1, "ch1")
    hub.join(ws2, "ch1")
    await hub.broadcast("ch1", {"type": "ping"})
    assert len(ws1.sent) == 1
    assert len(ws2.sent) == 1


@pytest.mark.asyncio
async def test_leave_stops_broadcast():
    hub = ChatHub()
    ws = MockWebSocket()
    hub.connect(ws, "alice")
    hub.join(ws, "ch1")
    hub.leave(ws, "ch1")
    await hub.broadcast("ch1", {"type": "ping"})
    assert len(ws.sent) == 0


@pytest.mark.asyncio
async def test_broadcast_skips_failed_socket():
    """Broadcast should not raise even if one socket fails."""

    class FailingWebSocket:
        async def send_text(self, data: str) -> None:
            raise RuntimeError("connection closed")

    hub = ChatHub()
    ws_good = MockWebSocket()
    ws_bad = FailingWebSocket()
    hub._channels["ch1"] = {ws_good, ws_bad}
    # Should not raise
    await hub.broadcast("ch1", {"type": "test"})
    assert len(ws_good.sent) == 1


def test_typing():
    hub = ChatHub()
    hub.set_typing("ch1", "alice")
    typing = hub.get_typing("ch1")
    assert "alice" in typing


def test_typing_expires():
    hub = ChatHub()
    # Manually insert an old timestamp (> 5 seconds ago)
    hub._typing["ch1"] = {"alice": time.time() - 10}
    typing = hub.get_typing("ch1")
    assert "alice" not in typing


def test_typing_mixed_fresh_and_expired():
    hub = ChatHub()
    hub._typing["ch1"] = {
        "alice": time.time() - 10,   # expired
        "bob": time.time() - 1,      # fresh
    }
    typing = hub.get_typing("ch1")
    assert "alice" not in typing
    assert "bob" in typing


def test_seq_increments():
    hub = ChatHub()
    seq1 = hub.next_seq()
    seq2 = hub.next_seq()
    seq3 = hub.next_seq()
    assert seq1 < seq2 < seq3
    assert seq1 == 1
    assert seq2 == 2
    assert seq3 == 3


@pytest.mark.asyncio
async def test_send_to_user():
    hub = ChatHub()
    ws = MockWebSocket()
    hub.connect(ws, "alice")
    await hub.send_to_user("alice", {"type": "dm"})
    assert len(ws.sent) == 1
    assert _last(ws)["type"] == "dm"


@pytest.mark.asyncio
async def test_send_to_user_unknown_is_noop():
    hub = ChatHub()
    # Should not raise for unknown user
    await hub.send_to_user("nobody", {"type": "dm"})


@pytest.mark.asyncio
async def test_disconnect_removes_from_channels():
    hub = ChatHub()
    ws = MockWebSocket()
    hub.connect(ws, "alice")
    hub.join(ws, "ch1")
    hub.join(ws, "ch2")
    hub.disconnect(ws, "alice")
    await hub.broadcast("ch1", {"type": "ping"})
    await hub.broadcast("ch2", {"type": "ping"})
    assert len(ws.sent) == 0
