"""Endpoint tests for tinyagentos/routes/taos_agent.py.

Covers the endpoints that are testable in-process with the FastAPI
test client (no live LLM / opencode / container needed):

    GET  /api/taos-agent/config
    PUT  /api/taos-agent/permitted-models
    PUT  /api/taos-agent/persona
    PATCH /api/taos-agent/settings (validation)
    POST /api/taos-agent/chat (guard responses)

Endpoints exercised in separate modules:
    - GET/PATCH settings, attachments upload/serve: test_taos_agent_route.py
    - POST /api/taos-agent/chat streaming happy path: test_taos_agent_chat.py
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
import tinyagentos.cluster.model_resolver as _model_resolver_mod
from tinyagentos.cluster.model_resolver import ModelLocation


# ---------------------------------------------------------------------------
# Fixtures (same pattern as tests/test_taos_agent_route.py)
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
# Helpers
# ---------------------------------------------------------------------------

def _fake_proxy(running: bool = True) -> MagicMock:
    proxy = MagicMock()
    proxy.is_running.return_value = running
    return proxy


# ---------------------------------------------------------------------------
# GET /api/taos-agent/config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_returns_full_payload(client, app):
    """GET /config returns model, permitted_models, persona, key_masked,
    framework, and system."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    resp = await client.get("/api/taos-agent/config")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {
        "model", "permitted_models", "persona",
        "key_masked", "framework", "system",
    }
    assert data["model"] == "gpt-4o"
    assert data["framework"] == "opencode"
    assert isinstance(data["permitted_models"], list)
    assert isinstance(data["persona"], str)
    assert data["system"] is True


@pytest.mark.asyncio
async def test_config_key_masked_none_when_no_key(client, app):
    """When taos_opencode_key is absent, key_masked is None."""
    resp = await client.get("/api/taos-agent/config")
    assert resp.status_code == 200
    assert resp.json()["key_masked"] is None


@pytest.mark.asyncio
async def test_config_key_masked_scrubs_long_key(client, app):
    """A real-looking key is masked (first 6 + ellipsis + last 4)."""
    app.state.taos_opencode_key = "sk-1234567890abcdef"
    resp = await client.get("/api/taos-agent/config")
    assert resp.status_code == 200
    masked = resp.json()["key_masked"]
    assert masked is not None
    assert masked.startswith("sk-123")
    assert masked.endswith("cdef")


@pytest.mark.asyncio
async def test_config_key_masked_short_key_returns_ellipsis(client, app):
    """A key shorter than 12 chars is replaced with the ellipsis sentinel."""
    app.state.taos_opencode_key = "short"
    resp = await client.get("/api/taos-agent/config")
    assert resp.status_code == 200
    assert resp.json()["key_masked"] == "…"


# ---------------------------------------------------------------------------
# PUT /api/taos-agent/permitted-models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_permitted_models_happy_path(client, app, monkeypatch):
    """Setting permitted models with a reachable model returns the new set."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})

    monkeypatch.setattr(
        _model_resolver_mod, "resolve_model_location",
        lambda request, model_id: ModelLocation(kind="cloud"),
    )

    resp = await client.put(
        "/api/taos-agent/permitted-models",
        json={"models": ["gpt-4o", "claude-4"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "gpt-4o" in data["permitted_models"]
    assert "claude-4" in data["permitted_models"]


@pytest.mark.asyncio
async def test_put_permitted_models_empty_list_returns_400(client):
    """An empty models list must be rejected with 400."""
    resp = await client.put(
        "/api/taos-agent/permitted-models",
        json={"models": []},
    )
    assert resp.status_code == 400
    assert "empty" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_put_permitted_models_unreachable_returns_409(client, app, monkeypatch):
    """A model that resolves to not_found returns 409."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})

    monkeypatch.setattr(
        _model_resolver_mod, "resolve_model_location",
        lambda request, model_id: ModelLocation(kind="not_found"),
    )

    resp = await client.put(
        "/api/taos-agent/permitted-models",
        json={"models": ["does-not-exist"]},
    )
    assert resp.status_code == 409
    error = resp.json()["error"].lower()
    assert "not reachable" in error or "not_found" in error


