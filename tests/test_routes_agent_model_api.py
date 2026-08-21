"""Endpoint tests for the Agent-as-a-Model /v1 surface (decision 19)."""
import asyncio

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


# --- POST /v1/chat/completions: the consent contract (turn execution pending) ---


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
@pytest.mark.parametrize("stream", ["true", 1, "false", 0])
async def test_chat_rejects_non_bool_stream(client, stream):
    """`stream` must be an explicit JSON boolean; a string/number is rejected
    with 400 rather than silently coerced to a non-streaming turn."""
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json={**_chat_body(model="agent-a"), "stream": stream},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "'stream' must be a boolean"


@pytest.mark.asyncio
async def test_chat_turn_returns_openai_envelope_with_mock(client, monkeypatch):
    """Turn slice: a consented request drives one turn and returns the OpenAI
    ChatCompletion shape. The opencode runtime is mocked so the test runs
    headlessly in CI (no opencode binary required)."""
    import tinyagentos.routes.agent_model_api as api

    class _FakeServer:
        base_url = "http://127.0.0.1:4188"

    async def _fake_ensure(app_state, agent_id):
        return _FakeServer()

    async def _fake_drive_turn(text, trace_id, sink, *, base_url, model_id,
                               model_provider_id="litellm", server_password=None,
                               adapter_factory=None, turn_timeout=300.0):
        sink({"kind": "final", "content": f"echo: {text}"})

    monkeypatch.setattr(api, "ensure_taos_opencode_server", _fake_ensure)
    monkeypatch.setattr(api, "drive_turn", _fake_drive_turn)

    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json=_chat_body(model="agent-a"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "agent-a"
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["message"]["content"] == "echo: hi"
    assert data["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_chat_turn_transport_failure_returns_502(client, monkeypatch):
    """A transport failure in the turn path degrades to 502, not 500/501."""
    import tinyagentos.routes.agent_model_api as api

    class _FakeServer:
        base_url = "http://127.0.0.1:4188"

    async def _fake_ensure(app_state, agent_id):
        return _FakeServer()

    async def _fake_drive_turn(text, trace_id, sink, *, base_url, model_id,
                               model_provider_id="litellm", server_password=None,
                               adapter_factory=None, turn_timeout=300.0):
        sink({"kind": "error", "error": "opencode transport down"})

    monkeypatch.setattr(api, "ensure_taos_opencode_server", _fake_ensure)
    monkeypatch.setattr(api, "drive_turn", _fake_drive_turn)

    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json=_chat_body(model="agent-a"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"]["code"] == "agent_error"


@pytest.mark.asyncio
async def test_chat_missing_user_message_returns_400(client, monkeypatch):
    """A request with no user role is a client error (400), not a 502."""
    import tinyagentos.routes.agent_model_api as api

    class _FakeServer:
        base_url = "http://127.0.0.1:4188"

    async def _fake_ensure(app_state, agent_id):
        return _FakeServer()

    async def _fake_drive_turn(text, trace_id, sink, *, base_url, model_id,
                               model_provider_id="litellm", server_password=None,
                               adapter_factory=None, turn_timeout=300.0):
        sink({"kind": "final", "content": text})

    monkeypatch.setattr(api, "ensure_taos_opencode_server", _fake_ensure)
    monkeypatch.setattr(api, "drive_turn", _fake_drive_turn)

    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "agent-a", "messages": [{"role": "system", "content": "x"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "invalid_request"


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
    # An unauthenticated caller with a malformed body must get 401 (auth first),
    # never a 422 that leaks the schema.
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
    # OpenAI envelope, not FastAPI's default {"detail": [...]} 422.
    assert resp.json()["error"]["type"] == "invalid_request_error"


# ---------------------------------------------------------------------------
# Middleware passthrough (tsk-hfs6zv): an EXTERNAL client (no session cookie)
# must reach the /v1 handlers, whose consent-key check is the credential.
# Before the exemption, the session gate 401d these requests before the
# handler ran, so the surface was unreachable from outside by design-gap.
# ---------------------------------------------------------------------------

from httpx import ASGITransport, AsyncClient


@pytest.fixture
def bare_client(client):
    """Session-less client against the same app: what an external
    OpenAI-compatible caller looks like."""
    app = client._transport.app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_external_models_reaches_route_auth(bare_client):
    """No session, no key: the 401 must be the ROUTE's OpenAI envelope,
    not the middleware's generic 401."""
    async with bare_client as c:
        resp = await c.get("/v1/models")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_external_chat_completions_reaches_route_auth(bare_client):
    async with bare_client as c:
        resp = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer sk-taosagent-bogus"},
            json={"model": "agent-a", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_external_valid_key_lists_models_end_to_end(client, bare_client):
    """A minted consent key authenticates an external caller through the
    middleware all the way to a 200 model list."""
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], ["memory_read"])
    async with bare_client as c:
        resp = await c.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert [m["id"] for m in resp.json()["data"]] == ["agent-a"]


@pytest.mark.asyncio
async def test_unlisted_v1_path_stays_session_gated(bare_client):
    """The exemption is exact-path: any other /v1 path must still hit the
    session gate, not fall through as a public 404."""
    async with bare_client as c:
        resp = await c.get("/v1/other")
    assert resp.status_code == 401
    # The middleware's generic 401 ({"error": "Authentication required"}),
    # never the route's OpenAI envelope ({"error": {"code": ...}}).
    assert not isinstance(resp.json().get("error"), dict)


@pytest.mark.asyncio
async def test_wrong_method_on_exempt_path_stays_gated(bare_client):
    """Method-sensitivity: POST /v1/models and GET /v1/chat/completions are
    not exempt."""
    async with bare_client as c:
        r1 = await c.post("/v1/models")
        r2 = await c.get("/v1/chat/completions")
    assert r1.status_code == 401
    assert r2.status_code == 401
    for r in (r1, r2):
        assert not isinstance(r.json().get("error"), dict)
