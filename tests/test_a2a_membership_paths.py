"""taOS #2115: every membership path must ensure the project's A2A channel.

PR #3788 made every project *creation* path create the channel. These tests
pin the membership paths that could still land a member on a project with
no channel — a project whose creation-time ensure failed (it is
best-effort), or one created before #3788:

- approving an auth request that grants ONLY canvas scopes (no
  project_tasks): the member row was written but ensure_a2a_channel was
  only called for project_tasks grants.
- assigning an existing agent (Agents app) with ONLY canvas scopes: same
  gap in add_agent_to_project.
- accepting a cross-instance collab invite (peer inbox): the member was
  added with no ensure call at all.

Each test starts from a project created directly in the store, which never
creates a channel, so a missing ensure on the membership path is
observable.

The member assertions also pin the resolution the channel sync applies
(CodeRabbit review on PR #4373): a registry-backed agent absent from
``config.agents`` joins under its registry handle, and an accepted human
collaborator stays in the channel under their contact id even with a real
config — neither may be silently dropped from ``channel.members``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


async def _a2a_channels(channel_store, project_id: str) -> list[dict]:
    channels = await channel_store.list_channels(
        project_id=project_id, archived=False
    )
    return [
        c for c in channels if (c.get("settings") or {}).get("kind") == "a2a"
    ]


@pytest.mark.asyncio
async def test_approve_canvas_only_grant_creates_a2a_channel(
    client, monkeypatch, tmp_path
):
    """A canvas-only approval adds the member AND creates the A2A channel."""
    from tinyagentos.agent_grants_store import AgentGrantsStore
    from tinyagentos.agent_registry_store import (
        AgentRegistryStore,
        load_or_create_signing_keypair,
    )
    from tinyagentos.auth_requests_store import AuthRequestsStore
    from tinyagentos.projects.project_store import ProjectStore

    registry = AgentRegistryStore(tmp_path / "reg-canvas-a2a.db")
    await registry.init()
    auth_store = AuthRequestsStore(tmp_path / "auth-canvas-a2a.db")
    await auth_store.init()
    grants = AgentGrantsStore(tmp_path / "grants-canvas-a2a.db")
    await grants.init()
    pstore = ProjectStore(tmp_path / "projects-canvas-a2a.db")
    await pstore.init()
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys-canvas-a2a")

    try:
        # Store-level creation never creates a channel.
        project = await pstore.create_project(
            name="Canvas A2A", slug="canvas-a2a", created_by="u"
        )
        pid = project["id"]
        channel_store = client._transport.app.state.chat_channels
        assert await _a2a_channels(channel_store, pid) == []

        record = await auth_store.create(
            identity_claim="canvas-bot",
            framework="canvas-cli",
            requested_scopes=["canvas_write"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=pid,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["canvas_write"], "project_id": pid},
        )
        assert resp.status_code == 200, resp.text

        members = await pstore.list_members(pid)
        assert len(members) == 1

        channels = await _a2a_channels(channel_store, pid)
        assert len(channels) == 1, (
            "canvas-only approval must create the project's A2A channel"
        )
        assert channels[0]["name"] == "a2a"
        assert channels[0]["type"] == "group"
        # The approved agent is registry-backed, not in config.agents: it
        # joins the channel under its registry handle, not its canonical id.
        assert "canvas-bot" in (channels[0].get("members") or [])
    finally:
        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()


@pytest.mark.asyncio
async def test_assign_canvas_only_creates_channel_and_repeat_does_not_duplicate(
    client, monkeypatch, tmp_path
):
    """Assigning an agent with only canvas scopes creates the channel; a
    second membership event on the same project must not duplicate it."""
    from tinyagentos.agent_grants_store import AgentGrantsStore
    from tinyagentos.agent_registry_store import (
        AgentRegistryStore,
        load_or_create_signing_keypair,
    )
    from tinyagentos.projects.project_store import ProjectStore

    registry = AgentRegistryStore(tmp_path / "reg-assign-a2a.db")
    await registry.init()
    grants = AgentGrantsStore(tmp_path / "grants-assign-a2a.db")
    await grants.init()
    pstore = ProjectStore(tmp_path / "projects-assign-a2a.db")
    await pstore.init()
    load_or_create_signing_keypair(tmp_path / "keys-assign-a2a")

    try:
        reg = await registry.register(
            framework="openclaw", display_name="a2a-dev",
            user_id="u", origin="external-selfjoin", handle="a2a-dev",
        )
        await registry.set_status(reg["canonical_id"], "active")
        cid = reg["canonical_id"]

        project = await pstore.create_project(
            name="P", slug="assign-a2a", created_by="u"
        )
        pid = project["id"]
        channel_store = client._transport.app.state.chat_channels
        assert await _a2a_channels(channel_store, pid) == []

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        resp = await client.post(
            f"/api/projects/{pid}/members/assign-agent",
            json={"canonical_id": cid, "scopes": ["canvas_read"]},
        )
        assert resp.status_code == 200, resp.text

        members = await pstore.list_members(pid)
        assert any(m["member_id"] == cid for m in members)

        channels = await _a2a_channels(channel_store, pid)
        assert len(channels) == 1, (
            "canvas-only assign must create the project's A2A channel"
        )
        # The assigned agent is registry-backed, not in config.agents: it
        # joins the channel under its registry handle.
        assert "a2a-dev" in (channels[0].get("members") or [])

        # A repeat membership event (the previously-working project_tasks path)
        # must not duplicate the channel.
        resp = await client.post(
            f"/api/projects/{pid}/members/assign-agent",
            json={"canonical_id": cid, "scopes": ["project_tasks"]},
        )
        assert resp.status_code == 200, resp.text
        channels = await _a2a_channels(channel_store, pid)
        assert len(channels) == 1
        assert "a2a-dev" in (channels[0].get("members") or [])
    finally:
        await registry.close()
        await grants.close()
        await pstore.close()


@pytest.mark.asyncio
async def test_collab_invite_accept_creates_a2a_channel(tmp_path):
    """Accepting a collab invite via the peer inbox adds the human member
    AND creates the project's A2A channel; a second accept cycle for the
    same contact must not duplicate it."""
    from tinyagentos.chat.channel_store import ChatChannelStore
    from tinyagentos.projects.invite_store import ProjectInviteStore
    from tinyagentos.projects.project_store import ProjectStore
    from tinyagentos.routes.peer import _handle_collab_response

    pstore = ProjectStore(tmp_path / "projects-collab-a2a.db")
    await pstore.init()
    istore = ProjectInviteStore(tmp_path / "invites-collab-a2a.db")
    await istore.init()
    cstore = ChatChannelStore(tmp_path / "chat-collab-a2a.db")
    await cstore.init()

    try:
        project = await pstore.create_project(
            name="P", slug="collab-a2a", created_by="u"
        )
        pid = project["id"]
        assert await _a2a_channels(cstore, pid) == []

        # A real config, as in production: the human contact is not in
        # config.agents, so the old member resolution dropped them from
        # the channel on this path (the previous config=None only passed
        # because member ids were used unchanged).
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    project_store=pstore,
                    project_invites=istore,
                    chat_channels=cstore,
                    config=SimpleNamespace(
                        agents=[{"id": "cfg-1", "name": "cfg-agent"}]
                    ),
                )
            )
        )

        async def _accept() -> dict:
            minted = await istore.mint(
                project_id=pid,
                scopes=[],
                approval_mode="manual",
                check_interval_secs=5,
                created_by="u",
                kind="collab",
                contact_id="contact-1",
            )
            return await _handle_collab_response(
                request,
                "contact-1",
                {},
                {
                    "invite_id": minted["record"]["invite_id"],
                    "project_id": pid,
                    "accepted": True,
                },
                "collab_invite_accept",
            )

        result = await _accept()
        assert result.get("error") is None, result

        members = await pstore.list_members(pid)
        assert any(
            m["member_id"] == "contact-1" and m["member_kind"] == "human"
            for m in members
        )

        channels = await _a2a_channels(cstore, pid)
        assert len(channels) == 1, (
            "collab invite accept must create the project's A2A channel"
        )
        assert "contact-1" in (channels[0].get("members") or [])

        # Second invite/accept cycle for the same contact: still one channel.
        result = await _accept()
        assert result.get("error") is None, result
        assert len(await _a2a_channels(cstore, pid)) == 1
        channels = await _a2a_channels(cstore, pid)
        assert "contact-1" in (channels[0].get("members") or [])
    finally:
        await pstore.close()
        await istore.close()
        await cstore.close()


@pytest.mark.asyncio
async def test_revoke_agent_keeps_registry_bystander_in_channel(
    client, monkeypatch, tmp_path
):
    """Revoking one agent's project scopes drops its member row and
    re-syncs the channel; a registry-backed bystander member must stay
    in the channel. The revoke path's ensure call must pass the agent
    registry, or the bystander — absent from config.agents — is dropped
    by the sync (jaylfc review on PR #4373)."""
    from tinyagentos.agent_grants_store import AgentGrantsStore
    from tinyagentos.agent_registry_store import (
        AgentRegistryStore,
        load_or_create_signing_keypair,
    )
    from tinyagentos.projects.project_store import ProjectStore

    registry = AgentRegistryStore(tmp_path / "reg-revoke-a2a.db")
    await registry.init()
    grants = AgentGrantsStore(tmp_path / "grants-revoke-a2a.db")
    await grants.init()
    pstore = ProjectStore(tmp_path / "projects-revoke-a2a.db")
    await pstore.init()
    load_or_create_signing_keypair(tmp_path / "keys-revoke-a2a")

    try:
        bystander = await registry.register(
            framework="openclaw", display_name="rev-bystander",
            user_id="u", origin="external-selfjoin", handle="rev-bystander",
        )
        await registry.set_status(bystander["canonical_id"], "active")
        target = await registry.register(
            framework="openclaw", display_name="rev-target",
            user_id="u", origin="external-selfjoin", handle="rev-target",
        )
        await registry.set_status(target["canonical_id"], "active")

        project = await pstore.create_project(
            name="P", slug="revoke-a2a", created_by="u"
        )
        pid = project["id"]
        channel_store = client._transport.app.state.chat_channels

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        for cid in (bystander["canonical_id"], target["canonical_id"]):
            resp = await client.post(
                f"/api/projects/{pid}/members/assign-agent",
                json={"canonical_id": cid, "scopes": ["project_tasks"]},
            )
            assert resp.status_code == 200, resp.text

        channels = await _a2a_channels(channel_store, pid)
        assert len(channels) == 1
        assert sorted(channels[0].get("members") or []) == [
            "rev-bystander", "rev-target",
        ]

        resp = await client.post(
            f"/api/projects/{pid}/members/revoke-agent",
            json={
                "canonical_id": target["canonical_id"],
                "scopes": ["project_tasks"],
            },
        )
        assert resp.status_code == 200, resp.text

        assert await pstore.get_member(pid, target["canonical_id"]) is None
        channels = await _a2a_channels(channel_store, pid)
        assert len(channels) == 1
        assert channels[0].get("members") or [] == ["rev-bystander"]
    finally:
        await registry.close()
        await grants.close()
        await pstore.close()
