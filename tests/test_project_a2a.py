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
async def test_build_agent_lookups_with_config(stores):
    """_build_agent_lookups builds id->agent and name->agent dicts from config."""
    from tinyagentos.projects.a2a import _build_agent_lookups

    agent1 = _agent("alice", "id1")
    agent2 = _agent("bob", "id2")
    config = _config(agent1, agent2)

    by_id, by_name = _build_agent_lookups(config)

    assert by_id["id1"]["name"] == "alice"
    assert by_id["id2"]["name"] == "bob"
    assert by_name["alice"]["id"] == "id1"
    assert by_name["bob"]["id"] == "id2"


@pytest.mark.asyncio
async def test_build_agent_lookups_with_none_config(stores):
    """_build_agent_lookups returns (None, None) when config is None."""
    from tinyagentos.projects.a2a import _build_agent_lookups

    by_id, by_name = _build_agent_lookups(None)

    assert by_id is None
    assert by_name is None


@pytest.mark.asyncio
async def test_build_agent_lookups_with_empty_agents(stores):
    """_build_agent_lookups handles empty agents list."""
    from tinyagentos.projects.a2a import _build_agent_lookups

    config = _config()

    by_id, by_name = _build_agent_lookups(config)

    assert by_id == {}
    assert by_name == {}


@pytest.mark.asyncio
async def test_resolve_member_names_with_config(stores):
    """_resolve_member_names resolves hex IDs to agent names using config."""
    from tinyagentos.projects.a2a import _resolve_member_names

    john_id = "91a640130122"
    tom_id = "ec4ac43c99c1"
    config = _config(_agent("john", john_id), _agent("tom", tom_id))

    members = [{"member_id": john_id}, {"member_id": tom_id}]
    resolved = _resolve_member_names(members, config)

    assert resolved == {"john", "tom"}


@pytest.mark.asyncio
async def test_resolve_member_names_with_deleted_member(stores):
    """_resolve_member_names drops members not found in config."""
    from tinyagentos.projects.a2a import _resolve_member_names

    known_id = "111111111111"
    ghost_id = "deadbeefcafe"
    config = _config(_agent("alice", known_id))

    members = [
        {"member_id": known_id},
        {"member_id": ghost_id},
    ]
    resolved = _resolve_member_names(members, config)

    assert resolved == {"alice"}


@pytest.mark.asyncio
async def test_resolve_member_names_without_config(stores):
    """_resolve_member_names returns member_ids as-is when config is None."""
    from tinyagentos.projects.a2a import _resolve_member_names

    member_ids = ["hex1", "hex2", "hex3"]
    members = [{"member_id": mid} for mid in member_ids]

    resolved = _resolve_member_names(members, None)

    assert resolved == set(member_ids)


@pytest.mark.asyncio
async def test_resolve_lead_names_with_lead_in_config(stores):
    """_resolve_lead_names returns the resolved agent name for the lead."""
    from tinyagentos.projects.a2a import _resolve_lead_names

    lead_id = "aaaaaaaaaaaa"
    config = _config(_agent("lead_agent", lead_id))
    project = {"lead_member_id": lead_id}

    resolved = _resolve_lead_names(project, config)

    assert resolved == ["lead_agent"]


@pytest.mark.asyncio
async def test_resolve_lead_names_without_lead(stores):
    """_resolve_lead_names returns empty list when no lead is set."""
    from tinyagentos.projects.a2a import _resolve_lead_names

    config = _config(_agent("alice", "id1"))
    project = {}

    resolved = _resolve_lead_names(project, config)

    assert resolved == []


@pytest.mark.asyncio
async def test_resolve_lead_names_lead_not_in_config(stores):
    """_resolve_lead_names returns empty list when lead ID is not in config."""
    from tinyagentos.projects.a2a import _resolve_lead_names

    lead_id = "nonexistent"
    config = _config(_agent("alice", "id1"))
    project = {"lead_member_id": lead_id}

    resolved = _resolve_lead_names(project, config)

    assert resolved == []


