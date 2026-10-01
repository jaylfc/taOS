"""Per-agent key re-scope (update_agent_key) — keystone for synced model management.

Since LiteLLM removal stage 2b-2a the key service has no process and no HTTP
client at all: a re-scope is a local key-store write.
"""
from __future__ import annotations

import pytest
from tinyagentos.llm_proxy import LLMProxy


@pytest.mark.asyncio
async def test_update_agent_key_rescopes_in_the_local_store(tmp_path):
    from tinyagentos.litellm_config import EMBEDDING_ALIAS
    from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path

    proxy = LLMProxy(port=4000, data_dir=tmp_path)
    key = LiteLLMKeyStore(default_keystore_path(tmp_path)).mint("a", ["m"])
    assert await proxy.update_agent_key(key, ["a", "b"]) is True
    assert LiteLLMKeyStore(default_keystore_path(tmp_path)).lookup(key)["allowed_models"] == [
        "a", "b", EMBEDDING_ALIAS
    ]


@pytest.mark.asyncio
async def test_update_agent_key_false_for_a_key_the_store_does_not_hold(tmp_path):
    proxy = LLMProxy(port=4000, data_dir=tmp_path)
    assert await proxy.update_agent_key("sk-legacy-postgres-key", ["a"]) is False


@pytest.mark.asyncio
async def test_update_agent_key_refuses_empty_models(tmp_path):
    # An empty scope must NOT silently become ["default"]: refuse, write nothing.
    from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path

    proxy = LLMProxy(port=4000, data_dir=tmp_path)
    key = LiteLLMKeyStore(default_keystore_path(tmp_path)).mint("a", ["m"])
    assert await proxy.update_agent_key(key, []) is False
    assert LiteLLMKeyStore(default_keystore_path(tmp_path)).lookup(key)["allowed_models"] == ["m"]


@pytest.mark.asyncio
async def test_model_change_keeps_the_embedding_alias(tmp_path):
    """After update_agent_key re-scopes to [Y], taos-embedding-default is still
    allowed so the agent does not lose embedding access on model change."""
    from tinyagentos.litellm_config import EMBEDDING_ALIAS
    from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path

    proxy = LLMProxy(port=4000, data_dir=tmp_path)
    key = LiteLLMKeyStore(default_keystore_path(tmp_path)).mint("a", ["old-model", EMBEDDING_ALIAS])
    assert await proxy.update_agent_key(key, ["new-model"]) is True
    assert LiteLLMKeyStore(default_keystore_path(tmp_path)).lookup(key)["allowed_models"] == [
        "new-model", EMBEDDING_ALIAS
    ]
