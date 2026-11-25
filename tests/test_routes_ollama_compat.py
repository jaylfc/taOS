"""Endpoint tests for the Ollama-compatible surface (#130).

The surface mirrors the Agent-as-a-Model consent contract (#129): the same
consent keys, the same consented-agent model set, Ollama's wire shapes.
The opencode runtime is mocked at the agent_model_api seam so the tests run
headlessly (no opencode binary required).
"""
import asyncio
import json

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
    tmp_dir = tmp_path_factory.mktemp("amk_ollama_test")
    store.db_path = tmp_dir / "agent_model_keys.db"
    asyncio.get_event_loop().run_until_complete(store.init())
    yield
    try:
        asyncio.get_event_loop().run_until_complete(store.close())
    except Exception:
        pass


def _mock_turn(monkeypatch, *, fail=False):
    """Patch the agent turn seam in agent_model_api (the module whose
    _run_agent_turn the Ollama surface reuses)."""
    import tinyagentos.routes.agent_model_api as api

    class _FakeServer:
        base_url = "http://127.0.0.1:4188"

    async def _fake_ensure(app_state, agent_id):
        return _FakeServer()

    async def _fake_drive_turn(text, trace_id, sink, *, base_url, model_id,
                               model_provider_id="litellm", server_password=None,
                               adapter_factory=None, turn_timeout=300.0):
        if fail:
            sink({"kind": "error", "error": "opencode transport down"})
        else:
            sink({"kind": "final", "content": f"echo: {text}"})

    monkeypatch.setattr(api, "ensure_taos_opencode_server", _fake_ensure)
    monkeypatch.setattr(api, "drive_turn", _fake_drive_turn)


async def _mint(client, agents=("agent-a",)):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", list(agents), [])
    return token