@pytest.mark.asyncio
async def test_resolve_lead_names_without_config(stores):
    """_resolve_lead_names returns lead_member_id as-is when config is None."""
    from tinyagentos.projects.a2a import _resolve_lead_names

    lead_id = "legacy_lead_id"
    project = {"lead_member_id": lead_id}

    resolved = _resolve_lead_names(project, None)

    assert resolved == [lead_id]


@pytest.mark.asyncio
async def test_find_a2a_channels_basic(stores):
    """_find_a2a_channels returns channels matching all four criteria."""
    from tinyagentos.projects.a2a import _find_a2a_channels

    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

    await channel_store.create_channel(
        name=A2A_NAME,
        type=A2A_TYPE,
        created_by="u1",
        settings={"kind": A2A_KIND},
        project_id=p["id"],
    )

    channels = await _find_a2a_channels(channel_store, p["id"])

    assert len(channels) == 1
    assert channels[0]["name"] == A2A_NAME
    assert channels[0]["type"] == A2A_TYPE
    assert (channels[0]["settings"] or {}).get("kind") == A2A_KIND


@pytest.mark.asyncio
async def test_find_a2a_channels_filters_archived(stores):
    """_find_a2a_channels excludes archived channels."""
    from tinyagentos.projects.a2a import _find_a2a_channels

    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

    active = await channel_store.create_channel(
        name=A2A_NAME,
        type=A2A_TYPE,
        created_by="u1",
        settings={"kind": A2A_KIND},
        project_id=p["id"],
    )

    await channel_store.set_settings(active["id"], {"archived": True})

    channels = await _find_a2a_channels(channel_store, p["id"])

    assert len(channels) == 0


@pytest.mark.asyncio
async def test_find_a2a_channels_wrong_kind(stores):
    """_find_a2a_channels filters by settings.kind."""
    from tinyagentos.projects.a2a import _find_a2a_channels

    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

    await channel_store.create_channel(
        name=A2A_NAME,
        type=A2A_TYPE,
        created_by="u1",
        settings={"kind": "not-a2a"},
        project_id=p["id"],
    )

    channels = await _find_a2a_channels(channel_store, p["id"])

    assert len(channels) == 0


@pytest.mark.asyncio
async def test_ensure_a2a_channel_creates_new(stores):
    """ensure_a2a_channel creates A2A channel when none exists."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

    ch = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert ch["name"] == A2A_NAME
    assert ch["type"] == A2A_TYPE
    assert ch["project_id"] == p["id"]
    assert (ch["settings"] or {}).get("kind") == A2A_KIND
    assert ch["members"] == []


@pytest.mark.asyncio
async def test_ensure_a2a_channel_is_idempotent(stores):
    """ensure_a2a_channel returns same channel on second call."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

    ch1 = await ensure_a2a_channel(channel_store, project_store, p["id"])
    ch2 = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert ch1["id"] == ch2["id"]
    channels = await channel_store.list_channels(project_id=p["id"])
    a2a = [c for c in channels if (c.get("settings") or {}).get("kind") == A2A_KIND]
    assert len(a2a) == 1


@pytest.mark.asyncio
async def test_ensure_a2a_channel_syncs_members_added(stores):
    """ensure_a2a_channel syncs added members."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")
    await ensure_a2a_channel(channel_store, project_store, p["id"])

    await project_store.add_member(p["id"], "agentA", member_kind="native")
    await project_store.add_member(p["id"], "agentB", member_kind="native")
    ch = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert sorted(ch["members"]) == ["agentA", "agentB"]


@pytest.mark.asyncio
async def test_ensure_a2a_channel_syncs_members_removed(stores):
    """ensure_a2a_channel syncs removed members."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")
    await project_store.add_member(p["id"], "agentA", member_kind="native")
    await project_store.add_member(p["id"], "agentB", member_kind="native")
    await ensure_a2a_channel(channel_store, project_store, p["id"])

    await project_store.remove_member(p["id"], "agentA")
    ch = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert ch["members"] == ["agentB"]


