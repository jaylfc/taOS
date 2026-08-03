"""Endpoint tests for tinyagentos/routes/taos_agent.py.

Covers every @router endpoint testable in-process with the FastAPI
test client (no live LLM / opencode / container needed):

    GET  /api/taos-agent/settings
    PATCH /api/taos-agent/settings
    GET  /api/taos-agent/config
    PUT  /api/taos-agent/permitted-models
    PUT  /api/taos-agent/persona
    POST /api/taos-agent/chat (guard responses)
    POST /api/taos-agent/attachments/upload
    GET  /api/taos-agent/attachments/files/{token}

Streaming happy path for POST /api/taos-agent/chat requires a live
opencode server process; that endpoint is skipped with a comment below.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

import tinyagentos.cluster.model_resolver as _model_resolver_mod
from tinyagentos.cluster.model_resolver import ModelLocation


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def _init_desktop_settings(client):
    """Ensure desktop_settings store is initialised for taos-agent routes."""
    ds = client._transport.app.state.desktop_settings
    if ds._db is not None:
        await ds.close()
    await ds.init()
    yield
    await ds.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_proxy(running: bool = True) -> MagicMock:
    proxy = MagicMock()
    proxy.is_running.return_value = running
    return proxy


# ---------------------------------------------------------------------------
# GET /api/taos-agent/settings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_settings_returns_model_when_set(client):
    """GET /settings returns the currently configured model."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    resp = await client.get("/api/taos-agent/settings")
    assert resp.status_code == 200
    assert resp.json()["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_get_settings_returns_null_when_no_model(client):
    """GET /settings returns null model when nothing has been configured."""
    resp = await client.get("/api/taos-agent/settings")
    assert resp.status_code == 200
    assert resp.json()["model"] is None


# ---------------------------------------------------------------------------
# PATCH /api/taos-agent/settings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_settings_happy_path(client):
    """PATCH /settings with a model persists and returns it."""
    resp = await client.patch("/api/taos-agent/settings", json={"model": "claude-3"})
    assert resp.status_code == 200
    assert resp.json()["model"] == "claude-3"


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
# GET /api/taos-agent/config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_returns_full_payload(client):
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
async def test_config_key_masked_none_when_no_key(client):
    """When taos_opencode_key is absent, key_masked is None."""
    resp = await client.get("/api/taos-agent/config")
    assert resp.status_code == 200
    assert resp.json()["key_masked"] is None


@pytest.mark.asyncio
async def test_config_key_masked_scrubs_long_key(client, monkeypatch):
    """A real-looking key is masked (first 6 + ellipsis + last 4)."""
    monkeypatch.setattr(
        client._transport.app.state, "taos_opencode_key", "sk-1234567890abcdef",
        raising=False,
    )
    resp = await client.get("/api/taos-agent/config")
    assert resp.status_code == 200
    masked = resp.json()["key_masked"]
    assert masked is not None
    assert masked.startswith("sk-123")
    assert masked.endswith("cdef")


@pytest.mark.asyncio
async def test_config_key_masked_short_key_returns_ellipsis(client, monkeypatch):
    """A key shorter than 12 chars is replaced with the ellipsis sentinel."""
    monkeypatch.setattr(
        client._transport.app.state, "taos_opencode_key", "short",
        raising=False,
    )
    resp = await client.get("/api/taos-agent/config")
    assert resp.status_code == 200
    assert resp.json()["key_masked"] == "…"


# ---------------------------------------------------------------------------
# PUT /api/taos-agent/permitted-models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_permitted_models_happy_path(client, monkeypatch):
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
async def test_put_permitted_models_unreachable_returns_409(client, monkeypatch):
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
async def test_put_permitted_models_downloaded_backend_down_returns_actionable_409(client, monkeypatch):
    """A downloaded model whose backend is confirmed down must return the
    specific "downloaded but backend not running" message, not the generic
    "not reachable anywhere" text."""
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
async def test_put_permitted_models_prepends_current_model(client, monkeypatch):
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
async def test_put_permitted_models_re_scopes_key(client, monkeypatch):
    """When proxy + key are present, key_rescoped reflects proxy.update_agent_key."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    proxy = _fake_proxy(running=True)
    monkeypatch.setattr(client._transport.app.state, "llm_proxy", proxy, raising=False)
    monkeypatch.setattr(
        client._transport.app.state, "taos_opencode_key", "sk-1234567890abcdef",
        raising=False,
    )
    proxy.update_agent_key = AsyncMock(return_value=True)

    monkeypatch.setattr(
        _model_resolver_mod, "resolve_model_location",
        lambda request, model_id: ModelLocation(kind="cloud"),
    )

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
async def test_put_persona_persists_across_get_config(client):
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
async def test_chat_proxy_not_running_returns_503(client, monkeypatch):
    """POST /chat when proxy is not running returns 503."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    proxy = _fake_proxy(running=False)
    monkeypatch.setattr(client._transport.app.state, "llm_proxy", proxy)

    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503
    error = resp.json()["error"].lower()
    assert "proxy" in error or "lite" in error


@pytest.mark.asyncio
async def test_chat_missing_messages_field_returns_422(client):
    """POST /chat with a body missing `messages` returns 422 (FastAPI validation)."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    resp = await client.post(
        "/api/taos-agent/chat",
        json={},
    )
    assert resp.status_code == 422


# Streaming happy path for POST /api/taos-agent/chat requires a live
# opencode server process; skipped here per task instructions.


# ---------------------------------------------------------------------------
# POST /api/taos-agent/attachments/upload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_attachment_happy_path(client):
    """A valid file upload returns metadata with the stored URL."""
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
    resp = await client.post(
        "/api/taos-agent/attachments/upload",
        files={"file": ("test.png", png_bytes, "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mime_type"] == "image/png"
    assert data["size"] == len(png_bytes)
    assert data["url"].startswith("/api/taos-agent/attachments/files/")


@pytest.mark.asyncio
async def test_upload_attachment_too_large_returns_413(client):
    """Files over 50 MB are rejected with 413."""
    big_bytes = b"x" * (51 * 1024 * 1024)
    resp = await client.post(
        "/api/taos-agent/attachments/upload",
        files={"file": ("big.bin", big_bytes, "application/octet-stream")},
    )
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# GET /api/taos-agent/attachments/files/{token}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_serve_attachment_happy_path(client):
    """An uploaded safe image is served inline with correct headers."""
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
    upload_resp = await client.post(
        "/api/taos-agent/attachments/upload",
        files={"file": ("test.png", png_bytes, "image/png")},
    )
    assert upload_resp.status_code == 200
    url = upload_resp.json()["url"]

    resp = await client.get(url)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.content == png_bytes


@pytest.mark.asyncio
async def test_serve_attachment_not_found_returns_404(client):
    """A token with no stored file returns 404."""
    resp = await client.get("/api/taos-agent/attachments/files/doesnotexist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_serve_attachment_path_traversal_returns_404(client):
    """Path traversal tokens are rejected with 404."""
    for token in ("../etc/passwd", "foo/bar", "foo\\bar"):
        resp = await client.get(f"/api/taos-agent/attachments/files/{token}")
        assert resp.status_code == 404
