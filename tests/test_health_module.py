from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.health import HealthMonitor


def _make_qmd_db(db_path: Path, vector_count: int = 0) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS content_vectors (hash TEXT, doc TEXT, created_at TEXT, embedded_at TEXT)")
    conn.execute("DELETE FROM content_vectors")
    for i in range(vector_count):
        conn.execute(
            "INSERT INTO content_vectors (hash, doc, created_at, embedded_at) VALUES (?, ?, ?, ?)",
            (f"h{i}", f"doc{i}", "2024-01-01", "2024-01-01"),
        )
    conn.commit()
    conn.close()


def _make_monitor(
    tmp_path: Path,
    backends: list[dict] | None = None,
    agents: list[dict] | None = None,
    poll_interval: int = 30,
    retention_days: int = 30,
    with_notifications: bool = True,
) -> HealthMonitor:
    config = MagicMock()
    config.backends = backends or []
    config.agents = agents or []
    config.metrics = {"poll_interval": poll_interval, "retention_days": retention_days}

    metrics = MagicMock()
    metrics.insert = AsyncMock()
    metrics.cleanup = AsyncMock(return_value=0)

    qmd = MagicMock()
    qmd.health = AsyncMock(return_value={"status": "ok", "response_ms": 5})

    http_client = MagicMock()

    notifications = None
    if with_notifications:
        notifications = MagicMock()
        notifications.emit_event = AsyncMock()

    return HealthMonitor(config, metrics, qmd, http_client, notifications=notifications)


@pytest.mark.asyncio
async def test_poll_once_all_healthy(tmp_path):
    backend = {"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}
    monitor = _make_monitor(tmp_path, backends=[backend])

    with patch("tinyagentos.health.check_backend_health", new_callable=AsyncMock,
               return_value={"status": "ok", "response_ms": 12}):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=10.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=50.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=70.0)):
                    await monitor._poll_once()

    calls = {c.args[0] for c in monitor.metrics.insert.call_args_list}
    assert "backend.b1.status" in calls
    assert "backend.b1.response_ms" in calls
    assert "system.cpu_pct" in calls
    assert "system.ram_pct" in calls
    assert "system.disk_pct" in calls
    assert "qmd.status" in calls
    assert "qmd.health_response_ms" in calls


@pytest.mark.asyncio
async def test_poll_once_backend_degraded(tmp_path):
    backend = {"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}
    monitor = _make_monitor(tmp_path, backends=[backend])

    with patch("tinyagentos.health.check_backend_health", new_callable=AsyncMock,
               return_value={"status": "error", "response_ms": 0}):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=20.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=60.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=80.0)):
                    await monitor._poll_once()

    status_call = [c for c in monitor.metrics.insert.call_args_list
                   if c.args[0] == "backend.b1.status"][0]
    assert status_call.args[1] == 0.0


@pytest.mark.asyncio
async def test_poll_once_backend_ok_records_1(tmp_path):
    backend = {"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}
    monitor = _make_monitor(tmp_path, backends=[backend])

    with patch("tinyagentos.health.check_backend_health", new_callable=AsyncMock,
               return_value={"status": "ok", "response_ms": 42}):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=5.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=30.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=40.0)):
                    await monitor._poll_once()

    status_call = [c for c in monitor.metrics.insert.call_args_list
                   if c.args[0] == "backend.b1.status"][0]
    assert status_call.args[1] == 1.0

    ms_call = [c for c in monitor.metrics.insert.call_args_list
               if c.args[0] == "backend.b1.response_ms"][0]
    assert ms_call.args[1] == 42.0


@pytest.mark.asyncio
async def test_poll_once_qmd_healthy(tmp_path):
    monitor = _make_monitor(tmp_path)
    monitor.qmd_client.health = AsyncMock(return_value={"status": "ok", "response_ms": 3})

    with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
        with patch("tinyagentos.health.psutil.virtual_memory",
                   return_value=MagicMock(percent=0.0)):
            with patch("tinyagentos.health.psutil.disk_usage",
                       return_value=MagicMock(percent=0.0)):
                await monitor._poll_once()

    qmd_status = [c for c in monitor.metrics.insert.call_args_list
                  if c.args[0] == "qmd.status"][0]
    assert qmd_status.args[1] == 1.0