@pytest.mark.asyncio
async def test_ensure_a2a_channel_no_op_when_members_match(stores):
    """ensure_a2a_channel is a no-op when members already match."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")
    await project_store.add_member(p["id"], "agentA", member_kind="native")
    ch1 = await ensure_a2a_channel(channel_store, project_store, p["id"])
    ch2 = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert ch1["members"] == ch2["members"] == ["agentA"]


@pytest.mark.asyncio
async def test_ensure_a2a_channel_with_config_converts_member_ids_to_names(stores):
    """ensure_a2a_channel stores agent names when config is provided."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

    john_id = "91a640130122"
    tom_id = "ec4ac43c99c1"
    config = _config(_agent("john", john_id), _agent("tom", tom_id))

    await project_store.add_member(p["id"], john_id, member_kind="native")
    await project_store.add_member(p["id"], tom_id, member_kind="native")

    ch = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)

    assert sorted(ch["members"]) == ["john", "tom"]


@pytest.mark.asyncio
async def test_ensure_a2a_channel_archives_duplicate(stores):
    """ensure_a2a_channel archives duplicate A2A channels."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

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
    a2a_active = [c for c in active if (c.get("settings") or {}).get("kind") == A2A_KIND]
    assert len(a2a_active) == 1
    assert a2a_active[0]["id"] == canonical["id"]


@pytest.mark.asyncio
async def test_ensure_a2a_channel_provisions_fresh_when_only_archived_exists(stores):
    """ensure_a2a_channel provisions fresh channel when only archived exists."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

    first = await ensure_a2a_channel(channel_store, project_store, p["id"])
    await channel_store.set_settings(first["id"], {"archived": True})

    fresh = await ensure_a2a_channel(channel_store, project_store, p["id"])

    assert fresh["id"] != first["id"]
    assert (fresh.get("settings") or {}).get("archived") is not True
    archived = await channel_store.get_channel(first["id"])
    assert (archived.get("settings") or {}).get("archived") is True


@pytest.mark.asyncio
async def test_ensure_a2a_channel_syncs_leads_from_lead_member_id(stores):
    """ensure_a2a_channel syncs leads from lead_member_id."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

    coord_id = "aaaaaaaaaaaa"
    worker_id = "bbbbbbbbbbbb"
    config = _config(_agent("coord", coord_id), _agent("worker", worker_id))

    await project_store.add_member(p["id"], coord_id, member_kind="native")
    await project_store.add_member(p["id"], worker_id, member_kind="native")
    await project_store.set_lead(p["id"], coord_id)

    ch = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)

    assert ch["settings"]["leads"] == ["coord"]
    assert sorted(ch["members"]) == ["coord", "worker"]


@pytest.mark.asyncio
async def test_ensure_a2a_channel_leads_empty_when_no_lead(stores):
    """ensure_a2a_channel sets empty leads when no lead is set."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

    agent_id = "cccccccccccc"
    config = _config(_agent("alice", agent_id))
    await project_store.add_member(p["id"], agent_id, member_kind="native")

    ch = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)

    assert ch["settings"]["leads"] == []


@pytest.mark.asyncio
async def test_ensure_a2a_channel_updates_leads_on_subsequent_call(stores):
    """ensure_a2a_channel updates leads when lead_member_id changes."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

    coord_id = "dddddddddddd"
    config = _config(_agent("coord", coord_id))
    await project_store.add_member(p["id"], coord_id, member_kind="native")

    ch1 = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)
    assert ch1["settings"]["leads"] == []

    await project_store.set_lead(p["id"], coord_id)
    ch2 = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)
    assert ch2["settings"]["leads"] == ["coord"]

    await project_store.set_lead(p["id"], None)
    ch3 = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)
    assert ch3["settings"]["leads"] == []


@pytest.mark.asyncio
async def test_ensure_a2a_channel_lead_removed_drops_from_members(stores):
    """ensure_a2a_channel drops lead from members when removed."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="test", created_by="u1")

    coord_id = "eeeeeeeeeeee"
    worker_id = "ffffffffffff"
    config = _config(_agent("coord", coord_id), _agent("worker", worker_id))

    await project_store.add_member(p["id"], coord_id, member_kind="native")
    await project_store.add_member(p["id"], worker_id, member_kind="native")
    await project_store.set_lead(p["id"], coord_id)

    ch1 = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)
    assert ch1["settings"]["leads"] == ["coord"]

    await project_store.remove_member(p["id"], coord_id)
    ch2 = await ensure_a2a_channel(channel_store, project_store, p["id"], config=config)
    assert ch2["settings"]["leads"] == []
    assert ch2["members"] == ["worker"]


