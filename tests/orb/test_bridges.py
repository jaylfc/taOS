#!/usr/bin/env python3
"""Tests for tinyagentos/orb/bridges."""

import sys
from types import ModuleType, SimpleNamespace

import pytest


def test_orbs_running_filters_on_app_and_keeps_order():
    """Test orbs_running filters on app and keeps input order."""
    from tinyagentos.orb.bridges import orbs_running

    lan_orbs = [
        {"name": "orb-1", "app": "mimiclaw", "addresses": ["10.0.0.1"], "port": 80},
        {"name": "orb-2", "app": "esphome", "addresses": ["10.0.0.2"], "port": 80},
        {"name": "orb-3", "app": "mimiclaw", "addresses": ["10.0.0.3"], "port": 80},
        {"name": "orb-4", "app": None, "addresses": ["10.0.0.4"], "port": 80},
    ]

    result = orbs_running(lan_orbs, "mimiclaw")

    assert len(result) == 2
    assert result[0]["name"] == "orb-1"
    assert result[1]["name"] == "orb-3"


def test_orb_host_ipv4_first():
    """Test orb_host returns first IPv4 address."""
    from tinyagentos.orb.bridges import orb_host

    orb = {"addresses": ["10.0.0.5", "fe80::1"], "name": "orb-1"}
    assert orb_host(orb) == "10.0.0.5"


def test_orb_host_ipv6_wrapped():
    """Test orb_host returns IPv6 address wrapped in brackets."""
    from tinyagentos.orb.bridges import orb_host

    orb = {"addresses": ["fe80::1"], "name": "orb-1"}
    assert orb_host(orb) == "[fe80::1]"


def test_orb_host_fallback_to_name_local():
    """Test orb_host falls back to name.local when no addresses."""
    from tinyagentos.orb.bridges import orb_host

    orb = {"addresses": [], "name": "orb-1"}
    assert orb_host(orb) == "orb-1.local"


def test_orb_host_empty_name_returns_none():
    """Test orb_host returns None when name is empty string."""
    from tinyagentos.orb.bridges import orb_host

    orb = {"addresses": [], "name": ""}
    assert orb_host(orb) is None


def test_orb_host_no_addresses_key_no_name_returns_none():
    """Test orb_host returns None when dict has no addresses key and no name."""
    from tinyagentos.orb.bridges import orb_host

    orb = {}
    assert orb_host(orb) is None


def test_target_builds_dict():
    """Test target builds the expected dict shape."""
    from tinyagentos.orb.bridges import target

    result = target("orb-1", "192.168.1.20", "Connect to ws://...")
    assert result == {"orb": "orb-1", "address": "192.168.1.20", "instruction": "Connect to ws://..."}


def test_bridge_result_to_dict_round_trip():
    """Test BridgeResult.to_dict returns correct shape with targets as list of dicts."""
    from tinyagentos.orb.bridges import BridgeResult

    br = BridgeResult(
        bridge_id="test",
        title="Test Bridge",
        installed=True,
        targets=({"orb": "orb-1", "address": "1.2.3.4", "instruction": "connect"},),
        instruction="Top instruction",
    )

    d = br.to_dict()
    assert d["bridge_id"] == "test"
    assert d["title"] == "Test Bridge"
    assert d["installed"] is True
    assert d["targets"] == [{"orb": "orb-1", "address": "1.2.3.4", "instruction": "connect"}]
    assert d["instruction"] == "Top instruction"


def test_adapters_includes_mimiclaw_and_sorted():
    """Test adapters() includes mimiclaw module and all have BRIDGE_ID/TITLE/evaluate, sorted by BRIDGE_ID."""
    from tinyagentos.orb.bridges import adapters

    modules = adapters()
    assert len(modules) >= 1
    bridge_ids = [m.BRIDGE_ID for m in modules]
    assert "mimiclaw" in bridge_ids
    # Check sorted by BRIDGE_ID
    assert bridge_ids == sorted(bridge_ids)
    for m in modules:
        assert hasattr(m, "BRIDGE_ID")
        assert hasattr(m, "TITLE")
        assert hasattr(m, "evaluate")
        assert callable(m.evaluate)


