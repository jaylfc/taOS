"""Scoped-key authentication for the in-process LLM gateway (G2).

Hostile cases first, then the happy paths, then parity with the LiteLLM
custom-auth hook it replaces.

HTTP tests drive the REAL gateway routes (create_app on a tmp data dir with
``TAOS_LLM_GATEWAY=1``, the real AuthMiddleware in front, the upstream mocked
with respx). What a caller may use is read off the real ``GET /models``
listing and the real ``POST /chat/completions`` verdict. Where a test needs
the resolved identity itself (caller_id, kind, key_id) it calls
``gateway_caller`` directly with the request shape the route passes it.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

import tinyagentos.llm_gateway.auth as gw
from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path

# G1's module-scoped app + signed-in client (one create_app per module, reset
# before and after every test).
from test_llm_gateway import (  # noqa: F401
    UPSTREAM_CHAT,
    _completion,
    _set_default,
    client,
    gateway_client,
)

BASE = "/api/llm/v1"
MODELS = f"{BASE}/models"
CHAT = f"{BASE}/chat/completions"
ROUTED = ["qwen3-8b", "gpt-small"]  # what the OpenAI-compatible test backend serves
_ASYNC = pytest.mark.asyncio(loop_scope="module")


def _reset_keys(app) -> None:
    """Everything these tests mint or set outside G1's own reset."""
    import sqlite3
    from tinyagentos.agent_budget_store import default_budget_path
    path = default_keystore_path(app.state.data_dir)
    LiteLLMKeyStore(path)  # schema present
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM gateway_keys")
        conn.execute("DELETE FROM agent_keys")
        conn.commit()
    finally:
        conn.close()
    budget = default_budget_path(app.state.data_dir)
    for p in (budget, budget.with_name(budget.name + "-wal"), budget.with_name(budget.name + "-shm")):
        p.unlink(missing_ok=True)


@pytest.fixture
def app(client):
    """The module's app (overrides conftest's per-test create_app)."""
    application = client._transport.app
    _reset_keys(application)
    yield application
    _reset_keys(application)


