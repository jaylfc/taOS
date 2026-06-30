"""Unit tests for DiskQuotaMonitor."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.disk_quota import DiskQuotaMonitor, _default_data_dir
from tinyagentos.containers.backend import ContainerInfo


def _make_config(agents=None):
    cfg = MagicMock()
    cfg.agents = agents or []
    return cfg


def _make_notif():
    n = MagicMock()
    n.emit_event = AsyncMock()
    return n


def _make_backend(containers=None):
    b = MagicMock()
    b.list_containers = AsyncMock(return_value=containers or [])
    return b


def _container(name: str) -> ContainerInfo:
    return ContainerInfo(name=name, status="Running", ip=None, memory_mb=0, cpu_cores=0)


# ------------------------------------------------------------------
# Threshold edge cases
# ------------------------------------------------------------------

@pytest.mark.asyncio
class TestThresholds:
    async def test_ok_state_below_warn(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=10.0)):
            results = await monitor.scan_all()

        assert len(results) == 1
        r = results[0]
        assert r["state"] == "ok"
        assert r["used_gib"] == 10.0
        assert r["quota_gib"] == 40.0

    async def test_warn_at_threshold(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        # 87.5% of 40 = 35 GiB — exactly at threshold
        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=35.0)):
            results = await monitor.scan_all()

        assert results[0]["state"] == "warn"

    async def test_hard_at_full(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=40.0)):
            results = await monitor.scan_all()

        assert results[0]["state"] == "hard"

    async def test_just_below_warn_is_ok(self):
        agent = {"name": "bob", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-bob")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        # 34.9 GiB is just below 87.5 %
        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=34.9)):
            results = await monitor.scan_all()

        assert results[0]["state"] == "ok"


# ------------------------------------------------------------------
# Notification only on transition
# ------------------------------------------------------------------

@pytest.mark.asyncio
class TestNotificationTransitions:
    async def test_no_notification_on_repeat_ok(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)
        monitor._last_state["alice"] = "ok"

        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=10.0)):
            await monitor.scan_all()

        notif.emit_event.assert_not_called()

    async def test_notification_on_ok_to_warn(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)
        monitor._last_state["alice"] = "ok"

        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=35.0)):
            await monitor.scan_all()

        notif.emit_event.assert_called_once()
        call_args = notif.emit_event.call_args
        assert call_args[0][0] == "disk_quota"
        assert "warn" in call_args[0][1].lower() or "%" in call_args[0][1]

    async def test_notification_on_warn_to_hard(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)
        monitor._last_state["alice"] = "warn"

        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=40.0)):
            await monitor.scan_all()

        notif.emit_event.assert_called_once()
        call_args = notif.emit_event.call_args
        assert call_args[1].get("level") == "error" or call_args[0][3] == "error"

    async def test_no_repeat_notification_on_warn_stay_warn(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)
        monitor._last_state["alice"] = "warn"

        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=36.0)):
            await monitor.scan_all()

        notif.emit_event.assert_not_called()

    async def test_notification_on_recovery(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)
        monitor._last_state["alice"] = "hard"

        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=10.0)):
            await monitor.scan_all()

        notif.emit_event.assert_called_once()
        call_args = notif.emit_event.call_args
        assert "cleared" in call_args[0][1].lower() or "info" in str(call_args)


# ------------------------------------------------------------------
# Hard threshold pauses the agent
# ------------------------------------------------------------------

@pytest.mark.asyncio
class TestHardThresholdPausesAgent:
    async def test_hard_sets_paused_true(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)
        monitor._last_state["alice"] = "warn"

        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=40.0)):
            await monitor.scan_all()

        assert agent.get("paused") is True

    async def test_ok_does_not_set_paused(self):
        agent = {"name": "bob", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-bob")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=5.0)):
            await monitor.scan_all()

        assert not agent.get("paused")


# ------------------------------------------------------------------
# Sample failure is skipped, not fatal
# ------------------------------------------------------------------

@pytest.mark.asyncio
class TestSampleFailure:
    async def test_failed_sample_skipped_not_fatal(self):
        agent1 = {"name": "alice", "disk_quota_gib": 40}
        agent2 = {"name": "bob", "disk_quota_gib": 40}
        cfg = _make_config([agent1, agent2])
        notif = _make_notif()
        backend = _make_backend([
            _container("taos-agent-alice"),
            _container("taos-agent-bob"),
        ])
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        call_count = 0

        async def _mock_sample(container_name: str):
            nonlocal call_count
            call_count += 1
            if "alice" in container_name:
                return None  # sampling failure
            return 5.0

        with patch.object(monitor, "_sample_usage", side_effect=_mock_sample):
            results = await monitor.scan_all()

        # Bob should still appear; alice is skipped
        assert len(results) == 1
        assert results[0]["name"] == "bob"

    async def test_exception_in_sample_skipped_not_fatal(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        async def _raise(_):
            raise RuntimeError("incus exploded")

        with patch.object(monitor, "_sample_usage", side_effect=_raise):
            results = await monitor.scan_all()

        assert results == []


# ------------------------------------------------------------------
# Resize quota
# ------------------------------------------------------------------

@pytest.mark.asyncio
class TestResizeQuota:
    async def test_btrfs_resize_updates_agent_record(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend()
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        with patch.object(monitor, "_detect_pool_type", AsyncMock(return_value="btrfs")), \
             patch("tinyagentos.disk_quota._run", AsyncMock(return_value=(0, ""))):
            result = await monitor.resize_quota("alice", 60)

        assert result["ok"] is True
        assert result["new_quota_gib"] == 60
        assert agent["disk_quota_gib"] == 60

    async def test_dir_backend_raises_value_error(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend()
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        with patch.object(monitor, "_detect_pool_type", AsyncMock(return_value="dir")):
            with pytest.raises(ValueError, match="dir-backend"):
                await monitor.resize_quota("alice", 60)

    async def test_unknown_agent_raises_value_error(self):
        cfg = _make_config([])
        notif = _make_notif()
        backend = _make_backend()
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        with pytest.raises(ValueError, match="agent not found"):
            await monitor.resize_quota("nobody", 50)

    async def test_incus_failure_raises_runtime_error(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend()
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        with patch.object(monitor, "_detect_pool_type", AsyncMock(return_value="btrfs")), \
             patch("tinyagentos.disk_quota._run", AsyncMock(return_value=(1, "no such device"))):
            with pytest.raises(RuntimeError):
                await monitor.resize_quota("alice", 60)


# ------------------------------------------------------------------
# Default quota applied when not set
# ------------------------------------------------------------------

@pytest.mark.asyncio
class TestDefaultQuota:
    async def test_uses_default_when_quota_not_set(self):
        agent = {"name": "alice"}  # no disk_quota_gib
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=5.0)):
            results = await monitor.scan_all()

        assert results[0]["quota_gib"] == DiskQuotaMonitor.DEFAULT_QUOTA_GIB

    async def test_persists_usage_to_agent_record(self):
        agent = {"name": "alice", "disk_quota_gib": 40}
        cfg = _make_config([agent])
        notif = _make_notif()
        backend = _make_backend([_container("taos-agent-alice")])
        monitor = DiskQuotaMonitor(cfg, backend, notif)

        before = time.time()
        with patch.object(monitor, "_sample_usage", AsyncMock(return_value=12.345)):
            await monitor.scan_all()

        assert agent["disk_usage_gib"] == 12.345
        assert agent["disk_last_checked_at"] >= before


# ------------------------------------------------------------------
# CLI data_dir resolution — must be install-dir relative, not a
# hardcoded /opt/tinyagentos literal and not $HOME-dependent.
# ------------------------------------------------------------------

class TestDefaultDataDir:
    def test_resolves_under_install_root(self):
        import tinyagentos.disk_quota as disk_quota_mod

        expected_root = Path(disk_quota_mod.__file__).resolve().parent.parent
        assert _default_data_dir() == expected_root / "data"

    def test_does_not_depend_on_home(self, monkeypatch, tmp_path):
        bogus_home = tmp_path / "stale-home-that-does-not-exist"
        monkeypatch.setenv("HOME", str(bogus_home))

        data_dir = _default_data_dir()

        assert str(bogus_home) not in str(data_dir)
        assert data_dir.name == "data"
