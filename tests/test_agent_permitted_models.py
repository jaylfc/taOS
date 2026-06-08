"""Tests for the agent permitted-model set and agent-facing model API."""
from __future__ import annotations

import pytest
from tinyagentos.llm_proxy import LLMProxy
from tinyagentos.cluster.model_resolver import ModelLocation


# ---------------------------------------------------------------------------
# Minimal fakes — mirror the pattern from test_update_agent_key.py
# ---------------------------------------------------------------------------

class _FakeProxy:
    """Duck-typed LLMProxy that records calls to update_agent_key."""
    update_agent_key = LLMProxy.update_agent_key

    def __init__(self, running=True, db=True, capture=None):
        self.url = "http://127.0.0.1:4000"
        self.database_url = "postgres://x" if db else None
        self._running = running
        self._capture = capture  # list to append (models,) tuples
        self._data_dir = None  # in-memory master key (no disk I/O in tests)

    def is_running(self):
        return self._running


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.text = ""


class _Client:
    def __init__(self, status=200, capture=None):
        self._s = status
        self._cap = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        if self._cap is not None:
            self._cap.setdefault("calls", []).append({"url": url, "json": json})
        return _Resp(self._s)


class _FakeState:
    """Minimal app.state stub."""
    def __init__(self, agents, proxy=None):
        self.config = _FakeConfig(agents)
        self.llm_proxy = proxy
        self.cluster_manager = None
        self.backend_catalog = None


class _FakeConfig:
    def __init__(self, agents):
        self.agents = agents
        self.config_path = "/dev/null"
        self.backends = []


class _FakeApp:
    def __init__(self, state):
        self.state = state


