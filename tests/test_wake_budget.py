from pathlib import Path

from tinyagentos.wake_budget import (
    _coerce_budget,
    _read_state,
    _write_state,
    _today,
    can_wake,
    get_consumption,
    get_fleet_wake_info,
    get_next_scheduled_wake,
    record_mention_wake,
    record_scheduled_wake,
    resolve_budget,
    resolve_mention_cap,
)


class _FakeConfig:
    def __init__(self, wake_budget=None, agents=None):
        self.wake_budget = wake_budget or {}
        self.agents = agents or []


class TestResolveBudget:
    def test_global_default(self):
        cfg = _FakeConfig({"global_default": 3, "per_agent": {}, "per_project": {}})
        assert resolve_budget("a1", None, cfg) == 3

    def test_per_agent_override(self):
        cfg = _FakeConfig({"global_default": 2, "per_agent": {"a1": 5}, "per_project": {}})
        assert resolve_budget("a1", None, cfg) == 5
        assert resolve_budget("a2", None, cfg) == 2

    def test_per_project_overrides_per_agent(self):
        cfg = _FakeConfig({
            "global_default": 2,
            "per_agent": {"a1": 5},
            "per_project": {"proj-1": 1},
        })
        assert resolve_budget("a1", "proj-1", cfg) == 1
        assert resolve_budget("a1", "proj-2", cfg) == 5

    def test_per_agent_overrides_global(self):
        cfg = _FakeConfig({"global_default": 2, "per_agent": {"a1": 0}, "per_project": {}})
        assert resolve_budget("a1", None, cfg) == 0

    def test_missing_config_defaults_to_two(self):
        cfg = _FakeConfig({})
        assert resolve_budget("a1", None, cfg) == 2

    def test_non_integer_global_defaults_to_two(self):
        cfg = _FakeConfig({"global_default": "bad", "per_agent": {}, "per_project": {}})
        assert resolve_budget("a1", None, cfg) == 2


class TestResolveMentionCap:
    def test_uncapped_by_default(self):
        cfg = _FakeConfig({"mention_cap": {}})
        assert resolve_mention_cap("a1", cfg) is None

    def test_per_agent_cap(self):
        cfg = _FakeConfig({"mention_cap": {"a1": 10}})
        assert resolve_mention_cap("a1", cfg) == 10
        assert resolve_mention_cap("a2", cfg) is None

    def test_null_is_uncapped(self):
        cfg = _FakeConfig({"mention_cap": {"a1": None}})
        assert resolve_mention_cap("a1", cfg) is None


class TestRecordAndConsume:
    def test_scheduled_wake_increments(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", "proj-1")
        c = get_consumption(data_dir, "a1", "proj-1")
        assert c["scheduled"] == 1
        assert c["mention"] == 0
        assert c["date"] == _today()

    def test_mention_wake_increments(self, tmp_path):
        data_dir = tmp_path
        record_mention_wake(data_dir, "a1")
        c = get_consumption(data_dir, "a1", None)
        assert c["mention"] == 1
        assert c["scheduled"] == 0

    def test_global_key_for_scheduled(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", None)
        c = get_consumption(data_dir, "a1", None)
        assert c["scheduled"] == 1

    def test_date_rollover(self, tmp_path):
        data_dir = tmp_path
        state_path = data_dir / "wake_budget.json"
        _write_state(state_path, {
            "daily": {"a1:proj-1": {"1999-01-01": 5}},
            "mentions": {"a1": {"1999-01-01": 3}},
        })
        c = get_consumption(data_dir, "a1", "proj-1")
        assert c["scheduled"] == 0
        assert c["mention"] == 0


class TestCanWake:
    def test_allowed_when_under_budget(self, tmp_path):
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        assert can_wake(tmp_path, "a1", "a1", None, cfg) is True

    def test_blocked_when_exhausted(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", None)
        record_scheduled_wake(data_dir, "a1", None)
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        assert can_wake(data_dir, "a1", "a1", None, cfg) is False

    def test_mention_always_passes(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", None)
        record_scheduled_wake(data_dir, "a1", None)
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        assert can_wake(data_dir, "a1", "a1", None, cfg, wake_type="mention") is True

    def test_zero_budget_blocks(self, tmp_path):
        cfg = _FakeConfig({"global_default": 0, "per_agent": {}, "per_project": {}})
        assert can_wake(tmp_path, "a1", "a1", None, cfg) is False


class TestNextScheduledWake:
    def test_returns_epoch_when_available(self, tmp_path):
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        nxt = get_next_scheduled_wake(tmp_path, "a1", None, cfg)
        assert nxt is not None
        assert nxt > 0

    def test_none_when_exhausted(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", None)
        record_scheduled_wake(data_dir, "a1", None)
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        assert get_next_scheduled_wake(data_dir, "a1", None, cfg) is None

    def test_none_when_zero_budget(self, tmp_path):
        cfg = _FakeConfig({"global_default": 0, "per_agent": {}, "per_project": {}})
        assert get_next_scheduled_wake(tmp_path, "a1", None, cfg) is None


class TestFleetWakeInfo:
    def test_returns_rows_for_active_agents(self, tmp_path):
        cfg = _FakeConfig(
            wake_budget={"global_default": 2, "per_agent": {}, "per_project": {}, "mention_cap": {}},
            agents=[
                {"id": "a1", "name": "agent-1", "status": "active"},
                {"id": "a2", "name": "agent-2", "status": "paused"},
            ],
        )
        rows = get_fleet_wake_info(tmp_path, cfg)
        assert len(rows) == 1
        assert rows[0]["agent_id"] == "a1"
        assert rows[0]["budget"] == 2
        assert rows[0]["consumed"] == 0
        assert rows[0]["remaining"] == 2
