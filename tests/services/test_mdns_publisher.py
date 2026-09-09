"""Tests for the ifaddr fallback in mdns_publisher."""
from __future__ import annotations

import socket
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.services import mdns_publisher as mp
from tinyagentos.services.mdns_publisher import MdnsPublisher


class _FakeIPEntry:
    def __init__(self, ip: str) -> None:
        self.ip = ip
        self.network_prefix = 24


class _FakeAdapter:
    def __init__(self, ips: list[_FakeIPEntry]) -> None:
        self.ips = ips
        self.nice_name = "eth0"
        self.index = 2


def _make_fake_ifaddr(adapters: list[_FakeAdapter]) -> MagicMock:
    fake_mod = MagicMock()
    fake_mod.get_adapters = MagicMock(return_value=adapters)
    return fake_mod


class _FailingSocket:
    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def connect(self, addr):
        raise OSError(101, "Network is unreachable")

    def getsockname(self):
        return ("0.0.0.0", 0)


class _SuccessfulSocket:
    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def connect(self, addr):
        pass

    def getsockname(self):
        return ("192.168.1.42", 0)


@pytest.mark.asyncio
async def test_publishes_when_default_route_is_absent(monkeypatch):
    monkeypatch.setattr(mp.socket, "socket", _FailingSocket)

    fake_ifaddr = _make_fake_ifaddr([_FakeAdapter([_FakeIPEntry("192.168.1.42")])])
    monkeypatch.setitem(sys.modules, "ifaddr", fake_ifaddr)

    zc_instance = MagicMock()
    zc_instance.async_register_service = AsyncMock()
    zc_instance.async_unregister_service = AsyncMock()
    zc_instance.async_close = AsyncMock()
    monkeypatch.setattr(mp, "AsyncZeroconf", MagicMock(return_value=zc_instance))

    pub = MdnsPublisher(port=6969)
    await pub.start()

    addresses = []
    if zc_instance.async_register_service.await_args is not None:
        info = zc_instance.async_register_service.await_args.args[0]
        addresses = [addr.hex() for addr in info.addresses]
    assert addresses != []


@pytest.mark.asyncio
async def test_multi_homed_host_advertises_every_non_loopback_ipv4(monkeypatch):
    monkeypatch.setattr(mp.socket, "socket", _SuccessfulSocket)

    fake_ifaddr = _make_fake_ifaddr([
        _FakeAdapter([_FakeIPEntry("192.168.1.42"), _FakeIPEntry("10.0.0.5")]),
    ])
    monkeypatch.setitem(sys.modules, "ifaddr", fake_ifaddr)

    zc_instance = MagicMock()
    zc_instance.async_register_service = AsyncMock()
    zc_instance.async_unregister_service = AsyncMock()
    zc_instance.async_close = AsyncMock()
    monkeypatch.setattr(mp, "AsyncZeroconf", MagicMock(return_value=zc_instance))

    pub = MdnsPublisher(port=6969)
    await pub.start()

    info = zc_instance.async_register_service.await_args.args[0]
    assert len(info.addresses) == 2
    assert socket.inet_aton("192.168.1.42") in info.addresses
    assert socket.inet_aton("10.0.0.5") in info.addresses