@pytest_asyncio.fixture(loop_scope="module")
async def bare(app):
    """A client with NO session cookie on the module's real app."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _h(value) -> dict:
    return {"Authorization": value}


def _b(key) -> dict:
    return _h(f"Bearer {key}")


def _chat(model="qwen3-8b") -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


def _mint(app, models=("gpt-small",), bound_to="agent-1", kind="agent", **kw) -> str:
    return gw.mint_gateway_key(
        bound_to=bound_to, kind=kind, allowed_models=list(models),
        data_dir=app.state.data_dir, **kw,
    )


async def _listed(c, key) -> list[str]:
    resp = await c.get(MODELS, headers=_b(key))
    assert resp.status_code == 200, resp.text
    return [m["id"] for m in resp.json()["data"]]


def _assert_openai_401(resp):
    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert set(body) == {"error"}, body
    err = body["error"]
    assert err["type"] == "invalid_request_error"
    assert err["code"] == "invalid_api_key"
    assert isinstance(err["message"], str) and err["message"]


class _State:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Req:
    """The attributes gateway_caller reads off a starlette Request."""

    def __init__(self, app, key=None, **state):
        self.app = app
        self.headers = {"authorization": f"Bearer {key}"} if key is not None else {}
        self.state = _State(**state)


def _resolve(app, key, **state) -> gw.GatewayCaller:
    return gw.gateway_caller(_Req(app, key, **state))


# ---------------------------------------------------------------------------
# Hostile: malformed or absent credentials (real routes)
# ---------------------------------------------------------------------------


@_ASYNC
class TestMalformedCredentials:
    async def test_missing_header(self, bare):
        _assert_openai_401(await bare.get(MODELS))

    async def test_missing_header_on_chat(self, bare):
        _assert_openai_401(await bare.post(CHAT, json=_chat()))

    async def test_bearer_with_nothing(self, bare):
        _assert_openai_401(await bare.get(MODELS, headers=_h("Bearer ")))

    async def test_bearer_alone(self, bare):
        _assert_openai_401(await bare.get(MODELS, headers=_h("Bearer")))

    async def test_bearer_whitespace_only(self, bare):
        _assert_openai_401(await bare.get(MODELS, headers=_h("Bearer    \t ")))

    async def test_non_bearer_scheme_with_a_valid_key(self, bare, app):
        key = _mint(app)
        for scheme in ("Basic", "Token", "Digest", "Bearer:", "Bearerx"):
            _assert_openai_401(await bare.get(MODELS, headers=_h(f"{scheme} {key}")))

    async def test_key_without_scheme(self, bare, app):
        _assert_openai_401(await bare.get(MODELS, headers=_h(_mint(app))))

    async def test_key_with_embedded_whitespace(self, bare, app):
        key = _mint(app)
        mid = len(key) // 2
        _assert_openai_401(await bare.get(MODELS, headers=_h(f"Bearer {key[:mid]} {key[mid:]}")))
        _assert_openai_401(await bare.get(MODELS, headers=_h(f"Bearer {key} extra")))

    async def test_unicode_junk(self, bare, app):
        key = _mint(app)
        junk = [
            "Bearer \u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9",
            f"Bearer {key}\u200b",
            f"Bearer \u200b{key}",
            "Bearer \U0001f511\U0001f511\U0001f511\U0001f511\U0001f511",
            f"Bearer {key[:-1]}\u00ff",
        ]
        for value in junk:
            resp = await bare.get(MODELS, headers={"Authorization": value.encode("utf-8")})
            _assert_openai_401(resp)

    async def test_nul_and_control_bytes(self, bare, app):
        key = _mint(app)
        for value in (f"Bearer {key}\x01", f"Bearer \x7f{key}"):
            _assert_openai_401(
                await bare.get(MODELS, headers={"Authorization": value.encode("latin-1")})
            )

    async def test_wrong_length_keys(self, bare, app):
        key = _mint(app)
        for bad in (key[:-1], key + "A", key[: len(gw.GATEWAY_KEY_PREFIX)], key * 2):
            _assert_openai_401(await bare.get(MODELS, headers=_b(bad)))

    async def test_oversized_key(self, bare):
        _assert_openai_401(await bare.get(MODELS, headers=_b("A" * 10000)))

    async def test_unknown_key_with_the_gateway_prefix(self, bare, app):
        key = _mint(app)
        forged = gw.GATEWAY_KEY_PREFIX + ("A" * (len(key) - len(gw.GATEWAY_KEY_PREFIX)))
        _assert_openai_401(await bare.get(MODELS, headers=_b(forged)))

    async def test_unknown_legacy_shaped_key(self, bare):
        _assert_openai_401(await bare.get(MODELS, headers=_b("sk-taos-" + "B" * 43)))

    async def test_deployer_minted_agent_local_token_is_refused(self, bare, app):
        """The per-agent LOCAL token the deployer hands an agent is its
        controller identity, not a model credential: 401 as a bearer on the
        exempt paths, even though it is a valid controller credential."""
        token = app.state.auth.mint_agent_local_token("test-agent")
        assert app.state.auth.validate_local_token(token)
        _assert_openai_401(await bare.get(MODELS, headers=_b(token)))
        _assert_openai_401(await bare.post(CHAT, json=_chat(), headers=_b(token)))


# ---------------------------------------------------------------------------
# Hostile: dead keys
# ---------------------------------------------------------------------------


@_ASYNC
class TestDeadKeys:
    async def test_revoked_key(self, bare, app):
        key = _mint(app, bound_to="agent-r")
        assert (await bare.get(MODELS, headers=_b(key))).status_code == 200
        assert gw.revoke_keys_for("agent-r", data_dir=app.state.data_dir) == 1
        _assert_openai_401(await bare.get(MODELS, headers=_b(key)))

    async def test_revoke_is_scoped_to_its_principal(self, bare, app):
        mine = _mint(app, bound_to="agent-a")
        theirs = _mint(app, bound_to="agent-b")
        gw.revoke_keys_for("agent-a", data_dir=app.state.data_dir)
        _assert_openai_401(await bare.get(MODELS, headers=_b(mine)))
        assert (await bare.get(MODELS, headers=_b(theirs))).status_code == 200

    async def test_revoke_counts_only_live_keys(self, app, bare):
        _mint(app, bound_to="agent-c")
        _mint(app, bound_to="agent-c")
        assert gw.revoke_keys_for("agent-c", data_dir=app.state.data_dir) == 2
        assert gw.revoke_keys_for("agent-c", data_dir=app.state.data_dir) == 0

    async def test_expired_key(self, bare, app, monkeypatch):
        key = _mint(app, ttl_seconds=60)
        assert (await bare.get(MODELS, headers=_b(key))).status_code == 200
        real_now = gw._now
        monkeypatch.setattr(gw, "_now", lambda: real_now() + 61)
        _assert_openai_401(await bare.get(MODELS, headers=_b(key)))

    async def test_revoked_legacy_key(self, bare, app):
        store = LiteLLMKeyStore(default_keystore_path(app.state.data_dir))
        token = store.mint("legacy-agent", ["gpt-small"])
        assert await _listed(bare, token) == ["gpt-small"]
        assert gw.revoke_keys_for("legacy-agent", data_dir=app.state.data_dir) == 1
        _assert_openai_401(await bare.get(MODELS, headers=_b(token)))


# ---------------------------------------------------------------------------
# Hostile: the empty allowlist (deliberate reversal of LiteLLM semantics)
# ---------------------------------------------------------------------------


class TestEmptyAllowlistDenies:
    MODELS_TO_TRY = ("taos-default", "default", "gpt-small", "", "*", "all", None)

    def test_empty_frozenset_denies_every_model(self):
        for kind in ("agent", "node", "test"):
            c = gw.GatewayCaller(caller_id="a", kind=kind, allowed_models=frozenset(), key_id="k")
            for m in self.MODELS_TO_TRY:
                assert c.may_use(m) is False, (kind, m)

    def test_scoped_caller_cannot_carry_none(self):
        """None means every model; only the admin kinds may hold it."""
        for kind in ("agent", "node", "test"):
            with pytest.raises(ValueError):
                gw.GatewayCaller(caller_id="x", kind=kind, allowed_models=None)

    def test_admin_kinds_must_carry_none(self):
        for kind in gw.ADMIN_KINDS:
            with pytest.raises(ValueError):
                gw.GatewayCaller(caller_id="x", kind=kind, allowed_models=frozenset({"a"}))

    @_ASYNC
    @respx.mock
    async def test_minted_empty_key_authenticates_but_may_use_nothing(self, bare, app):
        upstream = respx.post(UPSTREAM_CHAT)
        key = _mint(app, models=())
        assert await _listed(bare, key) == []
        for m in ("taos-default", *ROUTED):
            resp = await bare.post(CHAT, json=_chat(m), headers=_b(key))
            assert resp.status_code == 403, resp.text
            assert resp.json()["error"]["code"] == "model_not_permitted"
        assert not upstream.called

    @_ASYNC
    async def test_legacy_empty_key_may_use_nothing(self, bare, app):
        store = LiteLLMKeyStore(default_keystore_path(app.state.data_dir))
        token = store.mint("empty-legacy", [])
        assert await _listed(bare, token) == []
        resp = await bare.post(CHAT, json=_chat("gpt-small"), headers=_b(token))
        assert resp.status_code == 403


class TestTaosDefaultIsNotImplied:
    def test_alias_must_be_named(self):
        c = gw.GatewayCaller(
            caller_id="a", kind="agent", allowed_models=frozenset({"qwen3-8b"}), key_id="k"
        )
        # Even if taos-default currently resolves to qwen3-8b, the alias is not
        # granted by naming the concrete model.
        assert c.may_use("qwen3-8b") is True
        assert c.may_use("taos-default") is False

    def test_alias_alone_does_not_grant_its_target(self):
        c = gw.GatewayCaller(
            caller_id="a", kind="agent", allowed_models=frozenset({"taos-default"}), key_id="k"
        )
        assert c.may_use("taos-default") is True
        assert c.may_use("qwen3-8b") is False



# ---------------------------------------------------------------------------
# Hostile: no key material in logs; constant-time comparison
# ---------------------------------------------------------------------------


@_ASYNC
class TestSecrecy:
    async def test_no_key_material_in_logs(self, bare, app, caplog):
        caplog.set_level(logging.DEBUG)
        good = _mint(app, bound_to="log-agent")
        dead = _mint(app, bound_to="log-dead")
        gw.revoke_keys_for("log-dead", data_dir=app.state.data_dir)
        admin = app.state.auth.get_local_token()
        forged = gw.GATEWAY_KEY_PREFIX + "Z" * (len(good) - len(gw.GATEWAY_KEY_PREFIX))
        presented = [good, dead, admin, forged, good[:-1]]
        for k in presented:
            await bare.get(MODELS, headers=_b(k))
            await bare.post(CHAT, json=_chat("nope"), headers=_b(k))
        text = caplog.text + "".join(str(r.args) for r in caplog.records)
        for k in presented:
            assert k not in text
            body = k[len(gw.GATEWAY_KEY_PREFIX):] if k.startswith(gw.GATEWAY_KEY_PREFIX) else k
            assert body[:12] not in text
            assert hashlib.sha256(k.encode()).hexdigest() not in text
        # The rejections themselves ARE logged (so this test is not vacuous).
        assert any("gateway" in r.getMessage().lower() for r in caplog.records)

    async def test_scoped_key_is_compared_with_compare_digest(self, bare, app, monkeypatch):
        key = _mint(app)
        want = hashlib.sha256(key.encode()).hexdigest()
        calls = []
        real = hmac.compare_digest

        def spy(a, b):
            calls.append((a, b))
            return real(a, b)

        monkeypatch.setattr(gw.hmac, "compare_digest", spy)
        assert (await bare.get(MODELS, headers=_b(key))).status_code == 200
        flat = [x.decode() if isinstance(x, bytes) else x for pair in calls for x in pair]
        assert flat.count(want) >= 2, "stored hash must be compared to the presented hash"

    async def test_compare_digest_verdict_is_the_verdict(self, bare, app, monkeypatch):
        """If compare_digest says no, a key that exists is refused: the lookup
        is not the comparison. Same for the admin bearer."""
        key = _mint(app)
        admin = app.state.auth.get_local_token()
        monkeypatch.setattr(gw.hmac, "compare_digest", lambda a, b: False)
        _assert_openai_401(await bare.get(MODELS, headers=_b(key)))
        _assert_openai_401(await bare.get(MODELS, headers=_b(admin)))


class TestSecrecySource:
    def test_source_has_no_plain_equality_on_secrets(self):
        import inspect
        src = inspect.getsource(gw)
        assert "compare_digest" in src
        for bad in ("token ==", "== token", "key_hash ==", "== key_hash",
                    "presented ==", "== presented", "stored ==", "== stored"):
            assert bad not in src, bad


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@_ASYNC
class TestHappyPaths:
    async def test_session_is_admin(self, client, app):
        """G1's contract, kept: a signed-in session may use every model."""
        resp = await client.get(MODELS)
        assert resp.status_code == 200
        listed = [m["id"] for m in resp.json()["data"]]
        assert {"taos-default", *ROUTED, "vault-model", "claude-x"} <= set(listed)

    async def test_local_token_bearer_is_admin(self, bare, app, client):
        admin = app.state.auth.get_local_token()
        session_view = [m["id"] for m in (await client.get(MODELS)).json()["data"]]
        assert await _listed(bare, admin) == session_view
        caller = _resolve(app, admin, via="exempt", user_id=None)
        primary = app.state.auth.get_primary_user()
        assert caller == gw.GatewayCaller(
            caller_id=f"user:{primary['id']}", kind="local_token", allowed_models=None,
        )

    async def test_scheme_is_case_insensitive(self, bare, app):
        admin = app.state.auth.get_local_token()
        assert (await bare.get(MODELS, headers=_h(f"bearer {admin}"))).status_code == 200

    @respx.mock
    async def test_scoped_key_may_use_exactly_its_list(self, bare, app):
        upstream = respx.post(UPSTREAM_CHAT).mock(
            return_value=httpx.Response(200, json=_completion("gpt-small")))
        key = _mint(app, models=("gpt-small",), bound_to="scoped")
        assert await _listed(bare, key) == ["gpt-small"]
        ok = await bare.post(CHAT, json=_chat("gpt-small"), headers=_b(key))
        assert ok.status_code == 200, ok.text
        for m in ("qwen3-8b", "taos-default", "GPT-SMALL"):
            resp = await bare.post(CHAT, json=_chat(m), headers=_b(key))
            assert resp.status_code == 403, (m, resp.text)
        assert upstream.call_count == 1
        caller = _resolve(app, key, via="exempt", user_id=None)
        assert caller.caller_id == "scoped"
        assert caller.kind == "agent"
        assert caller.allowed_models == frozenset({"gpt-small"})
        assert caller.key_id and caller.key_id.startswith("gk_")

    async def test_mint_validate_revoke(self, bare, app):
        key = _mint(app, bound_to="cycle")
        assert key.startswith(gw.GATEWAY_KEY_PREFIX)
        assert len(key) == gw.GATEWAY_KEY_LEN
        assert (await bare.get(MODELS, headers=_b(key))).status_code == 200
        assert gw.revoke_keys_for("cycle", data_dir=app.state.data_dir) == 1
        _assert_openai_401(await bare.get(MODELS, headers=_b(key)))

    async def test_plaintext_is_never_stored(self, app, bare):
        import sqlite3
        key = _mint(app, bound_to="hashed")
        path = default_keystore_path(app.state.data_dir)
        raw = path.read_bytes()
        wal = path.with_name(path.name + "-wal")
        if wal.exists():
            raw += wal.read_bytes()
        assert key.encode() not in raw
        conn = sqlite3.connect(path)
        try:
            (h,) = conn.execute(
                "SELECT key_hash FROM gateway_keys WHERE bound_to = ?", ("hashed",)
            ).fetchone()
        finally:
            conn.close()
        assert h == hashlib.sha256(key.encode()).hexdigest()

    async def test_node_key(self, bare, app):
        key = gw.mint_for_node("pi-5", ["gpt-small"], data_dir=app.state.data_dir)
        caller = _resolve(app, key)
        assert (caller.caller_id, caller.kind) == ("node:pi-5", "node")
        assert await _listed(bare, key) == ["gpt-small"]
        assert gw.revoke_for_node("pi-5", data_dir=app.state.data_dir) == 1
        _assert_openai_401(await bare.get(MODELS, headers=_b(key)))



