"""Sandbox/CSP isolation for /apps/{id}/ service proxy responses."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_upstream_resp():
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "text/html"}

    async def _iter():
        yield b"ok"

    resp.aiter_bytes = _iter
    resp.aclose = AsyncMock()
    return resp


def _make_mock_client(return_value):
    mock_client = MagicMock()

    def fake_build(method, url, **kwargs):
        req = MagicMock()
        req.url = url
        return req

    mock_client.build_request = MagicMock(side_effect=fake_build)
    mock_client.send = AsyncMock(return_value=return_value)
    return mock_client


@pytest.mark.asyncio
async def test_service_proxy_sets_sandbox_csp_without_allow_same_origin(client, monkeypatch):
    mock_store = MagicMock()
    mock_store.get_runtime_location = AsyncMock(
        return_value={"runtime_host": "127.0.0.1", "runtime_port": 13000, "ui_path": "/"}
    )
    mock_store.is_installed = AsyncMock(return_value=True)
    monkeypatch.setattr(client._transport.app.state, "installed_apps", mock_store)

    mock_client = _make_mock_client(_make_upstream_resp())
    with patch("tinyagentos.routes.service_proxy._http_client", mock_client):
        resp = await client.get("/apps/myapp/")

    assert resp.status_code == 200
    csp = resp.headers.get("content-security-policy", "").lower()
    assert "sandbox" in csp
    assert "allow-same-origin" not in csp
