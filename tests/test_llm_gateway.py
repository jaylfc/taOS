"""In-process LLM gateway (/api/llm/v1), G1: the real app, a fake upstream.

Every test here drives ``create_app`` on a tmp data dir with
``TAOS_LLM_GATEWAY=1`` and mocks ONLY the upstream OpenAI-compatible backend
with respx, so auth middleware, routing and forwarding all run for real.
Hostile cases first; the happy paths come after.
"""
from __future__ import annotations

import copy
import json
import logging

import httpx
import pytest
import pytest_asyncio
import respx
import yaml
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks
from tinyagentos.app import create_app

UPSTREAM = "http://llm.test:8080/v1"
UPSTREAM_CHAT = f"{UPSTREAM}/chat/completions"
UPSTREAM_KEY = "sk-upstream-DO-NOT-LEAK-7f3a"
SECRET_KEY = "sk-from-secrets-DO-NOT-LEAK-91c2"

OPENAI_COMPAT = {
    "name": "local-llama",
    "type": "openai-compatible",
    "url": UPSTREAM,
    "models": [{"id": "qwen3-8b"}, {"id": "gpt-small"}],
    "api_key": UPSTREAM_KEY,
    "priority": 1,
}
ANTHROPIC = {
    "name": "claude-cloud",
    "type": "anthropic",
    "url": "https://api.anthropic.com",
    "models": [{"id": "claude-x"}],
    "api_key": "sk-ant-nope",
    "priority": 2,
}
SECRET_BACKED = {
    "name": "vault-llm",
    "type": "openai-compatible",
    "url": "http://vault.test:9000/v1",
    "models": [{"id": "vault-model"}],
    "api_key_secret": "VAULT_LLM_KEY",
    "priority": 3,
}

BASE = "/api/llm/v1"


def _completion(model: str, *, usage: bool = True, message: dict | None = None) -> dict:
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [{
            "index": 0,
            "message": message or {"role": "assistant", "content": "hi"},
            "finish_reason": "stop",
        }],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
    return body


# ---------------------------------------------------------------------------
# One app per MODULE (create_app is the expensive part), reset per test.
#
# Everything a test here can mutate is put back by ``_reset`` below, both
# before and after each test, so the tests stay independent and order-free
# (checked with --reverse and back-to-back runs). If you add a test that
# mutates some other piece of app state, reset it there too.
# ---------------------------------------------------------------------------

_DEFAULT_BACKENDS = [OPENAI_COMPAT, ANTHROPIC, SECRET_BACKED]
_TAOS_AGENT_PREF = ("user", "taos_agent")
_ASYNC = pytest.mark.asyncio(loop_scope="module")


def _write_test_config(data_dir) -> None:
    """The same config conftest's ``tmp_data_dir`` writes."""
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [
            {"name": "test-backend", "type": "rkllama", "url": "http://localhost:8080", "priority": 1}
        ],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {"name": "test-agent", "host": "192.168.1.100", "qmd_index": "test", "color": "#98fb98"}
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (data_dir / "config.yaml").write_text(yaml.dump(config))
    (data_dir / ".setup_complete").touch()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def gateway_client(tmp_path_factory):
    """ONE real app (create_app on a module tmp data dir, TAOS_LLM_GATEWAY=1)
    and ONE signed-in client for the whole module.

    Only the stores these routes touch are initialised: desktop_settings (the
    taos-default preference), secrets (backend keys), agent_model_keys (the
    /v1 surface proven unchanged below). The shared conftest ``client``
    initialises ~40 stores per TEST, which is what made this suite slow.
    """
    data_dir = tmp_path_factory.mktemp("llm_gateway")
    _write_test_config(data_dir)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TAOS_LLM_GATEWAY", "1")
        app = create_app(data_dir=data_dir)
    state = app.state
    stores = [state.desktop_settings, state.secrets, state.agent_model_keys]
    for store in stores:
        if store._db is not None:
            await store.close()
        await store.init()
    state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = state.auth.find_user("admin")["id"]
    session = state.auth.create_session(user_id=uid, long_lived=True)
    state._startup_complete = True
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": session},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c
    for store in stores:
        await store.close()
    await state.http_client.aclose()


