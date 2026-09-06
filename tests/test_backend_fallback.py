from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tinyagentos.backend_fallback import BackendFallback


def _make_backends():
    return [
        {"name": "secondary", "type": "ollama", "url": "http://secondary:11434", "priority": 2},
        {"name": "primary", "type": "ollama", "url": "http://primary:11434", "priority": 1},
        {"name": "tertiary", "type": "rkllama", "url": "http://tertiary:8080", "priority": 3},
    ]


def _ok_response(data: dict | None = None):
    """Build a fake httpx.Response with 200 status."""
    request = httpx.Request("GET", "http://fake")
    resp = httpx.Response(200, json=data or {"status": "ok"}, request=request)
    return resp


def _error_response():
    """Build a fake httpx.Response with 500 status."""
    request = httpx.Request("GET", "http://fake")
    return httpx.Response(500, text="Internal Server Error", request=request)


@pytest.mark.asyncio
class TestBackendFallbackOrdering:
    async def test_backends_sorted_by_priority(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)
        fb = BackendFallback(backends, client)
        names = [b["name"] for b in fb.backends]
        assert names == ["primary", "secondary", "tertiary"]

    async def test_request_tries_highest_priority_first(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_ok_response({"model": "test"}))

        fb = BackendFallback(backends, client)
        data, name = await fb.request("GET", "/api/tags")

        assert name == "primary"
        assert data == {"model": "test"}
        # Should have been called only once (primary succeeded)
        assert client.get.call_count == 1
        call_url = client.get.call_args[0][0]
        assert "primary" in call_url


@pytest.mark.asyncio
class TestBackendFallbackFailover:
    async def test_failover_to_next_backend(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)

        # Primary fails, secondary succeeds
        client.get = AsyncMock(side_effect=[
            httpx.ConnectError("connection refused"),
            _ok_response({"model": "fallback"}),
        ])

        fb = BackendFallback(backends, client)
        data, name = await fb.request("GET", "/api/tags")

        assert name == "secondary"
        assert data == {"model": "fallback"}
        assert client.get.call_count == 2

    async def test_failover_skips_to_third_when_two_fail(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)

        client.post = AsyncMock(side_effect=[
            httpx.ConnectError("connection refused"),
            httpx.ConnectError("connection refused"),
            _ok_response({"result": "ok"}),
        ])

        fb = BackendFallback(backends, client)
        data, name = await fb.request("POST", "/api/generate", json={"prompt": "hi"})

        assert name == "tertiary"
        assert data == {"result": "ok"}

    async def test_all_backends_down_returns_none(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        fb = BackendFallback(backends, client)
        data, name = await fb.request("GET", "/api/tags")

        assert data is None
        assert name is None


@pytest.mark.asyncio
class TestBackendFallbackBackoff:
    async def test_recently_failed_backend_is_skipped(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)

        # First call: primary fails, secondary succeeds
        client.get = AsyncMock(side_effect=[
            httpx.ConnectError("connection refused"),
            _ok_response({"model": "secondary"}),
        ])

        fb = BackendFallback(backends, client)
        data, name = await fb.request("GET", "/api/tags")
        assert name == "secondary"

        # Second call: primary should be skipped due to backoff
        client.get.reset_mock()
        client.get = AsyncMock(return_value=_ok_response({"model": "secondary-again"}))

        data, name = await fb.request("GET", "/api/tags")
        assert name == "secondary"
        # Only one call made (primary was skipped)
        assert client.get.call_count == 1

    async def test_backend_recovers_after_backoff_expires(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)

        fb = BackendFallback(backends, client)
        fb._backoff_seconds = 30

        # Mark primary as failed in the past (beyond backoff)
        fb._last_failed["primary"] = time.time() - 60

        client.get = AsyncMock(return_value=_ok_response({"model": "primary-recovered"}))

        data, name = await fb.request("GET", "/api/tags")
        assert name == "primary"
        # Failure record should be cleared
        assert "primary" not in fb._last_failed


@pytest.mark.asyncio
class TestGetHealthyBackend:
    async def test_returns_highest_priority_healthy(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_ok_response())

        fb = BackendFallback(backends, client)
        result = await fb.get_healthy_backend()

        assert result is not None
        assert result["name"] == "primary"

    async def test_skips_unhealthy_returns_next(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[
            httpx.ConnectError("refused"),
            _ok_response(),
        ])

        fb = BackendFallback(backends, client)
        result = await fb.get_healthy_backend()

        assert result is not None
        assert result["name"] == "secondary"

    async def test_all_unhealthy_returns_none(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        fb = BackendFallback(backends, client)
        result = await fb.get_healthy_backend()
        assert result is None


@pytest.mark.asyncio
class TestGetStatus:
    async def test_status_unknown_initially(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)
        fb = BackendFallback(backends, client)

        status = fb.get_status()
        assert len(status) == 3
        assert all(s["status"] == "unknown" for s in status)

    async def test_status_reflects_health(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)
        fb = BackendFallback(backends, client)

        now = time.time()
        fb._last_healthy["primary"] = now
        fb._last_failed["secondary"] = now

        status = fb.get_status()
        status_map = {s["name"]: s["status"] for s in status}
        assert status_map["primary"] == "healthy"
        assert status_map["secondary"] == "down"
        assert status_map["tertiary"] == "unknown"


@pytest.mark.asyncio
class TestGetPrimaryBackend:
    async def test_returns_highest_priority_when_all_healthy(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)
        fb = BackendFallback(backends, client)

        primary = fb.get_primary_backend()
        assert primary is not None
        assert primary["name"] == "primary"

    async def test_skips_recently_failed(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)
        fb = BackendFallback(backends, client)

        fb._last_failed["primary"] = time.time()

        primary = fb.get_primary_backend()
        assert primary is not None
        assert primary["name"] == "secondary"

    async def test_returns_none_when_all_failed(self):
        backends = _make_backends()
        client = AsyncMock(spec=httpx.AsyncClient)
        fb = BackendFallback(backends, client)

        now = time.time()
        fb._last_failed["primary"] = now
        fb._last_failed["secondary"] = now
        fb._last_failed["tertiary"] = now

        primary = fb.get_primary_backend()
        assert primary is None
