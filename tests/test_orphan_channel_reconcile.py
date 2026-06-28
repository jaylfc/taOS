"""Orphan agent DM channel handling.

Covers the two ways a DM channel can be left as a live orphan and the
reconciliation that sweeps existing ones:

  (a) the hard-delete branch of /destroy archives the agent's DM channel
      instead of leaving it live,
  (b) the reconciliation archives an orphan DM channel while leaving
      live-agent channels and non-agent channels untouched, and
  (c) the reconciliation is idempotent.
"""
from __future__ import annotations

import pytest

from tinyagentos.chat.orphan_reconcile import reconcile_orphan_dm_channels


async def _archived_ids(chat_channels) -> set[str]:
    archived = await chat_channels.list_channels(archived=True)
    return {c["id"] for c in archived}


@pytest.mark.asyncio
class TestDestroyArchivesChannel:
    async def test_destroy_orphan_row_archives_dm_channel(
        self, client, app, monkeypatch
    ):
        """/destroy of a never-used orphan row archives its DM channel instead
        of orphaning it (no container, no chat history, no trace)."""
        async def _no_container(name):
            return False
        monkeypatch.setattr("tinyagentos.containers.container_exists", _no_container)

        chat_channels = app.state.chat_channels
        ch = await chat_channels.create_channel(
            name="Ghost", type="dm", created_by="user",
            members=["user", "test-agent"],
        )
        # Link the channel to the live test-agent (no messages -> no history).
        app.state.config.agents[0]["chat_channel_id"] = ch["id"]

        resp = await client.delete("/api/agents/test-agent/destroy")
        assert resp.status_code == 200

        # The channel must now be archived and carry the agent linkage, never
        # left live.
        assert ch["id"] in await _archived_ids(chat_channels)
        archived = await chat_channels.get_channel(ch["id"])
        assert archived["settings"]["archived"] is True
        assert archived["settings"]["archived_agent_slug"] == "test-agent"
        assert archived["settings"].get("archived_agent_id")

        # And it must no longer appear in the live listing.
        live = await chat_channels.list_channels(archived=False)
        assert ch["id"] not in {c["id"] for c in live}


@pytest.mark.asyncio
class TestReconcileOrphanChannels:
    async def test_archives_orphans_leaves_others(self, client, app):
        """Orphan agent DMs are archived; a live-agent DM, an a2a group, a
        project channel, a human<->human DM, and a user<->human DM are all
        left untouched.

        Two orphan shapes are archived: a former agent recognised via the
        registry (plain slug, like the live-Pi 'hermes-confirm') and a
        canonical-id-shaped member with no registry/config backing at all.
        """
        config = app.state.config
        chat_channels = app.state.chat_channels

        # Orphan 1: plain-slug former agent, recognised via the registry.
        registry_ids = ["hermes-confirm-20260601-120000"]
        orphan_slug = await chat_channels.create_channel(
            name="hermes-confirm", type="dm", created_by="user",
            members=["user", "hermes-confirm"],
        )
        # Orphan 2: canonical-id-shaped member, no backing anywhere.
        orphan_canonical = await chat_channels.create_channel(
            name="Old Agent", type="dm", created_by="user",
            members=["user", "old-agent-20251201-093000"],
        )
        # Live agent DM (test-agent exists in the default config).
        live = await chat_channels.create_channel(
            name="Test Agent", type="dm", created_by="user",
            members=["user", "test-agent"],
        )
        # a2a group channel -- must never be touched.
        a2a = await chat_channels.create_channel(
            name="a2a", type="group", created_by="system",
            members=["user", "test-agent"],
            settings={"kind": "a2a"}, project_id="prj-1",
        )
        # Project channel.
        proj = await chat_channels.create_channel(
            name="prj-room", type="dm", created_by="user",
            members=["user", "someone"], project_id="prj-2",
        )
        # Human-to-human DM (no "user" sentinel).
        human = await chat_channels.create_channel(
            name="Alice & Bob", type="dm", created_by="alice",
            members=["alice", "bob"],
        )
        # user<->human DM: same ["user", X] shape as an agent DM, but X is a
        # human handle backing no known/never-known agent. Must NOT be archived.
        user_human = await chat_channels.create_channel(
            name="Bob", type="dm", created_by="user",
            members=["user", "bob"],
        )

        archived = await reconcile_orphan_dm_channels(
            config, chat_channels, registry_ids=registry_ids
        )

        archived_set = {r["channel_id"] for r in archived}
        assert archived_set == {orphan_slug["id"], orphan_canonical["id"]}

        archived_ids = await _archived_ids(chat_channels)
        assert orphan_slug["id"] in archived_ids
        assert orphan_canonical["id"] in archived_ids
        for other in (live["id"], a2a["id"], proj["id"], human["id"], user_human["id"]):
            assert other not in archived_ids

        # An orphan carries the archive linkage.
        ch = await chat_channels.get_channel(orphan_slug["id"])
        assert ch["settings"]["archived_agent_slug"] == "hermes-confirm"
        assert ch["settings"].get("archived_agent_id")

    async def test_user_human_dm_left_untouched(self, client, app):
        """A user<->human DM is never archived, even with no registry hints."""
        config = app.state.config
        chat_channels = app.state.chat_channels
        ch = await chat_channels.create_channel(
            name="Carol", type="dm", created_by="user",
            members=["user", "carol"],
        )

        archived = await reconcile_orphan_dm_channels(config, chat_channels)
        assert archived == []
        assert ch["id"] not in await _archived_ids(chat_channels)

    async def test_skips_channel_backed_by_archived_agent(self, client, app):
        """A DM channel whose agent is in the ARCHIVED list is not re-touched."""
        config = app.state.config
        chat_channels = app.state.chat_channels
        config.archived_agents = [
            {"id": "arc1", "archived_slug": "gone-agent", "original": {"name": "gone-agent"}}
        ]
        ch = await chat_channels.create_channel(
            name="Gone", type="dm", created_by="user",
            members=["user", "gone-agent"],
        )

        archived = await reconcile_orphan_dm_channels(config, chat_channels)
        assert archived == []
        assert ch["id"] not in await _archived_ids(chat_channels)

    async def test_idempotent(self, client, app):
        """Running the reconcile twice archives nothing the second time."""
        config = app.state.config
        chat_channels = app.state.chat_channels
        await chat_channels.create_channel(
            name="deerflow-test", type="dm", created_by="user",
            members=["user", "deerflow-test-20260101-000000"],
        )

        first = await reconcile_orphan_dm_channels(config, chat_channels)
        assert len(first) == 1

        second = await reconcile_orphan_dm_channels(config, chat_channels)
        assert second == []

    async def test_via_route(self, client, app):
        """POST /api/agents/reconcile-orphan-channels archives a canonical-id
        orphan and leaves a user<->human DM untouched."""
        chat_channels = app.state.chat_channels
        orphan = await chat_channels.create_channel(
            name="stray", type="dm", created_by="user",
            members=["user", "stray-agent-20260301-101010"],
        )
        human = await chat_channels.create_channel(
            name="Dave", type="dm", created_by="user",
            members=["user", "dave"],
        )

        resp = await client.post("/api/agents/reconcile-orphan-channels")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["archived"][0]["channel_id"] == orphan["id"]
        archived_ids = await _archived_ids(chat_channels)
        assert orphan["id"] in archived_ids
        assert human["id"] not in archived_ids
