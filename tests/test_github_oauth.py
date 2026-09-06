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


def _make_test_rsa_key() -> str:
    """Generate a throwaway RSA 2048-bit keypair for test JWT signing."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


_TEST_PRIVATE_KEY_PEM = _make_test_rsa_key()


def _make_mock_secrets(*, private_key: str | None = None) -> MagicMock:
    """Return a mock SecretsStore that returns ``private_key`` for the App key."""
    secrets = MagicMock()
    if private_key is not None:
        async def _get(name: str):
            if name == "github-app-private-key":
                return {"value": private_key}
            return None
        secrets.get = AsyncMock(side_effect=_get)
    else:
        secrets.get = AsyncMock(return_value=None)
    return secrets


def _make_mock_config(github_app_id: str = "123456") -> MagicMock:
    """Return a mock AppConfig with a github_app_id."""
    cfg = MagicMock()
    cfg.github_app_id = github_app_id
    return cfg


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
    body = resp.json()
    assert body["status"] == "pending"
    # The frontend backs off its poll interval when slow_down is signalled.
    assert body.get("slow_down") is True


@pytest.mark.asyncio
async def test_reconnect_same_login_updates_not_duplicates(store):
    first = await store.add("octocat", "a1", "gho_token1", "repo")
    second = await store.add("octocat", "a2", "gho_token2", "repo")
    # Same login -> same row refreshed in place, no duplicate.
    assert first["id"] == second["id"]
    assert second["avatar_url"] == "a2"
    identities = await store.list()
    assert len(identities) == 1
    assert await store.get_token(first["id"]) == "gho_token2"


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


# ---------------------------------------------------------------------------
# GitHub App installation endpoint tests
# ---------------------------------------------------------------------------

def _build_app_for_app_tests(
    store,
    *,
    config=None,
    installations=None,
    secrets=None,
    post_effects=(),
    get_effects=(),
    delete_effects=(),
):
    """Build a FastAPI app with App-specific state for installation tests."""
    app = FastAPI()
    app.include_router(github_oauth_router)
    http = MagicMock()
    http.post = AsyncMock(side_effect=list(post_effects)) if post_effects else AsyncMock()
    http.get = AsyncMock(side_effect=list(get_effects)) if get_effects else AsyncMock()
    http.delete = AsyncMock(side_effect=list(delete_effects)) if delete_effects else AsyncMock()
    app.state.http_client = http
    app.state.github_identities = store
    app.state.config = config
    app.state.secrets = secrets
    app.state.github_app_installations = installations
    return app


@pytest_asyncio.fixture
async def app_client_factory(store):
    """Factory that returns an AsyncClient wired to a minimal app with App state."""
    clients = []

    async def _make(**kwargs):
        app = _build_app_for_app_tests(store, **kwargs)
        transport = ASGITransport(app=app)
        c = AsyncClient(transport=transport, base_url="http://test")
        clients.append(c)
        return c

    yield _make
    for c in clients:
        await c.aclose()


# ---------------------------------------------------------------------------
# TestAppInstallationsList — list installations
# ---------------------------------------------------------------------------

class TestAppInstallationsList:
    @pytest.mark.asyncio
    async def test_returns_501_when_app_not_configured(self, app_client_factory):
        """GET /api/github/app/installations returns 501 if app not configured."""
        client = await app_client_factory()
        resp = await client.get(
            "/api/github/app/installations",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 501
        data = resp.json()
        assert "error" in data
        assert "not configured" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_returns_501_when_app_id_set_but_no_private_key(self, app_client_factory):
        """GET /api/github/app/installations returns 501 when config has app_id but no secret."""
        cfg = _make_mock_config()
        client = await app_client_factory(config=cfg)
        resp = await client.get(
            "/api/github/app/installations",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 501
        data = resp.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_returns_installations_when_configured(self, app_client_factory, store):
        """List installations with mocked GitHub API calls.

        Verifies the happy path: GitHub returns installations, we mint tokens
        and fetch repos for each, then return the combined response.
        """
        cfg = _make_mock_config()
        secrets = _make_mock_secrets(private_key=_TEST_PRIVATE_KEY_PEM)

        # Mock GitHub API responses:
        # 1. GET /app/installations → list of one installation
        # 2. POST /app/installations/{id}/access_tokens → token
        # 3. GET /installation/repositories → repos
        install_list = [{
            "id": 42,
            "account": {"login": "octocat", "type": "User", "avatar_url": "https://a.co/1.png"},
            "repository_selection": "selected",
            "created_at": "2026-01-01T00:00:00Z",
        }]
        token_resp = _make_response({"token": "ghs_testtoken"})
        repos_resp = _make_response({"repositories": [
            {"full_name": "octocat/hello-world", "name": "hello-world",
             "private": False, "description": "test repo"},
        ]})

        client = await app_client_factory(
            config=cfg,
            secrets=secrets,
            get_effects=[_make_response(install_list), repos_resp],
            post_effects=[token_resp],
        )
        resp = await client.get(
            "/api/github/app/installations",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "installations" in data
        assert len(data["installations"]) == 1
        inst = data["installations"][0]
        assert inst["id"] == 42
        assert inst["account"]["login"] == "octocat"
        assert len(inst["repositories"]) == 1
        assert inst["repositories"][0]["full_name"] == "octocat/hello-world"


# ---------------------------------------------------------------------------
# TestAppInstall — begin installation
# ---------------------------------------------------------------------------

class TestAppInstall:
    @pytest.mark.asyncio
    async def test_returns_501_when_app_not_configured(self, app_client_factory):
        """POST /api/github/app/install returns 501 if app not configured."""
        client = await app_client_factory()
        resp = await client.post(
            "/api/github/app/install",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 501
        data = resp.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_returns_install_url_when_configured(self, app_client_factory, store):
        """POST /api/github/app/install returns the GitHub installation URL."""
        cfg = _make_mock_config()
        secrets = _make_mock_secrets(private_key=_TEST_PRIVATE_KEY_PEM)

        # Mock: GET /app returns {"slug": "my-github-app"}
        app_slug_resp = _make_response({"slug": "my-github-app"})

        client = await app_client_factory(
            config=cfg,
            secrets=secrets,
            get_effects=[app_slug_resp],
        )
        resp = await client.post(
            "/api/github/app/install",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "install_url" in data
        assert "my-github-app/installations/new" in data["install_url"]


# ---------------------------------------------------------------------------
# TestAppCallback — post-install callback
# ---------------------------------------------------------------------------

class TestAppCallback:
    @pytest.mark.asyncio
    async def test_returns_400_without_installation_id(self, app_client_factory):
        """GET /api/github/app/callback without installation_id returns 400."""
        client = await app_client_factory()
        resp = await client.get(
            "/api/github/app/callback",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "installation_id" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_returns_501_with_installation_id_but_no_app_config(self, app_client_factory):
        """GET /api/github/app/callback?installation_id=1 returns 501 if app missing."""
        client = await app_client_factory()
        resp = await client.get(
            "/api/github/app/callback?installation_id=12345",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 501
        data = resp.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_successful_callback_records_installation(self, app_client_factory, store, tmp_path):
        """Successful callback fetches details and records the installation."""
        from tinyagentos.github_app_installations import GitHubAppInstallations

        cfg = _make_mock_config()
        secrets = _make_mock_secrets(private_key=_TEST_PRIVATE_KEY_PEM)
        installs = GitHubAppInstallations(tmp_path / "app_installs")
        await installs.init()

        token_resp = _make_response({"token": "ghs_callbacktoken"})
        detail_resp = _make_response({
            "account": {"login": "someorg", "type": "Organization", "avatar_url": "https://a.co/o.png"},
            "repository_selection": "all",
        })

        client = await app_client_factory(
            config=cfg,
            secrets=secrets,
            installations=installs,
            post_effects=[token_resp],
            get_effects=[detail_resp],
        )
        resp = await client.get(
            "/api/github/app/callback?installation_id=99",
            headers={"Accept": "application/json"},
        )
        # Success redirects to /app/secrets (or /app/secrets?install_warning=1)
        assert resp.status_code in (200, 302)

        # Verify the installation was recorded
        recorded = installs.get(99)
        assert recorded is not None
        assert recorded["account_login"] == "someorg"
        assert recorded["account_type"] == "Organization"

    @pytest.mark.asyncio
    async def test_callback_detail_enrichment_failure_still_records(self, app_client_factory, store, tmp_path):
        """When detail fetch fails after token minting, redirect with warning and save minimal record."""
        from tinyagentos.github_app_installations import GitHubAppInstallations

        cfg = _make_mock_config()
        secrets = _make_mock_secrets(private_key=_TEST_PRIVATE_KEY_PEM)
        installs = GitHubAppInstallations(tmp_path / "app_installs_enrich_fail")
        await installs.init()

        token_resp = _make_response({"token": "ghs_callbacktoken"})
        # Detail fetch returns non-200
        detail_resp = _make_response({"message": "Not Found"}, status_code=404)
        detail_resp.raise_for_status = MagicMock(side_effect=Exception("404"))

        client = await app_client_factory(
            config=cfg,
            secrets=secrets,
            installations=installs,
            post_effects=[token_resp],
            get_effects=[detail_resp],
        )
        resp = await client.get(
            "/api/github/app/callback?installation_id=88",
            headers={"Accept": "application/json"},
        )

        # Should redirect with warning, not error
        if resp.status_code == 302:
            location = resp.headers.get("location", "")
            assert "install_warning=1" in location
            assert "install_error" not in location
        elif resp.status_code == 200:
            data = resp.json()
            assert "install_warning" in str(data).lower()

        # Verify a minimal installation record was saved
        recorded = installs.get(88)
        assert recorded is not None


# ---------------------------------------------------------------------------
# TestAppInstallationDelete — delete installation
# ---------------------------------------------------------------------------

class TestAppInstallationDelete:
    @pytest.mark.asyncio
    async def test_returns_501_when_app_not_configured(self, app_client_factory):
        """DELETE /api/github/app/installations/1 returns 501 if app not configured."""
        client = await app_client_factory()
        resp = await client.delete(
            "/api/github/app/installations/12345",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 501
        data = resp.json()
        assert "error" in data

    @pytest.mark.asyncio
    async def test_successful_deletion(self, app_client_factory, store, tmp_path):
        """DELETE returns 200 when GitHub confirms deletion and local record is removed."""
        from tinyagentos.github_app_installations import GitHubAppInstallations

        cfg = _make_mock_config()
        secrets = _make_mock_secrets(private_key=_TEST_PRIVATE_KEY_PEM)
        installs = GitHubAppInstallations(tmp_path / "app_installs_del")
        await installs.init()
        await installs.add(installation_id=55, account_login="org")

        # GitHub confirms deletion with 204 No Content
        delete_resp = _make_response({}, status_code=204)
        delete_resp.raise_for_status = MagicMock()

        client = await app_client_factory(
            config=cfg,
            secrets=secrets,
            installations=installs,
            delete_effects=[delete_resp],
        )
        resp = await client.delete(
            "/api/github/app/installations/55",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"

        # Local record removed
        assert installs.get(55) is None

    @pytest.mark.asyncio
    async def test_deletion_upstream_404_returns_404(self, app_client_factory, store):
        """DELETE returns 404 when GitHub says the installation doesn't exist."""
        cfg = _make_mock_config()
        secrets = _make_mock_secrets(private_key=_TEST_PRIVATE_KEY_PEM)

        # GitHub returns 404
        delete_resp = _make_response({"message": "Not Found"}, status_code=404)
        delete_resp.raise_for_status = MagicMock(side_effect=Exception("404"))

        client = await app_client_factory(
            config=cfg,
            secrets=secrets,
            delete_effects=[delete_resp],
        )
        resp = await client.delete(
            "/api/github/app/installations/99999",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data