@pytest.mark.asyncio
async def test_put_permitted_models_downloaded_backend_down_returns_actionable_409(client, app, monkeypatch):
    """A downloaded model whose backend is confirmed down must return the
    specific "downloaded but backend not running" message (#1599), not the
    generic "not reachable anywhere" text."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})

    monkeypatch.setattr(
        _model_resolver_mod, "resolve_model_location",
        lambda request, model_id: ModelLocation(
            kind="downloaded_backend_down", backend_id="rkllama",
        ),
    )

    resp = await client.put(
        "/api/taos-agent/permitted-models",
        json={"models": ["qwen2.5-3b-rkllm"]},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert "downloaded" in body["error"].lower()
    assert "rkllama" in body["error"]
    assert "not running" in body["error"]
    assert body["backend"] == "rkllama"


@pytest.mark.asyncio
async def test_put_permitted_models_prepends_current_model(client, app, monkeypatch):
    """The current primary model is always included even if not in the list."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})

    monkeypatch.setattr(
        _model_resolver_mod, "resolve_model_location",
        lambda request, model_id: ModelLocation(kind="cloud"),
    )

    resp = await client.put(
        "/api/taos-agent/permitted-models",
        json={"models": ["claude-4"]},
    )
    assert resp.status_code == 200
    permitted = resp.json()["permitted_models"]
    assert permitted[0] == "gpt-4o"
    assert "claude-4" in permitted


@pytest.mark.asyncio
async def test_put_permitted_models_re_scopes_key(client, app, monkeypatch):
    """When proxy + key are present, key_rescoped reflects proxy.update_agent_key."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    app.state.llm_proxy = _fake_proxy(running=True)
    app.state.taos_opencode_key = "sk-1234567890abcdef"

    monkeypatch.setattr(
        _model_resolver_mod, "resolve_model_location",
        lambda request, model_id: ModelLocation(kind="cloud"),
    )

    proxy = app.state.llm_proxy
    proxy.update_agent_key = AsyncMock(return_value=True)

    resp = await client.put(
        "/api/taos-agent/permitted-models",
        json={"models": ["gpt-4o"]},
    )
    assert resp.status_code == 200
    assert resp.json()["key_rescoped"] is True
    proxy.update_agent_key.assert_called_once()


# ---------------------------------------------------------------------------
# PUT /api/taos-agent/persona
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_persona_happy_path(client):
    """Setting a persona returns it back."""
    resp = await client.put(
        "/api/taos-agent/persona",
        json={"persona": "You are a helpful pirate."},
    )
    assert resp.status_code == 200
    assert resp.json()["persona"] == "You are a helpful pirate."


@pytest.mark.asyncio
async def test_put_persona_persists_across_get_config(client, app):
    """After PUT persona, GET /config reflects the saved persona."""
    await client.put(
        "/api/taos-agent/persona",
        json={"persona": "Be concise."},
    )
    resp = await client.get("/api/taos-agent/config")
    assert resp.status_code == 200
    assert resp.json()["persona"] == "Be concise."


@pytest.mark.asyncio
async def test_put_persona_empty_string_accepted(client):
    """An empty persona string is accepted (it clears the override)."""
    resp = await client.put(
        "/api/taos-agent/persona",
        json={"persona": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["persona"] == ""


# ---------------------------------------------------------------------------
# PATCH /api/taos-agent/settings validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_settings_missing_model_returns_422(client):
    """Omitting the required `model` field returns 422."""
    resp = await client.patch("/api/taos-agent/settings", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_settings_non_string_model_returns_422(client):
    """A non-string model value returns 422."""
    resp = await client.patch(
        "/api/taos-agent/settings",
        json={"model": 123},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/taos-agent/chat guards
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_no_model_returns_400(client):
    """POST /chat with no model configured returns 400."""
    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 400
    assert "model" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_chat_proxy_not_running_returns_503(client, app):
    """POST /chat when proxy is not running returns 503."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    app.state.llm_proxy = _fake_proxy(running=False)

    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503
    error = resp.json()["error"].lower()
    assert "proxy" in error or "lite" in error


@pytest.mark.asyncio
async def test_chat_missing_messages_field_returns_422(client):
    """POST /chat with a body missing `messages` returns 422."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    resp = await client.post(
        "/api/taos-agent/chat",
        json={},
    )
    assert resp.status_code == 422