def test_adapters_raises_type_error_for_missing_evaluate(monkeypatch):
    """Test adapters() raises TypeError for a module missing evaluate."""
    import pkgutil
    import importlib
    import tinyagentos.orb.bridges as bridges_mod

    # Create a fake module without evaluate
    fake_module = ModuleType("tinyagentos.orb.bridges.broken")
    fake_module.BRIDGE_ID = "broken"
    fake_module.TITLE = "Broken Bridge"
    # No evaluate attribute

    def fake_iter_modules(path):
        return [SimpleNamespace(name="broken")]

    def fake_import_module(name):
        return fake_module

    monkeypatch.setattr(pkgutil, "iter_modules", fake_iter_modules)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    with pytest.raises(TypeError, match="bridge adapter broken missing BRIDGE_ID/TITLE/evaluate"):
        bridges_mod.adapters()


def test_evaluate_all_monkeypatch_adapters(monkeypatch):
    """Test evaluate_all with fake adapters, one raising RuntimeError."""
    import tinyagentos.orb.bridges as bridges_mod

    # Create good adapter module
    good_module = ModuleType("tinyagentos.orb.bridges.good")
    good_module.BRIDGE_ID = "good"
    good_module.TITLE = "Good Bridge"
    good_result = bridges_mod.BridgeResult(
        bridge_id="good",
        title="Good Bridge",
        installed=False,
        targets=(bridges_mod.target("orb-1", "1.2.3.4", "connect"),),
        instruction="Good instruction",
    )

    def good_evaluate(lan_orbs, agent_buddy):
        return good_result

    good_module.evaluate = good_evaluate

    # Create bad adapter module
    bad_module = ModuleType("tinyagentos.orb.bridges.bad")
    bad_module.BRIDGE_ID = "bad"
    bad_module.TITLE = "Bad Bridge"

    def bad_evaluate(lan_orbs, agent_buddy):
        raise RuntimeError("adapter failed")

    bad_module.evaluate = bad_evaluate

    def fake_adapters():
        return [bad_module, good_module]  # unsorted, evaluate_all doesn't sort

    monkeypatch.setattr(bridges_mod, "adapters", fake_adapters)

    result = bridges_mod.evaluate_all([], [])

    assert len(result) == 2
    # Find good and bad results
    good_found = False
    bad_found = False
    for r in result:
        if r.get("bridge_id") == "good":
            good_found = True
            assert r["title"] == "Good Bridge"
            assert r["installed"] is False
            assert len(r["targets"]) == 1
            assert r["instruction"] == "Good instruction"
        elif r.get("bridge_id") == "bad":
            bad_found = True
            assert r["title"] == "Bad Bridge"
            assert r["error"] == "adapter_failed"
    assert good_found
    assert bad_found


def test_mimiclaw_evaluate_two_orbs_one_matching():
    """Test mimiclaw.evaluate with two LAN orbs, one matching."""
    from tinyagentos.orb.bridges import mimiclaw

    lan_orbs = [
        {"name": "orb-1", "app": "mimiclaw", "addresses": ["192.168.1.20"], "port": 80},
        {"name": "orb-2", "app": "esphome", "addresses": ["192.168.1.21"], "port": 80},
    ]
    agent_buddy = []

    result = mimiclaw.evaluate(lan_orbs, agent_buddy)

    assert result.bridge_id == "mimiclaw"
    assert result.title == "MimiClaw"
    assert result.installed is None
    assert len(result.targets) == 1
    assert result.targets[0]["orb"] == "orb-1"
    assert result.targets[0]["address"] == "192.168.1.20"
    assert result.targets[0]["instruction"] == "Connect a WebSocket client to ws://192.168.1.20:18789/"
    assert result.instruction == "Connect a WebSocket client to the URL shown for your Orb."


def test_mimiclaw_evaluate_empty_input():
    """Test mimiclaw.evaluate with empty input returns no targets and correct instruction."""
    from tinyagentos.orb.bridges import mimiclaw

    result = mimiclaw.evaluate([], [])

    assert result.bridge_id == "mimiclaw"
    assert result.title == "MimiClaw"
    assert result.installed is None
    assert len(result.targets) == 0
    assert result.instruction == "No Orb running MimiClaw found on the LAN."


def test_mimiclaw_installed_returns_none():
    """Test mimiclaw.installed returns None."""
    from tinyagentos.orb.bridges import mimiclaw

    assert mimiclaw.installed() is None


def test_mimiclaw_constants():
    """Test mimiclaw module constants."""
    from tinyagentos.orb.bridges import mimiclaw

    assert mimiclaw.BRIDGE_ID == "mimiclaw"
    assert mimiclaw.TITLE == "MimiClaw"
    assert mimiclaw.APP_ID == "mimiclaw"
    assert mimiclaw.WS_PORT == 18789