@_ASYNC
@respx.mock
async def test_end_to_end_node_key_through_the_gateway(bare, app, client):
    """mint_for_node(n, ["taos-default"]) -> the real gateway -> the (mocked)
    upstream -> 200, and after revoke_for_node -> 401 without touching it."""
    upstream = respx.post(UPSTREAM_CHAT).mock(
        return_value=httpx.Response(200, json=_completion("qwen3-8b")))
    await _set_default(client, "qwen3-8b")
    key = gw.mint_for_node("e2e", ["taos-default"], data_dir=app.state.data_dir)
    resp = await bare.post(CHAT, json=_chat("taos-default"), headers=_b(key))
    assert resp.status_code == 200, resp.text
    assert resp.json()["choices"][0]["message"]["content"] == "hi"
    assert upstream.call_count == 1

    assert gw.revoke_for_node("e2e", data_dir=app.state.data_dir) == 1
    _assert_openai_401(await bare.post(CHAT, json=_chat("taos-default"), headers=_b(key)))
    assert upstream.call_count == 1


# ---------------------------------------------------------------------------
# The taos-default alias rule
# ---------------------------------------------------------------------------
#
# A caller allowed "taos-default" may use whatever it CURRENTLY resolves to,
# without the concrete model listed (only the owner sets the default). The
# other direction does not hold, and a concrete model requested directly still
# needs its own entry.


