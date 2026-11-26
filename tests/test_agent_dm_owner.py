"""An agent DM lists the literal "user" as a member; the agent's owner keeps reading it.

Channel reads compare the caller's user id to ``members``. Agent DMs are created
with ``["user", <agent slug>]`` so, without the owner stamp, the person who owns
the agent would be locked out of their own DM and any other member still refused.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks
from tinyagentos.agent_registry_store import AgentRegistryStore
from tinyagentos.chat.channel_store import ChatChannelStore


class _Cfg:
    def __init__(self, agents):
        self.agents = agents


async def _member(app, username):
    auth = app.state.auth
    code = auth.add_user_invite(username, "admin")
    auth.complete_invite(username, code, username.title(), "", "memberdmpass123")
    rec = auth.find_user(username)
    token = auth.create_session(user_id=rec["id"], long_lived=True)
    c = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        cookies={"taos_session": token}, event_hooks=csrf_event_hooks(),
    )
    return c, rec["id"]


@pytest.mark.asyncio
async def test_backfill_stamps_owner_on_agent_dm_and_is_idempotent(tmp_path):
    from tinyagentos.migrations.agent_dm_owner import backfill_agent_dm_owners

    registry = AgentRegistryStore(tmp_path / "r.db")
    await registry.init()
    channels = ChatChannelStore(tmp_path / "c.db")
    await channels.init()
    row = await registry.register(framework="none", display_name="Bot", user_id="alice-id")
    cfg = _Cfg([
        {"name": "bot", "registry_canonical_id": row["canonical_id"]},
        {"name": "loose"},
    ])
    dm = await channels.create_channel(name="bot", type="dm", created_by="user", members=["user", "bot"])
    loose = await channels.create_channel(name="loose", type="dm", created_by="user", members=["user", "loose"])
    group = await channels.create_channel(name="g", type="group", created_by="user", members=["user", "bot", "x"])
    try:
        assert await backfill_agent_dm_owners(cfg, registry, channels) == [dm["id"]]
        assert (await channels.get_channel(dm["id"]))["settings"]["owner_user_id"] == "alice-id"
        assert "owner_user_id" not in (await channels.get_channel(loose["id"]))["settings"]
        assert "owner_user_id" not in (await channels.get_channel(group["id"]))["settings"]
        assert await backfill_agent_dm_owners(cfg, registry, channels) == []
    finally:
        await registry.close()
        await channels.close()


@pytest.mark.asyncio
async def test_owner_reads_their_agent_dm_and_another_member_does_not(client, app):
    owner, owner_id = await _member(app, "dmowner")
    other, _ = await _member(app, "dmother")
    ch = await app.state.chat_channels.create_channel(
        name="dm", type="dm", created_by="user", members=["user", "bot"],
        settings={"owner_user_id": owner_id},
    )
    await app.state.chat_messages.send_message(ch["id"], "user", "user", "hello")
    try:
        assert (await owner.get(f"/api/chat/channels/{ch['id']}/messages")).status_code == 200
        assert (await other.get(f"/api/chat/channels/{ch['id']}/messages")).status_code == 403
        listed = (await owner.get("/api/chat/channels")).json()["channels"]
        assert ch["id"] in [c["id"] for c in listed]
        listed = (await other.get("/api/chat/channels")).json()["channels"]
        assert ch["id"] not in [c["id"] for c in listed]
        found = (await owner.get("/api/chat/search?q=hello")).json()["results"]
        assert found and found[0]["channel_id"] == ch["id"]
        assert (await other.get("/api/chat/search?q=hello")).json()["results"] == []
    finally:
        await owner.aclose()
        await other.aclose()


@pytest.mark.asyncio
async def test_deploy_stamps_the_deployer_as_dm_owner(client, app, monkeypatch):
    async def fake_deploy(req):
        return {"success": True, "name": req.name, "ip": "10.0.0.43", "llm_key": None,
                "steps": ["deployment_complete"], "container": f"taos-agent-{req.name}"}
    monkeypatch.setattr("tinyagentos.deployer.deploy_agent", fake_deploy)
    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    try:
        resp = await client.post("/api/agents/deploy", json={"name": "dmstamp", "framework": "none", "color": "#112233"})
        assert resp.status_code == 200, resp.text
        import asyncio
        await asyncio.sleep(0.3)
        agent = (await client.get("/api/agents/dmstamp")).json()
        ch = await app.state.chat_channels.get_channel(agent["chat_channel_id"])
        assert ch["settings"]["owner_user_id"] == app.state.auth.get_primary_user()["id"]
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_startup_upgrades_an_existing_install(tmp_path):
    """Old state: an agent DM listing "user", and memory filed under the shared id."""
    import yaml
    from tinyagentos.auth import AuthManager
    from tinyagentos.user_memory import UserMemoryStore

    auth = AuthManager(tmp_path)
    auth.setup_user("admin", "Admin", "", "adminpass")
    owner_id = auth.get_primary_user()["id"]

    registry = AgentRegistryStore(tmp_path / "agent_registry.db")
    await registry.init()
    # A deploy used to leave the registry owner empty.
    row = await registry.register(framework="none", display_name="Bot", user_id="")
    kept = await registry.register(framework="none", display_name="Kept", user_id="someone-else")
    await registry.close()
    channels = ChatChannelStore(tmp_path / "chat.db")
    await channels.init()
    dm = await channels.create_channel(name="bot", type="dm", created_by="user", members=["user", "bot"])
    await channels.close()
    mem = UserMemoryStore(tmp_path / "user_memory.db")
    await mem.init()
    await mem.save_chunk("user", "old shared note", "t", "notes")
    await mem.close()
    (tmp_path / "config.yaml").write_text(yaml.dump({
        "server": {"host": "0.0.0.0", "port": 6969}, "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {"name": "bot", "registry_canonical_id": row["canonical_id"]},
            {"name": "kept", "registry_canonical_id": kept["canonical_id"]},
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }))
    (tmp_path / ".setup_complete").touch()

    from tinyagentos.app import create_app

    app = create_app(data_dir=tmp_path)
    async with app.router.lifespan_context(app):
        assert (await app.state.agent_registry.get(row["canonical_id"]))["user_id"] == owner_id
        assert (await app.state.agent_registry.get(kept["canonical_id"]))["user_id"] == "someone-else"
        ch = await app.state.chat_channels.get_channel(dm["id"])
        assert ch["settings"]["owner_user_id"] == owner_id
        assert len(await app.state.user_memory.browse(owner_id)) == 1
        assert await app.state.user_memory.browse("user") == []
