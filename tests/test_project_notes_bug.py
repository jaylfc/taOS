"""Test that project_notes scope requires project_id binding."""

import pytest


class TestProjectNotesScopeBinding:
    """Verify project_notes scope requires project_id binding (CR-Critical finding #2320)."""

    @pytest.mark.asyncio
    async def test_approve_project_notes_without_project_id_is_400(
        self, client, monkeypatch, tmp_path
    ):
        """project_notes granted without a project_id must be rejected (400),
        since project_notes now requires project binding like project_tasks.

        This was the CR-Critical finding (#2320): project_notes was not in
        _PROJECT_SCOPES, so it could be granted unbound (no project_id). That did
        NOT make the grant usable cross-project: check_agent_scope_for_project
        (agent_token_auth.py:205) only authorizes a grant whose project_id EQUALS
        the requested project, and the four project_notes routes are all
        project-bound (the project_id comes from the URL), so an unbound grant
        matched nothing and authorized nothing. The real defect was that the
        operator saw a successful approval that minted an INERT grant -- the
        agent silently had no access at all, not access to every project. After the
        fix, project_notes is in _PROJECT_SCOPES and approving it without a
        project_id is rejected with 400 instead of minting an inert grant.
        """
        from tinyagentos.routes.agent_auth_requests import _PROJECT_SCOPES, VALID_SCOPES

        assert "project_notes" in VALID_SCOPES

        from tinyagentos.auth_requests_store import AuthRequestsStore
        from tinyagentos.agent_grants_store import AgentGrantsStore
        from tinyagentos.agent_registry_store import AgentRegistryStore, load_or_create_signing_keypair

        registry = AgentRegistryStore(tmp_path / "reg-test.db")
        await registry.init()
        auth_store = AuthRequestsStore(tmp_path / "auth-test.db")
        await auth_store.init()
        grants = AgentGrantsStore(tmp_path / "grants-test.db")
        await grants.init()
        priv, pub = load_or_create_signing_keypair(tmp_path / "keys-test")

        # Register agent with a unique handle
        reg = await registry.register(
            framework="openclaw", display_name="test-bot-diff", user_id="u",
            origin="external-selfjoin", handle="test-bot-diff",
        )
        await registry.set_status(reg["canonical_id"], "active")

        # Create auth request with project_notes but NO project_id
        record = await auth_store.create(
            identity_claim="@test-bot-unique", framework="openclaw",
            requested_scopes=["project_notes"],
            requested_skills=None,
            reason="",
            duration_secs=None,
            project_id=None,
        )

        # Monkeypatch stores onto client app state
        monkeypatch.setattr(client._transport.app.state, "agent_registry", registry)
        monkeypatch.setattr(client._transport.app.state, "auth_requests", auth_store)
        monkeypatch.setattr(client._transport.app.state, "agent_grants", grants)
        monkeypatch.setattr(
            client._transport.app.state, "agent_registry_keypair", (priv, pub)
        )

        # Try to approve without project_id
        # After fix: this MUST 400 because project_notes requires project_id
        resp = await client.post(
            f"/api/agents/auth-requests/{record['id']}/approve",
            json={"granted_scopes": ["project_notes"]},
        )

        assert resp.status_code == 400, (
            f"project_notes approved without project_id must be rejected with 400, "
            f"got {resp.status_code}. Response: {resp.text}"
        )

        # The rejection must also leave no side effects: the whole point of the
        # fix is that no inert grant reaches the store.
        assert await grants.list_grants(reg["canonical_id"]) == [], (
            "a rejected approval must not mint any grant"
        )

        await registry.close()
        await auth_store.close()
        await grants.close()

    @pytest.mark.asyncio
    async def test_project_notes_is_in_project_scopes(
        self, client, monkeypatch, tmp_path
    ):
        """Verify project_notes is now in _PROJECT_SCOPES after the fix."""
        from tinyagentos.routes.agent_auth_requests import _PROJECT_SCOPES

        assert "project_notes" in _PROJECT_SCOPES, (
            "project_notes should be in _PROJECT_SCOPES after the fix"
        )
