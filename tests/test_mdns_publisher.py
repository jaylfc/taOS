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


# ======================================================================
# Discovery service (#119): _tinyagentos._tcp with url/fingerprint/tier
# ======================================================================


def _registered_infos(zc_instance):
    return [c.args[0] for c in zc_instance.async_register_service.await_args_list]


def _props_of(info):
    """ServiceInfo properties as a plain str->str dict, however stored."""
    out = {}
    for key, value in info.properties.items():
        k = key.decode() if isinstance(key, bytes) else key
        v = value.decode() if isinstance(value, bytes) else value
        out[k] = v
    return out


@pytest.mark.asyncio
async def test_start_registers_discovery_service_with_txt(fake_zc, tmp_path):
    zc_instance, _ = fake_zc
    pub = MdnsPublisher(
        data_dir=tmp_path, port=6969, fingerprint="f" * 64, tier="x86-cuda-12gb"
    )

    await pub.start()

    infos = _registered_infos(zc_instance)
    assert len(infos) == 2
    discovery = [i for i in infos if i.type == mp.DISCOVERY_SERVICE_TYPE]
    assert len(discovery) == 1
    info = discovery[0]
    assert info.port == 6969
    assert info.server == "taos.local."
    props = _props_of(info)
    assert props["fingerprint"] == "f" * 64
    assert props["tier"] == "x86-cuda-12gb"
    assert props["url"] == "http://taos.local:6969"


@pytest.mark.asyncio
async def test_discovery_url_override_and_tier_optional(fake_zc, tmp_path):
    zc_instance, _ = fake_zc
    pub = MdnsPublisher(
        data_dir=tmp_path,
        port=6969,
        fingerprint="f" * 64,
        controller_url="http://192.168.1.42:6969",
    )

    await pub.start()

    discovery = [
        i for i in _registered_infos(zc_instance)
        if i.type == mp.DISCOVERY_SERVICE_TYPE
    ]
    props = _props_of(discovery[0])
    assert props["url"] == "http://192.168.1.42:6969"
    assert "tier" not in props


@pytest.mark.asyncio
async def test_discovery_tier_omitted_when_advertise_tier_false(
    fake_zc, tmp_path
):
    """advertise_tier=False keeps the tier out of the TXT records (it
    reveals the hardware profile to anyone on the LAN) while the rest of
    the discovery record is unchanged."""
    zc_instance, _ = fake_zc
    pub = MdnsPublisher(
        data_dir=tmp_path,
        port=6969,
        fingerprint="f" * 64,
        tier="x86-cuda-12gb",
        advertise_tier=False,
    )

    await pub.start()

    discovery = [
        i for i in _registered_infos(zc_instance)
        if i.type == mp.DISCOVERY_SERVICE_TYPE
    ]
    assert len(discovery) == 1
    props = _props_of(discovery[0])
    assert props["fingerprint"] == "f" * 64
    assert props["url"] == "http://taos.local:6969"
    assert "tier" not in props


@pytest.mark.asyncio
async def test_no_fingerprint_registers_only_http(fake_zc, tmp_path):
    zc_instance, _ = fake_zc
    pub = MdnsPublisher(data_dir=tmp_path, port=6969)

    await pub.start()

    assert zc_instance.async_register_service.await_count == 1


@pytest.mark.asyncio
async def test_stop_unregisters_both_services_before_final_close(
    fake_zc, tmp_path
):
    zc_instance, _ = fake_zc
    pub = MdnsPublisher(data_dir=tmp_path, port=6969, fingerprint="f" * 64)
    await pub.start()

    await pub.stop()

    assert zc_instance.async_unregister_service.await_count == 2
    names = [c[0] for c in zc_instance.mock_calls]
    last_close = max(i for i, n in enumerate(names) if n == "async_close")
    last_unreg = max(
        i for i, n in enumerate(names) if n == "async_unregister_service"
    )
    assert last_unreg < last_close


@pytest.mark.asyncio
async def test_discovery_register_failure_keeps_http_publish(
    monkeypatch, tmp_path
):
    """The discovery service is an add-on: if its registration fails the
    _http publish (and the publisher's active state) must survive."""
    monkeypatch.setattr(mp, "_detect_primary_ipv4", lambda: ["192.168.1.42"])
    monkeypatch.setattr(
        mp, "AddressResolver",
        lambda name: _FakeAddressResolver(found=False, parsed_addrs=[]),
    )
    zc_instance = MagicMock()
    zc_instance.async_close = AsyncMock()
    zc_instance.async_unregister_service = AsyncMock()

    async def _register(info, **kwargs):
        if info.type == mp.DISCOVERY_SERVICE_TYPE:
            raise RuntimeError("name conflict")

    zc_instance.async_register_service = AsyncMock(side_effect=_register)
    monkeypatch.setattr(mp, "AsyncZeroconf", MagicMock(return_value=zc_instance))

    pub = MdnsPublisher(data_dir=tmp_path, port=6969, fingerprint="f" * 64)
    await pub.start()  # must not raise

    assert pub.active is True
    await pub.stop()
    assert zc_instance.async_unregister_service.await_count == 1
