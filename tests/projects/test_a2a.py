from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio

from tinyagentos.chat.channel_store import ChatChannelStore
from tinyagentos.projects.a2a import (
    A2A_KIND,
    A2A_NAME,
    A2A_TYPE,
    backfill_all,
    ensure_a2a_channel,
)
from tinyagentos.projects.project_store import ProjectStore


def _config(*agents):
    """Build a minimal config-like object with the given agent dicts."""
    return SimpleNamespace(agents=list(agents))


def _agent(name: str, agent_id: str) -> dict:
    return {"id": agent_id, "name": name, "status": "running"}


@pytest_asyncio.fixture
async def stores(tmp_path):
    project_store = ProjectStore(tmp_path / "projects.db")
    await project_store.init()
    channel_store = ChatChannelStore(tmp_path / "chat.db")
    await channel_store.init()
    yield project_store, channel_store
    await channel_store.close()
    await project_store.close()


@pytest.mark.asyncio
async def test_ensure_creates_channel_when_missing(stores):
    project_store, channel_store = stores
    p = await project_store.create_project(name="Acme", slug="acme", created_by="u1")

    ch = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert ch["name"] == A2A_NAME
    assert ch["type"] == A2A_TYPE
    assert ch["project_id"] == p["id"]
    assert ch["settings"].get("kind") == A2A_KIND
    assert ch["members"] == []


@pytest.mark.asyncio
async def test_ensure_is_idempotent(stores):
    project_store, channel_store = stores
    p = await project_store.create_project(name="Acme", slug="acme2", created_by="u1")

    ch1 = await ensure_a2a_channel(channel_store, project_store, p["id"])
    ch2 = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert ch1["id"] == ch2["id"]
    all_channels = await channel_store.list_channels(project_id=p["id"])
    a2a = [c for c in all_channels if (c.get("settings") or {}).get("kind") == "a2a"]
    assert len(a2a) == 1


@pytest.mark.asyncio
async def test_ensure_syncs_members_added_after_creation(stores):
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="sync-add", created_by="u1")
    await ensure_a2a_channel(channel_store, project_store, p["id"])

    await project_store.add_member(p["id"], "agentA", member_kind="native")
    await project_store.add_member(p["id"], "agentB", member_kind="native")
    ch = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert sorted(ch["members"]) == ["agentA", "agentB"]


@pytest.mark.asyncio
async def test_ensure_syncs_members_on_remove(stores):
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="sync-rm", created_by="u1")
    await project_store.add_member(p["id"], "agentA", member_kind="native")
    await project_store.add_member(p["id"], "agentB", member_kind="native")
    await ensure_a2a_channel(channel_store, project_store, p["id"])

    await project_store.remove_member(p["id"], "agentA")
    ch = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert ch["members"] == ["agentB"]


@pytest.mark.asyncio
async def test_ensure_no_op_when_members_match(stores):
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="sync-same", created_by="u1")
    await project_store.add_member(p["id"], "agentA", member_kind="native")
    ch1 = await ensure_a2a_channel(channel_store, project_store, p["id"])
    ch2 = await ensure_a2a_channel(channel_store, project_store, p["id"])
    assert ch1["members"] == ch2["members"] == ["agentA"]


@pytest.mark.asyncio
async def test_backfill_creates_channels_for_all_active_projects(stores):
    project_store, channel_store = stores
    p1 = await project_store.create_project(name="P1", slug="bf1", created_by="u1")
    p2 = await project_store.create_project(name="P2", slug="bf2", created_by="u1")
    p3 = await project_store.create_project(name="P3", slug="bf3", created_by="u1")
    await project_store.set_status(p3["id"], "archived")

    count = await backfill_all(channel_store, project_store)

    assert count == 2
    assert await _has_a2a(channel_store, p1["id"])
    assert await _has_a2a(channel_store, p2["id"])
    assert not await _has_a2a(channel_store, p3["id"])


async def _has_a2a(channel_store, project_id: str) -> bool:
    chans = await channel_store.list_channels(project_id=project_id)
    return any((c.get("settings") or {}).get("kind") == "a2a" for c in chans)


