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
from tinyagentos.migrations.agent_dm_owner import backfill_agent_dm_owners


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
