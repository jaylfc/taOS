"""Tests for issue #656: WebSocket backpressure and per-IP rate limiting."""
from __future__ import annotations

import asyncio

import pytest

from tinyagentos.chat.hub import ChatHub


class MockWebSocket:
    def __init__(self, client_host: str = "127.0.0.1"):
        self.sent: list[str] = []
        self.closed: bool = False
        # Simulate FastAPI request.client attribute
        self.client = type("_Client", (), {"host": client_host})()

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


class SlowWebSocket:
    """Simulates a slow / dead client whose send_text never completes."""
    def __init__(self, client_host: str = "10.0.0.1"):
        self.sent: list[str] = []
        self.closed: bool = False
        self.client = type("_Client", (), {"host": client_host})()

    async def send_text(self, data: str) -> None:
        # Hangs forever
        await asyncio.sleep(9999)

    async def close(self) -> None:
        self.closed = True


class ErrorWebSocket:
    """Simulates a socket that raises on send (already disconnected)."""
    def __init__(self, client_host: str = "10.0.0.2"):
        self.sent: list[str] = []
        self.closed: bool = False
        self.client = type("_Client", (), {"host": client_host})()

    async def send_text(self, data: str) -> None:
        raise RuntimeError("connection closed")

    async def close(self) -> None:
        self.closed = True


# ── backpressure / timeout tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_slow_client_removed_after_timeout():
    """A slow client must be closed and removed from the channel after timeout."""
    hub = ChatHub(send_timeout=0.05)  # 50ms timeout for tests
    good_ws = MockWebSocket()
    slow_ws = SlowWebSocket()

    hub.connect(good_ws, "alice")
    hub.connect(slow_ws, "bob")
    hub.join(good_ws, "ch1")
    hub.join(slow_ws, "ch1")

    await hub.broadcast("ch1", {"type": "ping"})

    # Good socket received the message
    assert len(good_ws.sent) == 1
    # Slow socket was closed
    assert slow_ws.closed is True
    # Slow socket removed from channel
    assert slow_ws not in hub._channels.get("ch1", set())


@pytest.mark.asyncio
async def test_broadcast_does_not_block_on_slow_client():
    """broadcast() must complete within reasonable time even with slow clients."""
    hub = ChatHub(send_timeout=0.05)
    slow_ws = SlowWebSocket()
    hub._channels["ch1"] = {slow_ws}

    start = asyncio.get_event_loop().time()
    await hub.broadcast("ch1", {"type": "test"})
    elapsed = asyncio.get_event_loop().time() - start

    # Must complete well under 1 second (actual limit is 50ms + small overhead)
    assert elapsed < 1.0, f"broadcast took {elapsed:.2f}s — not bounded"


@pytest.mark.asyncio
async def test_error_socket_closed_and_removed():
    """A socket that raises on send must be closed and removed."""
    hub = ChatHub(send_timeout=0.05)
    err_ws = ErrorWebSocket()
    hub._channels["ch1"] = {err_ws}

    await hub.broadcast("ch1", {"type": "test"})
    assert err_ws not in hub._channels.get("ch1", set())


@pytest.mark.asyncio
async def test_send_to_user_slow_client_removed():
    """send_to_user must also apply timeout and remove slow sockets."""
    hub = ChatHub(send_timeout=0.05)
    slow_ws = SlowWebSocket()
    hub.connect(slow_ws, "bob")

    await hub.send_to_user("bob", {"type": "dm"})
    assert slow_ws.closed is True
    assert slow_ws not in hub._user_sockets.get("bob", set())


# ── per-IP connection limit ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_per_ip_connection_limit_enforced():
    """connect() must reject sockets beyond the per-IP cap."""
    hub = ChatHub(max_connections_per_ip=3)
    sockets = [MockWebSocket("192.168.1.1") for _ in range(3)]
    for i, ws in enumerate(sockets):
        allowed = hub.connect(ws, f"user{i}")
        assert allowed is True

    # 4th connection from same IP must be rejected
    overflow = MockWebSocket("192.168.1.1")
    allowed = hub.connect(overflow, "user_extra")
    assert allowed is False


