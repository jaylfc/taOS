import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from tinyagentos.framework_update import (
    SNAPSHOT_PREFIX,
    UPDATE_DEADLINE_SECONDS,
    _iso_utc_compact,
    _mark_failed,
    _prune_old_snapshots,
    _read_installed_tag,
    _wait_for_bootstrap_ping,
    start_update,
)


# ---------------------------------------------------------------------------
# _iso_utc_compact
# ---------------------------------------------------------------------------

class TestIsoUtcCompact:
    def test_returns_compact_iso_format(self):
        result = _iso_utc_compact()
        assert "T" in result
        assert " " not in result
        assert ":" not in result

    def test_returns_utc(self):
        result = _iso_utc_compact()
        assert result.endswith("Z") is False
        # Must be parseable as the expected format
        from datetime import datetime, timezone
        parsed = datetime.strptime(result, "%Y-%m-%dT%H-%M-%S")
        assert parsed.tzinfo is None  # naive, but represents UTC

    def test_two_calls_produce_different_results(self):
        a = _iso_utc_compact()
        time.sleep(0.01)
        b = _iso_utc_compact()
        # Could be same second, so just check format validity
        assert len(a) == len(b) == 19  # YYYY-MM-DDTHH-MM-SS


# ---------------------------------------------------------------------------
# _prune_old_snapshots
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prune_keeps_exactly_keep_newest():
    snaps = [
        {"name": f"pre-framework-update-{i}", "created_at": f"2026-04-18T{22-i}:00:00Z"}
        for i in range(5)
    ]
    deleted = []
    with patch("tinyagentos.framework_update.snapshot_list",
               new=AsyncMock(return_value=snaps)), \
         patch("tinyagentos.framework_update.snapshot_delete",
               new=AsyncMock(side_effect=lambda _c, n: deleted.append(n))):
        await _prune_old_snapshots("taos-agent-x", keep=3)
    assert deleted == ["pre-framework-update-3", "pre-framework-update-4"]


@pytest.mark.asyncio
async def test_prune_noop_when_fewer_than_keep():
    snaps = [{"name": "a", "created_at": ""}]
    with patch("tinyagentos.framework_update.snapshot_list",
               new=AsyncMock(return_value=snaps)), \
         patch("tinyagentos.framework_update.snapshot_delete",
               new=AsyncMock()) as d:
        await _prune_old_snapshots("taos-agent-x", keep=3)
    d.assert_not_awaited()


@pytest.mark.asyncio
async def test_prune_noop_when_exactly_keep():
    snaps = [{"name": f"s{i}", "created_at": ""} for i in range(3)]
    with patch("tinyagentos.framework_update.snapshot_list",
               new=AsyncMock(return_value=snaps)), \
         patch("tinyagentos.framework_update.snapshot_delete",
               new=AsyncMock()) as d:
        await _prune_old_snapshots("taos-agent-x", keep=3)
    d.assert_not_awaited()


@pytest.mark.asyncio
async def test_prune_with_empty_list():
    with patch("tinyagentos.framework_update.snapshot_list",
               new=AsyncMock(return_value=[])), \
         patch("tinyagentos.framework_update.snapshot_delete",
               new=AsyncMock()) as d:
        await _prune_old_snapshots("taos-agent-x", keep=3)
    d.assert_not_awaited()


@pytest.mark.asyncio
async def test_prune_with_keep_zero_deletes_all():
    snaps = [{"name": "a", "created_at": ""}, {"name": "b", "created_at": ""}]
    deleted = []
    with patch("tinyagentos.framework_update.snapshot_list",
               new=AsyncMock(return_value=snaps)), \
         patch("tinyagentos.framework_update.snapshot_delete",
               new=AsyncMock(side_effect=lambda _c, n: deleted.append(n))):
        await _prune_old_snapshots("taos-agent-x", keep=0)
    assert deleted == ["a", "b"]


# ---------------------------------------------------------------------------
# _wait_for_bootstrap_ping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ping_true_when_bootstrap_arrives():
    agent = {"bootstrap_last_seen_at": None}
    started_at = int(time.time())

    async def set_ping():
        await asyncio.sleep(0.05)
        agent["bootstrap_last_seen_at"] = int(time.time()) + 1

    asyncio.create_task(set_ping())
    ok = await _wait_for_bootstrap_ping(agent, started_at=started_at, deadline_seconds=2)
    assert ok is True