@_ASYNC
class TestAliasRule:
    @respx.mock
    async def test_concrete_key_cannot_call_the_alias(self, bare, app, client):
        upstream = respx.post(UPSTREAM_CHAT)
        await _set_default(client, "qwen3-8b")
        key = _mint(app, models=("qwen3-8b",))
        resp = await bare.post(CHAT, json=_chat("taos-default"), headers=_b(key))
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "model_not_permitted"
        assert not upstream.called

    @respx.mock
    async def test_alias_key_cannot_call_the_concrete_model_directly(self, bare, app, client):
        upstream = respx.post(UPSTREAM_CHAT)
        await _set_default(client, "qwen3-8b")
        key = _mint(app, models=("taos-default",))
        resp = await bare.post(CHAT, json=_chat("qwen3-8b"), headers=_b(key))
        assert resp.status_code == 403, resp.text
        assert not upstream.called

    @respx.mock
    async def test_alias_key_follows_the_owner_changing_the_default(self, bare, app, client):
        import json as _json
        upstream = respx.post(UPSTREAM_CHAT).mock(
            return_value=httpx.Response(200, json=_completion("x")))
        key = gw.mint_for_node("board", ["taos-default"], data_dir=app.state.data_dir)
        await _set_default(client, "qwen3-8b")
        r1 = await bare.post(CHAT, json=_chat("taos-default"), headers=_b(key))
        assert r1.status_code == 200, r1.text
        assert _json.loads(upstream.calls.last.request.content)["model"] == "qwen3-8b"
        await _set_default(client, "gpt-small")
        r2 = await bare.post(CHAT, json=_chat("taos-default"), headers=_b(key))
        assert r2.status_code == 200, r2.text
        assert _json.loads(upstream.calls.last.request.content)["model"] == "gpt-small"

    async def test_alias_key_lists_only_the_alias(self, bare, app, client):
        await _set_default(client, "qwen3-8b")
        key = _mint(app, models=("taos-default",))
        assert await _listed(bare, key) == ["taos-default"]

    @respx.mock
    async def test_default_is_resolved_once_and_that_value_is_forwarded(
        self, bare, app, client, monkeypatch,
    ):
        """No time-of-check/time-of-use gap: if the default changed between
        the permission check and the forward, the request still goes to the
        ONE value resolved, and the resolver runs exactly once."""
        import json as _json
        import tinyagentos.llm_gateway.resolve as resolve_mod
        upstream = respx.post(UPSTREAM_CHAT).mock(
            return_value=httpx.Response(200, json=_completion("x")))
        answers = iter(["qwen3-8b", "gpt-small"])
        calls = []

        async def flipping_default(state):
            calls.append(1)
            return next(answers)

        monkeypatch.setattr(resolve_mod, "default_chat_model", flipping_default)
        key = _mint(app, models=("taos-default",))
        resp = await bare.post(CHAT, json=_chat("taos-default"), headers=_b(key))
        assert resp.status_code == 200, resp.text
        assert len(calls) == 1
        assert _json.loads(upstream.calls.last.request.content)["model"] == "qwen3-8b"


