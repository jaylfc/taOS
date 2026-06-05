"""Tests for GET /api/taos-agent/config and PUT permitted-models / persona endpoints."""
from __future__ import annotations

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app


# ---------------------------------------------------------------------------
# Fixtures — own fixtures with desktop_settings initialised
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [
            {"name": "test-backend", "type": "rkllama", "url": "http://localhost:8080", "priority": 1}
        ],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    return tmp_path


@pytest.fixture
def app(tmp_data_dir):
    return create_app(data_dir=tmp_data_dir)


@pytest_asyncio.fixture
async def client(app):
    ds = app.state.desktop_settings
    if ds._db is not None:
        await ds.close()
    await ds.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        yield c
    await ds.close()
    await app.state.http_client.aclose()


# ---------------------------------------------------------------------------
# GET /api/taos-agent/config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetConfig:
    async def test_returns_shape(self, client):
        """GET /api/taos-agent/config returns all required fields."""
        resp = await client.get("/api/taos-agent/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["framework"] == "opencode"
        assert data["system"] is True
        assert "model" in data
        assert "permitted_models" in data
        assert "persona" in data
        assert "key_masked" in data

    async def test_returns_persisted_model(self, client, app):
        """model field reflects whatever PATCH /settings stored."""
        await client.patch("/api/taos-agent/settings", json={"model": "ollama/llama3"})
        resp = await client.get("/api/taos-agent/config")
        assert resp.status_code == 200
        assert resp.json()["model"] == "ollama/llama3"

    async def test_key_masked_null_when_not_provisioned(self, client, app):
        """key_masked is null when no key has been minted yet."""
        if hasattr(app.state, "taos_opencode_key"):
            del app.state.taos_opencode_key
        resp = await client.get("/api/taos-agent/config")
        assert resp.status_code == 200
        assert resp.json()["key_masked"] is None

    async def test_key_masked_format(self, client, app):
        """key_masked shows first 6 + ellipsis + last 4 of the key."""
        app.state.taos_opencode_key = "sk-agent-very-long-secret-key-1234"
        resp = await client.get("/api/taos-agent/config")
        assert resp.status_code == 200
        masked = resp.json()["key_masked"]
        assert masked is not None
        assert masked.startswith("sk-age")
        assert masked.endswith("1234")
        assert "\u2026" in masked or "..." in masked or "\xe2\x80\xa6" in masked or "\u2026" in repr(masked) or "…" in masked


# ---------------------------------------------------------------------------
# PUT /api/taos-agent/permitted-models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPutPermittedModels:
    async def _set_model(self, client, model: str):
        resp = await client.patch("/api/taos-agent/settings", json={"model": model})
        assert resp.status_code == 200

    async def test_empty_list_returns_400(self, client):
        resp = await client.put("/api/taos-agent/permitted-models", json={"models": []})
        assert resp.status_code == 400
        assert "empty" in resp.json().get("error", "").lower()

    async def test_unreachable_model_returns_409(self, client, monkeypatch):
        """A model not found in the cluster -> 409."""
        from types import SimpleNamespace

        def _not_found(request, model_id):
            return SimpleNamespace(kind="not_found")

        monkeypatch.setattr(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            _not_found,
        )
        resp = await client.put(
            "/api/taos-agent/permitted-models",
            json={"models": ["ghost/model"]},
        )
        assert resp.status_code == 409
        assert "ghost/model" in resp.json().get("error", "")

    async def test_current_model_always_included(self, client, monkeypatch):
        """The current primary model is prepended if missing from the requested set."""
        from types import SimpleNamespace

        await self._set_model(client, "ollama/primary")

        def _reachable(request, model_id):
            return SimpleNamespace(kind="local")

        monkeypatch.setattr(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            _reachable,
        )

        resp = await client.put(
            "/api/taos-agent/permitted-models",
            json={"models": ["ollama/extra"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "ollama/primary" in data["permitted_models"]
        assert "ollama/extra" in data["permitted_models"]

    async def test_key_rescoped_via_proxy(self, client, app, monkeypatch):
        """If llm_proxy + taos_opencode_key are set, update_agent_key is called."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        await self._set_model(client, "ollama/m1")
        app.state.taos_opencode_key = "sk-existing-key-0001"

        mock_proxy = MagicMock()
        mock_proxy.update_agent_key = AsyncMock(return_value=True)
        app.state.llm_proxy = mock_proxy

        def _reachable(request, model_id):
            return SimpleNamespace(kind="local")

        monkeypatch.setattr(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            _reachable,
        )

        resp = await client.put(
            "/api/taos-agent/permitted-models",
            json={"models": ["ollama/m1", "ollama/m2"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["key_rescoped"] is True
        mock_proxy.update_agent_key.assert_called_once()
        call_key = mock_proxy.update_agent_key.call_args.args[0]
        assert call_key == "sk-existing-key-0001"

    async def test_persists_permitted_models(self, client, monkeypatch):
        """permitted_models shows up in subsequent GET /config."""
        from types import SimpleNamespace

        await self._set_model(client, "ollama/main")

        def _reachable(request, model_id):
            return SimpleNamespace(kind="local")

        monkeypatch.setattr(
            "tinyagentos.cluster.model_resolver.resolve_model_location",
            _reachable,
        )

        await client.put(
            "/api/taos-agent/permitted-models",
            json={"models": ["ollama/main", "ollama/alt"]},
        )

        resp = await client.get("/api/taos-agent/config")
        assert resp.status_code == 200
        permitted = resp.json()["permitted_models"]
        assert "ollama/main" in permitted
        assert "ollama/alt" in permitted


# ---------------------------------------------------------------------------
# PUT /api/taos-agent/persona
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPutPersona:
    async def test_persona_persists(self, client):
        """PUT /api/taos-agent/persona stores the persona and GET /config reflects it."""
        resp = await client.put(
            "/api/taos-agent/persona",
            json={"persona": "You are a pirate."},
        )
        assert resp.status_code == 200
        assert resp.json()["persona"] == "You are a pirate."

        config_resp = await client.get("/api/taos-agent/config")
        assert config_resp.status_code == 200
        assert config_resp.json()["persona"] == "You are a pirate."

    async def test_empty_persona_clears(self, client):
        """Empty string persona is accepted (clears override — falls back to manual)."""
        await client.put("/api/taos-agent/persona", json={"persona": "some override"})
        resp = await client.put("/api/taos-agent/persona", json={"persona": ""})
        assert resp.status_code == 200
        assert resp.json()["persona"] == ""


# ---------------------------------------------------------------------------
# Admin gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAdminGate:
    async def test_put_permitted_models_forbidden_for_non_admin(self, client, app):
        """PUT /api/taos-agent/permitted-models -> 403 for a non-admin session."""
        app.state.auth.session_user = lambda token: {"is_admin": False, "username": "guest"}
        resp = await client.put(
            "/api/taos-agent/permitted-models",
            json={"models": ["ollama/m1"]},
        )
        assert resp.status_code == 403

    async def test_put_persona_forbidden_for_non_admin(self, client, app):
        """PUT /api/taos-agent/persona -> 403 for a non-admin session."""
        app.state.auth.session_user = lambda token: {"is_admin": False, "username": "guest"}
        resp = await client.put(
            "/api/taos-agent/persona",
            json={"persona": "Hacked!"},
        )
        assert resp.status_code == 403

    async def test_patch_settings_forbidden_for_non_admin(self, client, app):
        """PATCH /api/taos-agent/settings -> 403 for a non-admin session."""
        app.state.auth.session_user = lambda token: {"is_admin": False, "username": "guest"}
        resp = await client.patch(
            "/api/taos-agent/settings",
            json={"model": "ollama/hacked"},
        )
        assert resp.status_code == 403

    async def test_get_config_open_for_non_admin(self, client, app):
        """GET /api/taos-agent/config is open — no admin required."""
        app.state.auth.session_user = lambda token: {"is_admin": False, "username": "guest"}
        resp = await client.get("/api/taos-agent/config")
        assert resp.status_code == 200
