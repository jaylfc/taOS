#!/usr/bin/env python3
"""Tests for tinyagentos/orb/bridges wireclaw adapter."""

import sys
from unittest.mock import patch

import pytest


def test_wireclaw_installed_true_when_nats_available():
    """Test wireclaw.installed returns True when nats is available."""
    from tinyagentos.orb.bridges import wireclaw

    with patch("shutil.which", return_value="/usr/bin/nats"):
        assert wireclaw.installed() is True


def test_wireclaw_installed_false_when_nats_unavailable():
    """Test wireclaw.installed returns False when nats is not available."""
    from tinyagentos.orb.bridges import wireclaw

    with patch("shutil.which", return_value=None):
        assert wireclaw.installed() is False


def test_wireclaw_evaluate_with_wireclaw_orb():
    """Test wireclaw.evaluate with wireclaw orb present."""
    from tinyagentos.orb.bridges import wireclaw

    with patch("shutil.which", return_value="/usr/bin/nats"):
        lan_orbs = [
            {"name": "192.168.1.70", "app": "wireclaw", "addresses": ["192.168.1.70"], "port": 80},
        ]
        agent_buddy = []

        result = wireclaw.evaluate(lan_orbs, agent_buddy)

    assert result.bridge_id == "wireclaw"
    assert result.title == "WireClaw"
    assert result.installed is True
    assert len(result.targets) == 1
    assert result.targets[0]["orb"] == "192.168.1.70"
    assert result.targets[0]["address"] == "192.168.1.70"
    assert result.targets[0]["instruction"] == "Open http://192.168.1.70/ to configure it"
    assert result.instruction == "Open the device page shown. For wc.sh, set WIRECLAW_NATS_URL to your NATS server (it runs on your LAN, not on the Orb)."


def test_wireclaw_evaluate_without_wireclaw_orb():
    """Test wireclaw.evaluate when no wireclaw orb present."""
    from tinyagentos.orb.bridges import wireclaw

    with patch("shutil.which", return_value="/usr/bin/nats"):
        lan_orbs = [
            {"name": "orb-1", "app": "esphome", "addresses": ["192.168.1.71"], "port": 80},
        ]
        agent_buddy = []

        result = wireclaw.evaluate(lan_orbs, agent_buddy)

    assert result.bridge_id == "wireclaw"
    assert result.title == "WireClaw"
    assert result.installed is True
    assert len(result.targets) == 0
    assert result.instruction == "No Orb running WireClaw found on the LAN."


def test_wireclaw_evaluate_with_wireclaw_orb_but_nats_unavailable():
    """Test wireclaw.evaluate with wireclaw orb present but nats unavailable."""
    from tinyagentos.orb.bridges import wireclaw

    with patch("shutil.which", return_value=None):
        lan_orbs = [
            {"name": "192.168.1.70", "app": "wireclaw", "addresses": ["192.168.1.70"], "port": 80},
        ]
        agent_buddy = []

        result = wireclaw.evaluate(lan_orbs, agent_buddy)

    assert result.bridge_id == "wireclaw"
    assert result.title == "WireClaw"
    assert result.installed is False
    assert len(result.targets) == 1
    assert result.targets[0]["orb"] == "192.168.1.70"
    assert result.targets[0]["address"] == "192.168.1.70"
    assert result.targets[0]["instruction"] == "Open http://192.168.1.70/ to configure it"
    assert "WIRECLAW_NATS_URL" in result.instruction


def test_wireclaw_evaluate_no_nats_url_in_result():
    """Test wireclaw result does not contain nats:// URL."""
    from tinyagentos.orb.bridges import wireclaw

    with patch("shutil.which", return_value="/usr/bin/nats"):
        lan_orbs = [
            {"name": "192.168.1.70", "app": "wireclaw", "addresses": ["192.168.1.70"], "port": 80},
        ]
        agent_buddy = []

        result = wireclaw.evaluate(lan_orbs, agent_buddy)

        result_dict = result.to_dict()
        # Check that no string anywhere in the result dict contains "nats://192.168.1.70"
        def check_dict(d):
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, str) and "nats://192.168.1.70" in v:
                        return False
                    elif isinstance(v, dict):
                        if not check_dict(v):
                            return False
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict):
                                if not check_dict(item):
                                    return False
            return True

        assert check_dict(result_dict)


def test_wireclaw_top_level_instruction_contains_wireclaw_nats_url():
    """Test top-level instruction contains WIRECLAW_NATS_URL."""
    from tinyagentos.orb.bridges import wireclaw

    with patch("shutil.which", return_value="/usr/bin/nats"):
        lan_orbs = [
            {"name": "192.168.1.70", "app": "wireclaw", "addresses": ["192.168.1.70"], "port": 80},
        ]
        agent_buddy = []

        result = wireclaw.evaluate(lan_orbs, agent_buddy)

        assert "WIRECLAW_NATS_URL" in result.instruction


def test_wireclaw_in_adapters_list():
    """Test wireclaw is in adapters() list."""
    from tinyagentos.orb.bridges import adapters

    module_names = [m.__name__.rsplit('.', 1)[1] for m in adapters()]
    assert "wireclaw" in module_names


def test_wireclaw_evaluate_all_includes_wireclaw():
    """Test evaluate_all includes wireclaw entry with no error key."""
    from tinyagentos.orb.bridges import evaluate_all

    with patch("shutil.which", return_value="/usr/bin/nats"):
        lan_orbs = [
            {"name": "192.168.1.70", "app": "wireclaw", "addresses": ["192.168.1.70"], "port": 80},
        ]
        agent_buddy = []

        results = evaluate_all(lan_orbs, agent_buddy)

        wireclaw_result = None
        for r in results:
            if r.get("bridge_id") == "wireclaw":
                wireclaw_result = r
                break

        assert wireclaw_result is not None
        assert "error" not in wireclaw_result
