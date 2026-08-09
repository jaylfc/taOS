"""Endpoint tests for the Agent-as-a-Model /v1 surface (decision 19)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _ensure_amk_store(client, tmp_path_factory):
    """Init app.state.agent_model_keys on a fresh DB; the test client registers
    the store but does not run the lifespan that init()s it (production does)."""
    store = client._transport.app.state.agent_model_keys
    if store._db is not None:
        try:
            asyncio.get_event_loop().run_until_complete(store.close())
        except Exception:
            pass
    tmp_dir = tmp_path_factory.mktemp("amk_test")
    store.db_path = tmp_dir / "agent_model_keys.db"
    asyncio.get_event_loop().run_until_complete(store.init())
    yield
    try:
        asyncio.get_event_loop().run_until_complete(store.close())
    except Exception:
        pass


def _ensure_agent_in_config(state, agent_id: str, model: str = "test-model", llm_key: str = "sk-test-key"):
    """Inject a fake agent into app.state.config for tests."""
    state.config.agents.append({
        "id": agent_id,
        "name": agent_id,
        "model": model,
        "llm_key": llm_key,
    })


# ---------------------------------------------------------------------------
# GET /v1/models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_models_requires_a_key(client):
    resp = await client.get("/v1/models")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_models_rejects_unknown_key(client):
    resp = await client.get("/v1/models", headers={"Authorization": "Bearer sk-taosagent-nope"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_models_lists_consented_agents(client):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a", "agent-b"], ["memory_read"])
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["object"] == "list"
    ids = [m["id"] for m in data["data"]]
    assert ids == ["agent-a", "agent-b"]
    assert all(m["object"] == "model" and m["owned_by"] == "taos-agent" for m in data["data"])


@pytest.mark.asyncio
async def test_models_revoked_key_is_rejected(client):
    store = client._transport.app.state.agent_model_keys
    token, rec = await store.mint("u1", ["agent-a"], [])
    await store.revoke(rec["id"])
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /v1/chat/completions
# ---------------------------------------------------------------------------


def _chat_body(model="agent-a"):
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.asyncio
async def test_chat_requires_a_key(client):
    resp = await client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_chat_rejects_model_not_in_scope(client):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json=_chat_body(model="agent-z"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"


@pytest.mark.asyncio
async def test_chat_rejects_empty_messages(client):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "agent-a", "messages": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_chat_revoked_key_is_rejected(client):
    store = client._transport.app.state.agent_model_keys
    token, rec = await store.mint("u1", ["agent-a"], [])
    await store.revoke(rec["id"])
    resp = await client.post(
        "/v1/chat/completions",
        json=_chat_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_auth_precedes_body_validation(client):
    resp = await client.post("/v1/chat/completions", json={"bogus": True})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_chat_missing_model_is_openai_shaped_400(client):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "invalid_request_error"


# ---------------------------------------------------------------------------
# Happy path: mocked adapter returns text -> OpenAI ChatCompletion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_happy_path_returns_openai_response(client, monkeypatch):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    _ensure_agent_in_config(client._transport.app.state, "agent-a", model="gpt-4o", llm_key="sk-agent-key")

    fake_server = SimpleNamespace(base_url="http://127.0.0.1:9999")

    async def fake_ensure_server(state, agent_id, model, llm_key=None):
        return fake_server

    monkeypatch.setattr(
        "tinyagentos.routes.agent_model_api.ensure_agent_opencode_server",
        fake_ensure_server,
    )

    class _FakeAdapter:
        def __init__(self, cfg, sink):
            self._sink = sink
            self.session_id = None

        async def ensure_session(self):
            self.session_id = "ses_123"

        async def prompt(self, text, trace_id=None, attachments=None):
            self._sink({"kind": "delta", "content": "Hello"})
            self._sink({"kind": "delta", "content": " world"})
            self._sink({"kind": "final", "content": "Hello world"})

        async def close(self):
            pass

    monkeypatch.setattr(
        "tinyagentos.routes.agent_model_api.OpenCodeAdapter",
        _FakeAdapter,
    )

    resp = await client.post(
        "/v1/chat/completions",
        json=_chat_body(model="agent-a"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "agent-a"
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["message"]["content"] == "Hello world"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert "id" in data
    assert "created" in data


@pytest.mark.asyncio
async def test_chat_uses_agent_model_when_agent_has_one(client, monkeypatch):
    """The adapter should be configured with the agent's model, not the agent_id."""
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    _ensure_agent_in_config(client._transport.app.state, "agent-a", model="gpt-4o", llm_key="sk-agent-key")

    seen_cfgs = []

    class _FakeAdapter:
        def __init__(self, cfg, sink):
            seen_cfgs.append(cfg)
            self._sink = sink
            self.session_id = None

        async def ensure_session(self):
            self.session_id = "ses_123"

        async def prompt(self, text, trace_id=None, attachments=None):
            self._sink({"kind": "final", "content": "ok"})

        async def close(self):
            pass

    monkeypatch.setattr(
        "tinyagentos.routes.agent_model_api.OpenCodeAdapter",
        _FakeAdapter,
    )

    fake_server = SimpleNamespace(base_url="http://127.0.0.1:9999")

    async def fake_ensure_server(state, agent_id, model, llm_key=None):
        return fake_server

    monkeypatch.setattr(
        "tinyagentos.routes.agent_model_api.ensure_agent_opencode_server",
        fake_ensure_server,
    )

    resp = await client.post(
        "/v1/chat/completions",
        json=_chat_body(model="agent-a"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(seen_cfgs) == 1
    assert seen_cfgs[0].model_id == "gpt-4o"


@pytest.mark.asyncio
async def test_chat_returns_404_when_agent_not_in_config(client):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["missing-agent"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json=_chat_body(model="missing-agent"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"


@pytest.mark.asyncio
async def test_chat_adapter_error_returns_500(client, monkeypatch):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    _ensure_agent_in_config(client._transport.app.state, "agent-a", model="gpt-4o", llm_key="sk-agent-key")

    fake_server = SimpleNamespace(base_url="http://127.0.0.1:9999")

    async def fake_ensure_server(state, agent_id, model, llm_key=None):
        return fake_server

    monkeypatch.setattr(
        "tinyagentos.routes.agent_model_api.ensure_agent_opencode_server",
        fake_ensure_server,
    )

    class _ErrorAdapter:
        def __init__(self, cfg, sink):
            self._sink = sink
            self.session_id = None

        async def ensure_session(self):
            self.session_id = "ses_err"

        async def prompt(self, text, trace_id=None, attachments=None):
            self._sink({"kind": "error", "error": "adapter boom"})

        async def close(self):
            pass

    monkeypatch.setattr(
        "tinyagentos.routes.agent_model_api.OpenCodeAdapter",
        _ErrorAdapter,
    )

    resp = await client.post(
        "/v1/chat/completions",
        json=_chat_body(model="agent-a"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "service_unavailable"
