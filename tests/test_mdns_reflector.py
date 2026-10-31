"""Tests for the bundled mDNS reflector (screencast C2, tsk-iqtec6).

The reflector core is pure: it takes (ingress interface, source, payload)
and returns the list of (egress interface, payload) emits. No sockets are
opened here; the asyncio glue is exercised with fake transports.

The two reds the card requires are count-based and status-based on
purpose: "an announcement crossed the boundary" passes on a looping
implementation and cannot catch the flooding defect, and "status reports
something" cannot catch a silently dead bridge.
"""
from __future__ import annotations

import pytest

from tinyagentos.services.mdns_reflector import (
    MDNS_GROUP,
    MDNS_PORT,
    MdnsReflector,
    ReflectorCore,
    discovery_view,
)

# A minimal but well-formed mDNS response header (12 bytes) plus a body,
# enough for the reflector's "looks like DNS" floor.
ANNOUNCE = bytes.fromhex("000084000000000100000000") + b"\x0blivingroomtv\x00"
OTHER = bytes.fromhex("000084000000000100000000") + b"\x0abedroomtv\x00"

LAN_TV = ("192.168.1.50", MDNS_PORT)
CONTAINER_HOST = ("10.0.3.20", MDNS_PORT)


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _core(ifaces=("eth0", "br0", "wlan0"), clock=None, **kw) -> ReflectorCore:
    return ReflectorCore(
        list(ifaces),
        local_addresses={"192.168.1.10", "10.0.3.1", "192.168.2.10"},
        clock=clock or FakeClock(),
        **kw,
    )


# --- RED 1: loop prevention, built on the emitted packet count -----------


def test_reflected_packet_is_not_re_reflected():
    core = _core()
    emitted: list[tuple[str, bytes]] = []

    emitted += core.ingest("eth0", LAN_TV, ANNOUNCE)
    count_before = len(emitted)
    assert count_before == 2  # eth0 -> br0 and eth0 -> wlan0

    # The reflector hears its own forward on the egress interface (another
    # reflector or a looping switch hands it back, from a non-local source).
    egress_iface, payload = emitted[0]
    emitted += core.ingest(egress_iface, CONTAINER_HOST, payload)

    count_after = len(emitted)
    assert count_after == count_before, "packet count grew across reflect cycle"


def test_feedback_loop_settles_over_many_cycles():
    """Feed every emit back on its egress interface for several rounds: the
    total must stay at the first-hop fan-out, never grow."""
    core = _core()
    pending = core.ingest("eth0", LAN_TV, ANNOUNCE)
    total = len(pending)
    for _ in range(5):
        nxt: list[tuple[str, bytes]] = []
        for iface, payload in pending:
            nxt += core.ingest(iface, CONTAINER_HOST, payload)
        total += len(nxt)
        pending = nxt
    assert total == 2


def test_new_packet_is_still_forwarded_to_every_other_interface():
    core = _core()
    out = core.ingest("eth0", LAN_TV, ANNOUNCE)
    assert sorted(i for i, _ in out) == ["br0", "wlan0"]
    assert all(p == ANNOUNCE for _, p in out)
    out2 = core.ingest("br0", CONTAINER_HOST, OTHER)
    assert sorted(i for i, _ in out2) == ["eth0", "wlan0"]


def test_duplicate_from_same_source_is_deduped_within_ttl_then_allowed():
    clock = FakeClock()
    core = _core(clock=clock, dedup_ttl=1.0)
    assert len(core.ingest("eth0", LAN_TV, ANNOUNCE)) == 2
    assert core.ingest("eth0", LAN_TV, ANNOUNCE) == []
    clock.t += 1.5
    # Past the TTL a periodic re-announcement is legitimate traffic again.
    assert len(core.ingest("eth0", LAN_TV, ANNOUNCE)) == 2


def test_packet_from_a_local_address_is_never_reflected():
    core = _core()
    assert core.ingest("br0", ("10.0.3.1", MDNS_PORT), ANNOUNCE) == []
    assert core.stats()["dropped_local"] == 1


def test_runt_packet_is_dropped():
    core = _core()
    assert core.ingest("eth0", LAN_TV, b"\x00\x01") == []


def test_dedup_cache_is_bounded():
    core = _core(max_entries=8)
    for i in range(50):
        core.ingest("eth0", ("192.168.1.%d" % (i + 2), MDNS_PORT), ANNOUNCE + bytes([i]))
    assert core.cache_size() <= 8


# --- RED 2: liveness, broken bridge vs genuinely empty network ------------


def test_broken_bridge_is_distinguishable_from_empty_network():
    clock = FakeClock()

    # A healthy bridge on a network with no TVs: traffic crosses, no devices.
    healthy = _core(clock=clock)
    healthy.mark_running()
    healthy.ingest("eth0", LAN_TV, ANNOUNCE)
    empty_status = healthy.status()
    assert empty_status["state"] == "ok"

    # A broken bridge: packets arrive, every forward fails to send.
    broken = _core(clock=clock)
    broken.mark_running()
    for iface, _payload in broken.ingest("eth0", LAN_TV, ANNOUNCE):
        broken.note_send_failure(iface, OSError(101, "Network is unreachable"))
    broken_status = broken.status()
    assert broken_status["state"] == "unavailable"
    assert broken_status["reason"] == "send_failed"

    # The device list contract: same (empty) device list, different meaning.
    empty_view = discovery_view([], empty_status)
    broken_view = discovery_view([], broken_status)
    assert empty_view["state"] == "empty"
    assert broken_view["state"] == "discovery_unavailable"
    assert empty_view != broken_view


