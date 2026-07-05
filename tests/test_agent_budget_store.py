"""Tests for the cross-process per-agent LLM budget store."""
from __future__ import annotations

import pytest

from tinyagentos.agent_budget_store import AgentBudgetStore, default_budget_path


def test_get_missing_returns_none(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    assert store.get("agent-a") is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_set_budget_rejects_non_finite(tmp_path, bad):
    # A NaN cap would make is_over_budget always False and silently disable
    # enforcement, so the store must refuse to persist a non-finite cap.
    store = AgentBudgetStore(tmp_path / "budgets.db")
    with pytest.raises(ValueError):
        store.set_budget("agent-a", bad)
    assert store.get("agent-a") is None
    assert store.is_over_budget("agent-a") is False


def test_set_budget_then_get(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.set_budget("agent-a", 10.0)
    rec = store.get("agent-a")
    assert rec == {"agent": "agent-a", "max_budget_usd": 10.0, "spend_usd": 0.0}


def test_set_budget_preserves_existing_spend(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.add_spend("agent-a", 3.0)
    store.set_budget("agent-a", 10.0)
    rec = store.get("agent-a")
    assert rec["spend_usd"] == 3.0
    assert rec["max_budget_usd"] == 10.0


def test_set_budget_none_clears_cap(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.set_budget("agent-a", 10.0)
    store.set_budget("agent-a", None)
    assert store.get("agent-a")["max_budget_usd"] is None


def test_add_spend_accumulates_and_returns_total(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    total1 = store.add_spend("agent-a", 1.5)
    assert total1 == 1.5
    total2 = store.add_spend("agent-a", 2.5)
    assert total2 == 4.0
    assert store.get("agent-a")["spend_usd"] == 4.0


def test_add_spend_creates_row_with_no_cap(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.add_spend("agent-a", 1.0)
    rec = store.get("agent-a")
    assert rec["max_budget_usd"] is None
    assert rec["spend_usd"] == 1.0


def test_add_spend_ignores_non_positive_delta(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    assert store.add_spend("agent-a", 0.0) == 0.0
    assert store.get("agent-a") is None
    assert store.add_spend("agent-a", -5.0) == 0.0
    assert store.get("agent-a") is None


def test_add_spend_ignores_non_positive_delta_after_existing_spend(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.add_spend("agent-a", 2.0)
    assert store.add_spend("agent-a", -1.0) == 2.0
    assert store.get("agent-a")["spend_usd"] == 2.0


def test_reset_spend_zeroes_but_keeps_cap(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.set_budget("agent-a", 10.0)
    store.add_spend("agent-a", 8.0)
    store.reset_spend("agent-a")
    rec = store.get("agent-a")
    assert rec["spend_usd"] == 0.0
    assert rec["max_budget_usd"] == 10.0


def test_reset_spend_noop_when_no_row(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.reset_spend("agent-a")  # must not raise
    assert store.get("agent-a") is None


def test_is_over_budget_true_when_spend_reaches_cap(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.set_budget("agent-a", 5.0)
    store.add_spend("agent-a", 5.0)
    assert store.is_over_budget("agent-a") is True


def test_is_over_budget_true_when_spend_exceeds_cap(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.set_budget("agent-a", 5.0)
    store.add_spend("agent-a", 6.0)
    assert store.is_over_budget("agent-a") is True


def test_is_over_budget_false_when_under_cap(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.set_budget("agent-a", 5.0)
    store.add_spend("agent-a", 1.0)
    assert store.is_over_budget("agent-a") is False


def test_is_over_budget_false_when_unlimited(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.add_spend("agent-a", 1_000_000.0)
    assert store.is_over_budget("agent-a") is False


def test_is_over_budget_false_when_no_row(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    assert store.is_over_budget("agent-a") is False


def test_list_returns_all_agents(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.set_budget("agent-a", 5.0)
    store.add_spend("agent-b", 2.0)
    agents = {rec["agent"] for rec in store.list()}
    assert agents == {"agent-a", "agent-b"}


def test_delete_agent(tmp_path):
    store = AgentBudgetStore(tmp_path / "budgets.db")
    store.set_budget("agent-a", 5.0)
    assert store.delete_agent("agent-a") is True
    assert store.get("agent-a") is None
    assert store.delete_agent("agent-a") is False


def test_cross_process_handle_sees_writes(tmp_path):
    """A second handle (e.g. the auth hook's reader) sees the writer's data."""
    path = tmp_path / "budgets.db"
    writer = AgentBudgetStore(path)
    writer.set_budget("agent-a", 10.0)
    writer.add_spend("agent-a", 4.0)

    reader = AgentBudgetStore(path)
    rec = reader.get("agent-a")
    assert rec["max_budget_usd"] == 10.0
    assert rec["spend_usd"] == 4.0


def test_default_budget_path(tmp_path):
    assert default_budget_path(tmp_path) == tmp_path / ".agent_budgets.db"
