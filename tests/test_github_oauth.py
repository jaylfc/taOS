"""Tests for the GitHub OAuth device-flow routes + identities store.

A minimal FastAPI app with only the github_oauth router mounted, plus a real
GitHubIdentitiesStore on a tmp_path DB, so the tests run fast without the full
create_app initialisation.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tinyagentos.github_identities import GitHubIdentitiesStore
from tinyagentos.routes.github_oauth import router as github_oauth_router


def _make_response(data, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=data)
    resp.text = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
    resp.raise_for_status = MagicMock()
    return resp


@pytest_asyncio.fixture
async def store(tmp_path):
    s = GitHubIdentitiesStore(tmp_path / "github_identities.db")
    await s.init()
    yield s
    await s.close()


def _build_app(store, *, post_effects=(), get_effects=()):
    app = FastAPI()
    app.include_router(github_oauth_router)
    http = MagicMock()
    http.post = AsyncMock(side_effect=list(post_effects)) if post_effects else AsyncMock()
    http.get = AsyncMock(side_effect=list(get_effects)) if get_effects else AsyncMock()
    app.state.http_client = http
    app.state.github_identities = store
    return app


@pytest_asyncio.fixture
async def client_factory(store):
    clients = []

    async def _make(**kwargs):
        app = _build_app(store, **kwargs)
        transport = ASGITransport(app=app)
        c = AsyncClient(transport=transport, base_url="http://test")
        clients.append(c)
        return c

    yield _make
    for c in clients:
        await c.aclose()


# ---------------------------------------------------------------------------
# device/start
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_device_start_returns_user_code(client_factory):
    gh = _make_response({
        "device_code": "DEV123",
        "user_code": "WXYZ-1234",
        "verification_uri": "https://github.com/login/device",
        "interval": 5,
        "expires_in": 900,
    })
    c = await client_factory(post_effects=[gh])
    resp = await c.post("/api/github/oauth/device/start")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_code"] == "WXYZ-1234"
    assert data["device_code"] == "DEV123"
    assert data["verification_uri"] == "https://github.com/login/device"
    assert data["interval"] == 5


@pytest.mark.asyncio
async def test_device_start_bad_response_returns_502(client_factory):
    gh = _make_response({"error": "invalid_client", "error_description": "Bad client"})
    c = await client_factory(post_effects=[gh])
    resp = await c.post("/api/github/oauth/device/start")
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# device/poll
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_device_poll_pending(client_factory):
    gh = _make_response({"error": "authorization_pending"})
    c = await client_factory(post_effects=[gh])
    resp = await c.post("/api/github/oauth/device/poll", json={"device_code": "DEV123"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_device_poll_slow_down_is_pending(client_factory):
    gh = _make_response({"error": "slow_down"})
    c = await client_factory(post_effects=[gh])
    resp = await c.post("/api/github/oauth/device/poll", json={"device_code": "DEV123"})
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_device_poll_connected_stores_identity(client_factory, store):
    token_resp = _make_response({"access_token": "gho_secrettoken", "scope": "repo,read:user"})
    user_resp = _make_response({"login": "octocat", "avatar_url": "https://avatars/octocat.png"})
    c = await client_factory(post_effects=[token_resp], get_effects=[user_resp])

    resp = await c.post("/api/github/oauth/device/poll", json={"device_code": "DEV123"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "connected"
    assert data["identity"]["login"] == "octocat"
    assert data["identity"]["avatar_url"] == "https://avatars/octocat.png"
    # No token must ever appear in the response payload.
    assert "token" not in json.dumps(data)

    # The token was stored encrypted and is retrievable internally.
    identities = await store.list()
    assert len(identities) == 1
    identity_id = identities[0]["id"]
    assert await store.get_token(identity_id) == "gho_secrettoken"


@pytest.mark.asyncio
async def test_device_poll_expired_is_error(client_factory):
    gh = _make_response({"error": "expired_token"})
    c = await client_factory(post_effects=[gh])
    resp = await c.post("/api/github/oauth/device/poll", json={"device_code": "DEV123"})
    data = resp.json()
    assert data["status"] == "error"
    assert data["error"] == "expired_token"


# ---------------------------------------------------------------------------
# identities list / delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_identities_list_excludes_token(client_factory, store):
    await store.add("octocat", "https://avatars/octocat.png", "gho_secrettoken", "repo")
    c = await client_factory()
    resp = await c.get("/api/github/identities")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert set(items[0].keys()) == {"id", "login", "avatar_url", "created_at"}
    assert "token" not in json.dumps(items)
    assert "gho_secrettoken" not in json.dumps(items)


@pytest.mark.asyncio
async def test_delete_identity(client_factory, store):
    identity = await store.add("octocat", "", "gho_secrettoken", "repo")
    c = await client_factory()
    resp = await c.delete(f"/api/github/identities/{identity['id']}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    assert await store.list() == []


@pytest.mark.asyncio
async def test_delete_identity_invalid_uuid_returns_400(client_factory):
    c = await client_factory()
    resp = await c.delete("/api/github/identities/not-a-uuid")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_identity_not_found_returns_404(client_factory):
    import uuid as _uuid
    c = await client_factory()
    resp = await c.delete(f"/api/github/identities/{_uuid.uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Store: token encryption at rest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_token_encrypted_at_rest(store):
    identity = await store.add("octocat", "", "gho_plaintexttoken", "repo")
    async with store._db.execute(
        "SELECT token FROM github_identities WHERE id = ?", (identity["id"],)
    ) as cur:
        row = await cur.fetchone()
    # Raw DB value must NOT be the plaintext token.
    assert row[0] != "gho_plaintexttoken"
    assert "gho_plaintexttoken" not in row[0]
    # And get_token decrypts it back.
    assert await store.get_token(identity["id"]) == "gho_plaintexttoken"
