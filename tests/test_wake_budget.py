import os
from pathlib import Path

import pytest

from tinyagentos.wake_budget import (
    _agent_daily_keys,
    _coerce_budget,
    _read_state,
    _write_state,
    _today,
    can_wake,
    get_agent_consumption,
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
        assert can_wake(tmp_path, "a1", "a1", "proj-1", cfg) is True

    def test_blocked_when_exhausted(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", "proj-1")
        record_scheduled_wake(data_dir, "a1", "proj-1")
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        assert can_wake(data_dir, "a1", "a1", "proj-1", cfg) is False

    def test_zero_budget_blocks(self, tmp_path):
        cfg = _FakeConfig({"global_default": 0, "per_agent": {}, "per_project": {}})
        assert can_wake(tmp_path, "a1", "a1", "proj-1", cfg) is False


class TestNextScheduledWake:
    def test_returns_epoch_when_available(self, tmp_path):
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        nxt = get_next_scheduled_wake(tmp_path, "a1", "proj-1", cfg)
        assert nxt is not None
        assert nxt > 0

    def test_none_when_exhausted(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", "proj-1")
        record_scheduled_wake(data_dir, "a1", "proj-1")
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        assert get_next_scheduled_wake(data_dir, "a1", "proj-1", cfg) is None

    def test_none_when_zero_budget(self, tmp_path):
        cfg = _FakeConfig({"global_default": 0, "per_agent": {}, "per_project": {}})
        assert get_next_scheduled_wake(tmp_path, "a1", "proj-1", cfg) is None


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

    async def test_damaged_state_degrades_row_not_fleet(self, tmp_path):
        """A damaged wake_budget.json must degrade affected rows, not raise
        WakeBudgetStateError and take out the whole fleet report."""
        state_path = tmp_path / "wake_budget.json"
        state_path.write_bytes(b"\x00\x01\x02\x00")
        cfg = _FakeConfig(
            wake_budget={"global_default": 2, "per_agent": {}, "per_project": {}},
            agents=[
                {"id": "a1", "name": "agent-1", "status": "running"},
                {"id": "a2", "name": "agent-2", "status": "running"},
            ],
        )
        rows = await get_fleet_wake_info(tmp_path, cfg)
        assert len(rows) == 2
        for row in rows:
            assert row["next_wake_epoch"] is None
            assert row["remaining"] == 0
            assert row["consumed"] == 0
            assert row["state"] == "damaged"

    @pytest.mark.parametrize(
        "text",
        ['{"daily": []}', '{"daily": {"a1:global": [1, 2]}}', '{"daily": {"a1:global": 3}}'],
    )
    async def test_misshaped_daily_degrades_row_not_fleet(self, tmp_path, text):
        """Valid JSON whose nested ``daily`` shape is wrong must be a damaged row,
        not an AttributeError escaping get_fleet_wake_info. Before _read_state
        validated the nested shape, ``{"daily": []}`` passed the root check and
        ``.items()`` raised in get_agent_consumption -- the fleet handler only
        catches WakeBudgetStateError, so the whole report failed."""
        (tmp_path / "wake_budget.json").write_text(text)
        cfg = _FakeConfig(
            wake_budget={"global_default": 2, "per_agent": {}, "per_project": {}},
            agents=[{"id": "a1", "name": "agent-1", "status": "running"}],
        )
        rows = await get_fleet_wake_info(tmp_path, cfg)
        assert len(rows) == 1
        assert rows[0]["state"] == "damaged"
        assert rows[0]["consumed"] == 0
        assert rows[0]["remaining"] == 0
        with pytest.raises(WakeBudgetStateError):
            _read_state(tmp_path / "wake_budget.json")

    async def test_partial_read_preserves_consumption_marks_damaged(self, tmp_path, monkeypatch):
        """Defect 3 (tsk-oenmo2 mutating test): the grandparent #2669 code put
        ``get_consumption`` and ``get_next_scheduled_wake`` inside a single
        try block, so any error in the second read discarded the first read's
        consumption and reported the row as ``consumed:0, remaining:budget``
        (a full row masquerading as a damaged one). The split try/except
        preserves the first successful read and must additionally carry an
        explicit ``state: 'damaged'`` marker (LEAD RULING) so the fleet UI
        can distinguish a working-half/working-half row from a genuinely
        exhausted agent (working-half-masks-broken-half).

        Setup: a healthy state with one wake consumed today. Patch
        ``get_next_scheduled_wake`` to raise WakeBudgetStateError so only the
        second read fails.

        On grandparent code: both calls share one try/except, the row would
        be ``consumed:0, remaining:budget`` -- this test REDS.
        On current code: the first read succeeds, the second's exception is
        caught separately, the row carries the real consumption plus the
        damaged marker -- this test GREENS.
        """
        data_dir = tmp_path
        state_path = data_dir / "wake_budget.json"
        _write_state(state_path, {
            "daily": {"a1:global": {_today(): 1}},
        })
        monkeypatch.setattr(
            "tinyagentos.wake_budget.get_next_scheduled_wake",
            lambda *a, **kw: (_ for _ in ()).throw(WakeBudgetStateError("simulated second-read failure")),
        )
        cfg = _FakeConfig(
            wake_budget={"global_default": 2, "per_agent": {}, "per_project": {}},
            agents=[{"id": "a1", "name": "agent-1", "status": "running"}],
        )
        rows = await get_fleet_wake_info(data_dir, cfg)
        assert len(rows) == 1
        row = rows[0]
        assert row["consumed"] == 1
        assert row["remaining"] == 1
        assert row["next_wake_epoch"] is None
        assert row["state"] == "damaged"


class TestPerAgentConsumption:
    def test_sums_all_project_keys_for_agent(self, tmp_path):
        _write_state(tmp_path / "wake_budget.json", {
            "daily": {
                "a1:proj-x": {_today(): 1},
                "a1:proj-y": {_today(): 2},
                "a2:proj-x": {_today(): 3},
            }
        })
        c = get_agent_consumption(tmp_path, "a1")
        assert c["scheduled"] == 3
        assert c["date"] == _today()

    def test_zero_when_no_project_keys(self, tmp_path):
        _write_state(tmp_path / "wake_budget.json", {
            "daily": {
                "a1:proj-x": {"1999-01-01": 5},
            }
        })
        c = get_agent_consumption(tmp_path, "a1")
        assert c["scheduled"] == 0
        assert c["date"] == _today()

    def test_missing_file_is_zero(self, tmp_path):
        c = get_agent_consumption(tmp_path, "a1")
        assert c["scheduled"] == 0
        assert c["date"] == _today()


class TestPerAgentCanWake:
    def test_sums_across_projects(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", "proj-x")
        record_scheduled_wake(data_dir, "a1", "proj-y")
        cfg = _FakeConfig({"global_default": 3, "per_agent": {}, "per_project": {}})
        assert can_wake(data_dir, "a1", "a1", "proj-x", cfg) is True

    def test_blocks_when_agent_total_exhausted_across_projects(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", "proj-x")
        record_scheduled_wake(data_dir, "a1", "proj-y")
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        assert can_wake(data_dir, "a1", "a1", "proj-x", cfg) is False

    def test_per_agent_override_applies_to_total(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", "proj-x")
        record_scheduled_wake(data_dir, "a1", "proj-y")
        cfg = _FakeConfig({
            "global_default": 10,
            "per_agent": {"a1": 2},
            "per_project": {},
        })
        assert can_wake(data_dir, "a1", "a1", "proj-x", cfg) is False


class TestPerAgentNextScheduledWake:
    def test_uses_agent_total_consumption(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", "proj-x")
        cfg = _FakeConfig({"global_default": 3, "per_agent": {}, "per_project": {}})
        nxt = get_next_scheduled_wake(data_dir, "a1", "proj-x", cfg)
        assert nxt is not None

    def test_none_when_agent_total_exhausted(self, tmp_path):
        data_dir = tmp_path
        record_scheduled_wake(data_dir, "a1", "proj-x")
        record_scheduled_wake(data_dir, "a1", "proj-y")
        cfg = _FakeConfig({"global_default": 2, "per_agent": {}, "per_project": {}})
        assert get_next_scheduled_wake(data_dir, "a1", "proj-x", cfg) is None


@pytest.mark.asyncio
class TestPerAgentFleetInfo:
    async def test_sums_across_projects(self, tmp_path):
        _write_state(tmp_path / "wake_budget.json", {
            "daily": {
                "a1:proj-x": {_today(): 1},
                "a1:proj-y": {_today(): 2},
            }
        })
        cfg = _FakeConfig(
            wake_budget={"global_default": 5, "per_agent": {}, "per_project": {}},
            agents=[{"id": "a1", "name": "agent-1", "status": "running"}],
        )
        rows = await get_fleet_wake_info(tmp_path, cfg)
        assert len(rows) == 1
        assert rows[0]["agent_id"] == "a1"
        assert rows[0]["consumed"] == 3
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