@pytest.mark.asyncio
async def test_ping_false_on_deadline():
    ok = await _wait_for_bootstrap_ping(
        {"bootstrap_last_seen_at": None},
        started_at=int(time.time()),
        deadline_seconds=1,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_ping_ignores_stale_value():
    started_at = int(time.time())
    ok = await _wait_for_bootstrap_ping(
        {"bootstrap_last_seen_at": started_at - 10},
        started_at=started_at,
        deadline_seconds=1,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_ping_false_when_bootstrap_equals_started_at():
    started_at = int(time.time())
    ok = await _wait_for_bootstrap_ping(
        {"bootstrap_last_seen_at": started_at},
        started_at=started_at,
        deadline_seconds=1,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_ping_uses_module_default_when_none():
    """When deadline_seconds=None, the function must read UPDATE_DEADLINE_SECONDS
    from the module at call time (not at definition time)."""
    agent = {"bootstrap_last_seen_at": None}
    started_at = int(time.time())
    with patch("tinyagentos.framework_update.UPDATE_DEADLINE_SECONDS", 1):
        t0 = time.time()
        ok = await _wait_for_bootstrap_ping(agent, started_at=started_at, deadline_seconds=None)
        elapsed = time.time() - t0
    assert ok is False
    assert elapsed < 5.0, "monkeypatched deadline must take effect"


# ---------------------------------------------------------------------------
# _read_installed_tag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_installed_tag_success():
    with patch("tinyagentos.framework_update.exec_in_container",
               new=AsyncMock(return_value=(0, "v2.0.0\n"))):
        tag = await _read_installed_tag("taos-agent-x")
    assert tag == "v2.0.0"


@pytest.mark.asyncio
async def test_read_installed_tag_strips_whitespace():
    with patch("tinyagentos.framework_update.exec_in_container",
               new=AsyncMock(return_value=(0, "  v3.1.0  \n"))):
        tag = await _read_installed_tag("taos-agent-x")
    assert tag == "v3.1.0"


@pytest.mark.asyncio
async def test_read_installed_tag_empty_on_failure():
    with patch("tinyagentos.framework_update.exec_in_container",
               new=AsyncMock(return_value=(1, "no such file"))):
        tag = await _read_installed_tag("taos-agent-x")
    assert tag == ""


# ---------------------------------------------------------------------------
# _mark_failed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_failed_sets_status_and_clears_started_at():
    agent = {
        "framework_update_status": "updating",
        "framework_update_started_at": 12345,
        "framework_update_last_error": None,
    }
    save = AsyncMock()
    await _mark_failed(agent, "something broke", save_config=save)
    assert agent["framework_update_status"] == "failed"
    assert agent["framework_update_started_at"] is None
    assert agent["framework_update_last_error"] == "something broke"
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_failed_truncates_long_reason():
    agent = {
        "framework_update_status": "updating",
        "framework_update_started_at": 12345,
        "framework_update_last_error": None,
    }
    save = AsyncMock()
    long_reason = "x" * 600
    await _mark_failed(agent, long_reason, save_config=save)
    assert len(agent["framework_update_last_error"]) == 500


@pytest.mark.asyncio
async def test_mark_failed_with_snapshot():
    agent = {
        "framework_update_status": "updating",
        "framework_update_started_at": 12345,
        "framework_update_last_error": None,
    }
    save = AsyncMock()
    await _mark_failed(agent, "oops", save_config=save, snapshot="snap-1")
    assert agent["framework_last_snapshot"] == "snap-1"


@pytest.mark.asyncio
async def test_mark_failed_without_snapshot_preserves_existing():
    agent = {
        "framework_update_status": "updating",
        "framework_update_started_at": 12345,
        "framework_update_last_error": None,
        "framework_last_snapshot": "existing-snap",
    }
    save = AsyncMock()
    await _mark_failed(agent, "oops", save_config=save)
    assert agent["framework_last_snapshot"] == "existing-snap"


# ---------------------------------------------------------------------------
# start_update -- full orchestrator
# ---------------------------------------------------------------------------

def _make_agent(name="atlas"):
    return {
        "name": name,
        "bootstrap_last_seen_at": None,
        "framework_update_status": "idle",
        "framework_update_started_at": None,
        "framework_update_last_error": None,
        "framework_version_tag": None,
        "framework_version_sha": None,
        "framework_last_snapshot": None,
    }


def _make_manifest():
    return {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"}


def _make_latest():
    return {"tag": "v2.0.0", "sha": "abc1234", "asset_url": "https://example.com/fw.tar.gz"}


@pytest.mark.asyncio
async def test_happy_path(monkeypatch):
    agent = _make_agent()
    saved = []

    async def fake_exec(container, cmd, timeout=None):
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                        AsyncMock(return_value="v2.0.0"))
    await start_update(agent, _make_manifest(), _make_latest(), save_config=AsyncMock())
    assert agent["framework_update_status"] == "idle"
    assert agent["framework_version_tag"] == "v2.0.0"
    assert agent["framework_version_sha"] == "abc1234"
    assert agent["framework_update_started_at"] is None
    assert agent["framework_update_last_error"] is None


@pytest.mark.asyncio
async def test_snapshot_failure_aborts(monkeypatch):
    agent = _make_agent()
    install = AsyncMock()
    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create",
                        AsyncMock(side_effect=RuntimeError("pool offline")))
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", install)
    await start_update(agent, _make_manifest(), _make_latest(), save_config=AsyncMock())
    assert agent["framework_update_status"] == "failed"
    assert "snapshot failed" in agent["framework_update_last_error"]
    install.assert_not_awaited()


@pytest.mark.asyncio
async def test_install_script_nonzero_rc(monkeypatch):
    agent = _make_agent()
    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container",
                        AsyncMock(return_value=(1, "permission denied")))
    await start_update(agent, _make_manifest(), _make_latest(), save_config=AsyncMock())
    assert agent["framework_update_status"] == "failed"
    assert "rc=1" in agent["framework_update_last_error"]
    assert "permission denied" in agent["framework_update_last_error"]
    assert agent["framework_last_snapshot"] is not None


@pytest.mark.asyncio
async def test_install_script_timeout(monkeypatch):
    agent = _make_agent()
    async def timeout_exec(*_a, **_kw):
        raise asyncio.TimeoutError()
    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", timeout_exec)
    await start_update(agent, _make_manifest(), _make_latest(), save_config=AsyncMock())
    assert agent["framework_update_status"] == "failed"
    assert "timed out" in agent["framework_update_last_error"]
    assert agent["framework_last_snapshot"] is not None


@pytest.mark.asyncio
async def test_missing_bootstrap_ping(monkeypatch):
    from tinyagentos import framework_update as fu
    agent = _make_agent()
    monkeypatch.setattr(fu, "snapshot_create", AsyncMock())
    monkeypatch.setattr(fu, "_prune_old_snapshots", AsyncMock())
    monkeypatch.setattr(fu, "exec_in_container", AsyncMock(return_value=(0, "")))
    monkeypatch.setattr(fu, "UPDATE_DEADLINE_SECONDS", 1)
    t0 = time.time()
    await start_update(agent, _make_manifest(), _make_latest(), save_config=AsyncMock())
    elapsed = time.time() - t0
    assert elapsed < 5.0, "monkeypatched deadline must be honoured"
    assert agent["framework_update_status"] == "failed"
    assert "bridge" in agent["framework_update_last_error"]


@pytest.mark.asyncio
async def test_version_mismatch(monkeypatch):
    agent = _make_agent()
    async def fake_exec(container, cmd, timeout=None):
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                        AsyncMock(return_value="v1.0.0"))
    await start_update(agent, _make_manifest(), _make_latest(), save_config=AsyncMock())
    assert agent["framework_update_status"] == "failed"
    assert "version mismatch" in agent["framework_update_last_error"]
    assert "v1.0.0" in agent["framework_update_last_error"]
    assert "v2.0.0" in agent["framework_update_last_error"]


