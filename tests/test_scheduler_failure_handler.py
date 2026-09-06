"""Tests for tinyagentos.scheduler.failure_handler."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.scheduler.failure_handler import (
    handle_call,
    AgentPausedError,
    WorkerUnavailableError,
    HEARTBEAT_GRACE_SECS,
)


@pytest.fixture(autouse=True)
def _patch_retry_sleep(monkeypatch):
    """Suppress real asyncio.sleep in the retry wrapper for all tests here.

    Keeps the test suite fast without changing retry logic.
    """
    async def _noop(_seconds):
        pass

    monkeypatch.setattr("tinyagentos.clients.retry.asyncio.sleep", _noop)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_config(agent_name: str = "test-agent", paused: bool = False):
    """Return a minimal fake AppConfig with one agent."""
    config = MagicMock()
    config.agents = [
        {
            "name": agent_name,
            "host": "10.0.0.1",
            "color": "#fff",
            "on_worker_failure": "fallback",
            "fallback_models": [],
            "paused": paused,
        }
    ]
    return config


def _make_cluster_manager(worker_name: str = "worker-1", online: bool = True):
    """Return a fake ClusterManager."""
    mgr = MagicMock()
    worker = MagicMock()
    worker.name = worker_name
    worker.status = "online" if online else "offline"
    worker.last_heartbeat = 1000.0
    mgr.get_worker.return_value = worker
    return mgr


def _make_notif_store():
    store = MagicMock()
    store.add = AsyncMock()
    return store


def _ok_factory(value="result"):
    async def _coro():
        return value
    return _coro


def _fail_factory(exc=None):
    import httpx
    if exc is None:
        exc = httpx.ConnectError("simulated failure")

    async def _coro():
        raise exc
    return _coro


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestHandleCallSuccess:
    async def test_primary_success_returns_result(self):
        config = _make_config()
        mgr = _make_cluster_manager()
        notif = _make_notif_store()

        result = await handle_call(
            "test-agent",
            _ok_factory("hello"),
            [],
            "fallback",
            mgr,
            notif,
            config,
            primary_worker="worker-1",
            grace_secs=0,
        )
        assert result == "hello"

    async def test_paused_agent_raises_immediately(self):
        config = _make_config(paused=True)
        mgr = _make_cluster_manager()
        notif = _make_notif_store()

        with pytest.raises(AgentPausedError):
            await handle_call(
                "test-agent",
                _ok_factory(),
                [],
                "fallback",
                mgr,
                notif,
                config,
                primary_worker="worker-1",
                grace_secs=0,
            )

    async def test_paused_agent_does_not_call_factory(self):
        config = _make_config(paused=True)
        mgr = _make_cluster_manager()
        notif = _make_notif_store()
        called = {"n": 0}

        async def _factory():
            called["n"] += 1
            return "ok"

        with pytest.raises(AgentPausedError):
            await handle_call(
                "test-agent",
                _factory,
                [],
                "fallback",
                mgr,
                notif,
                config,
                primary_worker="worker-1",
                grace_secs=0,
            )
        assert called["n"] == 0


@pytest.mark.asyncio
class TestPausePolicy:
    async def test_pause_policy_skips_fallback(self):
        """With policy=pause, fallback chain is never consulted."""
        config = _make_config()
        # Worker never recovers (no heartbeat)
        mgr = _make_cluster_manager()
        mgr.get_worker.return_value = None  # returns None = unreachable
        notif = _make_notif_store()

        fallback_called = {"n": 0}

        async def _fallback():
            fallback_called["n"] += 1
            return "fallback-result"

        import httpx
        with pytest.raises(WorkerUnavailableError):
            await handle_call(
                "test-agent",
                _fail_factory(httpx.ConnectError("down")),
                [("fallback-model", _fallback)],
                "pause",
                mgr,
                notif,
                config,
                primary_worker="worker-1",
                grace_secs=0,
            )

        # Fallback must NOT have been called
        assert fallback_called["n"] == 0

    async def test_pause_policy_marks_agent_paused(self):
        config = _make_config()
        mgr = _make_cluster_manager()
        mgr.get_worker.return_value = None
        notif = _make_notif_store()

        import httpx
        with pytest.raises(WorkerUnavailableError):
            await handle_call(
                "test-agent",
                _fail_factory(httpx.ConnectError("down")),
                [],
                "pause",
                mgr,
                notif,
                config,
                primary_worker="worker-1",
                grace_secs=0,
            )

        assert config.agents[0]["paused"] is True

    async def test_pause_policy_emits_notification(self):
        config = _make_config()
        mgr = _make_cluster_manager()
        mgr.get_worker.return_value = None
        notif = _make_notif_store()

        import httpx
        with pytest.raises(WorkerUnavailableError):
            await handle_call(
                "test-agent",
                _fail_factory(httpx.ConnectError("down")),
                [],
                "pause",
                mgr,
                notif,
                config,
                primary_worker="worker-1",
                grace_secs=0,
            )

        notif.add.assert_awaited_once()
        call_kwargs = notif.add.call_args
        assert "agent.paused" in str(call_kwargs)


@pytest.mark.asyncio
class TestFallbackPolicy:
    async def test_fallback_policy_walks_chain(self):
        config = _make_config()
        mgr = _make_cluster_manager()
        mgr.get_worker.return_value = None  # primary worker down
        notif = _make_notif_store()

        import httpx

        result = await handle_call(
            "test-agent",
            _fail_factory(httpx.ConnectError("primary down")),
            [
                ("model-b", _fail_factory(httpx.ConnectError("also down"))),
                ("model-c", _ok_factory("fallback-hit")),
            ],
            "fallback",
            mgr,
            notif,
            config,
            primary_worker="worker-1",
            grace_secs=0,
        )
        assert result == "fallback-hit"

    async def test_fallback_policy_pauses_when_all_exhausted(self):
        config = _make_config()
        mgr = _make_cluster_manager()
        mgr.get_worker.return_value = None
        notif = _make_notif_store()

        import httpx
        with pytest.raises(WorkerUnavailableError):
            await handle_call(
                "test-agent",
                _fail_factory(httpx.ConnectError("primary down")),
                [
                    ("model-b", _fail_factory(httpx.ConnectError("also down"))),
                ],
                "fallback",
                mgr,
                notif,
                config,
                primary_worker="worker-1",
                grace_secs=0,
            )

        assert config.agents[0]["paused"] is True
        notif.add.assert_awaited_once()

    async def test_fallback_policy_no_fallbacks_configured(self):
        """Empty fallback list still pauses after primary fails."""
        config = _make_config()
        mgr = _make_cluster_manager()
        mgr.get_worker.return_value = None
        notif = _make_notif_store()

        import httpx
        with pytest.raises(WorkerUnavailableError):
            await handle_call(
                "test-agent",
                _fail_factory(httpx.ConnectError("down")),
                [],
                "fallback",
                mgr,
                notif,
                config,
                primary_worker="worker-1",
                grace_secs=0,
            )
        assert config.agents[0]["paused"] is True


@pytest.mark.asyncio
class TestEscalateImmediately:
    async def test_escalate_skips_grace_and_fallback(self):
        config = _make_config()
        mgr = _make_cluster_manager()
        notif = _make_notif_store()

        fallback_called = {"n": 0}

        async def _fallback():
            fallback_called["n"] += 1
            return "fb"

        import httpx
        with pytest.raises(WorkerUnavailableError):
            await handle_call(
                "test-agent",
                _fail_factory(httpx.ConnectError("down")),
                [("model-b", _fallback)],
                "escalate-immediately",
                mgr,
                notif,
                config,
                primary_worker="worker-1",
                grace_secs=0,
            )

        assert fallback_called["n"] == 0
        assert config.agents[0]["paused"] is True
        notif.add.assert_awaited_once()

    async def test_escalate_pauses_immediately_after_retry_exhausted(self):
        config = _make_config()
        mgr = _make_cluster_manager()
        notif = _make_notif_store()

        import httpx
        with pytest.raises(WorkerUnavailableError):
            await handle_call(
                "test-agent",
                _fail_factory(httpx.ConnectError("down")),
                [],
                "escalate-immediately",
                mgr,
                notif,
                config,
                primary_worker="worker-1",
            )
        assert config.agents[0]["paused"] is True

    async def test_escalate_success_path_works(self):
        config = _make_config()
        mgr = _make_cluster_manager()
        notif = _make_notif_store()

        result = await handle_call(
            "test-agent",
            _ok_factory("good"),
            [],
            "escalate-immediately",
            mgr,
            notif,
            config,
            primary_worker="worker-1",
        )
        assert result == "good"
        notif.add.assert_not_awaited()


@pytest.mark.asyncio
class TestHeartbeatGrace:
    async def test_worker_recovers_in_grace_window(self):
        """If the worker sends a heartbeat during the grace window, retry succeeds."""
        config = _make_config()
        notif = _make_notif_store()

        mgr = MagicMock()
        call_count = {"n": 0}

        # Simulate a worker whose heartbeat advances after 1 poll
        worker = MagicMock()
        worker.last_heartbeat = 1000.0
        worker.status = "online"
        mgr.get_worker.return_value = worker

        import httpx

        async def _factory():
            call_count["n"] += 1
            # First call fails (retry gives up), second call (post-grace) succeeds
            if call_count["n"] <= 5:  # 5 = max_attempts in with_retry default
                raise httpx.ConnectError("still down")
            return "recovered"

        # Patch heartbeat polling so it immediately "detects" recovery
        async def _fake_wait(wname, cluster, grace, poll=1.0):
            # Advance the worker's heartbeat to simulate recovery
            worker.last_heartbeat = 2000.0
            return True

        with patch(
            "tinyagentos.scheduler.failure_handler._wait_for_heartbeat",
            side_effect=_fake_wait,
        ):
            result = await handle_call(
                "test-agent",
                _factory,
                [],
                "fallback",
                mgr,
                notif,
                config,
                primary_worker="worker-1",
                grace_secs=1,
            )
        assert result == "recovered"
        assert config.agents[0]["paused"] is False

    async def test_notification_includes_agent_and_worker_names(self):
        """Pause notification payload must name the agent and the failing worker."""
        config = _make_config(agent_name="my-agent")
        mgr = _make_cluster_manager()
        mgr.get_worker.return_value = None
        notif = _make_notif_store()

        import httpx
        with pytest.raises(WorkerUnavailableError):
            await handle_call(
                "my-agent",
                _fail_factory(httpx.ConnectError("down")),
                [],
                "pause",
                mgr,
                notif,
                config,
                primary_worker="node-42",
                grace_secs=0,
            )

        notif.add.assert_awaited_once()
        call_args = notif.add.call_args
        # Check both agent name and worker name appear in the notification
        all_args = str(call_args)
        assert "my-agent" in all_args
        assert "node-42" in all_args
        assert "agent.paused" in all_args