def test_single_interface_cannot_bridge():
    core = _core(ifaces=("eth0",))
    core.mark_running()
    s = core.status()
    assert s["state"] == "unavailable"
    assert s["reason"] == "single_interface"


def test_not_running_is_unavailable():
    s = _core().status()
    assert s["state"] == "unavailable"
    assert s["reason"] == "not_running"


def test_silent_bridge_goes_unavailable_after_window():
    clock = FakeClock()
    core = _core(clock=clock, silence_window=600.0)
    core.mark_running()
    assert core.status()["state"] == "ok"  # inside the start-up grace
    clock.t += 601
    s = core.status()
    assert s["state"] == "unavailable"
    assert s["reason"] == "no_traffic"
    core.ingest("eth0", LAN_TV, ANNOUNCE)
    assert core.status()["state"] == "ok"


def test_successful_forward_after_failure_recovers():
    clock = FakeClock()
    core = _core(clock=clock)
    core.mark_running()
    for iface, _ in core.ingest("eth0", LAN_TV, ANNOUNCE):
        core.note_send_failure(iface, OSError("boom"))
    assert core.status()["state"] == "unavailable"
    clock.t += 1
    for iface, _ in core.ingest("eth0", LAN_TV, OTHER):
        core.note_sent(iface)
    assert core.status()["state"] == "ok"


def test_discovery_view_with_devices_and_disabled_reflector():
    ok = {"state": "ok"}
    assert discovery_view([{"id": "tv"}], ok)["state"] == "devices"
    # Disabled reflector makes no claim either way (host networking installs).
    disabled = {"state": "disabled"}
    assert discovery_view([], disabled)["state"] == "empty"
    # Even with devices listed, a dead bridge is surfaced.
    v = discovery_view([{"id": "tv"}], {"state": "unavailable", "reason": "send_failed"})
    assert v["state"] == "discovery_unavailable"
    assert v["devices"] == [{"id": "tv"}]


# --- glue: fake transports, no real sockets ------------------------------


class _FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple]] = []
        self.closed = False

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_reflector_glue_forwards_to_group_and_reports_bind_failure():
    transports: dict[str, _FakeTransport] = {}

    async def opener(name: str, ip: str):
        if name == "wlan0":
            raise OSError(98, "Address already in use")
        t = _FakeTransport()
        transports[name] = t
        return t

    r = MdnsReflector(
        {"eth0": "192.168.1.10", "br0": "10.0.3.1", "wlan0": "192.168.2.10"},
        opener=opener,
    )
    await r.start()
    st = r.status()
    assert st["state"] == "ok"
    assert sorted(st["interfaces"]) == ["br0", "eth0"]
    assert st["bind_errors"] == {"wlan0": "[Errno 98] Address already in use"}

    r.on_datagram("eth0", ANNOUNCE, LAN_TV)
    assert transports["br0"].sent == [(ANNOUNCE, (MDNS_GROUP, MDNS_PORT))]
    assert transports["eth0"].sent == []
    # Our own forward heard back on br0 is not re-emitted.
    r.on_datagram("br0", ANNOUNCE, CONTAINER_HOST)
    assert transports["eth0"].sent == []

    await r.stop()
    assert all(t.closed for t in transports.values())
    assert r.status()["reason"] == "not_running"


@pytest.mark.asyncio
async def test_reflector_glue_all_binds_failed_is_unavailable():
    async def opener(name, ip):
        raise OSError(1, "Operation not permitted")

    r = MdnsReflector({"eth0": "192.168.1.10", "br0": "10.0.3.1"}, opener=opener)
    await r.start()
    s = r.status()
    assert s["state"] == "unavailable"
    assert s["reason"] == "bind_failed"


# --- route --------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_route_reports_disabled_when_not_configured(client):
    client._transport.app.state.mdns_reflector = None
    resp = await client.get("/api/cast/discovery/status")
    assert resp.status_code == 200
    assert resp.json()["state"] == "disabled"


@pytest.mark.asyncio
async def test_status_route_reports_enabled_reflector_that_failed_to_start(client):
    # Enabled in config but start() blew up in lifespan: that is a broken
    # bridge, not a disabled feature, so it must not read as "disabled".
    client._transport.app.state.mdns_reflector = None
    client._transport.app.state.mdns_reflector_start_error = "boom"
    try:
        resp = await client.get("/api/cast/discovery/status")
    finally:
        client._transport.app.state.mdns_reflector_start_error = None
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "unavailable"
    assert body["reason"] == "start_failed"


@pytest.mark.asyncio
async def test_status_route_reports_unavailable_bridge(client):
    async def opener(name, ip):
        raise OSError(1, "Operation not permitted")

    r = MdnsReflector({"eth0": "192.168.1.10", "br0": "10.0.3.1"}, opener=opener)
    await r.start()
    client._transport.app.state.mdns_reflector = r
    resp = await client.get("/api/cast/discovery/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "unavailable"
    assert body["reason"] == "bind_failed"


def test_config_reflector_is_off_by_default_and_round_trips(tmp_path):
    from tinyagentos.config import load_config, save_config

    path = tmp_path / "config.yaml"
    cfg = load_config(path)
    assert cfg.mdns_reflector.get("enabled") is False
    assert "mdns_reflector" not in cfg.to_dict()

    cfg.mdns_reflector = {"enabled": True, "interfaces": ["eth0", "br0"]}
    save_config(cfg, path)
    again = load_config(path)
    assert again.mdns_reflector == {"enabled": True, "interfaces": ["eth0", "br0"]}
