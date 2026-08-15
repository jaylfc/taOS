import asyncio
import pytest


class _FakeAuthRequestsStore:
    async def list_pending(self):
        return []

    async def get(self, request_id):
        return None


class TestRequestableScopes:
    """The project_tasks scope must be requestable through the consent loop, and
    unknown scopes must still be rejected up front (they can never be enforced)."""

    def test_project_tasks_is_a_valid_scope(self):
        from tinyagentos.routes.agent_auth_requests import VALID_SCOPES
        assert "project_tasks" in VALID_SCOPES

    def test_observatory_control_is_a_valid_scope(self):
        from tinyagentos.routes.agent_auth_requests import VALID_SCOPES
        assert "observatory_control" in VALID_SCOPES

    @pytest.mark.asyncio
    async def test_create_accepts_project_tasks(self, client, monkeypatch):
        class _Store(_FakeAuthRequestsStore):
            async def count_pending_for(self, identity_claim, framework):
                return 0

            async def create(self, **kwargs):
                return {
                    "id": "req-1",
                    "identity_claim": kwargs["identity_claim"],
                    "requested_scopes": kwargs["requested_scopes"],
                }

        monkeypatch.setattr(client._transport.app.state, "auth_requests", _Store())
        resp = await client.post(
            "/api/agents/auth-requests",
            json={
                "identity_claim": "grok",
                "framework": "grok-cli",
                "requested_scopes": ["project_tasks"],
                "project_id": "prj-1",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    @pytest.mark.asyncio
    async def test_create_rejects_unknown_scope(self, client, monkeypatch):
        monkeypatch.setattr(
            client._transport.app.state, "auth_requests", _FakeAuthRequestsStore()
        )
        resp = await client.post(
            "/api/agents/auth-requests",
            json={
                "identity_claim": "grok",
                "framework": "grok-cli",
                "requested_scopes": ["not_a_real_scope"],
            },
        )
        assert resp.status_code == 400


class TestAgentAuthRequestsList:
    @pytest.mark.asyncio
    async def test_list_returns_200_with_requests_key(self, client, monkeypatch):
        store = _FakeAuthRequestsStore()
        monkeypatch.setattr(client._transport.app.state, "auth_requests", store)
        resp = await client.get("/api/agents/auth-requests")
        assert resp.status_code == 200
        data = resp.json()
        assert "requests" in data
        assert isinstance(data["requests"], list)

    @pytest.mark.asyncio
    async def test_list_returns_pending_requests(self, client, monkeypatch):
        sample = [
            {
                "id": "abc123",
                "identity_claim": "test-agent",
                "framework": "langchain",
                "requested_scopes": ["memory_read"],
                "requested_skills": [],
                "reason": "testing",
                "duration_secs": None,
                "project_id": None,
                "status": "pending",
                "canonical_id": None,
                "token": None,
                "granted_scopes": None,
                "created_ts": "2026-01-01T00:00:00+00:00",
                "decided_ts": None,
                "decided_by": None,
            }
        ]

        class _Store(_FakeAuthRequestsStore):
            async def list_pending(self):
                return sample

        monkeypatch.setattr(
            client._transport.app.state, "auth_requests", _Store(),
        )
        resp = await client.get("/api/agents/auth-requests")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["requests"]) == 1
        assert data["requests"][0]["status"] == "pending"


class TestApproveDisplayNameNormalization:
    """Verify _do_approve strips a leading '@' before persisting display_name."""

    @pytest.mark.asyncio
    async def test_approve_strips_leading_at_from_display_name(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys")

        record = await auth_store.create(
            identity_claim="@taOSmd-dev",
            framework="openclaw",
            requested_scopes=["memory_read"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=None,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 200, resp.text

        agents = await registry.list_all()
        assert len(agents) == 1
        assert agents[0]["display_name"] == "taOSmd-dev"
        assert not agents[0]["display_name"].startswith("@")

        await registry.close()
        await auth_store.close()
        await grants.close()

    @pytest.mark.asyncio
    async def test_approve_project_tasks_requires_explicit_project_id(
        self, client, monkeypatch, tmp_path
    ):
        """Granting project_tasks without a picked project_id is rejected, so an
        unauthenticated request cannot bind a task token to a project the
        operator never validated in the picker."""
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys")

        # The request names a project, but the picker did not send one on approve.
        record = await auth_store.create(
            identity_claim="grok",
            framework="grok",
            requested_scopes=["project_tasks"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id="prj-agent-named",
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["project_tasks"]},
        )
        assert resp.status_code == 400, resp.text
        assert "project_id" in resp.text

        # No agent should have been registered by the rejected approval.
        assert await registry.list_all() == []

        await registry.close()
        await auth_store.close()
        await grants.close()

    @pytest.mark.asyncio
    async def test_approve_preserves_name_without_at(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg2.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth2.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants2.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys2")

        record = await auth_store.create(
            identity_claim="taOSmd-dev",
            framework="openclaw",
            requested_scopes=["memory_read"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=None,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 200, resp.text

        agents = await registry.list_all()
        assert agents[0]["display_name"] == "taOSmd-dev"

        await registry.close()
        await auth_store.close()
        await grants.close()

    @pytest.mark.asyncio
    async def test_approve_bare_at_claim_falls_back_to_framework(
        self, client, monkeypatch, tmp_path
    ):
        """A degenerate '@'-only claim must never persist '@' or an empty
        display_name -- it falls back to the framework name."""
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg3.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth3.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants3.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys3")

        record = await auth_store.create(
            identity_claim="@",
            framework="openclaw",
            requested_scopes=["memory_read"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=None,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 200, resp.text

        agents = await registry.list_all()
        assert agents[0]["display_name"] == "openclaw"
        assert "@" not in agents[0]["display_name"]

        await registry.close()
        await auth_store.close()
        await grants.close()


class TestCanvasScopeApproval:
    """Canvas scopes require a project_id and follow the same narrow-not-widen
    rules as project_tasks."""

    @pytest.mark.asyncio
    async def test_approve_canvas_scopes_with_project_succeeds(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-canvas.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-canvas.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-canvas.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-canvas")

        record = await auth_store.create(
            identity_claim="canvas-bot",
            framework="canvas-cli",
            requested_scopes=["canvas_read", "canvas_write"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id="prj-canvas-1",
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={
                "granted_scopes": ["canvas_read", "canvas_write"],
                "project_id": "prj-canvas-1",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["canonical_id"]

        agents = await registry.list_all()
        assert len(agents) == 1
        agent_grants = await grants.list_grants(agents[0]["canonical_id"])
        assert {g["scope"] for g in agent_grants} == {"canvas_read", "canvas_write"}

        await registry.close()
        await auth_store.close()
        await grants.close()

    @pytest.mark.asyncio
    async def test_approve_canvas_write_adds_member_with_write_flag(
        self, client, monkeypatch, tmp_path
    ):
        """Approving a canvas_write grant bound to a project must add the agent as
        a project member with can_edit_canvas=1 (and not can_read_canvas unless
        granted), so the approved token is actually authorized to write the
        canvas instead of 403ing on every call."""
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-canvas-mem.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-canvas-mem.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-canvas-mem.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-canvas-mem.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-canvas-mem")

        project = await pstore.create_project(
            name="Canvas Proj", slug="canvas-proj", created_by="u"
        )
        pid = project["id"]

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
        member = members[0]
        assert member["can_edit_canvas"] == 1
        # canvas_write alone must NOT grant read.
        assert member["can_read_canvas"] == 0

        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_approve_canvas_read_and_write_sets_both_flags(
        self, client, monkeypatch, tmp_path
    ):
        """Both canvas scopes granted bound to a project flip both member flags."""
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-canvas-both.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-canvas-both.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-canvas-both.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-canvas-both.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-canvas-both")

        project = await pstore.create_project(
            name="Canvas Proj", slug="canvas-proj-both", created_by="u"
        )
        pid = project["id"]

        record = await auth_store.create(
            identity_claim="canvas-bot",
            framework="canvas-cli",
            requested_scopes=["canvas_read", "canvas_write"],
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
            json={
                "granted_scopes": ["canvas_read", "canvas_write"],
                "project_id": pid,
            },
        )
        assert resp.status_code == 200, resp.text

        members = await pstore.list_members(pid)
        assert len(members) == 1
        assert members[0]["can_read_canvas"] == 1
        assert members[0]["can_edit_canvas"] == 1

        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_approve_canvas_scopes_without_project_is_rejected(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-canvas-np.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-canvas-np.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-canvas-np.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-canvas-np")

        record = await auth_store.create(
            identity_claim="canvas-bot",
            framework="canvas-cli",
            requested_scopes=["canvas_read", "canvas_write"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id="prj-canvas-1",
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["canvas_read", "canvas_write"]},
        )
        assert resp.status_code == 400, resp.text
        assert "project_id" in resp.text

        # No agent should have been registered by the rejected approval.
        assert await registry.list_all() == []

        await registry.close()
        await auth_store.close()
        await grants.close()

    @pytest.mark.asyncio
    async def test_approve_canvas_scopes_with_blank_project_is_rejected(
        self, client, monkeypatch, tmp_path
    ):
        # A blank or whitespace project_id is not a real binding: it must fail
        # closed exactly like a missing one, or a downstream truthy check would
        # treat the token as unbound and allow cross-project canvas access.
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        for blank in ("", "   "):
            registry = AgentRegistryStore(tmp_path / f"reg-canvas-blank-{len(blank)}.db")
            await registry.init()
            auth_store = AuthRequestsStore(tmp_path / f"auth-canvas-blank-{len(blank)}.db")
            await auth_store.init()
            grants = AgentGrantsStore(tmp_path / f"grants-canvas-blank-{len(blank)}.db")
            await grants.init()
            priv, pub = load_or_create_signing_keypair(
                tmp_path / f"keys-canvas-blank-{len(blank)}"
            )

            record = await auth_store.create(
                identity_claim="canvas-bot",
                framework="canvas-cli",
                requested_scopes=["canvas_write"],
                requested_skills=None,
                reason="",
                duration_secs=None,
                project_id="prj-canvas-1",
            )

            monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
            monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
            monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
            monkeypatch.setattr(
                client._transport.app.state, "agent_registry_keypair", (priv, pub)
            )

            resp = await client.post(
                f"/api/agents/auth-requests/{record['id']}/approve",
                json={"granted_scopes": ["canvas_write"], "project_id": blank},
            )
            assert resp.status_code == 400, (blank, resp.text)
            assert "project_id" in resp.text
            # The blank-project approval must not register an agent.
            assert await registry.list_all() == []

        await registry.close()
        await auth_store.close()
        await grants.close()


class TestAddAgentToAnotherProject:
    """taOS #1862: an already-registered ACTIVE agent (handle collision) is
    ADDED to a further project instead of 409ing when the approval is for a
    project-scoped grant. The identity (canonical_id) and token are reused."""

    @pytest.mark.asyncio
    async def test_reuse_active_handle_adds_second_project(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-mp.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-mp.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-mp.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-mp.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-mp")

        pA = await pstore.create_project(name="A", slug="proj-a", created_by="u")
        pB = await pstore.create_project(name="B", slug="proj-b", created_by="u")

        # Register + approve the agent for project A (handle taosmd-dev).
        rA = await auth_store.create(
            identity_claim="@taOSmd-dev", framework="openclaw",
            requested_scopes=["project_tasks"], requested_skills=None, reason="",
            duration_secs=None, project_id=pA["id"],
        )
        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "agent_registry_keypair", (priv, pub))
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        respA = await client.post(
            f"/api/agents/auth-requests/{rA['id']}/approve",
            json={"granted_scopes": ["project_tasks"], "project_id": pA["id"]},
        )
        assert respA.status_code == 200, respA.text
        cid = respA.json()["canonical_id"]
        assert cid

        # Now approve a SECOND request with the SAME handle, for project B.
        rB = await auth_store.create(
            identity_claim="@taOSmd-dev", framework="openclaw",
            requested_scopes=["project_tasks"], requested_skills=None, reason="",
            duration_secs=None, project_id=pB["id"],
        )
        respB = await client.post(
            f"/api/agents/auth-requests/{rB['id']}/approve",
            json={"granted_scopes": ["project_tasks"], "project_id": pB["id"]},
        )
        assert respB.status_code == 200, respB.text
        # Same canonical_id is reused, NOT a fresh one (no 409).
        assert respB.json()["canonical_id"] == cid

        # Exactly one active agent with that handle.
        active = await registry.list_all(status="active")
        assert len([a for a in active if a["handle"] == "taosmd-dev"]) == 1

        # Member of BOTH projects.
        membersA = await pstore.list_members(pA["id"])
        membersB = await pstore.list_members(pB["id"])
        assert any(m["member_id"] == cid for m in membersA)
        assert any(m["member_id"] == cid for m in membersB)

        # Grants for BOTH projects.
        agent_grants = await grants.list_grants(cid)
        assert {g["project_id"] for g in agent_grants} >= {pA["id"], pB["id"]}

        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_reuse_binds_to_validated_project_not_agent_supplied(
        self, client, monkeypatch, tmp_path
    ):
        """Hardening (kilo review, taOS #1862): the multi-project ADD branch
        binds to the ADMIN-validated project_id, never to the agent-supplied
        record.project_id. An agent that self-requests project C but is approved
        by the admin for project B must land in B only, never C."""
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-h.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-h.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-h.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-h.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-h")

        pA = await pstore.create_project(name="A", slug="proj-a", created_by="u")
        pB = await pstore.create_project(name="B", slug="proj-b", created_by="u")
        pC = await pstore.create_project(name="C", slug="proj-c", created_by="u")

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "agent_registry_keypair", (priv, pub))
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        # Establish the active identity in project A.
        rA = await auth_store.create(
            identity_claim="@dev", framework="openclaw",
            requested_scopes=["project_tasks"], requested_skills=None, reason="",
            duration_secs=None, project_id=pA["id"],
        )
        respA = await client.post(
            f"/api/agents/auth-requests/{rA['id']}/approve",
            json={"granted_scopes": ["project_tasks"], "project_id": pA["id"]},
        )
        assert respA.status_code == 200, respA.text
        cid = respA.json()["canonical_id"]

        # Second request: the AGENT names project C in the record, but the admin
        # approves for project B. The reuse/ADD branch must honour B, not C.
        rB = await auth_store.create(
            identity_claim="@dev", framework="openclaw",
            requested_scopes=["project_tasks"], requested_skills=None, reason="",
            duration_secs=None, project_id=pC["id"],
        )
        respB = await client.post(
            f"/api/agents/auth-requests/{rB['id']}/approve",
            json={"granted_scopes": ["project_tasks"], "project_id": pB["id"]},
        )
        assert respB.status_code == 200, respB.text
        assert respB.json()["canonical_id"] == cid

        membersB = await pstore.list_members(pB["id"])
        membersC = await pstore.list_members(pC["id"])
        assert any(m["member_id"] == cid for m in membersB), "must be a member of the validated project B"
        assert not any(m["member_id"] == cid for m in membersC), "must NOT be added to the agent-supplied project C"

        agent_grants = await grants.list_grants(cid)
        grant_projects = {g["project_id"] for g in agent_grants}
        assert pB["id"] in grant_projects
        assert pC["id"] not in grant_projects, "no grant may bind to the agent-supplied project C"

        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_non_project_handle_collision_still_409(
        self, client, monkeypatch, tmp_path
    ):
        """A handle collision on a NON-project grant remains a genuine 409."""
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-mp409.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-mp409.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-mp409.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-mp409")

        await registry.register(
            framework="openclaw", display_name="existing-agent",
            user_id="user-existing", origin="taos-deployed", handle="taosmd-dev",
        )

        r = await auth_store.create(
            identity_claim="@taOSmd-dev", framework="openclaw",
            requested_scopes=["memory_read"], requested_skills=None, reason="",
            duration_secs=None, project_id=None,
        )
        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "agent_registry_keypair", (priv, pub))

        resp = await client.post(
            f"/api/agents/auth-requests/{r['id']}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 409, resp.text

        await registry.close()
        await auth_store.close()
        await grants.close()


class TestAssignAgentRoute:
    """Admin route POST /api/projects/{pid}/members/assign-agent."""

    @pytest.mark.asyncio
    async def test_assign_existing_agent_adds_membership_and_grant(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-assign.db")
        await registry.init()
        grants = AgentGrantsStore(tmp_path / "grants-assign.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-assign.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-assign")

        # An already-registered agent (owner u).
        reg = await registry.register(
            framework="openclaw", display_name="taosmd-dev",
            user_id="u", origin="external-selfjoin", handle="taosmd-dev",
        )
        await registry.set_status(reg["canonical_id"], "active")
        cid = reg["canonical_id"]

        project = await pstore.create_project(name="P", slug="proj-p", created_by="u")

        # Make the test client act as admin+owner so the gate passes.
        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        resp = await client.post(
            f"/api/projects/{project['id']}/members/assign-agent",
            json={"canonical_id": cid, "scopes": ["project_tasks"]},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["canonical_id"] == cid
        assert data["project_id"] == project["id"]
        assert data["granted_scopes"] == ["project_tasks"]

        members = await pstore.list_members(project["id"])
        assert any(m["member_id"] == cid for m in members)
        agent_grants = await grants.list_grants(cid)
        assert any(
            g["scope"] == "project_tasks" and g["project_id"] == project["id"]
            for g in agent_grants
        )

        await registry.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_assign_requires_admin(self, client, monkeypatch, tmp_path):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-assign2.db")
        await registry.init()
        grants = AgentGrantsStore(tmp_path / "grants-assign2.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-assign2.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-assign2")

        reg = await registry.register(
            framework="openclaw", display_name="taosmd-dev",
            user_id="u", origin="external-selfjoin", handle="taosmd-dev",
        )
        await registry.set_status(reg["canonical_id"], "active")
        project = await pstore.create_project(name="P", slug="proj-p2", created_by="u")

        # A non-admin, non-owner caller must be rejected by the admin gate.
        # add_user_invite creates a non-admin user; log in as them for this call.
        auth = client._transport.app.state.auth
        auth.add_user_invite("bob", "admin")
        bob = auth.find_user("bob")
        bob_token = auth.create_session(user_id=bob["id"], long_lived=True)

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        resp = await client.post(
            f"/api/projects/{project['id']}/members/assign-agent",
            json={"canonical_id": reg["canonical_id"], "scopes": ["project_tasks"]},
            cookies={"taos_session": bob_token},
        )
        assert resp.status_code == 403, resp.text

        await registry.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_approve_cannot_widen_canvas_scopes(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-canvas-widen.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-canvas-widen.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-canvas-widen.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-canvas-widen")

        record = await auth_store.create(
            identity_claim="canvas-bot",
            framework="canvas-cli",
            requested_scopes=["canvas_read"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id="prj-canvas-1",
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={
                "granted_scopes": ["canvas_read", "canvas_write"],
                "project_id": "prj-canvas-1",
            },
        )
        assert resp.status_code == 400, resp.text
        assert "subset of the requested scopes" in resp.text

        await registry.close()
        await auth_store.close()
        await grants.close()

    @pytest.mark.asyncio
    async def test_approve_canvas_scopes_cannot_widen_by_adding_project_tasks(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-canvas-mix.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-canvas-mix.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-canvas-mix.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-canvas-mix")

        record = await auth_store.create(
            identity_claim="canvas-bot",
            framework="canvas-cli",
            requested_scopes=["canvas_read"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id="prj-canvas-1",
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={
                "granted_scopes": ["canvas_read", "project_tasks"],
                "project_id": "prj-canvas-1",
            },
        )
        assert resp.status_code == 400, resp.text

        await registry.close()
        await auth_store.close()
        await grants.close()
    @pytest.mark.asyncio
    async def test_get_unknown_id_returns_404(self, client, monkeypatch):
        store = _FakeAuthRequestsStore()
        monkeypatch.setattr(client._transport.app.state, "auth_requests", store)
        resp = await client.get("/api/agents/auth-requests/nonexistent123")
        assert resp.status_code == 404


class TestHandleSetOnApprove:
    """Slice 7: approve sets the registry handle from the sanitized identity
    claim; the new agent then passes the a2a bus 'no handle' gate."""

    @pytest.mark.asyncio
    async def test_approve_sets_handle_on_registry(self, client, monkeypatch, tmp_path):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-handle.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-handle.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-handle.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-handle")

        record = await auth_store.create(
            identity_claim="@taOSmd-dev",
            framework="openclaw",
            requested_scopes=["memory_read"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=None,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 200, resp.text

        agents = await registry.list_all()
        assert len(agents) == 1
        assert agents[0]["handle"] == "taosmd-dev"

        handle = (agents[0].get("handle") or "").strip()
        assert handle, "handle must be set so the a2a 'no handle' gate passes"

        await registry.close()
        await auth_store.close()
        await grants.close()


class TestHandleCollisionActiveRejects:
    """Slice 7: a handle collision with an ACTIVE identity returns 409 and
    leaves the auth request PENDING so the approver can pick another variant."""

    @pytest.mark.asyncio
    async def test_409_when_handle_collides_with_active(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-collide.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-collide.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-collide.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-collide")

        existing = await registry.register(
            framework="openclaw",
            display_name="existing-agent",
            user_id="user-existing",
            origin="taos-deployed",
            handle="taosmd-dev",
        )

        record = await auth_store.create(
            identity_claim="@taOSmd-dev",
            framework="openclaw",
            requested_scopes=["memory_read"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=None,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 409, resp.text

        pending = await auth_store.get(record["id"])
        assert pending is not None
        assert pending["status"] == "pending"

        active_agents = await registry.list_all(status="active")
        assert len(active_agents) == 1

        await registry.close()
        await auth_store.close()
        await grants.close()


class TestHandleCollisionSuspendedAllowsReuse:
    """Slice 7: if the previous holder of the handle is SUSPENDED, the handle
    may be reused and approval succeeds."""

    @pytest.mark.asyncio
    async def test_approve_succeeds_after_handle_holder_suspended(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-suspended.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-suspended.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-suspended.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-suspended")

        old_agent = await registry.register(
            framework="openclaw",
            display_name="old-agent",
            user_id="user-old",
            origin="taos-deployed",
            handle="taosmd-dev",
        )
        await registry.set_status(
            old_agent["canonical_id"], "suspended", actor="user-old"
        )

        record = await auth_store.create(
            identity_claim="@taOSmd-dev",
            framework="openclaw",
            requested_scopes=["memory_read"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=None,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 200, resp.text

        new_agents = await registry.list_all()
        new_active = [a for a in new_agents if a["status"] == "active"]
        assert len(new_active) == 1
        assert new_active[0]["handle"] == "taosmd-dev"

        await registry.close()
        await auth_store.close()
        await grants.close()


class TestPartialUniqueHandleIndex:
    """The DB (not just the application pre-check) must reject a duplicate
    active handle, so two concurrent approvals of the same identity_claim
    cannot both become active with identical handles (a2a 'from' spoofing)."""

    @pytest.mark.asyncio
    async def test_index_blocks_two_active_with_same_handle(self, tmp_path):
        from tinyagentos.agent_registry_store import AgentRegistryStore

        registry = AgentRegistryStore(tmp_path / "reg-idx.db")
        await registry.init()

        # Both born pending with the same handle (allowed: index only covers
        # active + non-empty handle).
        a = await registry.register(
            framework="openclaw", display_name="a", origin="external-selfjoin",
            handle="dup-handle",
        )
        b = await registry.register(
            framework="openclaw", display_name="b", origin="external-selfjoin",
            handle="dup-handle",
        )
        await registry.set_status(a["canonical_id"], "active")

        # The second activation must be rejected by the partial unique index.
        with pytest.raises(Exception) as exc:
            await registry.set_status(b["canonical_id"], "active")
        assert "ux_agent_active_handle" in str(exc.value) or "UNIQUE" in str(exc.value)

        # And the loser must never be left active with the handle.
        b_row = await registry.get(b["canonical_id"])
        assert b_row["status"] == "pending"
        await registry.close()


class TestConcurrentApproveSameIdentity:
    """Two approvals racing on the SAME identity_claim: exactly one active
    agent with the handle set, the other returns 409 and leaves no extra
    active (or pending) agent behind."""

    @pytest.mark.asyncio
    async def test_concurrent_approve_one_active_other_409(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-race.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-race.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-race.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-race")

        # Two independent auth requests for the SAME identity_claim. The
        # per-request lock does NOT protect this case (different request ids),
        # so this exercises the unique-index race directly.
        r1 = await auth_store.create(
            identity_claim="@taOSmd-dev", framework="openclaw",
            requested_scopes=["memory_read"], requested_skills=None, reason="",
            duration_secs=None, project_id=None,
        )
        r2 = await auth_store.create(
            identity_claim="@taOSmd-dev", framework="openclaw",
            requested_scopes=["memory_read"], requested_skills=None, reason="",
            duration_secs=None, project_id=None,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        async def _approve(rid):
            return await client.post(
                f"/api/agents/auth-requests/{rid}/approve",
                json={"granted_scopes": ["memory_read"]},
            )

        # Drive both approvals concurrently so neither pre-check sees an active
        # handle yet; the unique index decides the winner.
        resps = await asyncio.gather(_approve(r1["id"]), _approve(r2["id"]))

        statuses = sorted(r.status_code for r in resps)
        assert statuses == [200, 409], [r.status_code for r in resps]

        active = await registry.list_all(status="active")
        assert len(active) == 1, active
        # The winner's handle is set and non-empty.
        assert active[0]["handle"] == "taosmd-dev"
        assert active[0]["handle"]

        # The loser is not left behind as an active or pending agent.
        assert len(await registry.list_all()) == 1

        await registry.close()
        await auth_store.close()
        await grants.close()

    @pytest.mark.asyncio
    async def test_approved_active_handle_is_nonempty(
        self, client, monkeypatch, tmp_path
    ):
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-nonempty.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-nonempty.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-nonempty.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-nonempty")

        record = await auth_store.create(
            identity_claim="grok-bot", framework="grok",
            requested_scopes=["memory_read"], requested_skills=None, reason="",
            duration_secs=None, project_id=None,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 200, resp.text

        active = await registry.list_all(status="active")
        assert len(active) == 1
        # Never left active with an empty handle (the orphan-window bug).
        assert active[0]["handle"]
        assert active[0]["handle"] == "grok-bot"

        await registry.close()
        await auth_store.close()
        await grants.close()


class TestDeferBindingApproval:
    """PR 2187 fix-forward: defer_binding must be wired through _do_approve so the
    admin's 'Assign later' choice actually mints an unbound token instead of
    silently binding to a project."""

    @pytest.mark.asyncio
    async def test_defer_with_project_scopes_no_project_id_succeeds_unbound(
        self, client, monkeypatch, tmp_path
    ):
        """defer_binding=true + project scopes + no explicit project_id returns 200
        with an unbound token (no project_id claim), unbound grants, no membership
        row, and no a2a channel. The project_id-required 400 guard is skipped."""
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
            verify_registry_token,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-defer.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-defer.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-defer.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-defer.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-defer")

        project = await pstore.create_project(
            name="Defer Proj", slug="defer-proj", created_by="u"
        )

        # The agent REQUESTED project_tasks against a project, but the admin
        # defers binding: no explicit project_id on approve, defer_binding=true.
        record = await auth_store.create(
            identity_claim="@defer-bot",
            framework="defer-cli",
            requested_scopes=["project_tasks"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=project["id"],
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["project_tasks"], "defer_binding": True},
        )
        assert resp.status_code == 200, resp.text
        cid = resp.json()["canonical_id"]

        # Token carries NO project_id claim (minted unbound).
        approved = await auth_store.get(record["id"])
        claims = verify_registry_token(approved["token"], pub)
        assert "project_id" not in claims

        # Grants are written UNBOUND (project_id IS NULL).
        agent_grants = await grants.list_grants(cid)
        assert len(agent_grants) == 1
        assert agent_grants[0]["scope"] == "project_tasks"
        assert agent_grants[0]["project_id"] is None

        # No membership row created for the project.
        members = await pstore.list_members(project["id"])
        assert len(members) == 0

        # No a2a channel created for the project.
        channels = await client._transport.app.state.chat_channels.list_channels(
            project_id=project["id"]
        )
        assert not any(c.get("name") == "a2a" for c in channels)

        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_defer_with_explicit_project_id_returns_400(
        self, client, monkeypatch, tmp_path
    ):
        """defer_binding=true combined with an explicit project_id is contradictory
        and must 400, rather than silently binding anyway (the bug PR 2187 shipped)."""
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-defer-400.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-defer-400.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-defer-400.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-defer-400.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-defer-400")

        project = await pstore.create_project(
            name="Defer400", slug="defer-400", created_by="u"
        )

        record = await auth_store.create(
            identity_claim="@defer400-bot",
            framework="defer-cli",
            requested_scopes=["project_tasks"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=None,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={
                "granted_scopes": ["project_tasks"],
                "project_id": project["id"],
                "defer_binding": True,
            },
        )
        assert resp.status_code == 400, resp.text
        assert "defer_binding" in resp.text

        # Nothing should have been registered by the rejected approval.
        assert await registry.list_all() == []

        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_non_deferred_project_binding_unchanged(
        self, client, monkeypatch, tmp_path
    ):
        """Without defer_binding, a project-scoped approval binds to the explicit
        project_id as before: token carries the project claim, grants are bound,
        membership + a2a channel are created."""
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
            verify_registry_token,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-nondefer.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-nondefer.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-nondefer.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-nondefer.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-nondefer")

        project = await pstore.create_project(
            name="NonDefer", slug="nondefer-proj", created_by="u"
        )

        record = await auth_store.create(
            identity_claim="@nondefer-bot",
            framework="defer-cli",
            requested_scopes=["project_tasks"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=project["id"],
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={
                "granted_scopes": ["project_tasks"],
                "project_id": project["id"],
            },
        )
        assert resp.status_code == 200, resp.text
        cid = resp.json()["canonical_id"]

        # Token carries the project_id claim.
        approved = await auth_store.get(record["id"])
        claims = verify_registry_token(approved["token"], pub)
        assert claims.get("project_id") == project["id"]

        # Grants are bound to the project.
        agent_grants = await grants.list_grants(cid)
        assert any(
            g["scope"] == "project_tasks" and g["project_id"] == project["id"]
            for g in agent_grants
        )

        # Membership row created.
        members = await pstore.list_members(project["id"])
        assert any(m["member_id"] == cid for m in members)

        # a2a channel created.
        channels = await client._transport.app.state.chat_channels.list_channels(
            project_id=project["id"]
        )
        assert any(c.get("name") == "a2a" for c in channels)

        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_defer_with_active_handle_returns_409_assign_agent(
        self, client, monkeypatch, tmp_path
    ):
        """defer_binding=true when the handle already maps to an active identity
        must 409 and point the operator at assign-agent, not at minting a
        duplicate identity."""
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-defer-active.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-defer-active.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-defer-active.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-defer-active")

        existing = await registry.register(
            framework="openclaw",
            display_name="defer-bot",
            user_id="user-existing",
            origin="taos-deployed",
            handle="defer-bot",
        )

        record = await auth_store.create(
            identity_claim="@defer-bot",
            framework="defer-cli",
            requested_scopes=["memory_read"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=None,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["memory_read"], "defer_binding": True},
        )
        assert resp.status_code == 409, resp.text
        assert "assign-agent" in resp.text
        assert "pick a different identity_claim" not in resp.text

        await registry.close()
        await auth_store.close()


# ---------------------------------------------------------------------------
# project_create kind: registered agent requests new project creation
# ---------------------------------------------------------------------------

class TestProjectCreateAuthRequest:
    """An already-registered agent (proven via its own registry Bearer JWT)
    requests creation of a new project.

    * Name or slug already taken → synchronous 409 (no Decision, auto-reject).
    * Both free → auth-request record + approve_deny Decision created; Jay
      approves via the Decisions app to actually create the project and set
      the requester as lead.
    """

    async def _wire(self, client, monkeypatch, tmp_path):
        """Fresh auth_requests + registry + decision stores, wired on app.state."""
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore

        app = client._transport.app

        auth_store = AuthRequestsStore(tmp_path / "auth-pc.db")
        await auth_store.init()
        registry = AgentRegistryStore(tmp_path / "reg-pc.db")
        await registry.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-pc")
        decision_store = app.state.decision_store
        if decision_store._db is None:
            await decision_store.init()

        monkeypatch.setattr(app.state, "auth_requests", auth_store)
        monkeypatch.setattr(app.state, "agent_registry", registry)
        monkeypatch.setattr(app.state, "agent_registry_keypair", (priv, pub))

        return auth_store, registry, (priv, pub), decision_store

    async def _register_active(self, registry, *, handle="@worker", display="worker"):
        rec = await registry.register(
            framework="test",
            display_name=display,
            origin="external-selfjoin",
            handle=handle,
        )
        cid = rec["canonical_id"]
        await registry.set_status(cid, "active")
        return cid

    @pytest.mark.asyncio
    async def test_name_taken_returns_409_no_decision(self, client, monkeypatch, tmp_path):
        from tinyagentos.agent_registry_store import mint_registry_token

        auth_store, registry, (priv, pub), decision_store = await self._wire(
            client, monkeypatch, tmp_path
        )
        cid = await self._register_active(registry, handle="@worker", display="worker")
        token = mint_registry_token(cid, priv, framework="test")

        # Pre-create a project with the same name.
        pstore = client._transport.app.state.project_store
        await pstore.create_project(
            name="my-project", slug="my-project", created_by=cid
        )

        before = len(await auth_store.list_pending())
        resp = await client.post(
            "/api/agents/auth-requests",
            json={
                "identity_claim": "@worker",
                "framework": "test",
                "kind": "project_create",
                "name": "my-project",
                "slug": "new-slug",
                "purpose": "test project",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["field"] == "name"
        assert "suggestions" in data
        assert len(await auth_store.list_pending()) == before
        projects = await pstore.list_projects()
        assert len(projects) == 1  # no new project

        await registry.close()
        await auth_store.close()

    @pytest.mark.asyncio
    async def test_slug_taken_returns_409_no_decision(self, client, monkeypatch, tmp_path):
        from tinyagentos.agent_registry_store import mint_registry_token

        auth_store, registry, (priv, pub), decision_store = await self._wire(
            client, monkeypatch, tmp_path
        )
        cid = await self._register_active(registry, handle="@worker", display="worker")
        token = mint_registry_token(cid, priv, framework="test")

        pstore = client._transport.app.state.project_store
        await pstore.create_project(
            name="other-project", slug="taken-slug", created_by=cid
        )

        before = len(await auth_store.list_pending())
        resp = await client.post(
            "/api/agents/auth-requests",
            json={
                "identity_claim": "@worker",
                "framework": "test",
                "kind": "project_create",
                "name": "fresh-project",
                "slug": "taken-slug",
                "purpose": "test project",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409
        data = resp.json()
        assert data["field"] == "slug"
        assert "suggestions" in data
        assert len(await auth_store.list_pending()) == before

        await registry.close()
        await auth_store.close()

    @pytest.mark.asyncio
    async def test_free_name_and_slug_creates_decision(self, client, monkeypatch, tmp_path):
        from tinyagentos.agent_registry_store import mint_registry_token

        auth_store, registry, (priv, pub), decision_store = await self._wire(
            client, monkeypatch, tmp_path
        )
        cid = await self._register_active(registry, handle="@worker", display="worker")
        token = mint_registry_token(cid, priv, framework="test")

        resp = await client.post(
            "/api/agents/auth-requests",
            json={
                "identity_claim": "@worker",
                "framework": "test",
                "kind": "project_create",
                "name": "brand-new",
                "slug": "brand-new",
                "purpose": "a brand new project",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "pending"
        assert "request_id" in data
        assert "decision_id" in data

        req_rec = await auth_store.get(data["request_id"])
        assert req_rec["kind"] == "project_create"
        assert req_rec["requested_name"] == "brand-new"
        assert req_rec["requested_slug"] == "brand-new"
        assert req_rec["purpose"] == "a brand new project"

        dec = await decision_store.get(data["decision_id"])
        assert dec is not None
        assert dec["type"] == "approve_deny"
        assert dec["metadata"]["kind"] == "project_create"
        assert dec["metadata"]["requester_canonical_id"] == cid
        assert dec["from_agent"] == "@worker"

        await registry.close()
        await auth_store.close()

    @pytest.mark.asyncio
    async def test_approve_creates_project_and_sets_lead(self, client, monkeypatch, tmp_path):
        from tinyagentos.agent_registry_store import mint_registry_token

        auth_store, registry, (priv, pub), decision_store = await self._wire(
            client, monkeypatch, tmp_path
        )
        cid = await self._register_active(registry, handle="@worker", display="worker")
        token = mint_registry_token(cid, priv, framework="test")

        pstore = client._transport.app.state.project_store
        before = len(await pstore.list_projects())

        resp = await client.post(
            "/api/agents/auth-requests",
            json={
                "identity_claim": "@worker",
                "framework": "test",
                "kind": "project_create",
                "name": "approved-proj",
                "slug": "approved-proj",
                "purpose": "approved project",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()

        # Approve the decision.
        resp2 = await client.post(
            f"/api/decisions/{data['decision_id']}/answer",
            json={"value": "approve"},
        )
        assert resp2.status_code == 200, resp2.text

        # Project should now exist.
        after = len(await pstore.list_projects())
        assert after == before + 1
        proj = await pstore.get_project_by_slug("approved-proj")
        assert proj is not None
        assert proj["lead_member_id"] == cid

        # Requester is a member with lead role.
        members = await pstore.list_members(proj["id"])
        lead_member = [m for m in members if m["member_id"] == cid]
        assert len(lead_member) == 1
        assert lead_member[0]["role"] == "lead"

        await registry.close()
        await auth_store.close()

    @pytest.mark.asyncio
    async def test_deny_creates_nothing(self, client, monkeypatch, tmp_path):
        from tinyagentos.agent_registry_store import mint_registry_token

        auth_store, registry, (priv, pub), decision_store = await self._wire(
            client, monkeypatch, tmp_path
        )
        cid = await self._register_active(registry, handle="@worker", display="worker")
        token = mint_registry_token(cid, priv, framework="test")

        pstore = client._transport.app.state.project_store
        before = len(await pstore.list_projects())

        resp = await client.post(
            "/api/agents/auth-requests",
            json={
                "identity_claim": "@worker",
                "framework": "test",
                "kind": "project_create",
                "name": "denied-proj",
                "slug": "denied-proj",
                "purpose": "denied project",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()

        # Deny the decision.
        resp2 = await client.post(
            f"/api/decisions/{data['decision_id']}/answer",
            json={"value": "deny"},
        )
        assert resp2.status_code == 200, resp2.text

        # No new project.
        after = len(await pstore.list_projects())
        assert after == before

        await registry.close()
        await auth_store.close()

    @pytest.mark.asyncio
    async def test_no_bearer_token_returns_401(self, client, monkeypatch, tmp_path):
        auth_store, registry, (priv, pub), decision_store = await self._wire(
            client, monkeypatch, tmp_path
        )
        resp = await client.post(
            "/api/agents/auth-requests",
            json={
                "identity_claim": "@worker",
                "framework": "test",
                "kind": "project_create",
                "name": "no-auth-proj",
                "slug": "no-auth-proj",
                "purpose": "no auth",
            },
        )
        assert resp.status_code == 401

        await registry.close()
        await auth_store.close()

