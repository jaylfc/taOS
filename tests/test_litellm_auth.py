"""Tests for tinyagentos.litellm_auth.user_api_key_auth, including the
per-agent budget hard-stop (agent governance Slice 2).

litellm is an optional "proxy" extra not installed in the default dev
environment (see pyproject.toml / CI), so every call into
``user_api_key_auth`` is wrapped to skip when litellm's proxy types are
unavailable, mirroring the pattern already used in test_litellm_callback.py.
"""
from __future__ import annotations

import pytest

from tinyagentos.agent_budget_store import AgentBudgetStore
from tinyagentos.litellm_keystore import LiteLLMKeyStore
import tinyagentos.litellm_auth as auth_mod


class _FakeRequest:
    """Minimal stand-in for the starlette Request used by _requested_model."""

    def __init__(self, body: dict | None = None):
        self._body = body or {}

    async def json(self):
        return self._body


async def _auth(request, api_key: str):
    try:
        return await auth_mod.user_api_key_auth(request, api_key)
    except ModuleNotFoundError:
        pytest.skip("litellm not installed")


@pytest.fixture(autouse=True)
def _reset_module_caches(monkeypatch):
    """Isolate the lazily-cached store handles between tests."""
    monkeypatch.setattr(auth_mod, "_store", None)
    monkeypatch.setattr(auth_mod, "_store_path", None)
    monkeypatch.setattr(auth_mod, "_budget_store_cache", None)
    monkeypatch.setattr(auth_mod, "_budget_store_cache_path", None)
    monkeypatch.delenv("TAOS_LITELLM_KEYSTORE", raising=False)
    monkeypatch.delenv("TAOS_AGENT_BUDGETS", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    yield


@pytest.mark.asyncio
async def test_master_key_bypasses_budget_check(tmp_path, monkeypatch):
    """The master-key admin passthrough must never consult the budget store."""
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-taos-master-123")
    # Point at a budget store that would reject any real agent lookup — the
    # master key must never even open it.
    budgets = AgentBudgetStore(tmp_path / "budgets.db")
    budgets.set_budget("some-agent", 0.0)
    budgets.add_spend("some-agent", 1.0)
    monkeypatch.setenv("TAOS_AGENT_BUDGETS", str(tmp_path / "budgets.db"))

    # Reaching this assertion at all proves the bypass: an over-budget agent
    # would have raised HTTPException(429) before returning. We assert a valid
    # auth object rather than the exact api_key, which litellm hashes.
    result = await _auth(_FakeRequest(), "sk-taos-master-123")
    assert result is not None


@pytest.mark.asyncio
async def test_agent_over_budget_is_rejected_with_429(tmp_path, monkeypatch):
    keystore = LiteLLMKeyStore(tmp_path / "keys.db")
    token = keystore.mint("agent-a", ["default"])
    monkeypatch.setenv("TAOS_LITELLM_KEYSTORE", str(tmp_path / "keys.db"))

    budgets = AgentBudgetStore(tmp_path / "budgets.db")
    budgets.set_budget("agent-a", 5.0)
    budgets.add_spend("agent-a", 5.0)
    monkeypatch.setenv("TAOS_AGENT_BUDGETS", str(tmp_path / "budgets.db"))

    from fastapi import HTTPException
    try:
        with pytest.raises(HTTPException) as exc_info:
            await _auth(_FakeRequest(), token)
    except ModuleNotFoundError:
        pytest.skip("litellm not installed")
    assert exc_info.value.status_code == 429
    assert "agent-a" in exc_info.value.detail


@pytest.mark.asyncio
async def test_agent_under_budget_passes(tmp_path, monkeypatch):
    keystore = LiteLLMKeyStore(tmp_path / "keys.db")
    token = keystore.mint("agent-a", ["default"])
    monkeypatch.setenv("TAOS_LITELLM_KEYSTORE", str(tmp_path / "keys.db"))

    budgets = AgentBudgetStore(tmp_path / "budgets.db")
    budgets.set_budget("agent-a", 5.0)
    budgets.add_spend("agent-a", 1.0)
    monkeypatch.setenv("TAOS_AGENT_BUDGETS", str(tmp_path / "budgets.db"))

    result = await _auth(_FakeRequest(), token)
    assert result.metadata["agent"] == "agent-a"


@pytest.mark.asyncio
async def test_agent_with_no_budget_row_passes(tmp_path, monkeypatch):
    keystore = LiteLLMKeyStore(tmp_path / "keys.db")
    token = keystore.mint("agent-a", ["default"])
    monkeypatch.setenv("TAOS_LITELLM_KEYSTORE", str(tmp_path / "keys.db"))

    # Budget store configured but has no row at all for this agent.
    AgentBudgetStore(tmp_path / "budgets.db")
    monkeypatch.setenv("TAOS_AGENT_BUDGETS", str(tmp_path / "budgets.db"))

    result = await _auth(_FakeRequest(), token)
    assert result.metadata["agent"] == "agent-a"


@pytest.mark.asyncio
async def test_budgets_not_configured_fails_open(tmp_path, monkeypatch):
    """No TAOS_AGENT_BUDGETS at all -> no budget check performed."""
    keystore = LiteLLMKeyStore(tmp_path / "keys.db")
    token = keystore.mint("agent-a", ["default"])
    monkeypatch.setenv("TAOS_LITELLM_KEYSTORE", str(tmp_path / "keys.db"))
    monkeypatch.delenv("TAOS_AGENT_BUDGETS", raising=False)

    result = await _auth(_FakeRequest(), token)
    assert result.metadata["agent"] == "agent-a"