@pytest.mark.asyncio
async def test_per_ip_limit_tracks_disconnects():
    """Disconnecting a socket frees its IP slot."""
    hub = ChatHub(max_connections_per_ip=2)
    ws1 = MockWebSocket("10.0.0.5")
    ws2 = MockWebSocket("10.0.0.5")
    assert hub.connect(ws1, "a") is True
    assert hub.connect(ws2, "b") is True

    # Now at cap — 3rd must fail
    ws3 = MockWebSocket("10.0.0.5")
    assert hub.connect(ws3, "c") is False

    # Disconnect one; 3rd slot should open
    hub.disconnect(ws1, "a")
    ws4 = MockWebSocket("10.0.0.5")
    assert hub.connect(ws4, "d") is True


@pytest.mark.asyncio
async def test_different_ips_have_independent_limits():
    """Each IP has its own counter; saturating one must not block another."""
    hub = ChatHub(max_connections_per_ip=2)
    ws_a1 = MockWebSocket("10.0.0.1")
    ws_a2 = MockWebSocket("10.0.0.1")
    assert hub.connect(ws_a1, "a1") is True
    assert hub.connect(ws_a2, "a2") is True

    ws_b1 = MockWebSocket("10.0.0.2")
    assert hub.connect(ws_b1, "b1") is True  # different IP — must not be blocked


@pytest.mark.asyncio
async def test_connect_returns_true_by_default():
    """Existing tests: connect() without a client must not break (backwards compat)."""
    hub = ChatHub()
    ws = MockWebSocket()
    result = hub.connect(ws, "alice")
    # Must return True (allowed) or None — never False
    assert result is not False


# ── no-client-attribute path ──────────────────────────────────────────────────

class NoClientWebSocket:
    """Simulates a WebSocket where ws.client is None (e.g. reverse-proxy / test env)."""

    def __init__(self):
        self.sent: list[str] = []
        self.closed: bool = False
        self.client = None  # no IP available

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


class NoClientAttrWebSocket:
    """Simulates a WebSocket that has no 'client' attribute at all."""

    def __init__(self):
        self.sent: list[str] = []
        self.closed: bool = False
        # deliberately no self.client

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_connect_no_client_attribute_allowed():
    """connect() must succeed (return True) when the socket has no client attribute."""
    hub = ChatHub(max_connections_per_ip=1)
    ws = NoClientAttrWebSocket()
    result = hub.connect(ws, "alice")
    assert result is True
    # _ip_counts must stay empty — no IP to track
    assert hub._ip_counts == {}


@pytest.mark.asyncio
async def test_connect_client_none_allowed():
    """connect() must succeed when ws.client is None (no IP available)."""
    hub = ChatHub(max_connections_per_ip=1)
    ws = NoClientWebSocket()
    result = hub.connect(ws, "bob")
    assert result is True
    assert hub._ip_counts == {}


@pytest.mark.asyncio
async def test_connect_no_client_does_not_consume_ip_slot():
    """Sockets without a client IP must not affect per-IP counting for real IPs."""
    hub = ChatHub(max_connections_per_ip=1)
    anon = NoClientWebSocket()
    real = MockWebSocket("172.16.0.1")

    hub.connect(anon, "anon")
    result = hub.connect(real, "real")
    assert result is True  # real IP slot is free


# ── IP slot released on timeout eviction ─────────────────────────────────────

class SlowWebSocketWithIP:
    """Slow client with a known IP so we can verify the slot is released."""

    def __init__(self, client_host: str = "10.1.2.3"):
        self.sent: list[str] = []
        self.closed: bool = False
        self.client = type("_Client", (), {"host": client_host})()

    async def send_text(self, data: str) -> None:
        await asyncio.sleep(9999)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_ip_slot_released_after_timeout_eviction():
    """When a slow socket times out and is evicted, its IP slot must be freed."""
    ip = "10.1.2.3"
    hub = ChatHub(send_timeout=0.05, max_connections_per_ip=1)
    slow_ws = SlowWebSocketWithIP(ip)

    hub.connect(slow_ws, "charlie")
    assert hub._ip_counts.get(ip) == 1

    # Trigger timeout eviction via send_to_user
    await hub.send_to_user("charlie", {"type": "ping"})

    # Slot must be released — a new connection from the same IP must be accepted
    assert hub._ip_counts.get(ip, 0) == 0
    new_ws = MockWebSocket(ip)
    result = hub.connect(new_ws, "charlie2")
    assert result is True