async def _reset_state(app) -> None:
    state = app.state
    # Routing: the backend list tests read and replace.
    state.config.backends = copy.deepcopy(_DEFAULT_BACKENDS)
    # Auth seam overrides installed by the scope tests.
    app.dependency_overrides.clear()
    # taos-default: the account default chat model preference.
    await state.desktop_settings.save_preference(*_TAOS_AGENT_PREF, {})
    # Backend keys held in the secrets store.
    await state.secrets.delete(SECRET_BACKED["api_key_secret"])
    # Agent-as-a-Model consent keys minted by tests.
    await state.agent_model_keys._db.execute("DELETE FROM agent_model_keys")
    await state.agent_model_keys._db.commit()
    # Per-agent local tokens minted by tests (the host token itself is kept).
    state.auth._local_token_agent_path().unlink(missing_ok=True)
    # The respx default router: routes and recorded calls.
    respx.mock.clear()
    respx.mock.reset()
    # Failover cooldowns must not leak between tests.
    from tinyagentos.llm_gateway.forward import _clear_cooldowns
    _clear_cooldowns()


@pytest_asyncio.fixture(loop_scope="module")
async def client(gateway_client, monkeypatch):
    """Overrides conftest's per-test ``client`` with the module's, reset
    before AND after every test."""
    monkeypatch.setenv("TAOS_LLM_GATEWAY", "1")
    monkeypatch.delenv(SECRET_BACKED["api_key_secret"], raising=False)
    app = gateway_client._transport.app
    await _reset_state(app)
    yield gateway_client
    await _reset_state(app)


def _app(client):
    return client._transport.app