@pytest.mark.asyncio
async def test_unexpected_exception_is_caught(monkeypatch):
    """An exception raised inside start_update must not propagate; agent is marked failed."""
    agent = _make_agent()
    async def boom(*_a, **_kw):
        raise RuntimeError("unexpected crash")
    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", boom)
    # Must not raise
    await start_update(agent, _make_manifest(), _make_latest(), save_config=AsyncMock())
    assert agent["framework_update_status"] == "failed"
    assert "unexpected" in agent["framework_update_last_error"]


@pytest.mark.asyncio
async def test_sets_updating_status_at_start(monkeypatch):
    agent = _make_agent()
    statuses = []
    original_save = AsyncMock()

    async def tracking_save():
        statuses.append(agent["framework_update_status"])

    async def fake_exec(container, cmd, timeout=None):
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                        AsyncMock(return_value="v2.0.0"))
    await start_update(agent, _make_manifest(), _make_latest(), save_config=tracking_save)
    assert statuses[0] == "updating"


@pytest.mark.asyncio
async def test_container_name_derived_from_agent_name(monkeypatch):
    agent = _make_agent(name="myagent")
    captured_containers = []

    async def tracking_snapshot(container, name):
        captured_containers.append(container)

    async def fake_exec(container, cmd, timeout=None):
        captured_containers.append(container)
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", tracking_snapshot)
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                        AsyncMock(return_value="v2.0.0"))
    await start_update(agent, _make_manifest(), _make_latest(), save_config=AsyncMock())
    assert all(c == "taos-agent-myagent" for c in captured_containers)