@pytest.mark.asyncio
async def test_poll_once_qmd_error(tmp_path):
    monitor = _make_monitor(tmp_path)
    monitor.qmd_client.health = AsyncMock(return_value={"status": "error", "response_ms": 0})

    with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
        with patch("tinyagentos.health.psutil.virtual_memory",
                   return_value=MagicMock(percent=0.0)):
            with patch("tinyagentos.health.psutil.disk_usage",
                       return_value=MagicMock(percent=0.0)):
                await monitor._poll_once()

    qmd_status = [c for c in monitor.metrics.insert.call_args_list
                  if c.args[0] == "qmd.status"][0]
    assert qmd_status.args[1] == 0.0


@pytest.mark.asyncio
async def test_poll_once_system_metrics_values(tmp_path):
    monitor = _make_monitor(tmp_path)

    with patch("tinyagentos.health.psutil.cpu_percent", return_value=42.5):
        with patch("tinyagentos.health.psutil.virtual_memory",
                   return_value=MagicMock(percent=73.2)):
            with patch("tinyagentos.health.psutil.disk_usage",
                       return_value=MagicMock(percent=88.1)):
                await monitor._poll_once()

    calls = {c.args[0]: c.args[1] for c in monitor.metrics.insert.call_args_list}
    assert calls["system.cpu_pct"] == 42.5
    assert calls["system.ram_pct"] == 73.2
    assert calls["system.disk_pct"] == 88.1


@pytest.mark.asyncio
async def test_poll_once_backend_exception_still_records_other_metrics(tmp_path):
    backend = {"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}
    monitor = _make_monitor(tmp_path, backends=[backend])

    with patch("tinyagentos.health.check_backend_health", new_callable=AsyncMock,
               side_effect=RuntimeError("connection refused")):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=15.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=55.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=65.0)):
                    await monitor._poll_once()

    calls = {c.args[0] for c in monitor.metrics.insert.call_args_list}
    assert "backend.b1.status" not in calls
    assert "system.cpu_pct" in calls
    assert "qmd.status" in calls


@pytest.mark.asyncio
async def test_poll_once_no_backends_no_agents(tmp_path):
    monitor = _make_monitor(tmp_path, backends=[], agents=[])

    with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
        with patch("tinyagentos.health.psutil.virtual_memory",
                   return_value=MagicMock(percent=0.0)):
            with patch("tinyagentos.health.psutil.disk_usage",
                       return_value=MagicMock(percent=0.0)):
                await monitor._poll_once()

    assert monitor.metrics.insert.call_count == 5


@pytest.mark.asyncio
async def test_poll_once_multiple_backends(tmp_path):
    backends = [
        {"name": "b1", "type": "rkllama", "url": "http://localhost:8080"},
        {"name": "b2", "type": "ollama", "url": "http://localhost:11434"},
    ]
    monitor = _make_monitor(tmp_path, backends=backends)

    async def fake_check(client, backend):
        if backend["name"] == "b1":
            return {"status": "ok", "response_ms": 10}
        return {"status": "error", "response_ms": 0}

    with patch("tinyagentos.health.check_backend_health", new_callable=AsyncMock,
               side_effect=fake_check):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=0.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=0.0)):
                    await monitor._poll_once()

    calls = {c.args[0]: c.args[1] for c in monitor.metrics.insert.call_args_list}
    assert calls["backend.b1.status"] == 1.0
    assert calls["backend.b2.status"] == 0.0


