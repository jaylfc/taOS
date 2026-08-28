import os
from pathlib import Path

import pytest

from tinyagentos.wake_budget import (
    _coerce_budget,
    _read_state,
    _write_state,
    _today,
    can_wake,
    get_consumption,
    get_fleet_wake_info,
    get_next_scheduled_wake,
    record_scheduled_wake,
    resolve_budget,
    WakeBudgetStateError,
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

    def test_non_integer_global_default_raises(self):
        cfg = _FakeConfig({"global_default": "bad", "per_agent": {}, "per_project": {}})
        with pytest.raises(ValueError):
            resolve_budget("a1", None, cfg)


class TestRecordAndConsume:
    def test_scheduled_wake_increments(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", "proj-1")
        c = get_consumption(data_dir, "a1", "proj-1")
        assert c["scheduled"] == 1
        assert c["date"] == _today()

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
        })
        c = get_consumption(data_dir, "a1", "proj-1")
        assert c["scheduled"] == 0
        assert c["date"] == _today()

    def test_prunes_past_dates(self, tmp_path):
        data_dir = tmp_path
        state_path = data_dir / "wake_budget.json"
        _write_state(state_path, {
            "daily": {
                "a1:proj-1": {"1999-01-01": 5, _today(): 1},
            },
        })
        record_scheduled_wake(data_dir, "a1", "proj-1")
        state = _read_state(state_path)
        agent_daily = state["daily"]["a1:proj-1"]
        assert "1999-01-01" not in agent_daily
        assert agent_daily[_today()] == 2


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


@pytest.mark.asyncio
class TestFleetWakeInfo:
    async def test_returns_rows_for_running_agents(self, tmp_path):
        cfg = _FakeConfig(
            wake_budget={"global_default": 2, "per_agent": {}, "per_project": {}},
            agents=[
                {"id": "a1", "name": "agent-1", "status": "running"},
                {"id": "a2", "name": "agent-2", "status": "paused"},
            ],
        )
        rows = await get_fleet_wake_info(tmp_path, cfg)
        assert len(rows) == 1
        assert rows[0]["agent_id"] == "a1"
        assert rows[0]["budget"] == 2
        assert rows[0]["consumed"] == 0
        assert rows[0]["remaining"] == 2


class TestDamagedState:
    def test_absent_file_is_fresh_state(self, tmp_path):
        """An absent wake_budget.json is a fresh state: _read_state returns an
        empty daily dict (no 'mentions' key) and can_wake returns True."""
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        assert can_wake(tmp_path, "a1", "a1", None, cfg) is True
        state = _read_state(tmp_path / "wake_budget.json")
        assert state == {"daily": {}}

    def test_damaged_state_fails_closed(self, tmp_path):
        """A damaged (present but unreadable/unparseable) wake_budget.json must
        fail closed: can_wake returns False instead of silently restoring a
        full budget. A healthy file with room still returns True as control."""
        data_dir = tmp_path
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        path = data_dir / "wake_budget.json"

        # Control: healthy file with room consumed still returns True.
        _write_state(path, {"daily": {"a1:global": {_today(): 1}}})
        assert can_wake(data_dir, "a1", "a1", None, cfg) is True

        # Control: _read_state succeeds on a healthy file.
        assert _read_state(path) == {"daily": {"a1:global": {_today(): 1}}}

        # Damaged: zeroed file (null bytes) -> UnicodeDecodeError on read_text.
        path.write_bytes(b"\x00\x01\x02\x00")
        with pytest.raises(WakeBudgetStateError):
            _read_state(path)
        assert can_wake(data_dir, "a1", "a1", None, cfg) is False

        # Damaged: truncated JSON -> JSONDecodeError on json.loads.
        path.write_text("{")
        with pytest.raises(WakeBudgetStateError):
            _read_state(path)
        assert can_wake(data_dir, "a1", "a1", None, cfg) is False

        # Damaged: non-dict root (valid JSON but wrong shape).
        path.write_text("[1, 2, 3]")
        with pytest.raises(WakeBudgetStateError):
            _read_state(path)
        assert can_wake(data_dir, "a1", "a1", None, cfg) is False

        # Damaged: unreadable file (chmod 000) -> PermissionError on read_text.
        # Root bypasses file permissions, so skip that sub-assertion as root.
        path.write_text("{}")
        os.chmod(path, 0o000)
        try:
            if os.geteuid() != 0:
                with pytest.raises(WakeBudgetStateError):
                    _read_state(path)
                assert can_wake(data_dir, "a1", "a1", None, cfg) is False
        finally:
            os.chmod(path, 0o644)


def _today() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
