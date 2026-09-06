from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from tinyagentos.lifecycle_manager import LifecycleManager


def _make_catalog(lifecycle_states: dict, backends_config: list[dict]):
    catalog = MagicMock()
    catalog.get_lifecycle_state = lambda name: lifecycle_states.get(name, "running")
    catalog.set_lifecycle_state = MagicMock(side_effect=lambda n, s: lifecycle_states.update({n: s}))
    catalog._backends_config = backends_config
    return catalog


@pytest.mark.asyncio
async def test_start_sets_state_to_running():
    """start() should transition stopped → starting → running on success."""
    states = {"b1": "stopped"}
    backends = [
        {
            "name": "b1", "type": "sd-cpp", "url": "http://b1",
            "start_cmd": "true",   # shell no-op that exits 0
            "startup_timeout_seconds": 5,
        }
    ]
    catalog = _make_catalog(states, backends)

    mgr = LifecycleManager(catalog)
    mgr._probe_health = AsyncMock(return_value=True)
    await mgr.start("b1")
    assert states["b1"] == "running"


@pytest.mark.asyncio
async def test_start_sets_error_on_timeout():
    """start() should set state to stopped if health probe never succeeds."""
    states = {"b1": "stopped"}
    backends = [
        {
            "name": "b1", "type": "sd-cpp", "url": "http://b1",
            "start_cmd": "true",
            "startup_timeout_seconds": 1,
        }
    ]
    catalog = _make_catalog(states, backends)

    mgr = LifecycleManager(catalog)
    mgr._probe_health = AsyncMock(return_value=False)  # never healthy
    with pytest.raises(TimeoutError):
        await mgr.start("b1")
    assert states["b1"] == "stopped"


@pytest.mark.asyncio
async def test_drain_and_stop_graceful():
    """drain_and_stop() should drain then stop the service."""
    states = {"b1": "running"}
    backends = [
        {
            "name": "b1", "type": "sd-cpp", "url": "http://b1",
            "stop_cmd": "true",
        }
    ]
    catalog = _make_catalog(states, backends)
    catalog.in_flight_count = MagicMock(return_value=0)

    mgr = LifecycleManager(catalog)
    await mgr.drain_and_stop("b1", force=False)
    assert states["b1"] == "stopped"


@pytest.mark.asyncio
async def test_kill_stops_immediately():
    """drain_and_stop(force=True) skips drain and stops immediately."""
    states = {"b1": "running"}
    backends = [
        {
            "name": "b1", "type": "sd-cpp", "url": "http://b1",
            "stop_cmd": "true",
        }
    ]
    catalog = _make_catalog(states, backends)

    mgr = LifecycleManager(catalog)
    await mgr.drain_and_stop("b1", force=True)
    assert states["b1"] == "stopped"


@pytest.mark.asyncio
async def test_keepalive_zero_never_stops():
    """keep_alive_minutes=0 means the keepalive timer is never started."""
    states = {"b1": "running"}
    backends = [
        {
            "name": "b1", "type": "rkllama", "url": "http://b1",
            "stop_cmd": "true", "keep_alive_minutes": 0,
        }
    ]
    catalog = _make_catalog(states, backends)

    mgr = LifecycleManager(catalog)
    mgr.notify_task_complete("b1")
    # Give the event loop a tick
    await asyncio.sleep(0)
    # Timer should NOT have been started — state stays running
    assert states["b1"] == "running"
    assert "b1" not in mgr._keepalive_tasks
