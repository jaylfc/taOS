"""A native-agent token superseded by rotation no longer acts as the owner.

Rotation is the kill switch for a registry token. The middleware used to set
the native agent's owner as the caller without checking the rotation cutoff, so
a rotated-out token still drove the owner's desktop and the skill-exec routes.
"""
from __future__ import annotations

import time

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    mint_registry_token,
)
from tinyagentos.app import create_app
from tinyagentos.native_agent_identity import ensure_native_agent_identity


@pytest_asyncio.fixture
async def env(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text(yaml.dump({
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [], "qmd": {"url": "http://localhost:7832"},
        "agents": [], "metrics": {"poll_interval": 30, "retention_days": 30},
    }))
    (data / ".setup_complete").touch()
    app = create_app(data_dir=data)
    auth = app.state.auth
    if not auth.is_configured():
        auth.setup_user("admin", "Admin", "", "adminpass")
    owner = auth.find_user("admin")["id"]
    registry = AgentRegistryStore(tmp_path / "reg.db")
    await registry.init()
    grants = AgentGrantsStore(tmp_path / "grants.db")
    await grants.init()
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys")
    app.state.agent_registry = registry
    app.state.agent_grants = grants
    app.state.agent_registry_keypair = (priv, pub)
    native = await ensure_native_agent_identity(
        registry=registry, grants=grants, data_dir=data, signing_key_pem=priv, user_id=owner,
    )
    emitted: list[str] = []
    broker = app.state.desktop_command_broker
    real_emit = broker.emit

    async def spy(user_id, command):
        emitted.append(user_id)
        return await real_emit(user_id, command)

    broker.emit = spy
    mint = lambda: mint_registry_token(  # noqa: E731
        native["canonical_id"], priv, user_id=owner, framework="taos-native")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.emitted, client.owner = emitted, owner
        client.registry, client.cid, client.mint = registry, native["canonical_id"], mint
        yield client
    await registry.close()
    await grants.close()


BODY = {"kind": "open-app", "payload": {"app": "files"}}


@pytest.mark.asyncio
async def test_current_native_token_acts_as_the_owner(env):
    resp = await env.post(
        "/api/desktop/command", json=BODY, headers={"Authorization": f"Bearer {env.mint()}"})
    assert resp.status_code == 200, resp.text
    assert env.emitted == [env.owner]


@pytest.mark.asyncio
async def test_rotated_out_native_token_no_longer_acts_as_the_owner(env):
    old = env.mint()
    await env.registry.bump_token_min_iat(env.cid, int(time.time()) + 3600)
    resp = await env.post(
        "/api/desktop/command", json=BODY, headers={"Authorization": f"Bearer {old}"})
    assert env.owner not in env.emitted, "a superseded token drove the owner's desktop"


@pytest.mark.asyncio
async def test_rotated_out_native_token_is_refused_on_skill_exec(env):
    url = "/api/skill-exec/no_such_skill/call"
    body = {"args": {"agent_name": "x"}}
    fresh = await env.post(url, json=body, headers={"Authorization": f"Bearer {env.mint()}"})
    # The current token passes the gate (the skill itself does not exist).
    assert fresh.status_code != 403, fresh.text
    old = env.mint()
    await env.registry.bump_token_min_iat(env.cid, int(time.time()) + 3600)
    stale = await env.post(url, json=body, headers={"Authorization": f"Bearer {old}"})
    assert stale.status_code == 403, stale.text