def _bare(app, **kw) -> AsyncClient:
    """A client holding NO session cookie."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", **kw)


async def _set_default(client, model: str) -> None:
    store = _app(client).state.desktop_settings
    prefs = await store.get_preference("user", "taos_agent")
    prefs["model"] = model
    await store.save_preference("user", "taos_agent", prefs)


def _chat(model="qwen3-8b", **extra) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}], **extra}


def _assert_openai_error(resp, status: int, code: str | None = None) -> dict:
    assert resp.status_code == status, resp.text
    err = resp.json()["error"]
    assert isinstance(err, dict), resp.text
    assert isinstance(err["message"], str) and err["message"]
    assert isinstance(err["type"], str) and err["type"]
    if code is not None:
        assert err["code"] == code, resp.text
    return err


# ---------------------------------------------------------------------------
# Hostile: credentials
# ---------------------------------------------------------------------------

@_ASYNC
@pytest.mark.parametrize("method,path", [("GET", "/models"), ("POST", "/chat/completions")])
async def test_no_credential_is_401_openai_shaped(client, method, path):
    async with _bare(_app(client)) as c:
        resp = await c.request(method, BASE + path, json=_chat())
    _assert_openai_error(resp, 401, "invalid_api_key")


@_ASYNC
@pytest.mark.parametrize("method,path", [("GET", "/models"), ("POST", "/chat/completions")])
async def test_invalid_bearer_is_401_openai_shaped(client, method, path):
    async with _bare(_app(client), headers={"Authorization": "Bearer sk-not-a-real-token"}) as c:
        resp = await c.request(method, BASE + path, json=_chat())
    _assert_openai_error(resp, 401, "invalid_api_key")


@_ASYNC
async def test_invalid_session_cookie_is_401(client):
    async with _bare(_app(client), cookies={"taos_session": "forged"}) as c:
        resp = await c.get(BASE + "/models")
    _assert_openai_error(resp, 401, "invalid_api_key")


@_ASYNC
async def test_agent_model_consent_key_does_not_open_the_gateway(client):
    """The /v1 Agent-as-a-Model key is a different credential for a different
    surface; it must not authenticate /api/llm/v1."""
    token, _ = await _app(client).state.agent_model_keys.mint("u1", ["agent-a"], [])
    async with _bare(_app(client), headers={"Authorization": f"Bearer {token}"}) as c:
        resp = await c.get(BASE + "/models")
    _assert_openai_error(resp, 401, "invalid_api_key")


# ---------------------------------------------------------------------------
# Hostile: request shape
# ---------------------------------------------------------------------------

@_ASYNC
@respx.mock
async def test_unknown_model_is_404_model_not_found(client):
    route = respx.post(UPSTREAM_CHAT)
    resp = await client.post(BASE + "/chat/completions", json=_chat("no-such-model"))
    err = _assert_openai_error(resp, 404, "model_not_found")
    assert "no-such-model" in err["message"]
    assert not route.called


@_ASYNC
@respx.mock
async def test_stream_true_proxies_verbatim(client):
    """stream:true proxies SSE chunks verbatim and ends with [DONE]."""
    body = _sse_chunk("hi") + _sse_done()
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}
    ))
    resp = await client.post(BASE + "/chat/completions", json=_chat(stream=True))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    data = resp.read()
    text = data.decode("utf-8")
    assert text.endswith("data: [DONE]\n\n")
    assert '"content": "hi"' in text


@_ASYNC
@respx.mock
@pytest.mark.parametrize("body", [
    {"model": "qwen3-8b"},                                   # no messages
    {"model": "qwen3-8b", "messages": []},                   # empty messages
    {"model": "qwen3-8b", "messages": "hello"},              # wrong type
    {"model": "qwen3-8b", "messages": ["hello"]},            # message not an object
    {"model": "qwen3-8b", "messages": [{"content": "x"}]},   # message without role
    {"messages": [{"role": "user", "content": "x"}]},        # no model
    {"model": 7, "messages": [{"role": "user", "content": "x"}]},
    {"model": "", "messages": [{"role": "user", "content": "x"}]},
    {"model": "qwen3-8b", "messages": [{"role": "user", "content": "x"}], "stream": "yes"},
    {"model": "qwen3-8b", "messages": [{"role": "user", "content": "x"}], "tools": {"a": 1}},
    ["not", "an", "object"],
], ids=["no-messages", "empty-messages", "messages-str", "message-str", "no-role",
        "no-model", "model-int", "model-empty", "stream-str", "tools-dict", "array-body"])
async def test_malformed_body_is_400(client, body):
    route = respx.post(UPSTREAM_CHAT)
    resp = await client.post(BASE + "/chat/completions", json=body)
    _assert_openai_error(resp, 400)
    assert not route.called


@_ASYNC
@respx.mock
async def test_non_json_body_is_400(client):
    route = respx.post(UPSTREAM_CHAT)
    resp = await client.post(
        BASE + "/chat/completions", content=b"{not json",
        headers={"content-type": "application/json"},
    )
    _assert_openai_error(resp, 400)
    assert not route.called


# ---------------------------------------------------------------------------
# Hostile: upstream failure
# ---------------------------------------------------------------------------

@_ASYNC
@respx.mock
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_upstream_5xx_is_502_without_secrets(client, caplog, status):
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        status, json={"error": {"message": f"boom, your key {UPSTREAM_KEY} at {UPSTREAM}"}},
    ))
    with caplog.at_level(logging.DEBUG):
        resp = await client.post(BASE + "/chat/completions", json=_chat())
    _assert_openai_error(resp, 502, "upstream_error")
    assert UPSTREAM_KEY not in resp.text
    assert UPSTREAM not in resp.text
    assert UPSTREAM_KEY not in caplog.text


@_ASYNC
@respx.mock
@pytest.mark.parametrize("exc", [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError])
async def test_upstream_timeout_or_unreachable_is_502(client, caplog, exc):
    respx.post(UPSTREAM_CHAT).mock(side_effect=exc(f"failed talking to {UPSTREAM} with {UPSTREAM_KEY}"))
    with caplog.at_level(logging.DEBUG):
        resp = await client.post(BASE + "/chat/completions", json=_chat())
    _assert_openai_error(resp, 502, "upstream_error")
    assert UPSTREAM_KEY not in resp.text
    assert UPSTREAM_KEY not in caplog.text


@_ASYNC
@respx.mock
@pytest.mark.parametrize("status", [401, 403])
async def test_upstream_rejecting_our_key_is_502_not_the_callers_fault(client, status):
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        status, json={"error": {"message": f"Incorrect API key provided: {UPSTREAM_KEY}"}},
    ))
    resp = await client.post(BASE + "/chat/completions", json=_chat())
    _assert_openai_error(resp, 502, "upstream_error")
    assert UPSTREAM_KEY not in resp.text


@_ASYNC
@respx.mock
async def test_upstream_4xx_passes_status_with_key_redacted(client):
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        400, json={"error": {"message": f"context too long for key {UPSTREAM_KEY}",
                             "type": "invalid_request_error", "code": "context_length_exceeded"}},
    ))
    resp = await client.post(BASE + "/chat/completions", json=_chat())
    err = _assert_openai_error(resp, 400, "context_length_exceeded")
    assert "context too long" in err["message"]
    assert UPSTREAM_KEY not in resp.text


@_ASYNC
@respx.mock
async def test_upstream_200_that_is_not_json_is_502(client):
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, text="<html>proxy</html>"))
    resp = await client.post(BASE + "/chat/completions", json=_chat())
    _assert_openai_error(resp, 502, "upstream_error")


@_ASYNC
@respx.mock
async def test_non_openai_backend_is_501_naming_the_model(client):
    route = respx.post(url__regex=r".*")
    resp = await client.post(BASE + "/chat/completions", json=_chat("claude-x"))
    err = _assert_openai_error(resp, 501, "backend_not_supported")
    assert "claude-x" in err["message"]
    assert not route.called


# ---------------------------------------------------------------------------
# Auth seam: the routes consult gateway_caller, and only it
# ---------------------------------------------------------------------------

@_ASYNC
@respx.mock
async def test_routes_enforce_the_callers_model_scope(client):
    """If a route stopped calling gateway_caller (or ignored may_use), the
    override below would change nothing and both assertions would fail."""
    from tinyagentos.llm_gateway.auth import GatewayCaller, gateway_caller

    route = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json=_completion("gpt-small")))
    app = _app(client)
    app.dependency_overrides[gateway_caller] = lambda: GatewayCaller(
        caller_id="agent:scoped", allowed_models=frozenset({"gpt-small"}), kind="test",
    )
    try:
        listed = await client.get(BASE + "/models")
        denied = await client.post(BASE + "/chat/completions", json=_chat("qwen3-8b"))
        allowed = await client.post(BASE + "/chat/completions", json=_chat("gpt-small"))
    finally:
        app.dependency_overrides.pop(gateway_caller, None)
    assert [m["id"] for m in listed.json()["data"]] == ["gpt-small"]
    _assert_openai_error(denied, 403, "model_not_permitted")
    assert allowed.status_code == 200, allowed.text
    assert route.call_count == 1


@_ASYNC
@respx.mock
async def test_taos_default_grants_whatever_it_resolves_to(client):
    """Alias rule (product decision): a caller allowed taos-default may use
    whatever it currently resolves to, without the concrete model listed."""
    from tinyagentos.llm_gateway.auth import GatewayCaller, gateway_caller

    route = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json=_completion("qwen3-8b")))
    await _set_default(client, "qwen3-8b")
    app = _app(client)
    app.dependency_overrides[gateway_caller] = lambda: GatewayCaller(
        caller_id="agent:scoped", allowed_models=frozenset({"taos-default"}), kind="test",
    )
    try:
        via_alias = await client.post(BASE + "/chat/completions", json=_chat("taos-default"))
        direct = await client.post(BASE + "/chat/completions", json=_chat("qwen3-8b"))
    finally:
        app.dependency_overrides.pop(gateway_caller, None)
    assert via_alias.status_code == 200, via_alias.text
    _assert_openai_error(direct, 403, "model_not_permitted")
    assert route.call_count == 1


class _State:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Req:
    def __init__(self, **state):
        self.state = _State(**state)


@pytest.mark.parametrize("state", [
    {},                                                        # middleware set nothing
    {"via": "exempt", "user_id": None},
    {"via": "registry_jwt_candidate", "user_id": None},
    {"via": "device_bearer_candidate", "user_id": None},
    {"via": "loopback", "user_id": None},                      # the shutdown-drain carve-out
    {"via": "session", "user_id": None},                       # session with no user
])
def test_gateway_caller_refuses_everything_but_session_and_local_token(state):
    from tinyagentos.llm_gateway.auth import gateway_caller
    from tinyagentos.llm_gateway.errors import GatewayError

    with pytest.raises(GatewayError) as exc:
        gateway_caller(_Req(**state))
    assert exc.value.status == 401


def test_gateway_caller_accepts_a_signed_in_session():
    from tinyagentos.llm_gateway.auth import gateway_caller

    caller = gateway_caller(_Req(via="session", user_id="u-1"))
    assert caller.caller_id == "user:u-1"
    assert caller.kind == "session"
    assert caller.allowed_models is None
    assert caller.may_use("anything")


def test_gateway_caller_accepts_the_local_token():
    from tinyagentos.llm_gateway.auth import gateway_caller

    caller = gateway_caller(_Req(via="local_token", user_id="u-1"))
    assert caller.kind == "local_token"
    assert caller.caller_id == "user:u-1"
    assert caller.may_use("anything")
    assert gateway_caller(_Req(via="local_token", user_id=None)).caller_id == "local"


def test_gateway_caller_refuses_a_per_agent_local_token():
    """An agent's bound token is not the admin credential: G1 would otherwise
    hand a model-scoped agent every model."""
    from tinyagentos.llm_gateway.auth import gateway_caller
    from tinyagentos.llm_gateway.errors import GatewayError

    with pytest.raises(GatewayError) as exc:
        gateway_caller(_Req(via="local_token", user_id="u-1", agent_name="helper"))
    assert exc.value.status == 401


@_ASYNC
@respx.mock
async def test_per_agent_local_token_is_refused_by_the_real_app(client):
    route = respx.post(UPSTREAM_CHAT)
    token = _app(client).state.auth.mint_agent_local_token("helper")
    async with _bare(_app(client), headers={"Authorization": f"Bearer {token}"}) as c:
        models = await c.get(BASE + "/models")
        chat = await c.post(BASE + "/chat/completions", json=_chat())
    _assert_openai_error(models, 401, "invalid_api_key")
    _assert_openai_error(chat, 401, "invalid_api_key")
    assert not route.called


def test_scoped_caller_may_use_only_its_models():
    from tinyagentos.llm_gateway.auth import GatewayCaller

    c = GatewayCaller(caller_id="x", allowed_models=frozenset({"a"}), kind="test")
    assert c.may_use("a") and not c.may_use("b")
    assert not GatewayCaller(caller_id="x", allowed_models=frozenset(), kind="test").may_use("a")


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

@_ASYNC
@respx.mock
async def test_plain_completion(client):
    route = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json=_completion("qwen3-8b")))
    resp = await client.post(BASE + "/chat/completions", json=_chat())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "hi"}
    assert body["usage"]["total_tokens"] == 4
    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {UPSTREAM_KEY}"
    assert UPSTREAM_KEY not in resp.text


@_ASYNC
@respx.mock
async def test_local_token_caller_is_served(client):
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json=_completion("qwen3-8b")))
    token = _app(client).state.auth.get_local_token()
    async with _bare(_app(client), headers={"Authorization": f"Bearer {token}"}) as c:
        resp = await c.post(BASE + "/chat/completions", json=_chat())
        models = await c.get(BASE + "/models")
    assert resp.status_code == 200, resp.text
    assert models.status_code == 200, models.text
    # The caller's own credential is never forwarded upstream.
    assert respx.calls.last.request.headers["authorization"] == f"Bearer {UPSTREAM_KEY}"


@_ASYNC
@respx.mock
async def test_request_reaches_upstream_verbatim_with_tools(client):
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Weather for a city",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                           "required": ["city"]},
        },
    }]
    sent = {
        "model": "qwen3-8b",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "weather in Paris?"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_0", "type": "function",
                "function": {"name": "get_weather", "arguments": "{\"city\": \"Lyon\"}"}}]},
            {"role": "tool", "tool_call_id": "call_0", "content": "{\"temp\": 20}"},
        ],
        "tools": tools,
        "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
        "temperature": 0.2,
        "max_tokens": 64,
        "stream": False,
        "x_vendor_field": {"kept": [1, 2, 3]},
    }
    reply_msg = {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"}}]}
    upstream_reply = _completion("qwen3-8b", message=reply_msg)
    upstream_reply["choices"][0]["finish_reason"] = "tool_calls"
    route = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json=upstream_reply))

    resp = await client.post(BASE + "/chat/completions", json=sent)

    assert resp.status_code == 200, resp.text
    assert json.loads(route.calls.last.request.content) == sent
    assert resp.json() == upstream_reply


@_ASYNC
@respx.mock
async def test_upstream_model_prefix_is_stripped_and_other_fields_kept(client):
    """The table stores ``openai/<id>``; the backend must see ``<id>``."""
    route = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json=_completion("gpt-small")))
    resp = await client.post(BASE + "/chat/completions", json=_chat("gpt-small", temperature=0))
    assert resp.status_code == 200, resp.text
    assert json.loads(route.calls.last.request.content) == _chat("gpt-small", temperature=0)


@_ASYNC
@respx.mock
async def test_secret_backed_key_is_resolved_from_the_secrets_store(client):
    await _app(client).state.secrets.add("VAULT_LLM_KEY", SECRET_KEY)
    route = respx.post("http://vault.test:9000/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("vault-model")))
    resp = await client.post(BASE + "/chat/completions", json=_chat("vault-model"))
    assert resp.status_code == 200, resp.text
    assert route.calls.last.request.headers["authorization"] == f"Bearer {SECRET_KEY}"
    assert SECRET_KEY not in resp.text


@_ASYNC
@respx.mock
async def test_taos_default_follows_the_account_default_without_restart(client):
    route = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json=_completion("x")))

    await _set_default(client, "qwen3-8b")
    r1 = await client.post(BASE + "/chat/completions", json=_chat("taos-default"))
    assert r1.status_code == 200, r1.text
    assert json.loads(route.calls.last.request.content)["model"] == "qwen3-8b"

    await _set_default(client, "gpt-small")
    r2 = await client.post(BASE + "/chat/completions", json=_chat("taos-default"))
    assert r2.status_code == 200, r2.text
    assert json.loads(route.calls.last.request.content)["model"] == "gpt-small"


@_ASYNC
@respx.mock
async def test_taos_default_with_no_default_set_is_a_clear_404(client):
    route = respx.post(UPSTREAM_CHAT)
    resp = await client.post(BASE + "/chat/completions", json=_chat("taos-default"))
    err = _assert_openai_error(resp, 404, "model_not_found")
    assert "default" in err["message"]
    assert not route.called


@_ASYNC
async def test_models_lists_the_routing_table_plus_taos_default(client):
    resp = await client.get(BASE + "/models")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert ids[0] == "taos-default"
    for expected in ("qwen3-8b", "gpt-small", "claude-x", "vault-model"):
        assert expected in ids
    assert len(ids) == len(set(ids))
    assert all(m["object"] == "model" and isinstance(m["owned_by"], str) for m in body["data"])
    # No upstream detail leaks into the listing.
    assert UPSTREAM_KEY not in resp.text and UPSTREAM not in resp.text


@_ASYNC
async def test_models_listing_follows_config_changes_per_request(client):
    app = _app(client)
    app.state.config.backends = [OPENAI_COMPAT]
    ids = [m["id"] for m in (await client.get(BASE + "/models")).json()["data"]]
    assert "claude-x" not in ids and "qwen3-8b" in ids


@_ASYNC
async def test_gateway_reads_the_same_table_as_the_litellm_config(client, monkeypatch):
    """One source: the gateway's routing table IS build_model_list's output,
    the same function generate_litellm_config wraps."""
    import tinyagentos.litellm_config as lc

    calls = []
    real = lc.build_model_list

    def spy(backends, *a, **kw):
        calls.append(backends)
        return real(backends, *a, **kw)

    monkeypatch.setattr(lc, "build_model_list", spy)
    resp = await client.get(BASE + "/models")
    assert resp.status_code == 200
    assert calls and calls[-1] is _app(client).state.config.backends

    from tinyagentos.llm_gateway.resolve import routing_table
    table = routing_table(_app(client).state)
    config = lc.generate_litellm_config(
        _app(client).state.config.backends, master_key="k",
        discovered={b["url"]: [] for b in _app(client).state.config.backends},
    )
    assert table == config["model_list"]


# ---------------------------------------------------------------------------
# Agent-as-a-Model (/v1) is untouched with the gateway mounted
# ---------------------------------------------------------------------------

@_ASYNC
async def test_agent_model_v1_surface_is_unchanged(client):
    async with _bare(_app(client)) as c:
        no_key_models = await c.get("/v1/models")
        no_key_chat = await c.post("/v1/chat/completions", json=_chat("agent-a"))
    for resp in (no_key_models, no_key_chat):
        assert resp.status_code == 401
        assert resp.json() == {"error": {"message": "invalid or missing consent key",
                                         "type": "invalid_request_error",
                                         "code": "invalid_api_key"}}

    token, _ = await _app(client).state.agent_model_keys.mint("u1", ["agent-a", "agent-b"], [])
    async with _bare(_app(client), headers={"Authorization": f"Bearer {token}"}) as c:
        listed = await c.get("/v1/models")
    assert listed.status_code == 200
    body = listed.json()
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == ["agent-a", "agent-b"]
    assert all(m["owned_by"] == "taos-agent" for m in body["data"])
    assert "taos-default" not in listed.text


def _sse_chunk(text: str, finish_reason: str = "stop") -> str:
    """One SSE data chunk for a streaming chat completion."""
    chunk = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "choices": [{
            "index": 0,
            "delta": {"content": text},
            "finish_reason": finish_reason,
        }],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _sse_usage_chunk(prompt_tokens: int, completion_tokens: int) -> str:
    """The final SSE chunk that carries usage only (empty choices)."""
    return f"data: {json.dumps({'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens}})}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# G3: streaming + usage/spend recording
# ---------------------------------------------------------------------------

@_ASYNC
@respx.mock
async def test_stream_true_proxies_chunks_verbatim_and_ends_with_done(client):
    """stream:true proxies SSE chunks verbatim and ends with [DONE]."""
    body = _sse_chunk("hel") + _sse_chunk("lo") + _sse_done()
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}
    ))
    resp = await client.post(BASE + "/chat/completions", json=_chat(stream=True))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] .startswith("text/event-stream")
    data = resp.read()
    text = data.decode("utf-8")
    assert text.endswith("data: [DONE]\n\n")
    assert '"content": "hel"' in text
    assert '"content": "lo"' in text


@_ASYNC
@respx.mock
async def test_stream_true_forwards_tool_call_deltas(client):
    """SSE chunks with tool_calls deltas pass through verbatim."""
    body = (
        _sse_chunk("") + '\n\n'
        + 'data: {"id": "chatcmpl-1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": null, "tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{\\"city\\": \\"Paris\\"}}}]}, "finish_reason": null}]}\n\n'
        + _sse_done()
    )
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}
    ))
    resp = await client.post(BASE + "/chat/completions", json=_chat(stream=True))
    assert resp.status_code == 200, resp.text
    data = resp.read()
    text = data.decode("utf-8")
    assert '"tool_calls"' in text


@_ASYNC
@respx.mock


@_ASYNC
@respx.mock
async def test_streamed_call_with_usage_upstream_records_right_tokens(client, monkeypatch):
    """A streamed call with a usage-reporting upstream records the right tokens."""
    trace_calls = []
    spend_calls = []

    async def fake_record_trace(*args, **kwargs):
        trace_calls.append((args, kwargs))

    def fake_record_spend(*args, **kwargs):
        spend_calls.append((args, kwargs))

    monkeypatch.setattr(
        "tinyagentos.llm_gateway.forward._record_trace", fake_record_trace
    )
    monkeypatch.setattr(
        "tinyagentos.llm_gateway.forward._record_spend", fake_record_spend
    )

    body = (
        _sse_chunk("hi")
        + _sse_usage_chunk(10, 5)
        + _sse_done()
    )
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}
    ))
    resp = await client.post(BASE + "/chat/completions", json=_chat(stream=True))
    assert resp.status_code == 200, resp.text
    assert len(trace_calls) == 1
    args, _ = trace_calls[0]
    usage = args[3]
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5


@_ASYNC
@respx.mock
async def test_upstream_no_usage_records_unknown_and_estimated_spend(client, monkeypatch):
    """An upstream with no usage records UNKNOWN and a positive estimated spend."""
    trace_calls = []
    spend_calls = []

    async def fake_record_trace(*args, **kwargs):
        trace_calls.append((args, kwargs))

    def fake_record_spend(*args, **kwargs):
        spend_calls.append((args, kwargs))

    monkeypatch.setattr(
        "tinyagentos.llm_gateway.forward._record_trace", fake_record_trace
    )
    monkeypatch.setattr(
        "tinyagentos.llm_gateway.forward._record_spend", fake_record_spend
    )

    body = _sse_chunk("hi") + _sse_done()
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}
    ))
    resp = await client.post(BASE + "/chat/completions", json=_chat(stream=True))
    assert resp.status_code == 200, resp.text
    assert len(trace_calls) == 1
    args, kwargs = trace_calls[0]
    usage = args[3]
    assert usage.source == "unknown"
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert kwargs.get("estimated") is True
    assert len(spend_calls) == 1
    spend_args, _ = spend_calls[0]
    assert spend_args[2] > 0


@_ASYNC
@respx.mock
async def test_injected_include_usage_chunk_is_not_forwarded(client):
    """The injected include_usage chunk is not forwarded to a caller that didn't ask for it."""
    body = (
        _sse_chunk("hi")
        + _sse_usage_chunk(3, 1)
        + _sse_done()
    )
    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}
    ))
    resp = await client.post(BASE + "/chat/completions", json=_chat(stream=True))
    assert resp.status_code == 200, resp.text
    data = resp.read()
    text = data.decode("utf-8")
    assert '"content": "hi"' in text
    assert "usage" not in text


