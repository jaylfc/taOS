"""Tests for deferred grant expiry inheritance in assign-agent binding.

This test suite verifies the fix for the bug where an assign-agent binding silently
dropped the deferred grant's expires_at, violating the ruling that grants are
unbounded unless someone sets a bound, and a bound, once set, is never silently
dropped or lengthened.

The bug: when an auth request is approved with duration_secs and `defer_binding`
is set, the stored row carries expires_at, but the later assign-agent binding
(`add_agent_to_project`) creates the project-bound grant WITHOUT the deferred
row expiry. The bound is silently dropped, which the ruling forbids.

The fix: the binding inherits the deferred row expires_at and refuses an expired
deferred row. Never lengthen: if both a stored bound and a request bound exist,
keep the earlier one.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    verify_registry_token,
)
from tinyagentos.auth_requests_store import AuthRequestsStore
from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.projects.project_store import ProjectStore


MAX_GRANT_DURATION_SECS = 10 * 365 * 24 * 3600


class TestDeferBindingExpiryInheritance:
    """Tests for deferred grant expiry inheritance in assign-agent binding."""

    def _parse(self, iso: str) -> datetime:
        return datetime.fromisoformat(iso)

    @pytest.mark.asyncio
    async def test_defer_binding_inherits_expiry_when_assigning(
        self, client, monkeypatch, tmp_path
    ):
        """Test 1: approve with duration_secs=3600 and defer_binding, then assign the agent to the project.

        The project-bound grant row MUST carry expires_at == deferred row expires_at.
        """
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-defer-1.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-defer-1.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-defer-1.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-defer-1.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-defer-1")

        project = await pstore.create_project(
            name="Defer Proj 1", slug="defer-proj-1", created_by="u"
        )

        # Create an auth request with duration_secs and defer_binding will be set on approve
        record = await auth_store.create(
            identity_claim="@defer-bot-1",
            framework="defer-cli-1",
            requested_scopes=["project_tasks"],
            requested_skills=None,
            reason="",
            duration_secs=3600,  # 1 hour bound
            project_id=project["id"],
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "agent_registry_keypair", (priv, pub))
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        # Approve with defer_binding - this creates an unbound grant with expires_at
        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["project_tasks"], "defer_binding": True},
        )
        assert resp.status_code == 200, resp.text
        cid = resp.json()["canonical_id"]

        # Verify the deferred grant has expires_at
        agent_grants = await grants.list_grants(cid)
        assert len(agent_grants) == 1
        deferred_grant = agent_grants[0]
        assert deferred_grant["project_id"] is None  # Unbound
        assert deferred_grant["expires_at"] is not None

        parsed_deferred_expiry = self._parse(deferred_grant["expires_at"])
        # Check it's approximately 1 hour from now
        delta = parsed_deferred_expiry - datetime.now(timezone.utc)
        assert timedelta(minutes=59) < delta <= timedelta(hours=1, minutes=1)

        # Now assign the agent to the project (this is where the bug occurs)
        resp = await client.post(
            f"/api/projects/{project['id']}/members/assign-agent",
            json={
                "canonical_id": cid,
                "scopes": ["project_tasks"],
                "is_lead": False,
            },
        )
        assert resp.status_code == 200, resp.text

        # Verify the project-bound grant inherits the expires_at
        project_grants = await grants.list_grants(cid)
        project_grant = [g for g in project_grants if g["project_id"] == project["id"]]
        assert len(project_grant) == 1
        project_grant = project_grant[0]

        # The bug: project_bound grant was created WITHOUT expires_at
        # This test should FAIL on current dev, PASS after fix
        assert project_grant["expires_at"] is not None, "project-bound grant should have expires_at inherited from deferred grant"

        # The expiry should be the same as the deferred grant
        parsed_project_expiry = self._parse(project_grant["expires_at"])
        assert parsed_project_expiry == parsed_deferred_expiry, "project-bound grant should inherit the same expires_at as deferred grant"

        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_defer_binding_refused_when_expired(
        self, client, monkeypatch, tmp_path
    ):
        """Test 2: same flow but the deferred row has already expired.

        The binding MUST be refused (4xx, no grant row written).
        """
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-defer-2.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-defer-2.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-defer-2.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-defer-2.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-defer-2")

        project = await pstore.create_project(
            name="Defer Proj 2", slug="defer-proj-2", created_by="u"
        )

        # Create an auth request with duration_secs and defer_binding will be set on approve
        record = await auth_store.create(
            identity_claim="@defer-bot-2",
            framework="defer-cli-2",
            requested_scopes=["project_tasks"],
            requested_skills=None,
            reason="",
            duration_secs=3600,  # 1 hour bound
            project_id=project["id"],
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "agent_registry_keypair", (priv, pub))
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        # Approve with defer_binding - this creates an unbound grant with expires_at
        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["project_tasks"], "defer_binding": True},
        )
        assert resp.status_code == 200, resp.text
        cid = resp.json()["canonical_id"]

        # Manually expire the deferred grant by setting expires_at in the past
        agent_grants = await grants.list_grants(cid)
        assert len(agent_grants) == 1
        deferred_grant = agent_grants[0]
        
        # Set expires_at to 2 hours ago
        past_expiry = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        await grants._db.execute(
            "UPDATE agent_grants SET expires_at = ? WHERE id = ?",
            (past_expiry, deferred_grant["id"]),
        )
        await grants._db.commit()

        # Now try to assign the agent to the project - this should be refused
        resp = await client.post(
            f"/api/projects/{project['id']}/members/assign-agent",
            json={
                "canonical_id": cid,
                "scopes": ["project_tasks"],
                "is_lead": False,
            },
        )

        # The bug: binding silently creates a project-bound grant without expiry
        # This test should FAIL on current dev (it will succeed with empty expires_at)
        # After fix: should be 4xx because deferred grant is expired
        assert resp.status_code == 400, f"Expected 400 when deferred grant is expired, got {resp.status_code}: {resp.text}"
        assert "expired" in resp.text.lower() or "refused" in resp.text.lower()

        # Verify no project-bound grant was created
        project_grants = await grants.list_grants(cid)
        project_grants = [g for g in project_grants if g["project_id"] == project["id"]]
        assert len(project_grants) == 0, "No project-bound grant should be created when deferred grant is expired"

        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_defer_binding_unbounded_without_expiry(
        self, client, monkeypatch, tmp_path
    ):
        """Test 3: Control: deferred row without expiry still binds unbounded.

        When duration_secs is None, the deferred grant should be unbounded.
        The project-bound grant should also be unbounded.
        """
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore

        registry = AgentRegistryStore(tmp_path / "reg-defer-3.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-defer-3.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-defer-3.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-defer-3.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-defer-3")

        project = await pstore.create_project(
            name="Defer Proj 3", slug="defer-proj-3", created_by="u"
        )

        # Create an auth request without duration_secs (unbounded)
        record = await auth_store.create(
            identity_claim="@defer-bot-3",
            framework="defer-cli-3",
            requested_scopes=["project_tasks"],
            requested_skills=None,
            reason="",
            duration_secs=None,  # No bound
            project_id=project["id"],
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "agent_registry_keypair", (priv, pub))
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        # Approve with defer_binding - this creates an unbound grant without expires_at
        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["project_tasks"], "defer_binding": True},
        )
        assert resp.status_code == 200, resp.text
        cid = resp.json()["canonical_id"]

        # Verify the deferred grant has no expiry (unbounded)
        agent_grants = await grants.list_grants(cid)
        assert len(agent_grants) == 1
        deferred_grant = agent_grants[0]
        assert deferred_grant["project_id"] is None  # Unbound
        assert deferred_grant["expires_at"] is None  # No bound

        # Now assign the agent to the project
        resp = await client.post(
            f"/api/projects/{project['id']}/members/assign-agent",
            json={
                "canonical_id": cid,
                "scopes": ["project_tasks"],
                "is_lead": False,
            },
        )
        assert resp.status_code == 200, resp.text

        # Verify the project-bound grant is also unbounded (no expires_at)
        project_grants = await grants.list_grants(cid)
        project_grant = [g for g in project_grants if g["project_id"] == project["id"]]
        assert len(project_grant) == 1
        project_grant = project_grant[0]

        assert project_grant["expires_at"] is None, "project-bound grant should be unbounded when deferred grant has no expiry"

        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()


class TestGlobalGrantDoesNotAffectProjectBinding:
    """Tests for the bug where a global (project_id=None) grant with expires_at
    was mistaken for a deferred project grant.

    BUG A: An expired global decisions_read grant (project_id=None, expires_at in
    the past) should NOT block assign-agent with a project scope like
    project_tasks. The project grant should be created successfully.

    BUG B: A live global decisions_read grant expiring in 1h should NOT cap the
    new project grant's expiry. The project grant should be unbounded (expires_at=None)
    when no request expiry is provided.
    """

    def _parse(self, iso: str) -> datetime:
        return datetime.fromisoformat(iso)

    @pytest.mark.asyncio
    async def test_expired_global_grant_does_not_block_project_binding(
        self, client, monkeypatch, tmp_path
    ):
        """BUG A: Expired global decisions_read grant should not block project binding.

        An agent has a global decisions_read grant with project_id=None and an
        expired expires_at. Assigning the agent to a project with project_tasks
        should succeed (200) and write the project_tasks grant row for the project.
        """
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-global-1.db")
        await registry.init()
        grants = AgentGrantsStore(tmp_path / "grants-global-1.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-global-1.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-global-1")

        project = await pstore.create_project(
            name="Global Proj 1", slug="global-proj-1", created_by="u"
        )

        # Register an agent directly (no auth request flow needed)
        reg_record = await registry.register(
            framework="global-cli-1",
            display_name="@global-bot-1",
            user_id="test",
            origin="external-selfjoin",
            handle="global-bot-1",
        )
        cid = reg_record["canonical_id"]
        await registry.set_status(cid, "active", actor="test")

        # Add a GLOBAL decisions_read grant with an EXPIRED expires_at
        # This is the bug trigger: a global scope (not in _PROJECT_SCOPES) with
        # project_id=None and an expired expires_at
        past_expiry = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        await grants.add_grant(
            cid, "decisions_read",
            project_id=None,  # Global grant
            expires_at=past_expiry,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "agent_registry_keypair", (priv, pub))
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        # Now assign the agent to the project with project_tasks
        # This should SUCCEED (200) despite the expired global grant
        resp = await client.post(
            f"/api/projects/{project['id']}/members/assign-agent",
            json={
                "canonical_id": cid,
                "scopes": ["project_tasks"],
                "is_lead": False,
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        # Verify the project_tasks grant was created for the project
        project_grants = await grants.list_grants(cid)
        project_grant = [g for g in project_grants if g["project_id"] == project["id"] and g["scope"] == "project_tasks"]
        assert len(project_grant) == 1, f"Expected project_tasks grant for project, got {project_grants}"
        project_grant = project_grant[0]

        # The project grant should be unbounded (no expires_at) since no duration was requested
        assert project_grant["expires_at"] is None, "project-bound grant should be unbounded when no request expiry"

        await registry.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_live_global_grant_does_not_cap_project_expiry(
        self, client, monkeypatch, tmp_path
    ):
        """BUG B: Live global decisions_read grant should not cap project grant expiry.

        An agent has a global decisions_read grant with project_id=None and a
        LIVE expires_at (e.g., 1 hour from now). Assigning the agent to a project
        with project_tasks and NO request expiry should result in an UNBOUNDED
        project grant (expires_at=None), NOT capped by the global grant's expiry.
        """
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-global-2.db")
        await registry.init()
        grants = AgentGrantsStore(tmp_path / "grants-global-2.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-global-2.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-global-2")

        project = await pstore.create_project(
            name="Global Proj 2", slug="global-proj-2", created_by="u"
        )

        # Register an agent directly
        reg_record = await registry.register(
            framework="global-cli-2",
            display_name="@global-bot-2",
            user_id="test",
            origin="external-selfjoin",
            handle="global-bot-2",
        )
        cid = reg_record["canonical_id"]
        await registry.set_status(cid, "active", actor="test")

        # Add a GLOBAL decisions_read grant with a LIVE expires_at (1 hour from now)
        future_expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        await grants.add_grant(
            cid, "decisions_read",
            project_id=None,  # Global grant
            expires_at=future_expiry,
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "agent_registry_keypair", (priv, pub))
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        # Now assign the agent to the project with project_tasks (NO expiry requested)
        resp = await client.post(
            f"/api/projects/{project['id']}/members/assign-agent",
            json={
                "canonical_id": cid,
                "scopes": ["project_tasks"],
                "is_lead": False,
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        # Verify the project_tasks grant was created for the project
        project_grants = await grants.list_grants(cid)
        project_grant = [g for g in project_grants if g["project_id"] == project["id"] and g["scope"] == "project_tasks"]
        assert len(project_grant) == 1, f"Expected project_tasks grant for project, got {project_grants}"
        project_grant = project_grant[0]

        # The project grant should be UNBOUNDED (expires_at=None), not capped by the global grant
        assert project_grant["expires_at"] is None, (
            f"project-bound grant should be unbounded (expires_at=None), "
            f"not capped by global grant's expiry. Got: {project_grant['expires_at']}"
        )

        await registry.close()
        await grants.close()
        await pstore.close()

    @pytest.mark.asyncio
    async def test_expired_deferred_project_grant_still_blocks(
        self, client, monkeypatch, tmp_path
    ):
        """Regression: An expired deferred PROJECT grant (project_tasks) still 400s.

        This ensures the fix doesn't break the intended behavior: a deferred
        project_tasks grant with an expired expires_at should still refuse the binding.
        """
        from tinyagentos.agent_registry_store import (
            AgentRegistryStore,
            load_or_create_signing_keypair,
        )
        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.projects.project_store import ProjectStore

        registry = AgentRegistryStore(tmp_path / "reg-regress-1.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-regress-1.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-regress-1.db")
        await grants.init()
        pstore = ProjectStore(tmp_path / "projects-regress-1.db")
        await pstore.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-regress-1")

        project = await pstore.create_project(
            name="Regress Proj 1", slug="regress-proj-1", created_by="u"
        )

        # Create an auth request with duration_secs and defer_binding
        record = await auth_store.create(
            identity_claim="@regress-bot-1",
            framework="regress-cli-1",
            requested_scopes=["project_tasks"],
            requested_skills=None,
            reason="",
            duration_secs=3600,  # 1 hour bound
            project_id=project["id"],
        )

        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(client._transport.app.state, "agent_registry_keypair", (priv, pub))
        monkeypatch.setattr(client._transport.app.state, "project_store", pstore)

        # Approve with defer_binding - creates a deferred project_tasks grant with expires_at
        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["project_tasks"], "defer_binding": True},
        )
        assert resp.status_code == 200, resp.text
        cid = resp.json()["canonical_id"]

        # Verify the deferred grant is a PROJECT scope (project_tasks) with expires_at
        agent_grants = await grants.list_grants(cid)
        assert len(agent_grants) == 1
        deferred_grant = agent_grants[0]
        assert deferred_grant["project_id"] is None
        assert deferred_grant["scope"] == "project_tasks"
        assert deferred_grant["expires_at"] is not None

        # Manually expire the deferred grant
        past_expiry = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        await grants._db.execute(
            "UPDATE agent_grants SET expires_at = ? WHERE id = ?",
            (past_expiry, deferred_grant["id"]),
        )
        await grants._db.commit()

        # Now try to assign the agent to the project - this should be REFUSED (400)
        resp = await client.post(
            f"/api/projects/{project['id']}/members/assign-agent",
            json={
                "canonical_id": cid,
                "scopes": ["project_tasks"],
                "is_lead": False,
            },
        )

        # Should be 400 because the deferred PROJECT grant is expired
        assert resp.status_code == 400, f"Expected 400 when deferred project grant is expired, got {resp.status_code}: {resp.text}"
        assert "expired" in resp.text.lower() or "refused" in resp.text.lower()

        # Verify no project-bound grant was created
        project_grants = await grants.list_grants(cid)
        project_grants = [g for g in project_grants if g["project_id"] == project["id"]]
        assert len(project_grants) == 0, "No project-bound grant should be created when deferred project grant is expired"

        await registry.close()
        await auth_store.close()
        await grants.close()
        await pstore.close()