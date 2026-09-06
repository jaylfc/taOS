import asyncio
import pytest
import time
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_prune_old_snapshots_keeps_three_newest():
    from tinyagentos.framework_update import _prune_old_snapshots
    snaps = [
        {"name": f"pre-framework-update-{i}", "created_at": f"2026/04/18 {22-i}:00 UTC"}
        for i in range(5)
    ]
    deleted = []
    with patch("tinyagentos.framework_update.snapshot_list",
               new=AsyncMock(return_value=snaps)), \
         patch("tinyagentos.framework_update.snapshot_delete",
               new=AsyncMock(side_effect=lambda _c, n: deleted.append(n))):
        await _prune_old_snapshots("taos-agent-atlas", keep=3)
    assert deleted == ["pre-framework-update-3", "pre-framework-update-4"]


@pytest.mark.asyncio
async def test_prune_noop_when_under_limit():
    from tinyagentos.framework_update import _prune_old_snapshots
    with patch("tinyagentos.framework_update.snapshot_list",
               new=AsyncMock(return_value=[{"name": "x", "created_at": ""}])), \
         patch("tinyagentos.framework_update.snapshot_delete",
               new=AsyncMock()) as d:
        await _prune_old_snapshots("taos-agent-atlas", keep=3)
    d.assert_not_awaited()


@pytest.mark.asyncio
async def test_prune_with_empty_snapshot_list():
    from tinyagentos.framework_update import _prune_old_snapshots
    with patch("tinyagentos.framework_update.snapshot_list",
               new=AsyncMock(return_value=[])), \
         patch("tinyagentos.framework_update.snapshot_delete",
               new=AsyncMock()) as d:
        await _prune_old_snapshots("taos-agent-atlas", keep=3)
    d.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_for_ping_returns_true_when_arrives_before_deadline():
    from tinyagentos.framework_update import _wait_for_bootstrap_ping
    agent = {"bootstrap_last_seen_at": None}
    started_at = int(time.time())
    async def ping():
        await asyncio.sleep(0.1)
        agent["bootstrap_last_seen_at"] = int(time.time()) + 1
    asyncio.create_task(ping())
    ok = await _wait_for_bootstrap_ping(agent, started_at=started_at, deadline_seconds=2)
    assert ok is True