class _FakeRequest:
    """Minimal Request stub for calling route functions directly."""
    def __init__(self, agents, proxy=None, headers=None):
        state = _FakeState(agents, proxy=proxy)
        self.app = _FakeApp(state)
        self.headers = headers or {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _noop_save(*args, **kwargs):
    pass


def _patch_save(monkeypatch):
    import tinyagentos.routes.agents as mod
    monkeypatch.setattr(mod, "save_config_locked", _noop_save)


def _make_location(kind):
    return ModelLocation(kind=kind)


def _patch_resolver(monkeypatch, mapping: dict):
    """mapping: {model_id: kind} — default "controller" for unknown models."""
    import tinyagentos.routes.agents as mod

    def _resolve(request, model_id):
        return _make_location(mapping.get(model_id, "controller"))

    monkeypatch.setattr(mod, "resolve_model_location", _resolve, raising=False)

    # Also patch the local import inside the route functions
    import tinyagentos.cluster.model_resolver as resolver_mod
    monkeypatch.setattr(resolver_mod, "resolve_model_location", _resolve)


# ---------------------------------------------------------------------------
# Piece 2 — GET /api/agents/{name}/permitted-models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_permitted_defaults_to_current_model(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import get_permitted_models
    agents = [{"name": "alpha", "model": "llama3"}]
    req = _FakeRequest(agents)
    resp = await get_permitted_models(req, "alpha")
    data = resp if isinstance(resp, dict) else resp.body
    if isinstance(resp, dict):
        assert resp["permitted"] == ["llama3"]
        assert resp["current"] == "llama3"
    else:
        import json
        body = json.loads(resp.body)
        assert body["permitted"] == ["llama3"]
        assert body["current"] == "llama3"


@pytest.mark.asyncio
async def test_get_permitted_returns_explicit_set(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import get_permitted_models
    agents = [{"name": "alpha", "model": "llama3", "permitted_models": ["llama3", "qwen3"]}]
    req = _FakeRequest(agents)
    resp = await get_permitted_models(req, "alpha")
    assert resp["permitted"] == ["llama3", "qwen3"]
    assert resp["current"] == "llama3"


@pytest.mark.asyncio
async def test_get_permitted_404_unknown_agent(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import get_permitted_models
    req = _FakeRequest([])
    resp = await get_permitted_models(req, "ghost")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Piece 2 — PUT /api/agents/{name}/permitted-models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_permitted_sets_field(monkeypatch):
    _patch_save(monkeypatch)
    _patch_resolver(monkeypatch, {})
    from tinyagentos.routes.agents import set_permitted_models, PermittedModelsUpdate
    agents = [{"name": "alpha", "model": "llama3", "llm_key": "sk-a"}]
    cap = {}
    import tinyagentos.llm_proxy as M
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **k: _Client(200, cap))
    proxy = _FakeProxy()
    req = _FakeRequest(agents, proxy=proxy)
    body = PermittedModelsUpdate(models=["llama3", "qwen3"])
    resp = await set_permitted_models(req, "alpha", body)
    assert resp["status"] == "updated"
    assert "llama3" in resp["permitted"]
    assert "qwen3" in resp["permitted"]
    assert agents[0]["permitted_models"] == resp["permitted"]


@pytest.mark.asyncio
async def test_put_permitted_empty_list_returns_400(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import set_permitted_models, PermittedModelsUpdate
    agents = [{"name": "alpha", "model": "llama3"}]
    req = _FakeRequest(agents)
    body = PermittedModelsUpdate(models=[])
    resp = await set_permitted_models(req, "alpha", body)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_permitted_unreachable_model_returns_409(monkeypatch):
    _patch_save(monkeypatch)
    _patch_resolver(monkeypatch, {"bad-model": "not_found"})
    from tinyagentos.routes.agents import set_permitted_models, PermittedModelsUpdate
    agents = [{"name": "alpha", "model": "llama3"}]
    req = _FakeRequest(agents)
    body = PermittedModelsUpdate(models=["llama3", "bad-model"])
    resp = await set_permitted_models(req, "alpha", body)
    assert resp.status_code == 409
    import json
    data = json.loads(resp.body)
    assert data["model"] == "bad-model"


@pytest.mark.asyncio
async def test_put_permitted_unreachable_current_returns_409(monkeypatch):
    # The auto-added current model must also be validated — an unreachable
    # current model must not be injected into the permitted set / key scope.
    _patch_save(monkeypatch)
    _patch_resolver(monkeypatch, {"stale-current": "not_found"})
    from tinyagentos.routes.agents import set_permitted_models, PermittedModelsUpdate
    agents = [{"name": "alpha", "model": "stale-current", "llm_key": "sk-a"}]
    req = _FakeRequest(agents)
    body = PermittedModelsUpdate(models=["qwen3"])  # all reachable, but current is not
    resp = await set_permitted_models(req, "alpha", body)
    assert resp.status_code == 409
    import json
    assert json.loads(resp.body)["model"] == "stale-current"


@pytest.mark.asyncio
async def test_put_permitted_prepends_current_if_omitted(monkeypatch):
    _patch_save(monkeypatch)
    _patch_resolver(monkeypatch, {})
    import tinyagentos.llm_proxy as M
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **k: _Client(200, {}))
    from tinyagentos.routes.agents import set_permitted_models, PermittedModelsUpdate
    agents = [{"name": "alpha", "model": "llama3", "llm_key": "sk-a"}]
    proxy = _FakeProxy()
    req = _FakeRequest(agents, proxy=proxy)
    # body does NOT include llama3
    body = PermittedModelsUpdate(models=["qwen3"])
    resp = await set_permitted_models(req, "alpha", body)
    assert resp["status"] == "updated"
    assert "llama3" in resp["permitted"]
    assert "qwen3" in resp["permitted"]
    # current should be first
    assert resp["permitted"][0] == "llama3"


@pytest.mark.asyncio
async def test_put_permitted_rescopes_key(monkeypatch):
    _patch_save(monkeypatch)
    _patch_resolver(monkeypatch, {})
    cap = {}
    import tinyagentos.llm_proxy as M
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **k: _Client(200, cap))
    from tinyagentos.routes.agents import set_permitted_models, PermittedModelsUpdate
    agents = [{"name": "alpha", "model": "llama3", "llm_key": "sk-a"}]
    proxy = _FakeProxy()
    req = _FakeRequest(agents, proxy=proxy)
    body = PermittedModelsUpdate(models=["llama3", "qwen3"])
    resp = await set_permitted_models(req, "alpha", body)
    assert resp["key_rescoped"] is True
    calls = cap.get("calls", [])
    assert any(c["json"]["models"] == resp["permitted"] for c in calls)


@pytest.mark.asyncio
async def test_put_permitted_404_unknown_agent(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import set_permitted_models, PermittedModelsUpdate
    req = _FakeRequest([])
    body = PermittedModelsUpdate(models=["llama3"])
    resp = await set_permitted_models(req, "ghost", body)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Piece 2 — update_agent_model scopes key to full permitted set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_agent_model_scopes_key_to_permitted_set(monkeypatch):
    _patch_save(monkeypatch)
    _patch_resolver(monkeypatch, {})
    cap = {}
    import tinyagentos.llm_proxy as M
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **k: _Client(200, cap))
    from tinyagentos.routes.agents import update_agent_model, AgentModelUpdate
    agents = [
        {
            "name": "alpha",
            "model": "llama3",
            "llm_key": "sk-a",
            "permitted_models": ["llama3", "qwen3"],
        }
    ]
    proxy = _FakeProxy()
    req = _FakeRequest(agents, proxy=proxy)
    body = AgentModelUpdate(model="qwen3")
    resp = await update_agent_model(req, "alpha", body)
    assert resp["status"] == "updated"
    assert resp["model"] == "qwen3"
    # The key should be scoped to ALL permitted models, not just [qwen3]
    assert set(resp["permitted"]) == {"llama3", "qwen3"}
    calls = cap.get("calls", [])
    rescope_calls = [c for c in calls if c["url"].endswith("/key/update")]
    assert rescope_calls, "update_agent_key was not called"
    models_sent = rescope_calls[-1]["json"]["models"]
    assert set(models_sent) == {"llama3", "qwen3"}


@pytest.mark.asyncio
async def test_update_agent_model_adds_new_model_to_permitted(monkeypatch):
    _patch_save(monkeypatch)
    _patch_resolver(monkeypatch, {})
    cap = {}
    import tinyagentos.llm_proxy as M
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **k: _Client(200, cap))
    from tinyagentos.routes.agents import update_agent_model, AgentModelUpdate
    # agent has no permitted_models yet
    agents = [{"name": "alpha", "model": "llama3", "llm_key": "sk-a"}]
    proxy = _FakeProxy()
    req = _FakeRequest(agents, proxy=proxy)
    body = AgentModelUpdate(model="qwen3")
    resp = await update_agent_model(req, "alpha", body)
    # Should auto-create permitted set with qwen3 prepended
    assert "qwen3" in resp["permitted"]
    calls = cap.get("calls", [])
    rescope_calls = [c for c in calls if c["url"].endswith("/key/update")]
    assert rescope_calls
    models_sent = rescope_calls[-1]["json"]["models"]
    assert "qwen3" in models_sent


