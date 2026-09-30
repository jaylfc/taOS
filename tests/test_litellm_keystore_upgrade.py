"""The agent_keys token_hash migration, run over a PRE-CHANGE database.

A fresh-DB test cannot see an upgrade crash, so every test here first builds
agent_keys with dev's ORIGINAL schema -- copied verbatim below, deliberately
NOT created through the current code -- and fills it with rows, then opens it
with the new LiteLLMKeyStore.

LiteLLM stays the default path during side-by-side, and its hook
(litellm_auth.user_api_key_auth -> LiteLLMKeyStore.lookup) reads the PLAINTEXT
token column, so the migration must add token_hash without dropping, blanking
or no longer writing that column.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

import pytest

import tinyagentos.litellm_auth as hook_mod
import tinyagentos.llm_gateway.auth as gw
from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path

# dev's agent_keys schema before this change, verbatim.
_ORIGINAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_keys (
    token           TEXT PRIMARY KEY,
    agent           TEXT NOT NULL,
    allowed_models  TEXT NOT NULL,
    created_ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_keys_agent ON agent_keys(agent);
"""

_OLD_ROWS = [
    ("agent-a", '["gpt-small", "default"]'),
    ("agent-b", "[]"),
    ("agent-c", '["qwen3-8b"]'),
]


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def _old_db(path: Path) -> dict[str, str]:
    """Create a pre-change keystore and return {agent: plaintext token}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_ORIGINAL_SCHEMA)
    tokens = {}
    for agent, models in _OLD_ROWS:
        tok = "sk-taos-" + secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO agent_keys (token, agent, allowed_models, created_ts) VALUES (?, ?, ?, ?)",
            (tok, agent, models, time.time()),
        )
        tokens[agent] = tok
    conn.commit()
    conn.close()
    return tokens


def _rows(path: Path) -> list[dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM agent_keys ORDER BY agent")]
    finally:
        conn.close()


@pytest.fixture
def hook_env(monkeypatch):
    for attr in ("_store", "_store_path", "_budget_store_cache", "_budget_store_cache_path"):
        monkeypatch.setattr(hook_mod, attr, None)
    monkeypatch.delenv("TAOS_AGENT_BUDGETS", raising=False)
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-master-not-under-test")

    def point_at(path):
        monkeypatch.setenv("TAOS_LITELLM_KEYSTORE", str(path))
    return point_at


class _HookReq:
    def __init__(self, model):
        self._b = {"model": model}

    async def json(self):
        return self._b


async def _hook_allows(token: str, model: str) -> bool:
    from fastapi import HTTPException
    try:
        res = await hook_mod.user_api_key_auth(_HookReq(model), token)
    except ModuleNotFoundError:
        pytest.skip("litellm not installed")
    except HTTPException:
        return False
    return bool(res.models)


class _Req:
    def __init__(self, data_dir, token):
        class _S:
            pass
        self.app = type("A", (), {"state": _S()})()
        self.app.state.data_dir = data_dir
        self.headers = {"authorization": f"Bearer {token}"}
        self.state = _S()


def _gateway(data_dir, token):
    return gw.gateway_caller(_Req(data_dir, token))


def test_every_old_row_is_backfilled(tmp_path):
    path = tmp_path / "keys.db"
    tokens = _old_db(path)
    LiteLLMKeyStore(path)
    rows = _rows(path)
    assert len(rows) == len(_OLD_ROWS)
    for r in rows:
        assert r["token"] == tokens[r["agent"]], "plaintext column must survive"
        assert r["token_hash"] == _sha(r["token"])


def test_second_open_is_idempotent(tmp_path):
    path = tmp_path / "keys.db"
    _old_db(path)
    LiteLLMKeyStore(path)
    first = _rows(path)
    LiteLLMKeyStore(path)  # no duplicate-column error
    LiteLLMKeyStore(path)
    assert _rows(path) == first
    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_keys)")]
    conn.close()
    assert cols.count("token_hash") == 1


def test_key_minted_after_migration_gets_its_hash_at_insert(tmp_path):
    path = tmp_path / "keys.db"
    _old_db(path)
    store = LiteLLMKeyStore(path)
    tok = store.mint("agent-new", ["gpt-small"])
    (row,) = [r for r in _rows(path) if r["agent"] == "agent-new"]
    assert row["token"] == tok
    assert row["token_hash"] == _sha(tok)


def test_row_written_by_an_old_process_is_backfilled_on_next_open(tmp_path):
    """Side-by-side: an older binary may still INSERT without token_hash."""
    path = tmp_path / "keys.db"
    _old_db(path)
    LiteLLMKeyStore(path)
    tok = "sk-taos-" + secrets.token_urlsafe(32)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO agent_keys (token, agent, allowed_models, created_ts) VALUES (?, ?, ?, ?)",
        (tok, "agent-late", '["gpt-small"]', time.time()),
    )
    conn.commit()
    conn.close()
    LiteLLMKeyStore(path)
    (row,) = [r for r in _rows(path) if r["agent"] == "agent-late"]
    assert row["token_hash"] == _sha(tok)


@pytest.mark.asyncio
async def test_old_key_still_authenticates_through_the_litellm_hook(tmp_path, hook_env):
    path = tmp_path / "keys.db"
    tokens = _old_db(path)
    LiteLLMKeyStore(path)  # migrate
    hook_env(path)
    assert await _hook_allows(tokens["agent-a"], "gpt-small")
    assert not await _hook_allows(tokens["agent-a"], "qwen3-8b")
    assert await _hook_allows(tokens["agent-c"], "qwen3-8b")


@pytest.mark.asyncio
async def test_old_key_authenticates_through_the_gateway(tmp_path):
    data_dir = tmp_path / "data"
    tokens = _old_db(default_keystore_path(data_dir))
    caller = _gateway(data_dir, tokens["agent-a"])
    assert (caller.caller_id, caller.kind) == ("agent-a", "agent")
    assert caller.allowed_models == frozenset({"gpt-small", "default"})


@pytest.mark.asyncio
async def test_key_minted_after_migration_works_through_both_paths(tmp_path, hook_env):
    data_dir = tmp_path / "data"
    path = default_keystore_path(data_dir)
    _old_db(path)
    tok = LiteLLMKeyStore(path).mint("agent-new", ["gpt-small"])
    hook_env(path)
    assert await _hook_allows(tok, "gpt-small")
    caller = _gateway(data_dir, tok)
    assert caller.caller_id == "agent-new"
    assert caller.may_use("gpt-small") and not caller.may_use("qwen3-8b")


def test_startup_does_not_crash_on_an_old_db(tmp_data_dir):
    """create_app over a data dir that already holds a pre-change keystore."""
    from tinyagentos.app import create_app
    tokens = _old_db(default_keystore_path(tmp_data_dir))
    app = create_app(data_dir=tmp_data_dir)
    # The controller's own opener (LLMProxy's lazily-built keystore) migrates.
    store = app.state.llm_proxy._keystore()
    assert store.lookup(tokens["agent-a"])["agent"] == "agent-a"
    assert all(r["token_hash"] == _sha(r["token"]) for r in _rows(default_keystore_path(tmp_data_dir)))
    caller = _gateway(app.state.data_dir, tokens["agent-c"])
    assert caller.may_use("qwen3-8b")
    # create_app points the module default at its data dir, so callers that
    # pass no data_dir (S1 pairing, agent lifecycle) use the same store.
    key = gw.mint_gateway_key(bound_to="dflt", kind="agent", allowed_models=["gpt-small"])
    assert _gateway(app.state.data_dir, key).caller_id == "dflt"
    assert gw.revoke_keys_for("dflt") == 1
