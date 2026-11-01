#!/usr/bin/env python3
"""Tests for tinyagentos/orb/bridges/agent_buddy.py."""

import pytest


def test_agent_buddy_evaluate_two_replies():
    """Test agent_buddy.evaluate with two probe replies returns two targets in order."""
    from tinyagentos.orb.bridges import agent_buddy

    lan_orbs = []  # ignored by this adapter
    probe_replies = [
        {"hostname": "buddy-a", "address": "192.168.1.30"},
        {"hostname": "buddy-b", "address": "10.0.0.7"},
    ]

    result = agent_buddy.evaluate(lan_orbs, probe_replies)

    assert result.bridge_id == "agent-buddy"
    assert result.title == "Agent Buddy"
    assert result.installed is None
    assert len(result.targets) == 2
    # First target
    assert result.targets[0]["orb"] == "buddy-a"
    assert result.targets[0]["address"] == "192.168.1.30"
    assert (
        result.targets[0]["instruction"]
        == "In your agent-buddy folder run: npm run pair -- <code shown on the Orb> --host 192.168.1.30"
    )
    # Second target
    assert result.targets[1]["orb"] == "buddy-b"
    assert result.targets[1]["address"] == "10.0.0.7"
    assert (
        result.targets[1]["instruction"]
        == "In your agent-buddy folder run: npm run pair -- <code shown on the Orb> --host 10.0.0.7"
    )
    # Top-level instruction
    assert result.instruction == "Pair each Orb with the command shown, using the 8-digit code on its screen."


def test_agent_buddy_evaluate_lan_orb_no_replies():
    """Test agent_buddy.evaluate with LAN orb but no probe replies returns no targets."""
    from tinyagentos.orb.bridges import agent_buddy

    lan_orbs = [
        {"name": "orb-1", "app": "agent-buddy", "addresses": ["192.168.1.50"], "port": 80},
    ]
    probe_replies = []  # no probe replies

    result = agent_buddy.evaluate(lan_orbs, probe_replies)

    assert result.bridge_id == "agent-buddy"
    assert result.title == "Agent Buddy"
    assert result.installed is None
    assert len(result.targets) == 0
    assert result.instruction == "No Orb running Agent Buddy found on the LAN."


def test_agent_buddy_evaluate_skips_empty_address():
    """Test agent_buddy.evaluate skips a reply with empty address string."""
    from tinyagentos.orb.bridges import agent_buddy

    lan_orbs = []
    probe_replies = [
        {"hostname": "buddy-a", "address": "192.168.1.30"},
        {"hostname": "buddy-b", "address": ""},  # should be skipped
        {"hostname": "buddy-c", "address": "10.0.0.7"},
    ]

    result = agent_buddy.evaluate(lan_orbs, probe_replies)

    assert len(result.targets) == 2
    assert result.targets[0]["orb"] == "buddy-a"
    assert result.targets[0]["address"] == "192.168.1.30"
    assert result.targets[1]["orb"] == "buddy-c"
    assert result.targets[1]["address"] == "10.0.0.7"


def test_agent_buddy_installed_returns_none():
    """Test agent_buddy.installed returns None."""
    from tinyagentos.orb.bridges import agent_buddy

    assert agent_buddy.installed() is None


def test_agent_buddy_bridge_result_installed_is_none():
    """Test BridgeResult.installed is None when evaluate is called."""
    from tinyagentos.orb.bridges import agent_buddy

    result = agent_buddy.evaluate([], [])
    assert result.installed is None


def test_agent_buddy_registered_in_adapters():
    """Test agent_buddy module is discovered by adapters()."""
    from tinyagentos.orb.bridges import adapters

    module_names = [m.__name__.rsplit(".", 1)[1] for m in adapters()]
    assert "agent_buddy" in module_names


def test_agent_buddy_in_evaluate_all_no_error():
    """Test evaluate_all includes agent-buddy with no error key."""
    from tinyagentos.orb.bridges import evaluate_all

    results = evaluate_all([], [])
    agent_buddy_results = [r for r in results if r.get("bridge_id") == "agent-buddy"]
    assert len(agent_buddy_results) == 1
    assert "error" not in agent_buddy_results[0]


def test_agent_buddy_constants():
    """Test agent_buddy module constants."""
    from tinyagentos.orb.bridges import agent_buddy

    assert agent_buddy.BRIDGE_ID == "agent-buddy"
    assert agent_buddy.TITLE == "Agent Buddy"
    assert agent_buddy.APP_ID == "agent-buddy"