# ---------------------------------------------------------------------------
# Piece 3 — GET /api/agents/me/models (agent-facing, bearer auth)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_get_own_models_resolves_by_key(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import agent_get_own_models
    agents = [
        {"name": "alpha", "model": "llama3", "llm_key": "sk-agent-abc", "permitted_models": ["llama3", "qwen3"]},
    ]
    req = _FakeRequest(agents, headers={"authorization": "Bearer sk-agent-abc"})
    resp = await agent_get_own_models(req)
    assert resp["permitted"] == ["llama3", "qwen3"]
    assert resp["current"] == "llama3"


@pytest.mark.asyncio
async def test_agent_get_own_models_defaults_permitted_to_current(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import agent_get_own_models
    agents = [{"name": "alpha", "model": "llama3", "llm_key": "sk-abc"}]
    req = _FakeRequest(agents, headers={"authorization": "Bearer sk-abc"})
    resp = await agent_get_own_models(req)
    assert resp["permitted"] == ["llama3"]


@pytest.mark.asyncio
async def test_agent_get_own_models_401_no_header(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import agent_get_own_models
    agents = [{"name": "alpha", "model": "llama3", "llm_key": "sk-abc"}]
    req = _FakeRequest(agents)
    resp = await agent_get_own_models(req)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_agent_get_own_models_401_unknown_token(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import agent_get_own_models
    agents = [{"name": "alpha", "model": "llama3", "llm_key": "sk-abc"}]
    req = _FakeRequest(agents, headers={"authorization": "Bearer sk-wrong"})
    resp = await agent_get_own_models(req)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Piece 3 — POST /api/agents/me/model (agent-facing, bearer auth)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_set_own_model_accepts_permitted(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import agent_set_own_model, AgentSelfModelUpdate
    agents = [
        {"name": "alpha", "model": "llama3", "llm_key": "sk-abc", "permitted_models": ["llama3", "qwen3"]},
    ]
    req = _FakeRequest(agents, headers={"authorization": "Bearer sk-abc"})
    body = AgentSelfModelUpdate(model="qwen3")
    resp = await agent_set_own_model(req, body)
    assert resp["status"] == "updated"
    assert resp["current"] == "qwen3"
    assert agents[0]["model"] == "qwen3"


@pytest.mark.asyncio
async def test_agent_set_own_model_rejects_non_permitted(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import agent_set_own_model, AgentSelfModelUpdate
    agents = [
        {"name": "alpha", "model": "llama3", "llm_key": "sk-abc", "permitted_models": ["llama3", "qwen3"]},
    ]
    req = _FakeRequest(agents, headers={"authorization": "Bearer sk-abc"})
    body = AgentSelfModelUpdate(model="gpt-4o")
    resp = await agent_set_own_model(req, body)
    assert resp.status_code == 403
    import json
    data = json.loads(resp.body)
    assert "gpt-4o" in data["error"]
    assert "permitted" in data


@pytest.mark.asyncio
async def test_agent_set_own_model_401_no_token(monkeypatch):
    _patch_save(monkeypatch)
    from tinyagentos.routes.agents import agent_set_own_model, AgentSelfModelUpdate
    agents = [{"name": "alpha", "model": "llama3", "llm_key": "sk-abc"}]
    req = _FakeRequest(agents)
    body = AgentSelfModelUpdate(model="llama3")
    resp = await agent_set_own_model(req, body)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_agent_set_own_model_no_rescope_on_same_permitted_set(monkeypatch):
    """Switching active model within the permitted set must NOT call update_agent_key."""
    _patch_save(monkeypatch)
    cap = {}
    import tinyagentos.llm_proxy as M
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **k: _Client(200, cap))
    from tinyagentos.routes.agents import agent_set_own_model, AgentSelfModelUpdate
    agents = [
        {"name": "alpha", "model": "llama3", "llm_key": "sk-abc", "permitted_models": ["llama3", "qwen3"]},
    ]
    req = _FakeRequest(agents, headers={"authorization": "Bearer sk-abc"})
    body = AgentSelfModelUpdate(model="qwen3")
    resp = await agent_set_own_model(req, body)
    assert resp["status"] == "updated"
    # No key/update call should have been made
    calls = cap.get("calls", [])
    assert not any(c["url"].endswith("/key/update") for c in calls)
