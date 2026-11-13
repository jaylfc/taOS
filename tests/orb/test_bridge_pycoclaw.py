#!/usr/bin/env python3
"""Tests for tinyagentos/orb/bridges/pycoclaw."""


def test_pycoclaw_evaluate_one_orb_with_addresses():
    """Test pycoclaw.evaluate with one pycoclaw orb with IPv4 addresses."""
    from tinyagentos.orb.bridges import pycoclaw

    lan_orbs = [
        {"name": "pyco-1", "app": "pycoclaw", "addresses": ["192.168.1.50"], "port": 80},
    ]
    agent_buddy = []

    result = pycoclaw.evaluate(lan_orbs, agent_buddy)

    assert result.bridge_id == "pycoclaw"
    assert result.title == "PycoClaw"
    assert result.installed is None
    assert len(result.targets) == 1
    assert result.targets[0]["orb"] == "pyco-1"
    assert result.targets[0]["address"] == "192.168.1.50"
    assert result.targets[0]["instruction"] == "In Scripto Studio, enter the device URL https://192.168.1.50/webrepl"
    assert result.instruction == "Enter the URL shown in Scripto Studio (scriptostudio.com/app)."


def test_pycoclaw_evaluate_orb_no_addresses_falls_back_to_name_local():
    """Test pycoclaw.evaluate with an orb that has no addresses falls back to name.local."""
    from tinyagentos.orb.bridges import pycoclaw

    lan_orbs = [
        {"name": "pyco", "app": "pycoclaw", "addresses": []},
    ]
    agent_buddy = []

    result = pycoclaw.evaluate(lan_orbs, agent_buddy)

    assert len(result.targets) == 1
    assert result.targets[0]["address"] == "pyco.local"
    assert result.targets[0]["instruction"] == "In Scripto Studio, enter the device URL https://pyco.local/webrepl"
    assert result.instruction == "Enter the URL shown in Scripto Studio (scriptostudio.com/app)."


def test_pycoclaw_evaluate_ignores_other_app_ids():
    """Test pycoclaw.evaluate ignores orbs with other app ids."""
    from tinyagentos.orb.bridges import pycoclaw

    lan_orbs = [
        {"name": "orb-1", "app": "esphome", "addresses": ["192.168.1.20"], "port": 80},
        {"name": "orb-2", "app": "mimiclaw", "addresses": ["192.168.1.21"], "port": 80},
    ]
    agent_buddy = []

    result = pycoclaw.evaluate(lan_orbs, agent_buddy)

    assert result.bridge_id == "pycoclaw"
    assert result.title == "PycoClaw"
    assert result.installed is None
    assert len(result.targets) == 0
    assert result.instruction == "No Orb running PycoClaw found on the LAN."


def test_pycoclaw_evaluate_empty_input():
    """Test pycoclaw.evaluate with empty input returns no targets and correct instruction."""
    from tinyagentos.orb.bridges import pycoclaw

    result = pycoclaw.evaluate([], [])

    assert result.bridge_id == "pycoclaw"
    assert result.title == "PycoClaw"
    assert result.installed is None
    assert len(result.targets) == 0
    assert result.instruction == "No Orb running PycoClaw found on the LAN."


def test_pycoclaw_installed_returns_none():
    """Test pycoclaw.installed returns None."""
    from tinyagentos.orb.bridges import pycoclaw

    assert pycoclaw.installed() is None


def test_pycoclaw_constants():
    """Test pycoclaw module constants."""
    from tinyagentos.orb.bridges import pycoclaw

    assert pycoclaw.BRIDGE_ID == "pycoclaw"
    assert pycoclaw.TITLE == "PycoClaw"
    assert pycoclaw.APP_ID == "pycoclaw"


def test_pycoclaw_in_adapters():
    """Test pycoclaw is discovered by adapters()."""
    from tinyagentos.orb import bridges

    module_names = [m.__name__.rsplit(".", 1)[1] for m in bridges.adapters()]
    assert "pycoclaw" in module_names


def test_evaluate_all_includes_pycoclaw_without_error():
    """Test evaluate_all returns a pycoclaw entry without an error key."""
    from tinyagentos.orb import bridges

    results = bridges.evaluate_all([], [])

    pycoclaw_entries = [r for r in results if r.get("bridge_id") == "pycoclaw"]
    assert len(pycoclaw_entries) == 1
    entry = pycoclaw_entries[0]
    assert "error" not in entry
    assert entry["title"] == "PycoClaw"
    assert entry["installed"] is None
    assert entry["instruction"] == "No Orb running PycoClaw found on the LAN."
