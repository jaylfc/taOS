"""Integration test for the worker-failure pause-and-notify flow.

This test exercises the full path:

    primary_call raises -> retry wrapper exhausts
    -> heartbeat grace polls cluster_manager (no recovery)
    -> fallback chain walked (each fallback fails the same way)
    -> agent marked paused
    -> notification added to a real NotificationStore

Where the unit tests in test_scheduler_failure_handler.py use mocks for
every dependency, this test wires up a real NotificationStore and a
simple in-memory cluster manager to catch regressions in the contract
between the handler and the things it calls into.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from tinyagentos.notifications import NotificationStore
from tinyagentos.scheduler.failure_handler import (
    WorkerUnavailableError,
    handle_call,
)


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    async def _noop(_s):
        pass

    monkeypatch.setattr("tinyagentos.clients.retry.asyncio.sleep", _noop)


class FakeWorker:
    def __init__(self, name: str, online: bool = False):
        self.name = name
        self.status = "online" if online else "offline"
        self.last_heartbeat = 0.0


class FakeClusterManager:
    """Minimal cluster manager that never recovers any worker."""

    def __init__(self, *workers: FakeWorker):
        self._by_name = {w.name: w for w in workers}

    def get_worker(self, name: str):
        return self._by_name.get(name)


class FakeConfig:
    def __init__(self, agents: list[dict]):
        self.agents = agents


@pytest.mark.asyncio
async def test_full_flow_fallback_chain_exhausted_pauses_agent(tmp_path):
    config = FakeConfig(
        agents=[
            {
                "name": "research-agent",
                "host": "10.0.0.1",
                "color": "#fff",
                "on_worker_failure": "fallback",
                "fallback_models": ["fallback-a", "fallback-b"],
                "paused": False,
            }
        ]
    )
    cluster = FakeClusterManager(
        FakeWorker("primary-worker", online=False),
        FakeWorker("fallback-a", online=False),
        FakeWorker("fallback-b", online=False),
    )

    notif_path = tmp_path / "notifications.db"
    notif_store = NotificationStore(notif_path)
    await notif_store.init()

    call_counts = {"primary": 0, "fallback-a": 0, "fallback-b": 0}

    def _make_failing_call(key: str):
        async def _call():
            call_counts[key] += 1
            raise ConnectionError(f"{key} down")

        return _call

    with pytest.raises(WorkerUnavailableError):
        await handle_call(
            agent_name="research-agent",
            primary_call=_make_failing_call("primary"),
            fallback_calls=[
                ("fallback-a", _make_failing_call("fallback-a")),
                ("fallback-b", _make_failing_call("fallback-b")),
            ],
            policy="fallback",
            cluster_manager=cluster,
            notif_store=notif_store,
            config=config,
            primary_worker="primary-worker",
            grace_secs=0.1,
        )

    # Primary was tried, plus each fallback was tried.
    assert call_counts["primary"] >= 1
    assert call_counts["fallback-a"] >= 1
    assert call_counts["fallback-b"] >= 1

    # Agent is now paused in config.
    assert config.agents[0]["paused"] is True

    # A notification was emitted.
    rows = await notif_store.list(limit=10)
    assert any(
        "research-agent" in (n.get("title", "") or "")
        and n.get("level") == "error"
        for n in rows
    ), f"Expected pause notification, got {rows}"

    await notif_store.close()


@pytest.mark.asyncio
async def test_paused_agent_rejects_new_calls(tmp_path):
    config = FakeConfig(
        agents=[
            {
                "name": "paused-agent",
                "host": "10.0.0.1",
                "color": "#fff",
                "on_worker_failure": "fallback",
                "fallback_models": [],
                "paused": True,
            }
        ]
    )
    cluster = FakeClusterManager(FakeWorker("primary-worker", online=True))

    notif_store = NotificationStore(tmp_path / "notifications.db")
    await notif_store.init()

    calls = 0

    async def _call():
        nonlocal calls
        calls += 1
        return "should-not-run"

    from tinyagentos.scheduler.failure_handler import AgentPausedError

    with pytest.raises(AgentPausedError):
        await handle_call(
            agent_name="paused-agent",
            primary_call=_call,
            fallback_calls=[],
            policy="fallback",
            cluster_manager=cluster,
            notif_store=notif_store,
            config=config,
            primary_worker="primary-worker",
            grace_secs=0.1,
        )

    assert calls == 0

    await notif_store.close()