def _ndjson_lines(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


# --- GET /api/tags -----------------------------------------------------------

@pytest.mark.asyncio
async def test_tags_requires_a_key(client):
    resp = await client.get("/api/tags")
    assert resp.status_code == 401
    # Ollama error envelope: a plain string message, not the OpenAI object.
    assert resp.json()["error"] == "invalid or missing consent key"


@pytest.mark.asyncio
async def test_tags_rejects_unknown_key(client):
    resp = await client.get("/api/tags", headers={"Authorization": "Bearer <redacted>"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tags_lists_consented_agents_in_ollama_shape(client):
    token = await _mint(client, ("agent-a", "agent-b"))
    resp = await client.get("/api/tags", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    models = resp.json()["models"]
    assert [m["name"] for m in models] == ["agent-a", "agent-b"]
    for m in models:
        assert m["model"] == m["name"]
        assert m["modified_at"]
        assert m["size"] == 0
        assert m["digest"].startswith("sha256:")
        assert isinstance(m["details"], dict)


@pytest.mark.asyncio
async def test_tags_revoked_key_is_rejected(client):
    store = client._transport.app.state.agent_model_keys
    token, rec = await store.mint("u1", ["agent-a"], [])
    await store.revoke(rec["id"])
    resp = await client.get("/api/tags", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


# --- POST /api/chat ----------------------------------------------------------

def _chat_body(model="agent-a", **kw):
    body = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    body.update(kw)
    return body


@pytest.mark.asyncio
async def test_chat_requires_a_key(client):
    resp = await client.post("/api/chat", json=_chat_body())
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid or missing consent key"


@pytest.mark.asyncio
async def test_chat_auth_precedes_body_validation(client):
    resp = await client.post("/api/chat", json={"bogus": True})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_rejects_model_not_in_scope(client):
    token = await _mint(client)
    resp = await client.post(
        "/api/chat",
        json=_chat_body(model="agent-z"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert "agent-z" in resp.json()["error"]


@pytest.mark.asyncio
async def test_chat_rejects_empty_messages(client):
    token = await _mint(client)
    resp = await client.post(
        "/api/chat",
        json={"model": "agent-a", "messages": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ["true", 1, "false", 0])
async def test_chat_rejects_non_bool_stream(client, stream):
    token = await _mint(client)
    resp = await client.post(
        "/api/chat",
        json=_chat_body(stream=stream),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "'stream' must be a boolean"


@pytest.mark.asyncio
async def test_chat_non_streaming_returns_ollama_envelope(client, monkeypatch):
    _mock_turn(monkeypatch)
    token = await _mint(client)
    resp = await client.post(
        "/api/chat",
        json=_chat_body(stream=False),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["model"] == "agent-a"
    assert data["message"]["role"] == "assistant"
    assert data["message"]["content"] == "echo: hi"
    assert data["done"] is True
    assert data["done_reason"] == "stop"


@pytest.mark.asyncio
async def test_chat_streaming_returns_ndjson(client, monkeypatch):
    _mock_turn(monkeypatch)
    token = await _mint(client)
    resp = await client.post(
        "/api/chat",
        json=_chat_body(stream=True),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert "application/x-ndjson" in resp.headers["content-type"]
    lines = _ndjson_lines(resp)
    assert lines[-1]["done"] is True
    content = "".join(line["message"]["content"] for line in lines)
    assert content == "echo: hi"


@pytest.mark.asyncio
async def test_chat_defaults_to_streaming_like_ollama(client, monkeypatch):
    """Ollama's API streams unless stream:false is sent; parity means the
    no-stream-field request answers NDJSON, not a single JSON object."""
    _mock_turn(monkeypatch)
    token = await _mint(client)
    resp = await client.post(
        "/api/chat",
        json=_chat_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert "application/x-ndjson" in resp.headers["content-type"]
    assert _ndjson_lines(resp)[-1]["done"] is True


@pytest.mark.asyncio
async def test_chat_transport_failure_returns_502(client, monkeypatch):
    _mock_turn(monkeypatch, fail=True)
    token = await _mint(client)
    resp = await client.post(
        "/api/chat",
        json=_chat_body(stream=False),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 502, resp.text


# --- POST /api/generate --------------------------------------------------------

def _gen_body(model="agent-a", **kw):
    body = {"model": model, "prompt": "hello"}
    body.update(kw)
    return body


@pytest.mark.asyncio
async def test_generate_requires_a_key(client):
    resp = await client.post("/api/generate", json=_gen_body())
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid or missing consent key"


@pytest.mark.asyncio
async def test_generate_rejects_model_not_in_scope(client):
    token = await _mint(client)
    resp = await client.post(
        "/api/generate",
        json=_gen_body(model="agent-z"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert "agent-z" in resp.json()["error"]


@pytest.mark.asyncio
async def test_generate_non_streaming_returns_ollama_envelope(client, monkeypatch):
    _mock_turn(monkeypatch)
    token = await _mint(client)
    resp = await client.post(
        "/api/generate",
        json=_gen_body(stream=False),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["model"] == "agent-a"
    assert data["response"] == "echo: hello"
    assert data["done"] is True
    assert data["done_reason"] == "stop"


@pytest.mark.asyncio
async def test_generate_streaming_returns_ndjson(client, monkeypatch):
    _mock_turn(monkeypatch)
    token = await _mint(client)
    resp = await client.post(
        "/api/generate",
        json=_gen_body(stream=True),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert "application/x-ndjson" in resp.headers["content-type"]
    lines = _ndjson_lines(resp)
    assert lines[-1]["done"] is True
    assert "".join(line["response"] for line in lines) == "echo: hello"


@pytest.mark.asyncio
async def test_generate_empty_prompt_is_400(client):
    token = await _mint(client)
    resp = await client.post(
        "/api/generate",
        json=_gen_body(prompt=""),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_generate_system_prompt_reaches_the_turn(client, monkeypatch):
    """A generate `system` is forwarded as a system message ahead of the
    prompt (the shared turn builder keeps source order)."""
    import tinyagentos.routes.agent_model_api as api

    class _FakeServer:
        base_url = "http://127.0.0.1:4188"

    async def _fake_ensure(app_state, agent_id):
        return _FakeServer()

    captured = []

    async def _fake_drive_turn(text, trace_id, sink, *, base_url, model_id,
                               model_provider_id="litellm", server_password=None,
                               adapter_factory=None, turn_timeout=300.0):
        captured.append(text)
        sink({"kind": "final", "content": "ok"})

    monkeypatch.setattr(api, "ensure_taos_opencode_server", _fake_ensure)
    monkeypatch.setattr(api, "drive_turn", _fake_drive_turn)

    token = await _mint(client)
    resp = await client.post(
        "/api/generate",
        json=_gen_body(system="be terse", stream=False),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert captured and "be terse" in captured[0]
    assert captured[0].index("be terse") < captured[0].index("hello")


# --- Middleware passthrough: an EXTERNAL Ollama client (no session cookie)
# must reach the handlers, whose consent-key check is the credential.

from httpx import ASGITransport, AsyncClient


@pytest.fixture
def bare_client(client):
    """Session-less client against the same app: what an external
    Ollama-compatible caller looks like."""
    app = client._transport.app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_external_tags_reaches_route_auth(bare_client):
    """No session, no key: the 401 must be the ROUTE's Ollama envelope,
    not the middleware's generic 401."""
    async with bare_client as c:
        resp = await c.get("/api/tags")
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid or missing consent key"


@pytest.mark.asyncio
async def test_external_chat_reaches_route_auth(bare_client):
    async with bare_client as c:
        resp = await c.post(
            "/api/chat",
            headers={"Authorization": "Bearer <redacted>"},
            json=_chat_body(),
        )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid or missing consent key"


@pytest.mark.asyncio
async def test_external_valid_key_lists_models_end_to_end(client, bare_client):
    token = await _mint(client, ("agent-a",))
    async with bare_client as c:
        resp = await c.get("/api/tags", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert [m["name"] for m in resp.json()["models"]] == ["agent-a"]


@pytest.mark.asyncio
async def test_wrong_method_on_exempt_paths_stays_gated(bare_client):
    """Method-sensitivity: POST /api/tags and GET /api/chat are not exempt;
    the middleware's generic 401 answers, never the route envelope."""
    async with bare_client as c:
        r1 = await c.post("/api/tags")
        r2 = await c.get("/api/chat")
    assert r1.status_code == 401
    assert r2.status_code == 401
    for r in (r1, r2):
        assert r.json()["error"] == "Authentication required"
