#!/usr/bin/env python3
"""Tests for tinyagentos/orb/bridges/esphome."""

import pytest


def test_esphome_installed_and_target(monkeypatch):
    """Test installed() True via monkeypatch and target instruction exact."""
    from tinyagentos.orb.bridges import esphome

    monkeypatch.setattr(esphome.shutil, "which", lambda x: "/usr/bin/esphome")
    lan_orbs = [
        {"name": "orb-1", "app": "esphome", "addresses": ["192.168.1.40"], "port": 80},
    ]
    agent_buddy = []

    assert esphome.installed() is True

    result = esphome.evaluate(lan_orbs, agent_buddy)

    assert result.bridge_id == "esphome"
    assert result.title == "ESPHome"
    assert result.installed is True
    assert len(result.targets) == 1
    assert result.targets[0]["orb"] == "orb-1"
    assert result.targets[0]["address"] == "192.168.1.40"
    assert (
        result.targets[0]["instruction"]
        == "esphome logs <your-config>.yaml --device 192.168.1.40"
    )
    assert result.instruction == "Run the command shown with your ESPHome config file."


def test_esphome_not_installed_target_still_present(monkeypatch):
    """Test installed() False: target still present, append to instruction."""
    from tinyagentos.orb.bridges import esphome

    monkeypatch.setattr(esphome.shutil, "which", lambda x: None)
    lan_orbs = [
        {"name": "orb-1", "app": "esphome", "addresses": ["192.168.1.40"], "port": 80},
    ]
    agent_buddy = []

    assert esphome.installed() is False

    result = esphome.evaluate(lan_orbs, agent_buddy)

    assert result.installed is False
    assert len(result.targets) == 1
    assert (
        result.targets[0]["instruction"]
        == "esphome logs <your-config>.yaml --device 192.168.1.40"
    )
    assert result.instruction.endswith(
        "Install ESPHome first: pip install esphome."
    )
    assert result.instruction == (
        "Run the command shown with your ESPHome config file."
        " Install ESPHome first: pip install esphome."
    )


def test_esphome_ignores_mimiclaw_orb(monkeypatch):
    """Test an orb with app 'mimiclaw' is ignored."""
    from tinyagentos.orb.bridges import esphome

    monkeypatch.setattr(esphome.shutil, "which", lambda x: "/usr/bin/esphome")
    lan_orbs = [
        {"name": "orb-1", "app": "mimiclaw", "addresses": ["192.168.1.40"], "port": 80},
    ]
    agent_buddy = []

    result = esphome.evaluate(lan_orbs, agent_buddy)

    assert len(result.targets) == 0
    assert result.instruction == "No Orb running ESPHome found on the LAN."


def test_esphome_ipv6_address(monkeypatch):
    """Test IPv6-only orb address is wrapped in brackets."""
    from tinyagentos.orb.bridges import esphome

    monkeypatch.setattr(esphome.shutil, "which", lambda x: "/usr/bin/esphome")
    lan_orbs = [
        {"name": "orb-1", "app": "esphome", "addresses": ["fe80::2"], "port": 80},
    ]
    agent_buddy = []

    result = esphome.evaluate(lan_orbs, agent_buddy)

    assert len(result.targets) == 1
    assert result.targets[0]["address"] == "[fe80::2]"
    assert (
        result.targets[0]["instruction"]
        == "esphome logs <your-config>.yaml --device [fe80::2]"
    )


def test_esphome_empty_input(monkeypatch):
    """Test empty input returns no targets and correct instruction."""
    from tinyagentos.orb.bridges import esphome

    monkeypatch.setattr(esphome.shutil, "which", lambda x: "/usr/bin/esphome")

    result = esphome.evaluate([], [])

    assert result.bridge_id == "esphome"
    assert result.title == "ESPHome"
    assert result.installed is True
    assert len(result.targets) == 0
    assert result.instruction == "No Orb running ESPHome found on the LAN."


def test_esphome_constants():
    """Test esphome module constants."""
    from tinyagentos.orb.bridges import esphome

    assert esphome.BRIDGE_ID == "esphome"
    assert esphome.TITLE == "ESPHome"
    assert esphome.APP_ID == "esphome"


def test_esphome_in_adapters_and_evaluate_all(monkeypatch):
    """Test esphome is auto-discovered by adapters() and evaluate_all has no error."""
    import tinyagentos.orb.bridges as bridges_mod
    from tinyagentos.orb.bridges import esphome

    monkeypatch.setattr(esphome.shutil, "which", lambda x: None)

    modules = bridges_mod.adapters()
    names = [m.__name__.rsplit(".", 1)[1] for m in modules]
    assert "esphome" in names

    all_results = bridges_mod.evaluate_all([], [])
    esphome_result = [r for r in all_results if r["bridge_id"] == "esphome"]
    assert len(esphome_result) == 1
    assert "error" not in esphome_result[0]
