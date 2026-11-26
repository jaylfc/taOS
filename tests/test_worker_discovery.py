"""Tests for tinyagentos.worker.discovery (mDNS controller scan, #119)."""
from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

from tinyagentos.worker import discovery as disc
from tinyagentos.worker.discovery import (
    ControllerCandidate,
    choose_candidate,
    discover_controllers,
    parse_controller_info,
)

FP_A = "a" * 64
FP_B = "b" * 64


def fake_info(
    *,
    name: str = "taOS._tinyagentos._tcp.local.",
    type_: str = disc.DISCOVERY_SERVICE_TYPE,
    server: str = "taos.local.",
    port: int = 6969,
    properties: dict | None = None,
    addresses: list[str] | None = None,
):
    """A ServiceInfo stand-in with the attributes the parser reads."""
    addrs = addresses if addresses is not None else ["192.168.1.10"]
    return SimpleNamespace(
        type=type_,
        name=name,
        server=server,
        port=port,
        properties=properties if properties is not None else {},
        addresses=[socket.inet_aton(ip) for ip in addrs],
        parsed_addresses=lambda: list(addrs),
    )


def full_props(**overrides):
    props = {
        b"url": b"http://taos.local:6969",
        b"fingerprint": FP_A.encode(),
        b"tier": b"x86-cuda-12gb",
    }
    props.update(overrides)
    return props


# ======================================================================
# parse_controller_info
# ======================================================================


class TestParse:
    def test_full_txt_bytes_properties(self):
        cand = parse_controller_info(fake_info(properties=full_props()))
        assert cand is not None
        assert cand.url == "http://taos.local:6969"
        assert cand.fingerprint == FP_A
        assert cand.tier == "x86-cuda-12gb"
        assert cand.port == 6969

    def test_str_properties_accepted(self):
        cand = parse_controller_info(
            fake_info(
                properties={
                    "url": "http://taos.local:6969",
                    "fingerprint": FP_A,
                    "tier": "arm-npu-16gb",
                }
            )
        )
        assert cand is not None
        assert cand.fingerprint == FP_A
        assert cand.tier == "arm-npu-16gb"

    def test_missing_fingerprint_is_foreign(self):
        props = full_props()
        del props[b"fingerprint"]
        assert parse_controller_info(fake_info(properties=props)) is None

    def test_empty_fingerprint_is_foreign(self):
        props = full_props()
        props[b"fingerprint"] = b""
        assert parse_controller_info(fake_info(properties=props)) is None

    def test_wrong_service_type_ignored(self):
        info = fake_info(type_="_http._tcp.local.", properties=full_props())
        assert parse_controller_info(info) is None

    def test_missing_url_constructed_from_addresses(self):
        props = full_props()
        del props[b"url"]
        cand = parse_controller_info(fake_info(properties=props))
        assert cand is not None
        assert cand.url == "http://192.168.1.10:6969"

    def test_missing_tier_is_none_not_fatal(self):
        props = full_props()
        del props[b"tier"]
        cand = parse_controller_info(fake_info(properties=props))
        assert cand is not None
        assert cand.tier is None


# ======================================================================
# discover_controllers (fake zeroconf layer)
# ======================================================================


class FakeBrowser:
    """Stands in for AsyncServiceBrowser: fires add_service immediately.

    Enforces the real zeroconf contract: the browser must be given the
    underlying Zeroconf instance (AsyncZeroconf.zeroconf), NOT the
    AsyncZeroconf wrapper -- the real AsyncServiceBrowser reaches for
    zc.cache, which the wrapper does not expose, and raises
    AttributeError before browsing anything.
    """

    instances: list = []
    names: list[str] = []

    def __init__(self, zc, service_type, listener=None):
        assert zc is FakeAsyncZeroconf.inner, (
            "AsyncServiceBrowser must receive the underlying Zeroconf "
            "instance (AsyncZeroconf.zeroconf), not the AsyncZeroconf "
            "wrapper"
        )
        self.service_type = service_type
        FakeBrowser.instances.append(self)
        for name in FakeBrowser.names:
            listener.add_service(zc, service_type, name)

    async def async_cancel(self):
        return None


class FakeAsyncZeroconf:
    infos: dict = {}
    closed = False
    inner = None

    def __init__(self):
        self.zeroconf = FakeAsyncZeroconf.inner

    async def async_get_service_info(self, service_type, name, timeout=None):
        return FakeAsyncZeroconf.infos.get(name)

    async def async_close(self):
        FakeAsyncZeroconf.closed = True


@pytest.fixture
def fake_zc_layer(monkeypatch):
    FakeBrowser.instances = []
    FakeBrowser.names = []
    FakeAsyncZeroconf.infos = {}
    FakeAsyncZeroconf.closed = False
    FakeAsyncZeroconf.inner = object()
    monkeypatch.setattr(disc, "AsyncServiceBrowser", FakeBrowser)
    monkeypatch.setattr(disc, "AsyncZeroconf", FakeAsyncZeroconf)
    return FakeAsyncZeroconf


@pytest.mark.asyncio
async def test_discover_returns_parsed_candidates(fake_zc_layer):
    name = "taOS._tinyagentos._tcp.local."
    FakeBrowser.names = [name]
    FakeAsyncZeroconf.infos = {name: fake_info(properties=full_props())}

    found = await discover_controllers(timeout=0.05)

    assert len(found) == 1
    assert found[0].fingerprint == FP_A
    assert FakeBrowser.instances[0].service_type == disc.DISCOVERY_SERVICE_TYPE
    assert FakeAsyncZeroconf.closed is True