@_ASYNC
@respx.mock
async def test_streamed_vs_non_streamed_parity(client, monkeypatch):
    """Streamed and non-streamed of the same prompt record comparable usage."""
    trace_calls = []
    spend_calls = []

    async def fake_record_trace(*args, **kwargs):
        trace_calls.append((args, kwargs))

    def fake_record_spend(*args, **kwargs):
        spend_calls.append((args, kwargs))

    monkeypatch.setattr(
        "tinyagentos.llm_gateway.forward._record_trace", fake_record_trace
    )
    monkeypatch.setattr(
        "tinyagentos.llm_gateway.forward._record_spend", fake_record_spend
    )

    usage_body = (
        _sse_chunk("hi")
        + _sse_usage_chunk(10, 5)
        + _sse_done()
    )

    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(
        200, content=usage_body, headers={"content-type": "text/event-stream"}
    ))
    stream_resp = await client.post(BASE + "/chat/completions", json=_chat(stream=True))
    assert stream_resp.status_code == 200, stream_resp.text

    trace_calls.clear()
    spend_calls.clear()

    respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(200, json={
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }))
    non_stream_resp = await client.post(BASE + "/chat/completions", json=_chat())
    assert non_stream_resp.status_code == 200, non_stream_resp.text

    assert len(trace_calls) == 1
    args, _ = trace_calls[0]
    usage = args[3]
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5


