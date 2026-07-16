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
