"""Tests for tinyagentos.services.mdns_publisher."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.services import mdns_publisher as mp
from tinyagentos.services.mdns_publisher import MdnsPublisher


@pytest.fixture
def fake_zc(monkeypatch):
    """Patch AsyncZeroconf and the IPv4 detector — no real sockets."""
    zc_instance = MagicMock()
    zc_instance.async_register_service = AsyncMock()
    zc_instance.async_unregister_service = AsyncMock()
    zc_instance.async_close = AsyncMock()
    factory = MagicMock(return_value=zc_instance)
    monkeypatch.setattr(mp, "AsyncZeroconf", factory)
    monkeypatch.setattr(mp, "_detect_primary_ipv4", lambda: "192.168.1.42")
    return zc_instance, factory


@pytest.mark.asyncio
async def test_start_registers_service_with_taos_local(fake_zc):
    zc_instance, _ = fake_zc
    pub = MdnsPublisher(port=6969)

    await pub.start()

    assert zc_instance.async_register_service.await_count == 1
    info = zc_instance.async_register_service.await_args.args[0]
    assert info.server == "taos.local."
    assert info.port == 6969


@pytest.mark.asyncio
async def test_stop_unregisters_then_closes(fake_zc):
    zc_instance, _ = fake_zc
    pub = MdnsPublisher(port=6969)
    await pub.start()

    await pub.stop()

    assert zc_instance.async_unregister_service.await_count == 1
    assert zc_instance.async_close.await_count == 1


@pytest.mark.asyncio
async def test_start_swallows_exceptions_and_stop_is_noop(monkeypatch):
    monkeypatch.setattr(mp, "_detect_primary_ipv4", lambda: "192.168.1.42")

    def _boom(*_a, **_kw):
        raise RuntimeError("multicast disabled")

    monkeypatch.setattr(mp, "AsyncZeroconf", _boom)

    pub = MdnsPublisher(port=6969)
    await pub.start()  # must not raise

    assert pub._active is False
    await pub.stop()  # must be a no-op, must not raise