@pytest.mark.asyncio
async def test_backfill_all_creates_channels_for_all_active_projects(stores):
    """backfill_all creates channels for all active projects."""
    project_store, channel_store = stores

    p1 = await project_store.create_project(name="P1", slug="bf1", created_by="u1")
    p2 = await project_store.create_project(name="P2", slug="bf2", created_by="u1")
    p3 = await project_store.create_project(name="P3", slug="bf3", created_by="u1")
    await project_store.set_status(p3["id"], "archived")

    count = await backfill_all(channel_store, project_store)

    assert count == 2
    chans1 = await channel_store.list_channels(project_id=p1["id"])
    assert any((c.get("settings") or {}).get("kind") == A2A_KIND for c in chans1)
    chans2 = await channel_store.list_channels(project_id=p2["id"])
    assert any((c.get("settings") or {}).get("kind") == A2A_KIND for c in chans2)
    chans3 = await channel_store.list_channels(project_id=p3["id"])
    assert not any((c.get("settings") or {}).get("kind") == A2A_KIND for c in chans3)


@pytest.mark.asyncio
async def test_backfill_all_is_idempotent(stores):
    """backfill_all is idempotent - calling twice is a no-op."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="bf-idem", created_by="u1")

    n1 = await backfill_all(channel_store, project_store)
    n2 = await backfill_all(channel_store, project_store)

    assert n1 == 1 and n2 == 1
    chans = await channel_store.list_channels(project_id=p["id"])
    a2a = [c for c in chans if (c.get("settings") or {}).get("kind") == A2A_KIND]
    assert len(a2a) == 1


@pytest.mark.asyncio
async def test_backfill_all_with_config_converts_existing_members(stores):
    """backfill_all with config converts existing hex ID members to names."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="bf-names", created_by="u1")

    john_id = "aabbcc001122"
    tom_id = "ddeeff334455"

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
    a2a = next(c for c in chans if (c.get("settings") or {}).get("kind") == A2A_KIND)
    assert sorted(a2a["members"]) == ["john", "tom"]
    assert len(a2a["members"]) == 2


@pytest.mark.asyncio
async def test_backfill_all_with_config_skips_deleted_members(stores):
    """backfill_all with config skips members not found in config."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="bf-skip", created_by="u1")

    known_id = "111111111111"
    ghost_id = "deadbeefcafe"

    await channel_store.create_channel(
        name=A2A_NAME,
        type=A2A_TYPE,
        created_by="u1",
        members=[known_id, ghost_id],
        settings={"kind": A2A_KIND},
        project_id=p["id"],
    )
    await project_store.add_member(p["id"], known_id, member_kind="native")
    await project_store.add_member(p["id"], ghost_id, member_kind="native")

    config = _config(_agent("alice", known_id))
    await backfill_all(channel_store, project_store, config=config)

    chans = await channel_store.list_channels(project_id=p["id"])
    a2a = next(c for c in chans if (c.get("settings") or {}).get("kind") == A2A_KIND)
    assert a2a["members"] == ["alice"]
    assert len(a2a["members"]) == 1


@pytest.mark.asyncio
async def test_backfill_all_with_config_updates_leads(stores):
    """backfill_all with config updates leads from project lead_member_id."""
    project_store, channel_store = stores
    p = await project_store.create_project(name="P", slug="bf-leads", created_by="u1")

    coord_id = "iiiijjjjjjjj"
    worker_id = "kkklllmlllll"

    await project_store.add_member(p["id"], coord_id, member_kind="native")
    await project_store.add_member(p["id"], worker_id, member_kind="native")
    await project_store.set_lead(p["id"], coord_id)

    config = _config(_agent("coord", coord_id), _agent("worker", worker_id))
    await backfill_all(channel_store, project_store, config=config)

    chans = await channel_store.list_channels(project_id=p["id"])
    a2a = next(c for c in chans if (c.get("settings") or {}).get("kind") == A2A_KIND)
    assert a2a["settings"]["leads"] == ["coord"]