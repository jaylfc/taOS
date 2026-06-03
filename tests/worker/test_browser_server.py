"""Tests for tinyagentos.worker.browser_server."""
from __future__ import annotations

import pytest
import httpx
from httpx import ASGITransport

from tinyagentos.worker.browser_container import BrowserContainerError, BrowserContainerRunner
from tinyagentos.worker.browser_server import create_browser_worker_app


def _make_app(auth_token: str | None = None):
    runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)
    return create_browser_worker_app(runner, auth_token=auth_token)


# ---------------------------------------------------------------------------
# No-auth app
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBrowserServerNoAuth:
    async def _client(self):
        app = _make_app()
        transport = ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")

    async def test_start_returns_200(self):
        async with await self._client() as client:
            resp = await client.post(
                "/worker/browser/start",
                json={"session_id": "sess-abc123", "profile_volume": "taos-browser-sess-abc123"},
            )
        assert resp.status_code == 200

    async def test_start_response_has_expected_fields(self):
        async with await self._client() as client:
            resp = await client.post(
                "/worker/browser/start",
                json={"session_id": "sess-abc123", "profile_volume": "taos-browser-sess-abc123"},
            )
        data = resp.json()
        for field in ("container_id", "neko_url", "cdp_url", "http_port", "epr_lo", "epr_hi"):
            assert field in data, f"missing field: {field}"

    async def test_start_container_id_is_mock(self):
        async with await self._client() as client:
            resp = await client.post(
                "/worker/browser/start",
                json={"session_id": "sess-abc123", "profile_volume": "vol1"},
            )
        assert resp.json()["container_id"].startswith("mock-neko-")

    async def test_stop_returns_200(self):
        async with await self._client() as client:
            # Start first so we have a container_id
            start = await client.post(
                "/worker/browser/start",
                json={"session_id": "sess-stop-test", "profile_volume": "vol1"},
            )
            container_id = start.json()["container_id"]
            resp = await client.post(
                "/worker/browser/stop",
                json={"container_id": container_id},
            )
        assert resp.status_code == 200

    async def test_stop_returns_ok_true(self):
        async with await self._client() as client:
            start = await client.post(
                "/worker/browser/start",
                json={"session_id": "sess-stop-test2", "profile_volume": "vol1"},
            )
            container_id = start.json()["container_id"]
            resp = await client.post(
                "/worker/browser/stop",
                json={"container_id": container_id},
            )
        assert resp.json() == {"ok": True}

    async def test_stop_with_http_port(self):
        async with await self._client() as client:
            start = await client.post(
                "/worker/browser/start",
                json={"session_id": "sess-stop-port", "profile_volume": "vol1"},
            )
            data = start.json()
            resp = await client.post(
                "/worker/browser/stop",
                json={"container_id": data["container_id"], "http_port": data["http_port"]},
            )
        assert resp.json() == {"ok": True}

    async def test_start_container_error_is_500(self):
        """A BrowserContainerError from the runner surfaces as HTTP 500."""
        runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)

        async def _boom(*args, **kwargs):
            raise BrowserContainerError("docker run failed (rc=1): boom")

        runner.start = _boom  # type: ignore[method-assign]
        app = create_browser_worker_app(runner)
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/worker/browser/start",
                json={"session_id": "sess-err", "profile_volume": "vol1"},
            )
        assert resp.status_code == 500
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# Auth-required app
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBrowserServerWithAuth:
    def _app(self):
        return _make_app(auth_token="secret")

    async def test_missing_auth_header_is_401_start(self):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=self._app()), base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/worker/browser/start",
                json={"session_id": "s1", "profile_volume": "v1"},
            )
        assert resp.status_code == 401

    async def test_wrong_token_is_401_start(self):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=self._app()), base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/worker/browser/start",
                json={"session_id": "s1", "profile_volume": "v1"},
                headers={"Authorization": "Bearer wrongtoken"},
            )
        assert resp.status_code == 401

    async def test_correct_token_is_200_start(self):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=self._app()), base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/worker/browser/start",
                json={"session_id": "s1", "profile_volume": "v1"},
                headers={"Authorization": "Bearer secret"},
            )
        assert resp.status_code == 200

    async def test_missing_auth_header_is_401_stop(self):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=self._app()), base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/worker/browser/stop",
                json={"container_id": "mock-neko-abc12345"},
            )
        assert resp.status_code == 401

    async def test_correct_token_is_200_stop(self):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=self._app()), base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/worker/browser/stop",
                json={"container_id": "mock-neko-abc12345"},
                headers={"Authorization": "Bearer secret"},
            )
        assert resp.status_code == 200