@pytest.mark.asyncio
async def test_backfill_is_idempotent(stores):
    """Calling backfill twice is a no-op the second time."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="bf-idem", created_by="u1")

    n1 = await backfill_all(channel_store, project_store)
    n2 = await backfill_all(channel_store, project_store)

    assert n1 == 1 and n2 == 1
    chans = await channel_store.list_channels(project_id=p["id"])
    a2a = [c for c in chans if (c.get("settings") or {}).get("kind") == "a2a"]
    assert len(a2a) == 1


@pytest.mark.asyncio
async def test_ensure_archives_duplicate_a2a_channels(stores):
    """Defensive: if duplicate A2A channels exist (race / migration / manual
    insert), the oldest is canonical and the rest are archived."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="dup", created_by="u1")

    canonical = await ensure_a2a_channel(channel_store, project_store, p["id"])
    duplicate = await channel_store.create_channel(
        name=A2A_NAME,
        type=A2A_TYPE,
        created_by="u1",
        members=[],
        settings={"kind": A2A_KIND},
        project_id=p["id"],
    )

    result = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert result["id"] == canonical["id"]
    dup_after = await channel_store.get_channel(duplicate["id"])
    assert (dup_after.get("settings") or {}).get("archived") is True
    active = await channel_store.list_channels(project_id=p["id"], archived=False)
    a2a_active = [c for c in active if (c.get("settings") or {}).get("kind") == "a2a"]
    assert len(a2a_active) == 1
    assert a2a_active[0]["id"] == canonical["id"]


@pytest.mark.asyncio
async def test_ensure_provisions_new_when_only_archived_exists(stores):
    """If the only A2A channel for a project is archived, ensure must
    provision a fresh active one rather than re-electing the archived row."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="archived-only", created_by="u1")

    first = await ensure_a2a_channel(channel_store, project_store, p["id"])
    await channel_store.set_settings(first["id"], {"archived": True})

    fresh = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert fresh["id"] != first["id"]
    assert (fresh.get("settings") or {}).get("archived") is not True
    archived = await channel_store.get_channel(first["id"])
    assert (archived.get("settings") or {}).get("archived") is True


# ---------------------------------------------------------------------------
# ID → name resolution tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_populates_members_as_names_when_config_provided(stores):
    """ensure_a2a_channel stores agent names, not hex IDs, in channel members."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="id-to-name", created_by="u1")

    john_id = "91a640130122"
    tom_id = "ec4ac43c99c1"
    config = _config(_agent("john", john_id), _agent("tom", tom_id))

    # project_members stores hex IDs (the real runtime behaviour)
    await project_store.add_member(p["id"], john_id, member_kind="native")
    await project_store.add_member(p["id"], tom_id, member_kind="native")

    ch = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)

    assert sorted(ch["members"]) == ["john", "tom"]


@pytest.mark.asyncio
async def test_ensure_backfill_converts_hex_ids_to_names(stores):
    """backfill_all with config converts an existing channel whose members are
    stored as hex IDs into one whose members are stored as names. Member count
    is preserved; only the identifier form changes."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="backfill-names", created_by="u1")

    john_id = "aabbcc001122"
    tom_id = "ddeeff334455"

    # Create channel with hex IDs as members (simulates pre-fix state)
    await channel_store.create_channel(
        name=A2A_NAME,
        type=A2A_TYPE,
        created_by="u1",
        members=[john_id, tom_id],
        settings={"kind": A2A_KIND},
        project_id=p["id"],
    )
    await project_store.add_member(p["id"], john_id, member_kind="native")
    await project_store.add_member(p["id"], tom_id, member_kind="native")

    config = _config(_agent("john", john_id), _agent("tom", tom_id))
    await backfill_all(channel_store, project_store, config=config)

    chans = await channel_store.list_channels(project_id=p["id"])
    a2a = next(c for c in chans if (c.get("settings") or {}).get("kind") == "a2a")
    assert sorted(a2a["members"]) == ["john", "tom"]
    assert len(a2a["members"]) == 2


@pytest.mark.asyncio
async def test_ensure_drops_unknown_member_ids_silently(stores):
    """Members whose IDs don't resolve in config (deleted agents) are dropped
    without raising an exception."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="unknown-drop", created_by="u1")

    known_id = "111111111111"
    ghost_id = "deadbeefcafe"  # not in config
    config = _config(_agent("alice", known_id))

    await project_store.add_member(p["id"], known_id, member_kind="native")
    await project_store.add_member(p["id"], ghost_id, member_kind="native")

    ch = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)

    assert ch["members"] == ["alice"]


