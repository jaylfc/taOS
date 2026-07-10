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


class TestAgentAuthRequestsGet:
    @pytest.mark.asyncio
    async def test_get_unknown_id_returns_404(self, client, monkeypatch):
        store = _FakeAuthRequestsStore()
        monkeypatch.setattr(client._transport.app.state, "auth_requests", store)
        resp = await client.get("/api/agents/auth-requests/nonexistent123")
        assert resp.status_code == 404
