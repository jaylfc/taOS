"""Agent keys minted before the embedding alias was granted can embed after upgrade.

Every mint path now scopes an agent key to its models plus
``taos-embedding-default``, but keys minted earlier (naira and mary on the Pi,
09-30) lack it, so their ``/v1/embeddings`` calls get 403 model_not_permitted.
Each test builds the keystore with the schema those installs have -- written
here verbatim, deliberately NOT through the current code -- and a LiteLLM key
mirrored into ``gateway_keys`` (the row the gateway resolves first), then boots
the REAL controller app and drives the agent listener.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
import respx
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.litellm_config import EMBEDDING_ALIAS
from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path

NPU = "http://npu.test:8080"
BACKENDS = [{"name": "npu", "type": "rkllama", "url": NPU, "priority": 1}]

# The keystore schema on installs from before this change, verbatim.
_DEPLOYED_SCHEMA = """
CREATE TABLE agent_keys (
    token           TEXT PRIMARY KEY,
    agent           TEXT NOT NULL,
    allowed_models  TEXT NOT NULL,
    created_ts      REAL NOT NULL
, token_hash TEXT);
CREATE INDEX idx_agent_keys_agent ON agent_keys(agent);
CREATE TABLE gateway_keys (
    key_id          TEXT PRIMARY KEY,
    key_hash        TEXT NOT NULL UNIQUE,
    bound_to        TEXT NOT NULL,
    kind            TEXT NOT NULL,
    allowed_models  TEXT NOT NULL,
    created_ts      REAL NOT NULL,
    expires_ts      REAL,
    revoked_ts      REAL
);
CREATE INDEX idx_gateway_keys_bound ON gateway_keys(bound_to);
CREATE INDEX idx_agent_keys_hash ON agent_keys(token_hash);
"""


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def _seed(path: Path, agents: dict[str, list[str]], *, revoked: set[str] = frozenset()) -> dict[str, str]:
    """A pre-change keystore: each agent a LiteLLM key plus its gk_lit_ mirror."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_DEPLOYED_SCHEMA)
    tokens = {}
    now = time.time()
    for agent, models in agents.items():
        tok = "sk-taos-" + secrets.token_urlsafe(32)
        h = _sha(tok)
        conn.execute(
            "INSERT INTO agent_keys (token, agent, allowed_models, created_ts, token_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (tok, agent, json.dumps(models), now, h),
        )
        conn.execute(
            "INSERT INTO gateway_keys (key_id, key_hash, bound_to, kind, allowed_models, "
            "created_ts, expires_ts, revoked_ts) VALUES (?, ?, ?, 'agent', ?, ?, NULL, ?)",
            ("gk_lit_" + h[:16], h, agent, json.dumps(models), now,
             now if agent in revoked else None),
        )
        tokens[agent] = tok
    conn.commit()
    conn.close()
    return tokens


def _rows(path: Path) -> tuple[dict, dict]:
    conn = sqlite3.connect(path)
    a = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT agent, allowed_models FROM agent_keys")}
    g = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT bound_to, allowed_models FROM gateway_keys")}
    conn.close()
    return a, g


def _boot(tmp_data_dir):
    from tinyagentos.app import create_app
    from tinyagentos.llm_gateway.listener import create_agent_listener_app

    cfg_path = tmp_data_dir / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["backends"] = BACKENDS
    cfg_path.write_text(yaml.dump(cfg))
    app = create_app(data_dir=tmp_data_dir)
    app.state._startup_complete = True
    return create_agent_listener_app(app)


async def _embed(listener, token: str) -> httpx.Response:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{NPU}/api/tags").mock(return_value=httpx.Response(
            200, json={"models": [{"name": "nomic-embed-text"}]}))
        router.post(f"{NPU}/api/embed").mock(return_value=httpx.Response(
            200, json={"model": "nomic-embed-text", "embeddings": [[0.1, 0.2]],
                       "prompt_eval_count": 2}))
        router.route(host="127.0.0.1", port=4000).mock(
            side_effect=httpx.ConnectError("connection refused"))
        async with AsyncClient(transport=ASGITransport(app=listener),
                               base_url="http://127.0.0.1:4000") as c:
            return await c.post("/v1/embeddings", json={"model": EMBEDDING_ALIAS, "input": "hi"},
                                headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_pre_alias_agent_key_embeds_after_upgrade(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    path = default_keystore_path(tmp_data_dir)
    tokens = _seed(path, {"naira": ["kilo-auto/free", "stepfun/step-3.7-flash:free"]})
    listener = _boot(tmp_data_dir)

    resp = await _embed(listener, tokens["naira"])

    assert resp.status_code == 200, resp.text
    a, g = _rows(path)
    assert a["naira"] == ["kilo-auto/free", "stepfun/step-3.7-flash:free", EMBEDDING_ALIAS]
    assert g["naira"] == a["naira"]


@pytest.mark.asyncio
async def test_deny_all_and_revoked_keys_are_not_widened(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    path = default_keystore_path(tmp_data_dir)
    _seed(path, {"deny-all": [], "gone": ["qwen3-8b"]}, revoked={"gone"})

    LiteLLMKeyStore(path)

    a, g = _rows(path)
    assert a["deny-all"] == [] and g["deny-all"] == []
    assert g["gone"] == ["qwen3-8b"]


def test_grant_runs_once_so_a_later_revocation_sticks(tmp_data_dir):
    path = default_keystore_path(tmp_data_dir)
    tokens = _seed(path, {"mary": ["kilo-auto/free"]})
    store = LiteLLMKeyStore(path)
    assert store.lookup(tokens["mary"])["allowed_models"] == ["kilo-auto/free", EMBEDDING_ALIAS]

    store.set_models(tokens["mary"], ["kilo-auto/free"])  # operator takes embedding away
    LiteLLMKeyStore(path)  # a later open (restart, the LiteLLM hook)

    a, g = _rows(path)
    assert a["mary"] == ["kilo-auto/free"] and g["mary"] == ["kilo-auto/free"]