class TestMintValidation:
    def test_bare_string_allowlist_is_refused(self, tmp_path):
        with pytest.raises(TypeError):
            gw.mint_gateway_key(bound_to="a", kind="agent", allowed_models="gpt-a", data_dir=tmp_path)

    def test_bad_kinds_and_principals(self, tmp_path):
        bad = [
            dict(bound_to="a", kind="admin", allowed_models=[]),
            dict(bound_to="a", kind="session", allowed_models=[]),
            dict(bound_to="", kind="agent", allowed_models=[]),
            dict(bound_to="node:x", kind="agent", allowed_models=[]),
            dict(bound_to="x", kind="node", allowed_models=[]),
            dict(bound_to="node:", kind="node", allowed_models=[]),
            dict(bound_to="a", kind="agent", allowed_models=["ok", ""]),
            dict(bound_to="a", kind="agent", allowed_models=[], ttl_seconds=0),
        ]
        for kw in bad:
            with pytest.raises(ValueError):
                gw.mint_gateway_key(data_dir=tmp_path, **kw)

    def test_node_principal(self):
        assert gw.node_principal("pi-5") == "node:pi-5"
        with pytest.raises(ValueError):
            gw.node_principal("")


@pytest.fixture
def parity_env(app):
    """The key store, a stale on-disk LiteLLM master key (old installs still
    have the file; nothing may accept it) and the budget store path."""
    from tinyagentos.agent_budget_store import default_budget_path
    data_dir = app.state.data_dir
    master = "sk-taos-stale-litellm-master-key-0123456789"
    (data_dir / ".litellm_master_key").write_text(master)
    return {
        "store": LiteLLMKeyStore(default_keystore_path(data_dir)),
        "master": master,
        "budget_path": default_budget_path(data_dir),
    }


