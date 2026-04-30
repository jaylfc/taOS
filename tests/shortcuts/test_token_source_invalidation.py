import subprocess
from unittest.mock import MagicMock
import pytest
from tinyagentos.shortcuts.token_source import (
    read_token_source,
    invalidate_agent_cache,
    _cache,
)

AGENT = "inv-agent"


def test_invalidate_clears_only_target_agent(monkeypatch):
    """invalidate_agent_cache(agent) removes that agent's entries but not others."""
    _cache.clear()
    call_count = {"a": 0, "b": 0}

    def fake_run(args, **kwargs):
        name = args[2]  # "taos-agent-<name>"
        if "inv-agent" in name:
            call_count["a"] += 1
            return MagicMock(stdout="token-a\n", returncode=0)
        call_count["b"] += 1
        return MagicMock(stdout="token-b\n", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    source = {"kind": "container_env", "var": "TOK"}
    read_token_source("inv-agent", source)
    read_token_source("other-agent", source)

    assert call_count["a"] == 1
    assert call_count["b"] == 1

    invalidate_agent_cache("inv-agent")

    read_token_source("inv-agent", source)
    assert call_count["a"] == 2  # new call

    read_token_source("other-agent", source)
    assert call_count["b"] == 1  # still cached


def test_invalidate_nonexistent_agent_is_noop():
    """invalidate_agent_cache on an unknown agent must not raise."""
    invalidate_agent_cache("ghost-agent")  # must not raise
