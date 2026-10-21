"""Tests for the ifaddr fallback in mdns_publisher."""
from __future__ import annotations

import os
import re
import socket
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.services import mdns_publisher as mp
from tinyagentos.services.mdns_publisher import MdnsPublisher, choose_hostname


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
async def test_publishes_when_default_route_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(mp.socket, "socket", _FailingSocket)

    fake_ifaddr = _make_fake_ifaddr([_FakeAdapter([_FakeIPEntry("192.168.1.42")])])
    monkeypatch.setitem(sys.modules, "ifaddr", fake_ifaddr)

    zc_instance = MagicMock()
    zc_instance.async_register_service = AsyncMock()
    zc_instance.async_unregister_service = AsyncMock()
    zc_instance.async_close = AsyncMock()
    monkeypatch.setattr(mp, "AsyncZeroconf", MagicMock(return_value=zc_instance))

    pub = MdnsPublisher(port=6969, data_dir=tmp_path)
    await pub.start()

    addresses = []
    if zc_instance.async_register_service.await_args is not None:
        info = zc_instance.async_register_service.await_args.args[0]
        addresses = [addr.hex() for addr in info.addresses]
    assert addresses != []


@pytest.mark.asyncio
async def test_multi_homed_host_advertises_every_non_loopback_ipv4(monkeypatch, tmp_path):
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

    pub = MdnsPublisher(port=6969, data_dir=tmp_path)
    await pub.start()

    info = zc_instance.async_register_service.await_args.args[0]
    assert len(info.addresses) == 2
    assert socket.inet_aton("192.168.1.42") in info.addresses
    assert socket.inet_aton("10.0.0.5") in info.addresses


# --- New tests for choose_hostname and MdnsPublisher hostname selection ---


class _FakeAddressResolver:
    """Fake AddressResolver that simulates probe results."""

    def __init__(self, found: bool, parsed_addrs: list[str]):
        self._found = found
        self._parsed_addrs = parsed_addrs
        self.requested_name = None

    async def async_request(self, zc, timeout: int = 1500) -> bool:
        self.requested_name = getattr(self, "name", "taos.local.")
        return self._found

    def parsed_addresses(self) -> list[str]:
        return self._parsed_addrs


def _make_fake_zc():
    zc = MagicMock()
    zc.async_register_service = AsyncMock()
    zc.async_unregister_service = AsyncMock()
    zc.async_close = AsyncMock()
    return zc


@pytest.mark.asyncio
async def test_choose_hostname_persisted_valid_name_no_probe(tmp_path, monkeypatch):
    """(i) Persisted valid name is returned without any probe."""
    data_dir = tmp_path
    hostname_file = data_dir / "mdns_hostname"
    hostname_file.write_text("taos-abcd.local.\n")

    own_ips = ["192.168.1.42"]
    zc = _make_fake_zc()

    # Mock AddressResolver to track if it's called
    resolver_called = {"count": 0}

    class _TrackingResolver(_FakeAddressResolver):
        async def async_request(self, zc, timeout=1500):
            resolver_called["count"] += 1
            return await super().async_request(zc, timeout)

    with patch("tinyagentos.services.mdns_publisher.AddressResolver", _TrackingResolver):
        result = await choose_hostname(zc, data_dir, own_ips)

    assert result == "taos-abcd.local."
    assert resolver_called["count"] == 0  # No probe should happen


@pytest.mark.asyncio
async def test_choose_hostname_probe_finds_foreign_ip_picks_suffix(tmp_path, monkeypatch):
    """(ii) Probe finds taos.local at a foreign IP -> returns taos-xxxx.local. and writes the file."""
    data_dir = tmp_path
    own_ips = ["192.168.1.42"]
    zc = _make_fake_zc()

    # First probe for taos.local finds a foreign IP
    resolver_for_taos = _FakeAddressResolver(found=True, parsed_addrs=["10.0.0.99"])
    # Second probe for taos-xxxx.local finds nothing (available)
    resolver_for_suffix = _FakeAddressResolver(found=False, parsed_addrs=[])

    resolvers = [resolver_for_taos, resolver_for_suffix]

    def resolver_factory(name):
        resolver_for_taos.name = name
        resolver_for_suffix.name = name
        return resolvers.pop(0)

    with patch("tinyagentos.services.mdns_publisher.AddressResolver", side_effect=resolver_factory):
        result = await choose_hostname(zc, data_dir, own_ips)

    assert result.startswith("taos-")
    assert result.endswith(".local.")
    assert re.match(r"^taos-[a-z0-9]{4}\.local\.$", result)
    # File should be written
    hostname_file = data_dir / "mdns_hostname"
    assert hostname_file.exists()
    assert hostname_file.read_text().strip() == result


@pytest.mark.asyncio
async def test_choose_hostname_probe_finds_only_own_ip_uses_taos_local(tmp_path, monkeypatch):
    """(iii) Probe finds only our own IP -> taos.local."""
    data_dir = tmp_path
    own_ips = ["192.168.1.42"]
    zc = _make_fake_zc()

    # Probe for taos.local finds only our own IP
    resolver = _FakeAddressResolver(found=True, parsed_addrs=["192.168.1.42"])

    with patch("tinyagentos.services.mdns_publisher.AddressResolver", return_value=resolver):
        result = await choose_hostname(zc, data_dir, own_ips)

    assert result == "taos.local."
    hostname_file = data_dir / "mdns_hostname"
    assert hostname_file.exists()
    assert hostname_file.read_text().strip() == result