@pytest.mark.asyncio
async def test_poll_once_agent_vector_count_every_10th_cycle(tmp_path):
    agent = {"name": "agent1", "qmd_index": "idx1"}
    monitor = _make_monitor(tmp_path, agents=[agent])
    monitor._poll_count = 9

    db_path = tmp_path / "idx1.sqlite"
    _make_qmd_db(db_path, vector_count=7)

    with patch("tinyagentos.health.get_agent_db") as mock_get_db:
        mock_db = MagicMock()
        mock_db.vector_count.return_value = 7
        mock_get_db.return_value = mock_db
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=0.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=0.0)):
                    await monitor._poll_once()

    vec_calls = [c for c in monitor.metrics.insert.call_args_list
                 if c.args[0] == "agent.agent1.vectors"]
    assert len(vec_calls) == 1
    assert vec_calls[0].args[1] == 7.0
    assert vec_calls[0].kwargs.get("labels") == {"agent": "agent1"}


@pytest.mark.asyncio
async def test_poll_once_agent_vector_count_skipped_on_non_10th(tmp_path):
    agent = {"name": "agent1", "qmd_index": "idx1"}
    monitor = _make_monitor(tmp_path, agents=[agent])
    monitor._poll_count = 7

    with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
        with patch("tinyagentos.health.psutil.virtual_memory",
                   return_value=MagicMock(percent=0.0)):
            with patch("tinyagentos.health.psutil.disk_usage",
                       return_value=MagicMock(percent=0.0)):
                await monitor._poll_once()

    vec_calls = [c for c in monitor.metrics.insert.call_args_list
                 if c.args[0] == "agent.agent1.vectors"]
    assert len(vec_calls) == 0


@pytest.mark.asyncio
async def test_poll_once_agent_db_missing_returns_none(tmp_path):
    agent = {"name": "agent1", "qmd_index": "idx1"}
    monitor = _make_monitor(tmp_path, agents=[agent])
    monitor._poll_count = 10

    with patch("tinyagentos.health.get_agent_db", return_value=None):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=0.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=0.0)):
                    await monitor._poll_once()

    vec_calls = [c for c in monitor.metrics.insert.call_args_list
                 if c.args[0] == "agent.agent1.vectors"]
    assert len(vec_calls) == 0


@pytest.mark.asyncio
async def test_poll_once_daily_cleanup_runs(tmp_path):
    monitor = _make_monitor(tmp_path)
    monitor._last_cleanup = 0
    monitor.metrics.cleanup = AsyncMock(return_value=100)

    with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
        with patch("tinyagentos.health.psutil.virtual_memory",
                   return_value=MagicMock(percent=0.0)):
            with patch("tinyagentos.health.psutil.disk_usage",
                       return_value=MagicMock(percent=0.0)):
                await monitor._poll_once()

    monitor.metrics.cleanup.assert_called_once_with(30)


@pytest.mark.asyncio
async def test_poll_once_cleanup_skipped_within_day(tmp_path):
    import time as _time
    monitor = _make_monitor(tmp_path)
    monitor._last_cleanup = int(_time.time()) - 100

    with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
        with patch("tinyagentos.health.psutil.virtual_memory",
                   return_value=MagicMock(percent=0.0)):
            with patch("tinyagentos.health.psutil.disk_usage",
                       return_value=MagicMock(percent=0.0)):
                await monitor._poll_once()

    monitor.metrics.cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_poll_once_cleanup_zero_deleted_no_log(tmp_path):
    monitor = _make_monitor(tmp_path)
    monitor._last_cleanup = 0
    monitor.metrics.cleanup = AsyncMock(return_value=0)

    with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
        with patch("tinyagentos.health.psutil.virtual_memory",
                   return_value=MagicMock(percent=0.0)):
            with patch("tinyagentos.health.psutil.disk_usage",
                       return_value=MagicMock(percent=0.0)):
                await monitor._poll_once()

    monitor.metrics.cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_backend_state_change_emits_down_notification(tmp_path):
    backend = {"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}
    monitor = _make_monitor(tmp_path, backends=[backend])
    monitor._backend_states["b1"] = "ok"

    with patch("tinyagentos.health.check_backend_health", new_callable=AsyncMock,
               return_value={"status": "error", "response_ms": 0}):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=0.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=0.0)):
                    await monitor._poll_once()

    monitor.notifications.emit_event.assert_called_once_with(
        "backend.down",
        "Backend 'b1' is unreachable",
        "Health check failed for http://localhost:8080",
        level="warning",
    )