@pytest.mark.asyncio
async def test_wait_for_ping_returns_false_on_timeout():
    from tinyagentos.framework_update import _wait_for_bootstrap_ping
    ok = await _wait_for_bootstrap_ping(
        {"bootstrap_last_seen_at": None},
        started_at=int(time.time()),
        deadline_seconds=1,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_wait_ignores_stale_pings():
    from tinyagentos.framework_update import _wait_for_bootstrap_ping
    started_at = int(time.time())
    ok = await _wait_for_bootstrap_ping(
        {"bootstrap_last_seen_at": started_at - 5},
        started_at=started_at, deadline_seconds=1,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_wait_uses_module_default_deadline():
    from tinyagentos import framework_update as fu
    from tinyagentos.framework_update import _wait_for_bootstrap_ping
    agent = {"bootstrap_last_seen_at": None}
    started_at = int(time.time())
    original = fu.UPDATE_DEADLINE_SECONDS
    fu.UPDATE_DEADLINE_SECONDS = 1
    try:
        ok = await _wait_for_bootstrap_ping(agent, started_at=started_at)
        assert ok is False
    finally:
        fu.UPDATE_DEADLINE_SECONDS = original


@pytest.mark.asyncio
async def test_iso_utc_compact_format():
    from tinyagentos.framework_update import _iso_utc_compact
    result = _iso_utc_compact()
    assert "T" in result
    assert ":" not in result
    assert len(result) == 19


@pytest.mark.asyncio
async def test_read_installed_tag_success():
    from tinyagentos.framework_update import _read_installed_tag
    with patch("tinyagentos.framework_update.exec_in_container",
               new=AsyncMock(return_value=(0, "v2.0.0\n"))):
        tag = await _read_installed_tag("taos-agent-atlas")
    assert tag == "v2.0.0"


@pytest.mark.asyncio
async def test_read_installed_tag_failure():
    from tinyagentos.framework_update import _read_installed_tag
    with patch("tinyagentos.framework_update.exec_in_container",
               new=AsyncMock(return_value=(1, "file not found"))):
        tag = await _read_installed_tag("taos-agent-atlas")
    assert tag == ""


@pytest.mark.asyncio
async def test_mark_failed_sets_state_and_clears_started_at():
    from tinyagentos.framework_update import _mark_failed
    agent = {
        "framework_update_status": "updating",
        "framework_update_started_at": 12345,
        "framework_update_last_error": None,
    }
    save = AsyncMock()
    await _mark_failed(agent, "something broke", save_config=save, snapshot="snap-1")
    assert agent["framework_update_status"] == "failed"
    assert agent["framework_update_started_at"] is None
    assert agent["framework_update_last_error"] == "something broke"
    assert agent["framework_last_snapshot"] == "snap-1"
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_failed_truncates_long_reason():
    from tinyagentos.framework_update import _mark_failed
    agent = {"framework_update_status": "updating", "framework_update_started_at": 1}
    save = AsyncMock()
    long_reason = "x" * 600
    await _mark_failed(agent, long_reason, save_config=save)
    assert len(agent["framework_update_last_error"]) == 500


@pytest.mark.asyncio
async def test_mark_failed_without_snapshot():
    from tinyagentos.framework_update import _mark_failed
    agent = {"framework_update_status": "updating", "framework_update_started_at": 1}
    save = AsyncMock()
    await _mark_failed(agent, "err", save_config=save)
    assert "framework_last_snapshot" not in agent


@pytest.mark.asyncio
async def test_start_update_happy_path(monkeypatch):
    from tinyagentos.framework_update import start_update
    agent = {"name": "atlas", "framework": "openclaw", "bootstrap_last_seen_at": None}
    manifest = {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"}
    latest = {"tag": "T2", "sha": "b2b2b2b", "asset_url": "u"}

    async def fake_exec(container, cmd, timeout=None):
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                         AsyncMock(return_value="T2"))
    await start_update(agent, manifest, latest, save_config=AsyncMock())
    assert agent["framework_update_status"] == "idle"
    assert agent["framework_version_tag"] == "T2"
    assert agent["framework_version_sha"] == "b2b2b2b"
    assert agent["framework_update_started_at"] is None


@pytest.mark.asyncio
async def test_start_update_fails_on_nonzero_exit(monkeypatch):
    from tinyagentos.framework_update import start_update
    agent = {"name": "atlas", "framework": "openclaw"}
    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container",
                         AsyncMock(return_value=(1, "blew up")))
    await start_update(agent,
                        {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"},
                        {"tag": "T", "sha": "s", "asset_url": "u"},
                        save_config=AsyncMock())
    assert agent["framework_update_status"] == "failed"
    assert agent["framework_last_snapshot"] is not None
    assert "rc=1" in agent["framework_update_last_error"]


@pytest.mark.asyncio
async def test_start_update_fails_on_missing_bootstrap(monkeypatch):
    from tinyagentos import framework_update as fu
    from tinyagentos.framework_update import start_update
    agent = {"name": "atlas", "framework": "openclaw", "bootstrap_last_seen_at": None}
    monkeypatch.setattr(fu, "snapshot_create", AsyncMock())
    monkeypatch.setattr(fu, "_prune_old_snapshots", AsyncMock())
    monkeypatch.setattr(fu, "exec_in_container", AsyncMock(return_value=(0, "")))
    monkeypatch.setattr(fu, "UPDATE_DEADLINE_SECONDS", 1)
    elapsed_start = time.time()
    await start_update(agent,
                        {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"},
                        {"tag": "T", "sha": "s", "asset_url": "u"},
                        save_config=AsyncMock())
    assert time.time() - elapsed_start < 5.0
    assert agent["framework_update_status"] == "failed"
    assert "bridge" in agent["framework_update_last_error"]


@pytest.mark.asyncio
async def test_start_update_aborts_before_install_on_snapshot_failure(monkeypatch):
    from tinyagentos.framework_update import start_update
    install = AsyncMock()
    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create",
                         AsyncMock(side_effect=RuntimeError("pool offline")))
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", install)
    agent = {"name": "atlas", "framework": "openclaw"}
    await start_update(agent,
                        {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"},
                        {"tag": "T", "sha": "s", "asset_url": "u"},
                        save_config=AsyncMock())
    assert agent["framework_update_status"] == "failed"
    install.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_update_fails_on_version_mismatch(monkeypatch):
    from tinyagentos.framework_update import start_update
    agent = {"name": "atlas", "framework": "openclaw", "bootstrap_last_seen_at": None}

    async def fake_exec(container, cmd, timeout=None):
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                         AsyncMock(return_value="T1"))
    await start_update(agent,
                        {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"},
                        {"tag": "T2", "sha": "s", "asset_url": "u"},
                        save_config=AsyncMock())
    assert agent["framework_update_status"] == "failed"
    assert "version mismatch" in agent["framework_update_last_error"]


@pytest.mark.asyncio
async def test_start_update_handles_install_timeout(monkeypatch):
    from tinyagentos.framework_update import start_update
    agent = {"name": "atlas", "framework": "openclaw"}

    async def slow_install(*args, **kwargs):
        await asyncio.sleep(100)
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())

    async def install_with_timeout(*args, **kwargs):
        timeout = kwargs.get("timeout", 120)
        try:
            return await asyncio.wait_for(slow_install(*args, **kwargs), timeout=timeout)
        except asyncio.TimeoutError:
            raise

    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", install_with_timeout)
    monkeypatch.setattr("tinyagentos.framework_update.UPDATE_DEADLINE_SECONDS", 1)
    await start_update(agent,
                        {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"},
                        {"tag": "T", "sha": "s", "asset_url": "u"},
                        save_config=AsyncMock())
    assert agent["framework_update_status"] == "failed"
    assert "timed out" in agent["framework_update_last_error"]


@pytest.mark.asyncio
async def test_start_update_clears_error_on_success(monkeypatch):
    from tinyagentos.framework_update import start_update
    agent = {
        "name": "atlas",
        "framework": "openclaw",
        "bootstrap_last_seen_at": None,
        "framework_update_last_error": "previous error",
    }

    async def fake_exec(container, cmd, timeout=None):
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                         AsyncMock(return_value="T2"))
    await start_update(agent,
                        {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"},
                        {"tag": "T2", "sha": "s", "asset_url": "u"},
                        save_config=AsyncMock())
    assert agent["framework_update_last_error"] is None


@pytest.mark.asyncio
async def test_start_update_sets_updating_state_initially(monkeypatch):
    from tinyagentos.framework_update import start_update
    agent = {"name": "atlas", "framework": "openclaw", "bootstrap_last_seen_at": None}
    states = []
    original_save = None

    async def tracking_save():
        states.append(dict(agent))

    async def fake_exec(container, cmd, timeout=None):
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                         AsyncMock(return_value="T2"))
    await start_update(agent,
                        {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"},
                        {"tag": "T2", "sha": "s", "asset_url": "u"},
                        save_config=tracking_save)
    assert states[0]["framework_update_status"] == "updating"
    assert states[0]["framework_update_last_error"] is None


@pytest.mark.asyncio
async def test_start_update_container_name_constructed_from_agent_name(monkeypatch):
    from tinyagentos.framework_update import start_update
    agent = {"name": "my-agent", "framework": "openclaw", "bootstrap_last_seen_at": None}
    captured_containers = []

    async def fake_exec(container, cmd, timeout=None):
        captured_containers.append(container)
        agent["bootstrap_last_seen_at"] = int(time.time()) + 5
        return 0, ""

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create",
                         AsyncMock(side_effect=lambda c, n: captured_containers.append(c)))
    monkeypatch.setattr("tinyagentos.framework_update._prune_old_snapshots", AsyncMock())
    monkeypatch.setattr("tinyagentos.framework_update.exec_in_container", fake_exec)
    monkeypatch.setattr("tinyagentos.framework_update._read_installed_tag",
                         AsyncMock(return_value="T2"))
    await start_update(agent,
                        {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"},
                        {"tag": "T2", "sha": "s", "asset_url": "u"},
                        save_config=AsyncMock())
    assert "taos-agent-my-agent" in captured_containers


@pytest.mark.asyncio
async def test_start_update_unexpected_exception_marks_failed(monkeypatch):
    from tinyagentos.framework_update import start_update
    agent = {"name": "atlas", "framework": "openclaw"}

    async def bad_snapshot(*args, **kwargs):
        raise Exception("unexpected disk error")

    monkeypatch.setattr("tinyagentos.framework_update.snapshot_create", bad_snapshot)
    await start_update(agent,
                        {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"},
                        {"tag": "T", "sha": "s", "asset_url": "u"},
                        save_config=AsyncMock())
    assert agent["framework_update_status"] == "failed"
    assert "unexpected" in agent["framework_update_last_error"]
