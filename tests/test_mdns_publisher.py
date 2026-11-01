"""Tests for tinyagentos.services.mdns_publisher."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.services import mdns_publisher as mp
from tinyagentos.services.mdns_publisher import MdnsPublisher


class _FakeAddressResolver:
    """Fake AddressResolver that simulates probe results — always available."""

    def __init__(self, found: bool, parsed_addrs: list[str]):
        self._found = found
        self._parsed_addrs = parsed_addrs
        self.requested_name = None

    async def async_request(self, zc, timeout: int = 1500) -> bool:
        self.requested_name = getattr(self, "name", "taos.local.")
        return self._found

    def parsed_addresses(self) -> list[str]:
        return self._parsed_addrs


@pytest.fixture
def fake_zc(monkeypatch):
    """Patch AsyncZeroconf, _detect_primary_ipv4, and AddressResolver — no real sockets."""
    zc_instance = MagicMock()
    zc_instance.async_register_service = AsyncMock()
    zc_instance.async_unregister_service = AsyncMock()
    zc_instance.async_close = AsyncMock()
    factory = MagicMock(return_value=zc_instance)
    monkeypatch.setattr(mp, "AsyncZeroconf", factory)
    monkeypatch.setattr(mp, "_detect_primary_ipv4", lambda: ["192.168.1.42"])
    # Stub AddressResolver to avoid real mDNS multicast probes
    monkeypatch.setattr(mp, "AddressResolver", lambda name: _FakeAddressResolver(found=False, parsed_addrs=[]))
    return zc_instance, factory


@pytest.mark.asyncio
async def test_start_registers_service_with_taos_local(fake_zc, tmp_path):
    zc_instance, _ = fake_zc
    pub = MdnsPublisher(data_dir=tmp_path, port=6969)

    await pub.start()

    assert zc_instance.async_register_service.await_count == 1
    info = zc_instance.async_register_service.await_args.args[0]
    assert info.server == "taos.local."
    assert info.port == 6969


@pytest.mark.asyncio
async def test_stop_unregisters_then_closes(fake_zc, tmp_path):
    zc_instance, _ = fake_zc
    pub = MdnsPublisher(data_dir=tmp_path, port=6969)
    await pub.start()

    await pub.stop()

    assert zc_instance.async_unregister_service.await_count == 1
    assert zc_instance.async_close.await_count == 2
    # async_close is called once in start()'s finally, once in stop().
    # async_unregister_service happens between the two close calls.
    names = [c[0] for c in zc_instance.mock_calls]
    close_indices = [i for i, c in enumerate(names) if c == "async_close"]
    unreg_index = names.index("async_unregister_service")
    assert close_indices[0] < unreg_index < close_indices[1]


@pytest.mark.asyncio
async def test_failed_register_closes_zeroconf_instance(monkeypatch, tmp_path):
    """If async_register_service raises after AsyncZeroconf() is built,
    the half-initialised instance must be closed — otherwise its sockets
    and multicast subscriptions leak. Caught by kilo-code-bot on #449."""
    monkeypatch.setattr(mp, "_detect_primary_ipv4", lambda: ["192.168.1.42"])
    zc_instance = MagicMock()
    zc_instance.async_register_service = AsyncMock(
        side_effect=RuntimeError("port in use")
    )
    zc_instance.async_close = AsyncMock()
    monkeypatch.setattr(mp, "AsyncZeroconf", MagicMock(return_value=zc_instance))

    pub = MdnsPublisher(data_dir=tmp_path, port=6969)
    await pub.start()  # must not raise

    assert zc_instance.async_close.await_count == 2
    assert pub._active is False


@pytest.mark.asyncio
async def test_start_swallows_exceptions_and_stop_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(mp, "_detect_primary_ipv4", lambda: ["192.168.1.42"])

    def _boom(*_a, **_kw):
        raise RuntimeError("multicast disabled")

    monkeypatch.setattr(mp, "AsyncZeroconf", _boom)

    pub = MdnsPublisher(data_dir=tmp_path, port=6969)
    await pub.start()  # must not raise

    assert pub._active is False
    await pub.stop()  # must be a no-op, must not raise
