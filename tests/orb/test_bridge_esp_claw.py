#!/usr/bin/env python3
"""Tests for tinyagentos/orb/bridges/esp_claw.py."""

import pytest


def test_esp_claw_evaluate_one_orb_with_address():
    """Test esp_claw.evaluate with one orb matching esp-claw app id."""
    from tinyagentos.orb.bridges import esp_claw

    lan_orbs = [
        {"name": "esp-claw-1", "app": "esp-claw", "addresses": ["192.168.1.60"], "port": 80},
    ]
    agent_buddy = []

    result = esp_claw.evaluate(lan_orbs, agent_buddy)

    assert result.bridge_id == "esp-claw"
    assert result.title == "ESP-Claw"
    assert result.installed is None
    assert len(result.targets) == 1
    assert result.targets[0]["orb"] == "esp-claw-1"
    assert result.targets[0]["address"] == "192.168.1.60"
    assert result.targets[0]["instruction"] == "Open http://192.168.1.60/ in a browser to configure it"
    assert result.instruction == "Open the device web UI shown for your Orb."


def test_esp_claw_evaluate_underscore_app_id_does_not_match():
    """Test esp_claw.evaluate does not match app 'esp_claw' (underscore)."""
    from tinyagentos.orb.bridges import esp_claw

    lan_orbs = [
        {"name": "esp-claw-1", "app": "esp_claw", "addresses": ["192.168.1.60"], "port": 80},
    ]
    agent_buddy = []

    result = esp_claw.evaluate(lan_orbs, agent_buddy)

    assert result.bridge_id == "esp-claw"
    assert result.title == "ESP-Claw"
    assert result.installed is None
    assert len(result.targets) == 0
    assert result.instruction == "No Orb running ESP-Claw found on the LAN."


def test_esp_claw_installed_returns_none():
    """Test esp_claw.installed returns None."""
    from tinyagentos.orb.bridges import esp_claw

    assert esp_claw.installed() is None


def test_esp_claw_adapter_registered_and_evaluate_all():
    """Test esp_claw is in adapters() and evaluate_all includes it without error."""
    from tinyagentos.orb.bridges import adapters, evaluate_all

    module_names = [m.__name__.rsplit(".", 1)[1] for m in adapters()]
    assert "esp_claw" in module_names

    results = evaluate_all([], [])
    esp_claw_result = next((r for r in results if r.get("bridge_id") == "esp-claw"), None)
    assert esp_claw_result is not None
    assert "error" not in esp_claw_result
    assert esp_claw_result["bridge_id"] == "esp-claw"
    assert esp_claw_result["title"] == "ESP-Claw"
    assert esp_claw_result["installed"] is None
    assert esp_claw_result["targets"] == []
    assert esp_claw_result["instruction"] == "No Orb running ESP-Claw found on the LAN."


def test_esp_claw_constants():
    """Test esp_claw module constants."""
    from tinyagentos.orb.bridges import esp_claw

    assert esp_claw.BRIDGE_ID == "esp-claw"
    assert esp_claw.TITLE == "ESP-Claw"
    assert esp_claw.APP_ID == "esp-claw"