# ---------------------------------------------------------------------------
# Failover: retry/failover across backends serving the same model
# ---------------------------------------------------------------------------

BACKUP_OPENAI = {
    "name": "backup-llama",
    "type": "openai-compatible",
    "url": "http://backup.test:8080/v1",
    "models": [{"id": "qwen3-8b"}],
    "api_key": "sk-backup",
    "priority": 2,
}
BACKUP_CHAT = "http://backup.test:8080/v1/chat/completions"


def _enable_failover(client) -> None:
    app = _app(client)
    app.state.config.backends = [OPENAI_COMPAT, BACKUP_OPENAI]


@_ASYNC
@respx.mock
async def test_first_backend_connect_error_second_answers_200(client):
    _enable_failover(client)
    primary = respx.post(UPSTREAM_CHAT).mock(side_effect=httpx.ConnectError("primary down"))
    backup = respx.post(BACKUP_CHAT).mock(return_value=httpx.Response(200, json=_completion("qwen3-8b")))
    resp = await client.post(BASE + "/chat/completions", json=_chat())
    assert resp.status_code == 200, resp.text
    assert primary.call_count == 1
    assert backup.call_count == 1


@_ASYNC
@respx.mock
async def test_first_backend_503_goes_to_second(client):
    _enable_failover(client)
    primary = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(503, json={"error": {"message": "unavailable"}}))
    backup = respx.post(BACKUP_CHAT).mock(return_value=httpx.Response(200, json=_completion("qwen3-8b")))
    resp = await client.post(BASE + "/chat/completions", json=_chat())
    assert resp.status_code == 200, resp.text
    assert primary.call_count == 1
    assert backup.call_count == 1