@pytest.mark.asyncio
async def test_choose_hostname_probe_raises_falls_back(tmp_path, monkeypatch):
    """(iv) Probe raises -> falls back, start() still completes."""
    data_dir = tmp_path
    own_ips = ["192.168.1.42"]
    zc = _make_fake_zc()

    class _RaisingResolver:
        def __init__(self, *a, **kw):
            pass

        async def async_request(self, zc, timeout=1500):
            raise RuntimeError("multicast disabled")

        def parsed_addresses(self):
            return []

    with patch("tinyagentos.services.mdns_publisher.AddressResolver", _RaisingResolver):
        result = await choose_hostname(zc, data_dir, own_ips)

    # Should fall back to taos.local. but NOT persist it
    assert result == "taos.local."
    hostname_file = data_dir / "mdns_hostname"
    assert not hostname_file.exists(), "Probe exception must not persist hostname"


@pytest.mark.asyncio
async def test_probe_exception_returns_default_and_persists_nothing(tmp_path, monkeypatch):
    """Probe exception (OSError) returns taos.local. and persists nothing."""
    data_dir = tmp_path
    own_ips = ["10.0.0.5"]
    zc = MagicMock()
    zc.zeroconf = MagicMock()

    class _OSErrorResolver:
        def __init__(self, *a, **kw):
            pass

        async def async_request(self, zc, timeout=1500):
            raise OSError("multicast disabled")

        def parsed_addresses(self):
            return []

    with patch("tinyagentos.services.mdns_publisher.AddressResolver", _OSErrorResolver):
        result = await choose_hostname(zc, data_dir, own_ips)

    assert result == "taos.local."
    assert not (data_dir / "mdns_hostname").exists()


@pytest.mark.asyncio
async def test_choose_hostname_invalid_persisted_content_ignored(tmp_path, monkeypatch):
    """(vi) Invalid persisted content is ignored and re-probed."""
    data_dir = tmp_path
    hostname_file = data_dir / "mdns_hostname"
    hostname_file.write_text("invalid-hostname\n")

    own_ips = ["192.168.1.42"]
    zc = _make_fake_zc()

    resolver_called = {"count": 0}

    class _TrackingResolver(_FakeAddressResolver):
        async def async_request(self, zc, timeout=1500):
            resolver_called["count"] += 1
            return await super().async_request(zc, timeout)

    resolver = _TrackingResolver(found=False, parsed_addrs=[])

    with patch("tinyagentos.services.mdns_publisher.AddressResolver", return_value=resolver):
        result = await choose_hostname(zc, data_dir, own_ips)

    assert result == "taos.local."
    assert resolver_called["count"] == 1  # Should have probed


@pytest.mark.asyncio
async def test_mdns_publisher_hostname_property(tmp_path, monkeypatch):
    """MdnsPublisher exposes hostname property (no trailing dot) and service_instance_name."""
    data_dir = tmp_path
    own_ips = ["192.168.1.42"]
    zc = _make_fake_zc()

    resolver = _FakeAddressResolver(found=False, parsed_addrs=[])

    with patch("tinyagentos.services.mdns_publisher.AddressResolver", return_value=resolver):
        with patch("tinyagentos.services.mdns_publisher.AsyncZeroconf", return_value=zc):
            with patch("tinyagentos.services.mdns_publisher._detect_primary_ipv4", return_value=own_ips):
                pub = MdnsPublisher(port=6969, data_dir=data_dir)
                await pub.start()

    assert pub.hostname == "taos.local"  # No trailing dot
    assert pub.service_instance_name is not None
    assert pub.service_instance_name.endswith("._http._tcp.local.")


@pytest.mark.asyncio
async def test_mdns_publisher_start_swallows_probe_exceptions(tmp_path, monkeypatch):
    """MdnsPublisher.start() completes even when probe raises."""
    data_dir = tmp_path
    own_ips = ["192.168.1.42"]
    zc = _make_fake_zc()

    class _RaisingResolver:
        def __init__(self, *a, **kw):
            pass

        async def async_request(self, zc, timeout=1500):
            raise RuntimeError("multicast disabled")

        def parsed_addresses(self):
            return []

    with patch("tinyagentos.services.mdns_publisher.AddressResolver", _RaisingResolver):
        with patch("tinyagentos.services.mdns_publisher.AsyncZeroconf", return_value=zc):
            with patch("tinyagentos.services.mdns_publisher._detect_primary_ipv4", return_value=own_ips):
                pub = MdnsPublisher(port=6969, data_dir=data_dir)
                await pub.start()  # Must not raise

    assert pub._active is True


# Test (v) TAOS_MDNS=0 -> publisher not started is tested at the app.py lifespan level
# See test_mdns_off_switch in tests/test_mdns_publisher.py


def test_hostname_regex_validation():
    """Test the hostname regex validation logic."""
    valid_names = [
        "taos.local.",
        "taos-abcd.local.",
        "taos-1234.local.",
        "taos-xy9z.local.",
    ]
    invalid_names = [
        "taos.local",  # missing trailing dot
        "taos-.local.",  # empty suffix
        "taos-abc.local.",  # 3 chars
        "taos-abcde.local.",  # 5 chars
        "taos-ABCD.local.",  # uppercase
        "taos-ab_cd.local.",  # underscore
        "taos.abcd.local.",  # wrong separator
        "other.local.",  # wrong prefix
    ]

    pattern = re.compile(r"^taos(-[a-z0-9]{4})?\.local\.$")

    for name in valid_names:
        assert pattern.match(name), f"Should match: {name}"

    for name in invalid_names:
        assert not pattern.match(name), f"Should not match: {name}"