"""Tests for build_bootstrap_system_prompt in bridge_session."""
from __future__ import annotations

import pytest


def test_bootstrap_uses_assembled_prompt(monkeypatch):
    monkeypatch.setattr(
        "tinyagentos.prompt_assembly._load_agent_rules",
        lambda: "MEMORY_RULES",
    )
    agent = type("A", (), {
        "slug": "atlas", "soul_md": "SOUL",
        "agent_md": "", "memory_plugin": "taosmd",
    })()
    from tinyagentos.bridge_session import build_bootstrap_system_prompt
    out = build_bootstrap_system_prompt(agent)
    assert "MEMORY_RULES" in out
    assert "SOUL" in out


def test_bootstrap_accepts_dict_agent(monkeypatch):
    monkeypatch.setattr(
        "tinyagentos.prompt_assembly._load_agent_rules",
        lambda: "RULES_FOR_<your-agent-name>",
    )
    from tinyagentos.bridge_session import build_bootstrap_system_prompt
    out = build_bootstrap_system_prompt({
        "name": "atlas", "soul_md": "SOUL", "agent_md": "", "memory_plugin": "taosmd",
    })
    assert "RULES_FOR_atlas" in out
    assert "SOUL" in out