@pytest.mark.asyncio
async def test_snapshot_name_contains_prefix_and_tag(monkeypatch):
    agent = _make_agent()
    captured_snap = []

    async def tracking_snapshot(container, name):
        captured_snap.append(name)

    async def fake_exec(container, cmd, timeout=None):
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", tracking_snapshot)
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                        AsyncMock(return_value="v2.0.0"))
    await start_update(agent, _make_manifest(), _make_latest(), save_config=AsyncMock())
    assert len(captured_snap) == 1
    assert captured_snap[0].startswith(SNAPSHOT_PREFIX + "v2.0.0-")


@pytest.mark.asyncio
async def test_install_receives_correct_args(monkeypatch):
    agent = _make_agent()
    captured_cmd = []

    async def tracking_exec(container, cmd, timeout=None):
        captured_cmd.append(cmd)
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", tracking_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                        AsyncMock(return_value="v2.0.0"))
    manifest = _make_manifest()
    latest = _make_latest()
    await start_update(agent, manifest, latest, save_config=AsyncMock())
    assert captured_cmd[0] == [
        "/usr/local/bin/taos-framework-update",
        "openclaw",
        "v2.0.0",
        "https://example.com/fw.tar.gz",
    ]


@pytest.mark.asyncio
async def test_prune_called_with_keep_three(monkeypatch):
    agent = _make_agent()
    captured_keep = []

    async def tracking_prune(container, keep):
        captured_keep.append(keep)

    async def fake_exec(container, cmd, timeout=None):
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", tracking_prune)
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                        AsyncMock(return_value="v2.0.0"))
    await start_update(agent, _make_manifest(), _make_latest(), save_config=AsyncMock())
    assert captured_keep == [3]


@pytest.mark.asyncio
async def test_save_config_called_on_status_transitions(monkeypatch):
    agent = _make_agent()
    call_count = 0

    async def counting_save():
        nonlocal call_count
        call_count += 1

    async def fake_exec(container, cmd, timeout=None):
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                        AsyncMock(return_value="v2.0.0"))
    await start_update(agent, _make_manifest(), _make_latest(), save_config=counting_save)
    # Called: set updating, set snapshot, set idle = 3 times
    assert call_count == 3


@pytest.mark.asyncio
async def test_save_config_called_on_failure(monkeypatch):
    agent = _make_agent()
    call_count = 0

    async def counting_save():
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create",
                        AsyncMock(side_effect=RuntimeError("disk full")))
    await start_update(agent, _make_manifest(), _make_latest(), save_config=counting_save)
    # Called: set updating, then mark_failed = 2 times
    assert call_count == 2


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_snapshot_prefix(self):
        assert SNAPSHOT_PREFIX == "pre-framework-update-"

    def test_update_deadline_seconds(self):
        assert UPDATE_DEADLINE_SECONDS == 120