def _over_budget(parity_env, agent):
    from tinyagentos.agent_budget_store import AgentBudgetStore
    b = AgentBudgetStore(parity_env["budget_path"])
    b.set_budget(agent, 1.0)
    b.add_spend(agent, 2.0)


# ---------------------------------------------------------------------------
# Budget: the hook's hard stop, same status AND body
# ---------------------------------------------------------------------------


@_ASYNC
class TestBudget:
    @respx.mock
    async def test_over_budget_matches_the_hook_on_the_wire(self, bare, app, parity_env):
        # Frozen from the LiteLLM custom_auth hook's 429 as LiteLLM rendered it
        # (string param and code), so a client that handled that refusal
        # handles this one. LiteLLM itself is gone (removal stage 2b-2a).
        upstream = respx.post(UPSTREAM_CHAT)
        token = parity_env["store"].mint("broke-agent", ["gpt-small"])
        _over_budget(parity_env, "broke-agent")
        want_status = 429
        want_body = {"error": {"message": "agent 'broke-agent' has exceeded its LLM budget",
                               "type": "auth_error", "param": "None", "code": "429"}}

        for resp in (
            await bare.post(CHAT, json=_chat("gpt-small"), headers=_b(token)),
            await bare.get(MODELS, headers=_b(token)),
        ):
            assert resp.status_code == want_status, resp.text
            assert resp.json() == want_body
        assert not upstream.called

    async def test_over_budget_gateway_key_bound_to_the_agent(self, bare, app, parity_env):
        key = _mint(app, bound_to="broke-gw")
        _over_budget(parity_env, "broke-gw")
        resp = await bare.get(MODELS, headers=_b(key))
        assert resp.status_code == 429
        assert resp.json()["error"]["message"] == "agent 'broke-gw' has exceeded its LLM budget"

    @respx.mock
    async def test_under_budget_passes(self, bare, app, parity_env):
        from tinyagentos.agent_budget_store import AgentBudgetStore
        respx.post(UPSTREAM_CHAT).mock(
            return_value=httpx.Response(200, json=_completion("gpt-small")))
        token = parity_env["store"].mint("thrifty", ["gpt-small"])
        b = AgentBudgetStore(parity_env["budget_path"])
        b.set_budget("thrifty", 5.0)
        b.add_spend("thrifty", 1.0)
        resp = await bare.post(CHAT, json=_chat("gpt-small"), headers=_b(token))
        assert resp.status_code == 200, resp.text

    async def test_node_key_has_no_agent_budget(self, bare, app, parity_env):
        """A node is not an agent: even a budget row under its principal (or
        its bare name) does not block it."""
        key = gw.mint_for_node("busy", ["gpt-small"], data_dir=app.state.data_dir)
        _over_budget(parity_env, "node:busy")
        _over_budget(parity_env, "busy")
        assert await _listed(bare, key) == ["gpt-small"]

    async def test_admin_is_never_budget_checked(self, bare, app, parity_env):
        # The host local token is the admin credential now (the master key is
        # refused outright); a budget row under its caller id never blocks it.
        token = app.state.auth.get_local_token()
        _over_budget(parity_env, "local")
        _over_budget(parity_env, "litellm-master")
        assert (await bare.get(MODELS, headers=_b(token))).status_code == 200
        assert (await bare.get(MODELS, headers=_b(parity_env["master"]))).status_code == 401
