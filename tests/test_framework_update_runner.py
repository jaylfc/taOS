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
    ]  # newest first
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


@pytest.mark.asyncio
async def test_start_update_fails_on_missing_bootstrap(monkeypatch):
    from tinyagentos import framework_update as fu
    from tinyagentos.framework_update import start_update
    agent = {"name": "atlas", "framework": "openclaw", "bootstrap_last_seen_at": None}
    monkeypatch.setattr(fu, "snapshot_create", AsyncMock())
    monkeypatch.setattr(fu, "_prune_old_snapshots", AsyncMock())
    monkeypatch.setattr(fu, "exec_in_container", AsyncMock(return_value=(0, "")))
    monkeypatch.setattr(fu, "UPDATE_DEADLINE_SECONDS", 1)
    # Time-bound the call so a regression on the default-arg fix surfaces
    # as a fast failure (~5 s ceiling) instead of a 120 s CI hang.
    elapsed_start = time.time()
    await start_update(agent,
                        {"id": "openclaw", "install_script": "/usr/local/bin/taos-framework-update"},
                        {"tag": "T", "sha": "s", "asset_url": "u"},
                        save_config=AsyncMock())
    assert time.time() - elapsed_start < 5.0, (
        "monkeypatched UPDATE_DEADLINE_SECONDS must take effect — "
        "_wait_for_bootstrap_ping is reading the module attr at call time"
    )
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