@pytest.mark.asyncio
async def test_ensure_diff_computed_in_name_space(stores):
    """When a project gains a new member, the diff is computed in name-space so
    there is no spurious add/remove of the hex ID that was previously in the
    channel."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="diff-namespace", created_by="u1")

    john_id = "aaaa00000001"
    don_id = "bbbb00000002"
    config = _config(_agent("john", john_id), _agent("don", don_id))

    # First call: john only
    await project_store.add_member(p["id"], john_id, member_kind="native")
    ch = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)
    assert ch["members"] == ["john"]

    # Second call: add don
    await project_store.add_member(p["id"], don_id, member_kind="native")
    ch2 = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)
    assert sorted(ch2["members"]) == ["don", "john"]


@pytest.mark.asyncio
async def test_ensure_name_collision_both_kept(stores):
    """Two agents with the same name (possible if config has duplicates) — both
    resolve to the same string so the set deduplicates to one entry. Documents
    the expected behaviour: the name appears once in members."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="collision", created_by="u1")

    id1 = "cccc00000001"
    id2 = "cccc00000002"
    # Both agents have name "twin" — unusual config but must not crash
    config = _config(_agent("twin", id1), _agent("twin", id2))

    await project_store.add_member(p["id"], id1, member_kind="native")
    await project_store.add_member(p["id"], id2, member_kind="native")

    ch = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)

    # set deduplication: only one "twin" in members
    assert ch["members"] == ["twin"]


# ---------------------------------------------------------------------------
# End-to-end mention routing through a2a channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mention_routes_to_agent_in_a2a_channel():
    """@<name> in a project a2a channel routes to the matching agent.

    This is the primary regression test for the bug: before the fix, channel
    members were hex IDs so parse_mentions found no match and the router fell
    through to quiet mode (no dispatch). With the fix, members are names so
    the mention resolves and the agent is enqueued.
    """
    from unittest.mock import AsyncMock, MagicMock
    from tinyagentos.agent_chat_router import AgentChatRouter
    from tinyagentos.chat.group_policy import GroupPolicy

    class _FakeBridge:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []
        async def enqueue_user_message(self, slug: str, msg: dict) -> None:
            self.calls.append((slug, msg))

    bridge = _FakeBridge()
    state = MagicMock()
    state.config = MagicMock()
    state.config.agents = [{"name": "john", "id": "91a640130122", "status": "running"}]
    state.chat_messages = MagicMock()
    state.chat_messages.send_message = AsyncMock(return_value={
        "id": "m1", "channel_id": "c1",
        "author_id": "john", "author_type": "agent",
        "content": "", "created_at": 1.0,
    })
    state.chat_messages.get_messages = AsyncMock(return_value=[])
    state.chat_channels = MagicMock()
    state.chat_channels.update_last_message_at = AsyncMock()
    state.chat_hub = MagicMock()
    state.chat_hub.broadcast = AsyncMock()
    state.chat_hub.next_seq = MagicMock(return_value=1)
    state.bridge_sessions = bridge
    state.group_policy = GroupPolicy()

    router = AgentChatRouter(state)

    # A2A channel with name-resolved members (post-fix)
    channel = {
        "id": "c-a2a",
        "type": "group",
        "members": ["user", "john"],  # names, not hex IDs
        "settings": {"kind": "a2a", "response_mode": "quiet", "max_hops": 3,
                     "cooldown_seconds": 5, "rate_cap_per_minute": 20, "muted": []},
    }
    message = {
        "id": "m1", "channel_id": "c-a2a", "author_id": "user",
        "author_type": "user", "content": "@john draft chapter 1",
        "metadata": {"hops_since_user": 0},
    }
    await router._route(message, channel)

    assert len(bridge.calls) == 1
    slug, enqueued = bridge.calls[0]
    assert slug == "john"
    assert enqueued["force_respond"] is True
