"""Tests for framework adapters (HTTP proxy pattern).

These adapters proxy to standalone binary frameworks. The health endpoint
should always return ok. The message endpoint will return a connection error when the
binary gateway is not running (expected in unit tests).
"""
import importlib
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


ADAPTERS = [
    ("microclaw", "tinyagentos.adapters.microclaw_adapter"),
    ("ironclaw", "tinyagentos.adapters.ironclaw_adapter"),
    ("nullclaw", "tinyagentos.adapters.nullclaw_adapter"),
    ("moltis", "tinyagentos.adapters.moltis_adapter"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("framework,module_path", ADAPTERS)
async def test_adapter_health(framework, module_path):
    mod = importlib.import_module(module_path)
    app = mod.app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["framework"] == framework


@pytest.mark.asyncio
@pytest.mark.parametrize("framework,module_path", ADAPTERS)
async def test_adapter_message_without_framework(framework, module_path):
    """Proxy adapters return an error message when the gateway is not running.

    Adapters that use with_retry (nullclaw, moltis) apply exponential backoff
    — up to 31 s of sleep over 7 attempts — when no framework process is
    listening.  In CI this turns a fast unit test into a 31 s hang per case.

    Fix: patch with_retry in the adapter module so it calls the factory once
    (max_attempts=1) with no delay.  The retry policy itself is covered by
    test_adapter_retry.py; this test only checks that the adapter surfaces a
    non-empty error string.
    """
    mod = importlib.import_module(module_path)
    app = mod.app
    transport = ASGITransport(app=app)

    async def _single_attempt(factory, **_kwargs):
        """Drop-in for with_retry that executes the factory once, no sleep."""
        return await factory()

    # Only adapters that import with_retry need this patch.
    ctx = (
        patch.object(mod, "with_retry", side_effect=_single_attempt)
        if hasattr(mod, "with_retry")
        else _null_context()
    )
    with ctx:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/message", json={"text": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        # Proxy adapters return a connection error string when the binary is not running
        assert "content" in data
        assert isinstance(data["content"], str)
        assert len(data["content"]) > 0


class _null_context:
    """Trivial no-op context manager for adapters that don't use with_retry."""
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False
