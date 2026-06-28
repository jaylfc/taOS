"""Tests for the in-house LiteLLM key store + custom_auth hook."""
import importlib
import sys
import types

import pytest

from tinyagentos.litellm_keystore import (
    LiteLLMKeyStore,
    default_keystore_path,
)


def test_mint_and_lookup(tmp_path):
    store = LiteLLMKeyStore(tmp_path / "keys.db")
    token = store.mint("agent-a", ["gpt-4o", "default"])
    assert token.startswith("sk-taos-")
    rec = store.lookup(token)
    assert rec == {"agent": "agent-a", "allowed_models": ["gpt-4o", "default"]}


def test_lookup_unknown_returns_none(tmp_path):
    store = LiteLLMKeyStore(tmp_path / "keys.db")
    assert store.lookup("sk-taos-nope") is None


def test_empty_allowlist_stored_as_deny_all(tmp_path):
    store = LiteLLMKeyStore(tmp_path / "keys.db")
    token = store.mint("agent-a", None)
    assert store.lookup(token)["allowed_models"] == []


def test_set_models_rescopes(tmp_path):
    store = LiteLLMKeyStore(tmp_path / "keys.db")
    token = store.mint("agent-a", ["a"])
    assert store.set_models(token, ["b", "c"]) is True
    assert store.lookup(token)["allowed_models"] == ["b", "c"]
    assert store.set_models("sk-taos-missing", ["x"]) is False


def test_delete_and_delete_agent(tmp_path):
    store = LiteLLMKeyStore(tmp_path / "keys.db")
    t1 = store.mint("agent-a", ["a"])
    t2 = store.mint("agent-a", ["b"])
    t3 = store.mint("agent-b", ["c"])
    assert store.delete(t1) is True
    assert store.lookup(t1) is None
    assert store.delete_agent("agent-a") == 1  # t2 remains
    assert store.lookup(t2) is None
    assert store.lookup(t3) is not None  # other agent untouched


def test_cross_process_handle_sees_writes(tmp_path):
    """A second handle (the hook's reader) sees the controller's writes."""
    path = tmp_path / "keys.db"
    writer = LiteLLMKeyStore(path)
    token = writer.mint("agent-a", ["m"])
    reader = LiteLLMKeyStore(path)
    assert reader.lookup(token)["agent"] == "agent-a"


def test_default_keystore_path(tmp_path):
    assert default_keystore_path(tmp_path) == tmp_path / ".litellm_keys.db"


# --- custom_auth hook ---------------------------------------------------


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _stub_litellm(monkeypatch):
    """Inject a minimal ``litellm.proxy._types.UserAPIKeyAuth`` so the hook
    runs without the (container-only) litellm package installed."""
    class _UserAPIKeyAuth:
        def __init__(self, **kw):
            self.api_key = kw.get("api_key")
            self.key_alias = kw.get("key_alias")
            self.models = kw.get("models", [])
            self.metadata = kw.get("metadata", {})

    litellm = types.ModuleType("litellm")
    proxy = types.ModuleType("litellm.proxy")
    _types = types.ModuleType("litellm.proxy._types")
    _types.UserAPIKeyAuth = _UserAPIKeyAuth
    proxy._types = _types
    litellm.proxy = proxy
    monkeypatch.setitem(sys.modules, "litellm", litellm)
    monkeypatch.setitem(sys.modules, "litellm.proxy", proxy)
    monkeypatch.setitem(sys.modules, "litellm.proxy._types", _types)


def _reload_auth(monkeypatch, keystore_path, master="sk-master"):
    monkeypatch.setenv("TAOS_LITELLM_KEYSTORE", str(keystore_path))
    monkeypatch.setenv("LITELLM_MASTER_KEY", master)
    _stub_litellm(monkeypatch)
    import tinyagentos.litellm_auth as auth
    importlib.reload(auth)  # reset the cached module-level store
    return auth


@pytest.mark.asyncio
async def test_hook_master_key_passthrough(monkeypatch, tmp_path):
    auth = _reload_auth(monkeypatch, tmp_path / "keys.db")
    result = await auth.user_api_key_auth(_FakeRequest({"model": "x"}), "sk-master")
    assert result.api_key == "sk-master"


@pytest.mark.asyncio
async def test_hook_unknown_key_401(monkeypatch, tmp_path):
    from fastapi import HTTPException
    auth = _reload_auth(monkeypatch, tmp_path / "keys.db")
    with pytest.raises(HTTPException) as exc:
        await auth.user_api_key_auth(_FakeRequest({"model": "x"}), "sk-taos-bad")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_hook_valid_key_in_scope(monkeypatch, tmp_path):
    path = tmp_path / "keys.db"
    token = LiteLLMKeyStore(path).mint("agent-a", ["gpt-4o"])
    auth = _reload_auth(monkeypatch, path)
    result = await auth.user_api_key_auth(_FakeRequest({"model": "gpt-4o"}), token)
    assert result.metadata["agent"] == "agent-a"
    assert "gpt-4o" in result.models


@pytest.mark.asyncio
async def test_hook_out_of_scope_model_403(monkeypatch, tmp_path):
    from fastapi import HTTPException
    path = tmp_path / "keys.db"
    token = LiteLLMKeyStore(path).mint("agent-a", ["gpt-4o"])
    auth = _reload_auth(monkeypatch, path)
    with pytest.raises(HTTPException) as exc:
        await auth.user_api_key_auth(_FakeRequest({"model": "claude-3"}), token)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_hook_empty_allowlist_is_deny_all(monkeypatch, tmp_path):
    """An empty allowlist must 403 every model, not fall through to allow-all
    (which is how LiteLLM reads UserAPIKeyAuth(models=[]))."""
    from fastapi import HTTPException
    path = tmp_path / "keys.db"
    token = LiteLLMKeyStore(path).mint("agent-a", None)  # stored as []
    auth = _reload_auth(monkeypatch, path)
    with pytest.raises(HTTPException) as exc:
        await auth.user_api_key_auth(_FakeRequest({"model": "anything"}), token)
    assert exc.value.status_code == 403
