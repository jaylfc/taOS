"""Tests for the in-house LiteLLM key store + custom_auth hook."""


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


