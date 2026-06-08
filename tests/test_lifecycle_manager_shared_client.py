"""Tests for #660 — shared httpx.AsyncClient reuse in lifecycle_manager health probes."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

import httpx

from tinyagentos.lifecycle_manager import LifecycleManager


def _make_catalog(lifecycle_states: dict, backends_config: list[dict]):
    catalog = MagicMock()
    catalog.get_lifecycle_state = lambda name: lifecycle_states.get(name, "running")
    catalog.set_lifecycle_state = MagicMock(
        side_effect=lambda n, s: lifecycle_states.update({n: s})
    )
    catalog._backends_config = backends_config
    return catalog


@pytest.mark.asyncio
async def test_probe_health_uses_shared_client_when_set():
    """_probe_health must call shared_client.get instead of opening a new client."""
    catalog = _make_catalog({}, [])
    mgr = LifecycleManager(catalog)

    mock_client = MagicMock()
    mock_client.get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=MagicMock(return_value={"status": "ok"}),
        )
    )
    mgr.shared_client = mock_client

    result = await mgr._probe_health("http://localhost:1234")

    assert result is True
    mock_client.get.assert_awaited_once_with("http://localhost:1234/health", timeout=3)


@pytest.mark.asyncio
async def test_probe_health_falls_back_without_shared_client():
    """_probe_health must still work when shared_client is None (uses one-shot client)."""
    catalog = _make_catalog({}, [])
    mgr = LifecycleManager(catalog)
    assert mgr.shared_client is None  # default

    # httpx is imported inside _probe_health, so patch via sys.modules
    mock_response = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"status": "ok"}),
    )

    class _MockAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, timeout=None):
            return mock_response

    import unittest.mock as _mock
    with _mock.patch("httpx.AsyncClient", _MockAsyncClient):
        result = await mgr._probe_health("http://localhost:5678")

    assert result is True


@pytest.mark.asyncio
async def test_shared_client_exception_returns_false():
    """_probe_health must return False (not raise) when shared_client raises."""
    catalog = _make_catalog({}, [])
    mgr = LifecycleManager(catalog)

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mgr.shared_client = mock_client

    result = await mgr._probe_health("http://localhost:9999")
    assert result is False


@pytest.mark.asyncio
async def test_start_uses_shared_client_in_probe():
    """start() health polling must use the shared client when injected."""
    states = {"svc": "stopped"}
    backends = [
        {
            "name": "svc", "type": "rkllama", "url": "http://localhost:9000",
            "start_cmd": "true",
            "startup_timeout_seconds": 5,
        }
    ]
    catalog = _make_catalog(states, backends)
    mgr = LifecycleManager(catalog)

    probe_calls: list[str] = []

    mock_client = MagicMock()

    async def _mock_get(url, timeout):
        probe_calls.append(url)
        return MagicMock(
            status_code=200,
            json=MagicMock(return_value={"status": "ok"}),
        )

    mock_client.get = _mock_get
    mgr.shared_client = mock_client

    await mgr.start("svc")
    assert states["svc"] == "running"
    assert any("9000/health" in u for u in probe_calls)