@_ASYNC
@respx.mock
async def test_first_backend_400_returned_as_is_no_second_call(client):
    _enable_failover(client)
    primary = respx.post(UPSTREAM_CHAT).mock(return_value=httpx.Response(400, json={
        "error": {"message": "bad request", "type": "invalid_request_error", "code": "bad_request"}
    }))
    resp = await client.post(BASE + "/chat/completions", json=_chat())
    err = _assert_openai_error(resp, 400, "bad_request")
    assert primary.call_count == 1
    assert respx.post(BACKUP_CHAT).call_count == 0


@_ASYNC
@respx.mock
async def test_all_backends_down_one_clear_502_within_deadline(client):
    _enable_failover(client)
    primary = respx.post(UPSTREAM_CHAT).mock(side_effect=httpx.ConnectError("primary down"))
    backup = respx.post(BACKUP_CHAT).mock(side_effect=httpx.ConnectError("backup down"))
    resp = await client.post(BASE + "/chat/completions", json=_chat())
    _assert_openai_error(resp, 502, "upstream_error")
    assert primary.call_count == 1
    assert backup.call_count == 1


@_ASYNC
@respx.mock
async def test_streamed_call_failed_mid_stream_is_not_retried(client, monkeypatch):
    _enable_failover(client)
    backup = respx.post(BACKUP_CHAT).mock(return_value=httpx.Response(200, json=_completion("qwen3-8b")))

    original_send = httpx.AsyncClient.send

    async def mock_send(self, request, **kwargs):
        if str(request.url) == UPSTREAM_CHAT:
            resp = httpx.Response(200, headers={"content-type": "text/event-stream"})
            async def failing_aiter_raw(chunk_size=None):
                yield b"data: {\"choices\": [{\"delta\": {\"content\": \"hi\"}}]}\n\n"
                raise httpx.ReadError("stream broken")
            resp.aiter_raw = lambda chunk_size=None: failing_aiter_raw(chunk_size)
            return resp
        return await original_send(self, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", mock_send)
    with pytest.raises(httpx.ReadError):
        await client.post(BASE + "/chat/completions", json=_chat(stream=True))
    assert backup.call_count == 0


@_ASYNC
@respx.mock
async def test_cooldown_means_second_request_goes_straight_to_healthy_backend(client):
    _enable_failover(client)
    primary = respx.post(UPSTREAM_CHAT).mock(side_effect=httpx.ConnectError("primary down"))
    backup = respx.post(BACKUP_CHAT).mock(return_value=httpx.Response(200, json=_completion("qwen3-8b")))

    resp1 = await client.post(BASE + "/chat/completions", json=_chat())
    assert resp1.status_code == 200, resp1.text

    resp2 = await client.post(BASE + "/chat/completions", json=_chat())
    assert resp2.status_code == 200, resp2.text

    assert primary.call_count == 1
    assert backup.call_count == 2