@pytest.mark.asyncio
async def test_backend_state_change_emits_up_notification(tmp_path):
    backend = {"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}
    monitor = _make_monitor(tmp_path, backends=[backend])
    monitor._backend_states["b1"] = "error"

    with patch("tinyagentos.health.check_backend_health", new_callable=AsyncMock,
               return_value={"status": "ok", "response_ms": 10}):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=0.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=0.0)):
                    await monitor._poll_once()

    monitor.notifications.emit_event.assert_called_once_with(
        "backend.up",
        "Backend 'b1' recovered",
        "Health check succeeded for http://localhost:8080",
        level="info",
    )


@pytest.mark.asyncio
async def test_backend_no_state_change_no_notification(tmp_path):
    backend = {"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}
    monitor = _make_monitor(tmp_path, backends=[backend])
    monitor._backend_states["b1"] = "ok"

    with patch("tinyagentos.health.check_backend_health", new_callable=AsyncMock,
               return_value={"status": "ok", "response_ms": 10}):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=0.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=0.0)):
                    await monitor._poll_once()

    monitor.notifications.emit_event.assert_not_called()


@pytest.mark.asyncio
async def test_backend_first_seen_no_notification(tmp_path):
    backend = {"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}
    monitor = _make_monitor(tmp_path, backends=[backend])

    with patch("tinyagentos.health.check_backend_health", new_callable=AsyncMock,
               return_value={"status": "ok", "response_ms": 10}):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=0.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=0.0)):
                    await monitor._poll_once()

    monitor.notifications.emit_event.assert_not_called()
    assert monitor._backend_states["b1"] == "ok"


@pytest.mark.asyncio
async def test_no_notifications_when_none(tmp_path):
    backend = {"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}
    monitor = _make_monitor(tmp_path, backends=[backend], with_notifications=False)
    monitor._backend_states["b1"] = "ok"

    with patch("tinyagentos.health.check_backend_health", new_callable=AsyncMock,
               return_value={"status": "error", "response_ms": 0}):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=0.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=0.0)):
                    await monitor._poll_once()

    assert monitor.notifications is None


@pytest.mark.asyncio
async def test_start_stop(tmp_path):
    monitor = _make_monitor(tmp_path)
    await monitor.start()
    assert monitor._task is not None
    await monitor.stop()
    assert monitor._task.done()


@pytest.mark.asyncio
async def test_poll_once_qmd_response_ms_recorded(tmp_path):
    monitor = _make_monitor(tmp_path)
    monitor.qmd_client.health = AsyncMock(return_value={"status": "ok", "response_ms": 17})

    with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
        with patch("tinyagentos.health.psutil.virtual_memory",
                   return_value=MagicMock(percent=0.0)):
            with patch("tinyagentos.health.psutil.disk_usage",
                       return_value=MagicMock(percent=0.0)):
                await monitor._poll_once()

    ms_call = [c for c in monitor.metrics.insert.call_args_list
               if c.args[0] == "qmd.health_response_ms"][0]
    assert ms_call.args[1] == 17.0


@pytest.mark.asyncio
async def test_poll_once_backend_response_ms_default_zero(tmp_path):
    backend = {"name": "b1", "type": "rkllama", "url": "http://localhost:8080"}
    monitor = _make_monitor(tmp_path, backends=[backend])

    with patch("tinyagentos.health.check_backend_health", new_callable=AsyncMock,
               return_value={"status": "ok"}):
        with patch("tinyagentos.health.psutil.cpu_percent", return_value=0.0):
            with patch("tinyagentos.health.psutil.virtual_memory",
                       return_value=MagicMock(percent=0.0)):
                with patch("tinyagentos.health.psutil.disk_usage",
                           return_value=MagicMock(percent=0.0)):
                    await monitor._poll_once()

    ms_call = [c for c in monitor.metrics.insert.call_args_list
               if c.args[0] == "backend.b1.response_ms"][0]
    assert ms_call.args[1] == 0.0