@pytest.mark.asyncio
async def test_discover_dedupes_by_fingerprint(fake_zc_layer):
    names = ["taOS._tinyagentos._tcp.local.", "taOS-2._tinyagentos._tcp.local."]
    FakeBrowser.names = names
    FakeAsyncZeroconf.infos = {
        n: fake_info(name=n, properties=full_props()) for n in names
    }

    found = await discover_controllers(timeout=0.05)

    assert len(found) == 1


@pytest.mark.asyncio
async def test_discover_skips_unparseable_services(fake_zc_layer):
    name = "taOS._tinyagentos._tcp.local."
    FakeBrowser.names = [name]
    FakeAsyncZeroconf.infos = {name: fake_info(properties={b"url": b"x"})}

    assert await discover_controllers(timeout=0.05) == []


@pytest.mark.asyncio
async def test_discover_no_services_returns_empty(fake_zc_layer):
    assert await discover_controllers(timeout=0.05) == []


@pytest.mark.asyncio
async def test_discover_browser_failure_still_closes_zeroconf(
    fake_zc_layer, monkeypatch
):
    """If browser construction raises after AsyncZeroconf exists, the
    instance must still be closed -- the early return must not leak it."""

    class _BoomBrowser:
        def __init__(self, *a, **k):
            raise RuntimeError("no browser")

    monkeypatch.setattr(disc, "AsyncServiceBrowser", _BoomBrowser)
    assert await disc.discover_controllers(timeout=0) == []
    assert FakeAsyncZeroconf.closed is True


@pytest.mark.asyncio
async def test_discover_zeroconf_failure_returns_empty(monkeypatch):
    def _boom():
        raise RuntimeError("multicast unavailable")

    monkeypatch.setattr(disc, "AsyncZeroconf", _boom)
    assert await discover_controllers(timeout=0.05) == []


# ======================================================================
# choose_candidate
# ======================================================================


def cand(fp: str, url: str = "http://taos.local:6969") -> ControllerCandidate:
    return ControllerCandidate(
        name="taOS", url=url, fingerprint=fp, tier=None, host="taos.local", port=6969
    )


class TestChoose:
    def test_expect_fingerprint_match(self):
        chosen = choose_candidate(
            [cand(FP_A), cand(FP_B)], expect_fingerprint=FP_B
        )
        assert chosen is not None and chosen.fingerprint == FP_B

    def test_expect_fingerprint_miss_never_prompts(self):
        def _no_input(prompt=""):
            raise AssertionError("must not prompt")

        assert (
            choose_candidate(
                [cand(FP_A)], expect_fingerprint=FP_B, input_fn=_no_input
            )
            is None
        )

    def test_single_candidate_auto_picked(self):
        assert choose_candidate([cand(FP_A)]) is not None

    def test_multiple_candidates_prompt_by_number(self):
        chosen = choose_candidate(
            [cand(FP_A), cand(FP_B, "http://taos2.local:6969")],
            input_fn=lambda prompt="": "2",
        )
        assert chosen is not None and chosen.fingerprint == FP_B

    def test_pick_list_shows_short_fingerprint_next_to_controller(self):
        lines: list[str] = []
        chosen = choose_candidate(
            [cand(FP_A), cand(FP_B, "http://taos2.local:6969")],
            input_fn=lambda prompt="": "1",
            print_fn=lines.append,
        )
        assert chosen is not None and chosen.fingerprint == FP_A
        url_lines = [ln for ln in lines if "http://taos.local:6969" in ln]
        assert url_lines, lines
        # The short form (first 8 hex chars) sits on the controller's own
        # line, comparable with what the controller's Cluster page shows.
        assert FP_A[:8] in url_lines[0]
        # The full fingerprint is not dumped into the pick list.
        assert all(FP_A not in ln and FP_B not in ln for ln in lines)

    def test_prompt_eof_returns_none(self):
        def _eof(prompt=""):
            raise EOFError

        assert (
            choose_candidate([cand(FP_A), cand(FP_B)], input_fn=_eof) is None
        )

    def test_prompt_invalid_returns_none(self):
        assert (
            choose_candidate(
                [cand(FP_A), cand(FP_B)], input_fn=lambda prompt="": "9"
            )
            is None
        )

    def test_no_candidates_returns_none(self):
        assert choose_candidate([]) is None


# ======================================================================
# CLI
# ======================================================================


class TestCli:
    def test_pick_prints_url_then_fingerprint(self, monkeypatch, capsys):
        async def _scan(timeout=0.0):
            return [cand(FP_A)]

        monkeypatch.setattr(disc, "discover_controllers", _scan)
        rc = disc.main(["--pick"])
        assert rc == 0
        lines = capsys.readouterr().out.splitlines()
        # install-worker.sh captures line 1 as the controller URL and
        # line 2 as the fingerprint it passes to pair --expect-fingerprint,
        # so the picked controller's identity gets verified at pairing.
        assert lines == ["http://taos.local:6969", FP_A]


def test_pick_cli_stdout_is_only_url_and_fingerprint(monkeypatch, capsys):
    """--pick is captured by install-worker.sh: stdout must carry only
    the chosen URL and fingerprint, one per line. The selection prompt
    belongs on stderr -- builtin input() would glue it onto the URL
    line in stdout."""
    import io

    async def _fake_discover(timeout=5.0):
        return [
            cand(FP_A),
            cand(FP_B, "http://taos2.local:6969"),
        ]

    monkeypatch.setattr(disc, "discover_controllers", _fake_discover)
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))
    rc = disc.main(["--pick"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == f"{'http://taos.local:6969'}\n{FP_A}\n"
