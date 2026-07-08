"""Tests for the headless taOSnet passkey fetch.

The passkey fetch must degrade to "web-seed only" (return None) on every
failure mode so a passkey lookup can never block or crash a model download.
httpx is mocked via MockTransport so no network is touched.
"""
from __future__ import annotations

import httpx
import pytest

from tinyagentos.taosnet import passkey_client as pk


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_no_token_returns_none_without_calling():
    # No controller token means the host has not joined a mesh; skip the fetch.
    assert await pk.fetch_passkey(None) is None
    assert await pk.fetch_passkey("") is None


@pytest.mark.asyncio
async def test_passkey_returned_and_bearer_sent():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"passkey": "PK-abc123"})

    async with _client(handler) as c:
        result = await pk.fetch_passkey("tok-xyz", client=c)

    assert result == "PK-abc123"
    assert seen["auth"] == "Bearer tok-xyz"
    assert seen["path"] == "/api/taosnet/passkey"


@pytest.mark.asyncio
async def test_null_passkey_returns_none():
    # Account has no passkey issued yet -> web-seed only.
    async with _client(lambda r: httpx.Response(200, json={"passkey": None})) as c:
        assert await pk.fetch_passkey("tok", client=c) is None


@pytest.mark.asyncio
async def test_401_returns_none():
    # Token missing/invalid/revoked (host row gone) -> web-seed only.
    async with _client(lambda r: httpx.Response(401, json={"detail": "not_authenticated"})) as c:
        assert await pk.fetch_passkey("tok", client=c) is None


@pytest.mark.asyncio
async def test_server_error_returns_none():
    async with _client(lambda r: httpx.Response(500, text="boom")) as c:
        assert await pk.fetch_passkey("tok", client=c) is None


@pytest.mark.asyncio
async def test_transport_error_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    async with _client(handler) as c:
        assert await pk.fetch_passkey("tok", client=c) is None


@pytest.mark.asyncio
async def test_base_override_used():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        return httpx.Response(200, json={"passkey": "x"})

    async with _client(handler) as c:
        await pk.fetch_passkey("tok", base="https://example.test", client=c)

    assert seen["host"] == "example.test"


def test_get_controller_token_from_env(monkeypatch):
    monkeypatch.delenv("TAOS_CONTROLLER_TOKEN", raising=False)
    assert pk.get_controller_token() is None
    monkeypatch.setenv("TAOS_CONTROLLER_TOKEN", "")
    assert pk.get_controller_token() is None
    monkeypatch.setenv("TAOS_CONTROLLER_TOKEN", "the-token")
    assert pk.get_controller_token() == "the-token"


def test_taosnet_base_default_and_override(monkeypatch):
    monkeypatch.delenv("TAOS_TAOSNET_BASE", raising=False)
    assert pk.taosnet_base() == "https://taos.my"
    monkeypatch.setenv("TAOS_TAOSNET_BASE", "https://self.hosted/")
    assert pk.taosnet_base() == "https://self.hosted"
