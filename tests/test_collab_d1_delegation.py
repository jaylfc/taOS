"""Tests for cross-user collab D1 — agent delegation handshake + sponsor_contact_id."""

from __future__ import annotations

import json
import time

import pytest


# ---------------------------------------------------------------------------
# Scope denylist tests
# ---------------------------------------------------------------------------

class TestDelegationScopeValidation:
    def test_hard_denies_files_write(self):
        from tinyagentos.delegation_handler import validate_delegation_scopes

        tier, elevated, denied = validate_delegation_scopes(
            ["a2a_send", "files_write", "project_tasks"]
        )
        assert "files_write" in denied
        assert "files_write" not in tier
        assert "a2a_send" in tier
        assert "project_tasks" in tier
        assert elevated == []

    def test_hard_denies_decisions_write(self):
        from tinyagentos.delegation_handler import validate_delegation_scopes

        tier, elevated, denied = validate_delegation_scopes(
            ["decisions_write", "canvas_read"]
        )
        assert "decisions_write" in denied
        assert "decisions_write" not in tier
        assert "canvas_read" in tier
        assert elevated == []

    def test_allows_default_scopes(self):
        from tinyagentos.delegation_handler import validate_delegation_scopes
        from tinyagentos.delegation_handler import SPONSORED_DEFAULT_SCOPES

        tier, elevated, denied = validate_delegation_scopes(list(SPONSORED_DEFAULT_SCOPES))
        assert len(denied) == 0
        assert elevated == []
        assert set(tier) == SPONSORED_DEFAULT_SCOPES

    def test_empty_request_returns_no_scopes(self):
        from tinyagentos.delegation_handler import validate_delegation_scopes

        tier, elevated, denied = validate_delegation_scopes([])
        assert tier == []
        assert elevated == []
        assert denied == []


# ---------------------------------------------------------------------------
# Envelope body validation tests
# ---------------------------------------------------------------------------

class TestDelegationEnvelopeValidation:
    def test_valid_envelope_body(self):
        from tinyagentos.delegation_handler import _validate_delegation_envelope_body

        ok, err, parsed = _validate_delegation_envelope_body({
            "agent_slug": "grok-taos",
            "display_name": "Grok TAOS",
            "requested_scopes": ["a2a_send", "project_tasks"],
            "project_id": "prj-123",
        })
        assert ok is True
        assert err == ""
        assert parsed is not None
        assert parsed["agent_slug"] == "grok-taos"
        assert parsed["display_name"] == "Grok TAOS"
        assert parsed["project_id"] == "prj-123"

    def test_missing_field(self):
        from tinyagentos.delegation_handler import _validate_delegation_envelope_body

        ok, err, parsed = _validate_delegation_envelope_body({
            "agent_slug": "grok-taos",
            "display_name": "Grok TAOS",
        })
        assert ok is False
        assert "missing required field" in err
        assert parsed is None

    def test_empty_scopes(self):
        from tinyagentos.delegation_handler import _validate_delegation_envelope_body

        ok, err, parsed = _validate_delegation_envelope_body({
            "agent_slug": "grok-taos",
            "display_name": "Grok TAOS",
            "requested_scopes": [],
            "project_id": "prj-123",
        })
        assert ok is False
        assert "must not be empty" in err

    def test_scopes_not_a_list(self):
        from tinyagentos.delegation_handler import _validate_delegation_envelope_body

        ok, err, parsed = _validate_delegation_envelope_body({
            "agent_slug": "grok-taos",
            "display_name": "Grok TAOS",
            "requested_scopes": "not-a-list",
            "project_id": "prj-123",
        })
        assert ok is False
        assert "must be a list" in err

    def test_empty_agent_slug(self):
        from tinyagentos.delegation_handler import _validate_delegation_envelope_body

        ok, err, parsed = _validate_delegation_envelope_body({
            "agent_slug": "",
            "display_name": "Grok TAOS",
            "requested_scopes": ["a2a_send"],
            "project_id": "prj-123",
        })
        assert ok is False
        assert "must be a non-empty string" in err


# ---------------------------------------------------------------------------
# Sponsor list / set tests
# ---------------------------------------------------------------------------

class TestSponsorRegistryMethods:
    @pytest.mark.asyncio
    async def test_list_by_sponsor_empty(self, tmp_path):
        from tinyagentos.agent_registry_store import AgentRegistryStore

        db_path = tmp_path / "test_registry.db"
        store = AgentRegistryStore(db_path)
        await store.init()

        sponsored = await store.list_by_sponsor("hub:hogne")
        assert sponsored == []
        await store.close()

    @pytest.mark.asyncio
    async def test_list_by_sponsor_with_registration(self, tmp_path):
        from tinyagentos.agent_registry_store import AgentRegistryStore

        db_path = tmp_path / "test_registry.db"
        store = AgentRegistryStore(db_path)
        await store.init()

        # Register with sponsor
        reg = await store.register(
            framework="test",
            display_name="Sponsored Agent",
            user_id="user-1",
            origin="external-selfjoin",
            handle="sponsored-agent",
            sponsor_contact_id="hub:hogne",
        )
        canonical_id = reg["canonical_id"]
        # List by sponsor
        sponsored = await store.list_by_sponsor("hub:hogne")
        assert len(sponsored) == 1
        assert sponsored[0]["sponsor_contact_id"] == "hub:hogne"

        # List by different sponsor — empty
        other = await store.list_by_sponsor("hub:other")
        assert other == []
        await store.close()

    @pytest.mark.asyncio
    async def test_list_by_sponsor_filter_by_status(self, tmp_path):
        from tinyagentos.agent_registry_store import AgentRegistryStore

        db_path = tmp_path / "test_registry.db"
        store = AgentRegistryStore(db_path)
        await store.init()

        await store.register(
            framework="test",
            display_name="Active Sponsored",
            user_id="user-1",
            origin="external-selfjoin",
            handle="active-sponsor",
            sponsor_contact_id="hub:hogne",
        )
        # Only active agents
        active = await store.list_by_sponsor("hub:hogne", status="active")
        assert len(active) == 0  # external-selfjoin starts pending

        # With no status filter, shows all
        all_sponsored = await store.list_by_sponsor("hub:hogne")
        assert len(all_sponsored) == 1  # pending agent
        await store.close()

    @pytest.mark.asyncio
    async def test_set_sponsor(self, tmp_path):
        from tinyagentos.agent_registry_store import AgentRegistryStore

        db_path = tmp_path / "test_registry.db"
        store = AgentRegistryStore(db_path)
        await store.init()

        reg = await store.register(
            framework="test",
            display_name="Agent",
            user_id="user-1",
            origin="taos-deployed",
            handle="test-agent",
        )
        cid = reg["canonical_id"]

        # Initially NULL
        agent = await store.get(cid)
        assert agent["sponsor_contact_id"] is None

        # Set sponsor
        await store.set_sponsor(cid, "hub:hogne")
        agent = await store.get(cid)
        assert agent["sponsor_contact_id"] == "hub:hogne"

        # Clear sponsor
        await store.set_sponsor(cid, None)
        agent = await store.get(cid)
        assert agent["sponsor_contact_id"] is None
        await store.close()

    @pytest.mark.asyncio
    async def test_migration_adds_sponsor_column(self, tmp_path):
        from tinyagentos.agent_registry_store import _migration_v7_add_sponsor_contact_id

        import aiosqlite

        db_path = tmp_path / "test_registry.db"
        # Simulate pre-migration DB with agent_registry but no sponsor_contact_id
        conn = await aiosqlite.connect(db_path)
        await conn.execute("""
            CREATE TABLE agent_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_id TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                framework TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                origin TEXT NOT NULL DEFAULT 'taos-deployed',
                handle TEXT NOT NULL DEFAULT '',
                role TEXT,
                capabilities TEXT NOT NULL DEFAULT '[]',
                created_ts TEXT NOT NULL,
                revoked_at TEXT,
                status TEXT NOT NULL DEFAULT 'active'
            )
        """)
        await conn.commit()

        # Migration should add the column
        await _migration_v7_add_sponsor_contact_id(conn)

        # Verify column exists
        cols = {row[1] for row in await (await conn.execute(
            "PRAGMA table_info(agent_registry)"
        )).fetchall()}
        assert "sponsor_contact_id" in cols

        # Idempotent
        await _migration_v7_add_sponsor_contact_id(conn)

        await conn.close()


# ---------------------------------------------------------------------------
# Invite metadata tests
# ---------------------------------------------------------------------------

class TestInviteMetadata:
    @pytest.mark.asyncio
    async def test_row_to_dict_deserializes_metadata(self, tmp_path):
        from tinyagentos.projects.invite_store import ProjectInviteStore

        db_path = tmp_path / "test_invites.db"
        store = ProjectInviteStore(db_path)
        await store.init()

        metadata = {
            "kind": "delegation_sponsored",
            "sponsor_contact_id": "hub:hogne",
            "agent_slug": "grok-taos",
        }

        result = await store.mint(
            project_id="prj-test",
            scopes=["project_tasks", "a2a_send"],
            approval_mode="auto",
            check_interval_secs=1800,
            created_by="hub:hogne",
            metadata=metadata,
        )

        invite_id = result["record"]["invite_id"]
        invite = await store.get(invite_id)
        # metadata should be deserialized to a dict
        assert isinstance(invite["metadata"], dict)
        assert invite["metadata"]["kind"] == "delegation_sponsored"
        assert invite["metadata"]["sponsor_contact_id"] == "hub:hogne"
        await store.close()


# ---------------------------------------------------------------------------
# Regression tests for CodeRabbit findings (#2048) — rewritten to test
# through real production paths (N1 fix).
# ---------------------------------------------------------------------------


class TestMintReturnShape:
    """🔴 CRITICAL: delegation_handler must read invite_id from correct path.

    Tests process_delegation_request end-to-end so the handler's
    invite["record"]["invite_id"] path is exercised — not just the store.
    """

    @pytest.mark.asyncio
    async def test_process_delegation_request_returns_invite_id(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock
        from tinyagentos.delegation_handler import process_delegation_request
        from tinyagentos.projects.invite_store import ProjectInviteStore

        db_path = tmp_path / "test_invites.db"
        store = ProjectInviteStore(db_path)
        await store.init()

        # Project store mock: member check → True, auto_approve → True
        project_store = AsyncMock()
        project_store.is_project_member.return_value = True
        project_store.get_project_setting.return_value = True

        request = MagicMock()
        request.app.state.project_store = project_store
        request.app.state.project_invites = store

        envelope_body = {
            "agent_slug": "sponsored-agent",
            "display_name": "Sponsored Agent",
            "requested_scopes": ["a2a_send", "project_tasks"],
            "project_id": "prj-test",
        }

        result = await process_delegation_request(
            request,
            contact_id="hub:sponsor",
            envelope_body=envelope_body,
        )

        # Handler must return invite_id from invite["record"]["invite_id"]
        # (not invite["id"] or any flat key — this was the A1 bug).
        assert result["status"] == "approved"
        invite_id = result["invite_id"]
        assert isinstance(invite_id, str)
        assert len(invite_id) >= 20  # token_urlsafe id (PIN-free invite)

        # The invite must actually exist in the store and be redeemable.
        invite_row = await store.get(invite_id)
        assert invite_row is not None
        assert invite_row["pin_required"] == 0  # pin_required=False
        assert invite_row["status"] == "pending"

        await store.close()

    @pytest.mark.asyncio
    async def test_mint_pin_required_false_persisted_through_handler(self, tmp_path):
        """pin_required=False must be a TOP-LEVEL mint arg (not metadata)
        so the column is actually written (N1+A2 regression guard)."""
        from unittest.mock import AsyncMock, MagicMock
        from tinyagentos.delegation_handler import process_delegation_request
        from tinyagentos.projects.invite_store import ProjectInviteStore

        db_path = tmp_path / "test_invites.db"
        store = ProjectInviteStore(db_path)
        await store.init()

        project_store = AsyncMock()
        project_store.is_project_member.return_value = True
        project_store.get_project_setting.return_value = True

        request = MagicMock()
        request.app.state.project_store = project_store
        request.app.state.project_invites = store

        result = await process_delegation_request(
            request,
            contact_id="hub:sponsor",
            envelope_body={
                "agent_slug": "grok-taos",
                "display_name": "Grok TAOS",
                "requested_scopes": ["a2a_send"],
                "project_id": "prj-pin",
            },
        )
        assert result["status"] == "approved"

        invite = await store.get(result["invite_id"])
        assert invite["pin_required"] == 0  # persisted as 0

        # Redeem with empty pin — must succeed (A2 fix: pin_required=False
        # skips PIN verification).
        record = await store.redeem(result["invite_id"], "")
        assert record["status"] == "claimed"
        await store.close()


class TestCascadeSponsorRevokeFailClosed:
    """🟠 MAJOR: project-scoped revoke with missing project_store must fail closed."""

    @pytest.mark.asyncio
    async def test_cascade_with_project_id_and_no_store_fails(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock
        from tinyagentos.delegation_handler import cascade_sponsor_revoke

        # Mock request with registry but NO project_store
        request = MagicMock()
        mock_registry = AsyncMock()
        mock_registry.list_by_sponsor.return_value = [
            {"canonical_id": "agent-1", "sponsor_contact_id": "hub:sponsor"}
        ]
        request.app.state.agent_registry = mock_registry
        request.app.state.project_store = None  # missing!

        result = await cascade_sponsor_revoke(
            request,
            contact_id="hub:sponsor",
            project_id="prj-1",
        )

        # Must fail closed — project_store absent with project_id set is an error
        assert result["status"] == "error"
        assert "project store not available" in result["error"]


class TestProjectStoreSettingsGuard:
    """🟡 MINOR: get_project_setting must handle non-dict settings."""

    @pytest.mark.asyncio
    async def test_get_setting_non_dict_returns_default(self, tmp_path):
        from tinyagentos.projects.project_store import ProjectStore

        db_path = tmp_path / "test_settings.db"
        store = ProjectStore(db_path)
        await store.init()

        # Create a project with settings as a list (malformed)
        import json, time
        await store._db.execute(
            "INSERT INTO projects (id, name, slug, created_by, created_at, updated_at, settings) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("prj-bad", "Bad Project", "bad-project", "test-user",
             time.time(), time.time(), json.dumps(["not-a-dict"])),
        )
        await store._db.commit()

        result = await store.get_project_setting("prj-bad", "some_key", default="fallback")
        assert result == "fallback"
        await store.close()


class TestSetSponsorGuard:
    """🟡 MINOR: set_sponsor must not re-parent an existing sponsored identity.

    Tests the guard in AgentRegistryStore.set_sponsor directly — the guard
    is now IN the production code, so direct calls ARE the real path (N1 fix).
    """

    @pytest.mark.asyncio
    async def test_set_sponsor_reparents_to_different_sponsor(self, tmp_path):
        """Re-parenting an identity to a different sponsor (cross-project
        reuse via handle) must now SUCCEED — the old immutability guard
        was removed so cascade_sponsor_revoke(B) can find identities
        sponsored by B even when the handle was first sponsored by A."""
        from tinyagentos.agent_registry_store import AgentRegistryStore

        db_path = tmp_path / "test_registry.db"
        store = AgentRegistryStore(db_path)
        await store.init()

        reg = await store.register(
            framework="test",
            display_name="Agent With Sponsor",
            user_id="user-1",
            origin="external-selfjoin",
            handle="sponsored-reparent",
            sponsor_contact_id="hub:sponsor-a",
        )
        cid = reg["canonical_id"]

        # Re-parent to a different sponsor — must succeed now.
        result = await store.set_sponsor(cid, "hub:sponsor-b")
        assert result is not None
        assert result["sponsor_contact_id"] == "hub:sponsor-b"

        # Verify the store agrees.
        agent = await store.get(cid)
        assert agent["sponsor_contact_id"] == "hub:sponsor-b"
        await store.close()

    @pytest.mark.asyncio
    async def test_set_sponsor_noop_on_same_sponsor(self, tmp_path):
        """Setting the same sponsor that is already set must be a no-op
        (returns the current record without a write)."""
        from tinyagentos.agent_registry_store import AgentRegistryStore

        db_path = tmp_path / "test_registry.db"
        store = AgentRegistryStore(db_path)
        await store.init()

        reg = await store.register(
            framework="test",
            display_name="Agent",
            user_id="user-1",
            origin="external-selfjoin",
            handle="same-sponsor",
            sponsor_contact_id="hub:sponsor-a",
        )
        cid = reg["canonical_id"]

        result = await store.set_sponsor(cid, "hub:sponsor-a")
        assert result is not None
        assert result["sponsor_contact_id"] == "hub:sponsor-a"
        await store.close()

    @pytest.mark.asyncio
    async def test_set_sponsor_clearing_allowed(self, tmp_path):
        """Clearing sponsor via set_sponsor(cid, None) must still work
        (revoke cascades depend on it)."""
        from tinyagentos.agent_registry_store import AgentRegistryStore

        db_path = tmp_path / "test_registry.db"
        store = AgentRegistryStore(db_path)
        await store.init()

        reg = await store.register(
            framework="test",
            display_name="Agent",
            user_id="user-1",
            origin="external-selfjoin",
            handle="clear-test",
            sponsor_contact_id="hub:sponsor",
        )
        cid = reg["canonical_id"]

        # Set initial sponsor
        agent = await store.get(cid)
        assert agent["sponsor_contact_id"] == "hub:sponsor"

        # Clear with None — must work
        await store.set_sponsor(cid, None)
        agent = await store.get(cid)
        assert agent["sponsor_contact_id"] is None
        await store.close()


# ---------------------------------------------------------------------------
# E2E: mint through delegation_handler → redeem (A1 + A2 killed together)
# ---------------------------------------------------------------------------

class TestDelegationE2E:
    """End-to-end: process_delegation_request with auto_approve on, then
    redeem the resulting invite.  This single test proves A1 (invite_id
    is read from the correct path) and A2 (pin_required=False skips PIN
    verification) together."""

    @pytest.mark.asyncio
    async def test_mint_and_redeem_e2e(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock
        from tinyagentos.delegation_handler import process_delegation_request
        from tinyagentos.projects.invite_store import (
            ProjectInviteStore,
            InviteAlreadyRedeemedError,
        )

        db_path = tmp_path / "test_invites.db"
        store = ProjectInviteStore(db_path)
        await store.init()

        # Auto-approve path: member check passes, auto_approve_delegation=True
        project_store = AsyncMock()
        project_store.is_project_member.return_value = True
        project_store.get_project_setting.return_value = True

        request = MagicMock()
        request.app.state.project_store = project_store
        request.app.state.project_invites = store

        # Step 1: process delegation request (auto-approve)
        result = await process_delegation_request(
            request,
            contact_id="hub:hogne",
            envelope_body={
                "agent_slug": "grok-taos",
                "display_name": "Grok TAOS",
                "requested_scopes": ["a2a_send", "project_tasks", "files_write"],
                "project_id": "prj-e2e",
            },
        )

        assert result["status"] == "approved"
        invite_id = result["invite_id"]
        assert len(invite_id) >= 20  # token_urlsafe id (PIN-free invite)

        # Step 2: verify invite in store
        invite = await store.get(invite_id)
        assert invite["status"] == "pending"
        assert invite["pin_required"] == 0
        scopes = invite["scopes"]
        assert "a2a_send" in scopes
        assert "project_tasks" in scopes
        assert "files_write" not in scopes  # hard-denied

        # Step 3: redeem with empty pin (pin_required=False)
        record = await store.redeem(invite_id, "")
        assert record["status"] == "claimed"

        # Step 4: already claimed → error, not PIN error (A2 proof)
        with pytest.raises(InviteAlreadyRedeemedError):
            await store.redeem(invite_id, "9999")

        await store.close()


# ---------------------------------------------------------------------------
# Manual approval path (the WIRED path): approving a collab_delegation_gate
# decision must actually mint the sponsored invite via complete_delegation_approval.
# ---------------------------------------------------------------------------

class TestManualApprovalWiring:
    """Regression guard for the dead manual path: a delegation request that
    lands in a Decisions card (kind collab_delegation_gate) must, on approval,
    actually mint the sponsored project invite.  Previously nothing read the
    decision's metadata kind, so approval was a no-op."""

    @pytest.mark.asyncio
    async def test_approve_delegation_gate_mints_invite(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock

        from tinyagentos.decisions.decision_store import DecisionStore
        from tinyagentos.projects.invite_store import ProjectInviteStore
        from tinyagentos.routes.decisions import AnswerIn, answer_decision

        invite_store = ProjectInviteStore(tmp_path / "invites.db")
        await invite_store.init()
        decision_store = DecisionStore(tmp_path / "decisions.db")
        await decision_store.init()

        # The contact must still be a human collaborator at approval time.
        project_store = AsyncMock()
        project_store.is_project_member.return_value = True

        request = MagicMock()
        request.app.state.project_invites = invite_store
        request.app.state.project_store = project_store
        request.app.state.decision_store = decision_store

        decision = await decision_store.create(
            from_agent="hub:sponsor",
            question=(
                "hub:sponsor wants to delegate agent 'Grok TAOS' (grok-taos) "
                "to this project. Requested scopes: a2a_send, project_tasks."
            ),
            type="approve_deny",
            priority="blocking",
            project_id="prj-test",
            user_id="admin-user",
            metadata={
                "kind": "collab_delegation_gate",
                "contact_id": "hub:sponsor",
                "agent_slug": "grok-taos",
                "display_name": "Grok TAOS",
                "granted_scopes": ["a2a_send", "project_tasks"],
                "denied_scopes": [],
                "project_id": "prj-test",
            },
        )

        user = MagicMock()
        user.is_admin = True
        user.user_id = "admin-user"

        updated = await answer_decision(
            decision["id"], AnswerIn(value="approve"), request, user
        )

        assert updated["status"] == "answered"

        # The delegation must have actually minted a project invite.
        invites = await invite_store.list_for_project("prj-test")
        assert len(invites) == 1
        minted = invites[0]
        assert minted["display_name"] == "Grok TAOS"
        assert minted["created_by"] == "hub:sponsor"
        scopes = minted["scopes"]
        assert "a2a_send" in scopes
        assert "project_tasks" in scopes

        await invite_store.close()
        await decision_store.close()

    @pytest.mark.asyncio
    async def test_deny_delegation_gate_mints_nothing(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock

        from tinyagentos.decisions.decision_store import DecisionStore
        from tinyagentos.projects.invite_store import ProjectInviteStore
        from tinyagentos.routes.decisions import AnswerIn, answer_decision

        invite_store = ProjectInviteStore(tmp_path / "invites.db")
        await invite_store.init()
        decision_store = DecisionStore(tmp_path / "decisions.db")
        await decision_store.init()

        project_store = AsyncMock()
        project_store.is_project_member.return_value = True

        request = MagicMock()
        request.app.state.project_invites = invite_store
        request.app.state.project_store = project_store
        request.app.state.decision_store = decision_store

        decision = await decision_store.create(
            from_agent="hub:sponsor",
            question="delegation gate",
            type="approve_deny",
            priority="blocking",
            project_id="prj-test",
            user_id="admin-user",
            metadata={
                "kind": "collab_delegation_gate",
                "contact_id": "hub:sponsor",
                "agent_slug": "grok-taos",
                "display_name": "Grok TAOS",
                "granted_scopes": ["a2a_send"],
                "denied_scopes": [],
                "project_id": "prj-test",
            },
        )

        user = MagicMock()
        user.is_admin = True
        user.user_id = "admin-user"

        updated = await answer_decision(
            decision["id"], AnswerIn(value="deny"), request, user
        )

        assert updated["status"] == "answered"
        invites = await invite_store.list_for_project("prj-test")
        assert invites == []

        await invite_store.close()
        await decision_store.close()


# ---------------------------------------------------------------------------
# Security blockers (jaylfc Aug 17 14:16 re-review) — red-first regression tests.
# ---------------------------------------------------------------------------


class TestSponsoredInviteMetadata:
    """Blockers #1 — sponsored-invite metadata must carry sponsor_contact_id."""

    @pytest.mark.asyncio
    async def test_process_delegation_request_writes_sponsor_metadata(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock
        from tinyagentos.delegation_handler import process_delegation_request
        from tinyagentos.projects.invite_store import ProjectInviteStore

        store = ProjectInviteStore(tmp_path / "invites.db")
        await store.init()

        project_store = AsyncMock()
        project_store.is_project_member.return_value = True
        project_store.get_project_setting.return_value = True

        request = MagicMock()
        request.app.state.project_store = project_store
        request.app.state.project_invites = store

        result = await process_delegation_request(
            request,
            contact_id="hub:sponsor",
            envelope_body={
                "agent_slug": "grok-taos",
                "display_name": "Grok TAOS",
                "requested_scopes": ["a2a_send"],
                "project_id": "prj-test",
            },
        )
        assert result["status"] == "approved"

        invite = await store.get(result["invite_id"])
        assert invite["metadata"]["sponsor_contact_id"] == "hub:sponsor"
        await store.close()


class TestScopeTierVsElevated:
    """Blockers #2 — tier (allowlist) vs elevated scopes must be split."""

    def test_validate_splits_tier_from_elevated(self):
        from tinyagentos.delegation_handler import validate_delegation_scopes

        tier, elevated, denied = validate_delegation_scopes(
            ["a2a_send", "tools_execute"]
        )
        assert "a2a_send" in tier
        assert "tools_execute" in elevated
        assert "tools_execute" not in tier
        assert denied == []

    @pytest.mark.asyncio
    async def test_auto_approve_never_grants_elevated(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock
        from tinyagentos.delegation_handler import process_delegation_request
        from tinyagentos.projects.invite_store import ProjectInviteStore
        from tinyagentos.decisions.decision_store import DecisionStore

        store = ProjectInviteStore(tmp_path / "invites.db")
        await store.init()
        decision_store = DecisionStore(tmp_path / "decisions.db")
        await decision_store.init()

        project_store = AsyncMock()
        project_store.is_project_member.return_value = True
        project_store.get_project_setting.return_value = True  # auto-approve ON

        request = MagicMock()
        request.app.state.project_store = project_store
        request.app.state.project_invites = store
        request.app.state.decision_store = decision_store

        result = await process_delegation_request(
            request,
            contact_id="hub:sponsor",
            envelope_body={
                "agent_slug": "grok-taos",
                "display_name": "Grok TAOS",
                "requested_scopes": ["a2a_send", "tools_execute"],
                "project_id": "prj-test",
            },
        )
        # An elevated scope must never be auto-granted: the request must route
        # to manual approval, not mint an invite containing tools_execute.
        assert result["status"] == "pending_approval"
        # And no invite may have been minted on the auto path.
        invites = await store.list_for_project("prj-test")
        assert invites == []
        await store.close()
        await decision_store.close()


class TestApprovalMembershipFailClosed:
    """Blockers #3 — approval-time membership re-check must fail closed."""

    @pytest.mark.asyncio
    async def test_complete_approval_fails_closed_without_project_store(self, tmp_path):
        from unittest.mock import MagicMock
        from tinyagentos.delegation_handler import complete_delegation_approval
        from tinyagentos.projects.invite_store import ProjectInviteStore

        store = ProjectInviteStore(tmp_path / "invites.db")
        await store.init()

        request = MagicMock()
        request.app.state.project_store = None  # missing -> must fail closed
        request.app.state.project_invites = store

        result = await complete_delegation_approval(
            request,
            decision_metadata={
                "contact_id": "hub:sponsor",
                "agent_slug": "grok-taos",
                "display_name": "Grok TAOS",
                "granted_scopes": ["a2a_send"],
                "project_id": "prj-test",
            },
        )
        # Failing closed: a missing project_store must yield an error, not a
        # silently minted invite (the old code failed OPEN and minted anyway).
        assert result["status"] == "error"
        assert "project store" in result["error"]
        # And no invite may have been minted.
        invites = await store.list_for_project("prj-test")
        assert invites == []
        await store.close()


class TestSetSponsorAtomicGuard:
    """Blockers #4 — set_sponsor immutability must be enforced in the UPDATE
    predicate, not a read->check->write race."""

    @pytest.mark.asyncio
    async def test_concurrent_set_sponsor_reparents_once(self, tmp_path):
        import asyncio

        from tinyagentos.agent_registry_store import AgentRegistryStore

        db_path = tmp_path / "registry.db"
        # Two stores over the same file = two connections = a real race window.
        store_a = AgentRegistryStore(db_path)
        await store_a.init()
        store_b = AgentRegistryStore(db_path)
        await store_b.init()

        reg = await store_a.register(
            framework="test",
            display_name="Race Agent",
            user_id="user-1",
            origin="external-selfjoin",
            handle="race-agent",
        )
        cid = reg["canonical_id"]

        # Concurrent re-parent attempts: whichever wins, the identity ends up
        # with exactly one sponsor and cannot be re-parented a second time.
        await asyncio.gather(
            store_a.set_sponsor(cid, "hub:A"),
            store_b.set_sponsor(cid, "hub:B"),
        )

        agent = await store_a.get(cid)
        assert agent["sponsor_contact_id"] in ("hub:A", "hub:B")

        # A later re-parent to a third sponsor must be refused.
        await store_a.set_sponsor(cid, "hub:C")
        agent = await store_a.get(cid)
        assert agent["sponsor_contact_id"] != "hub:C"
        await store_a.close()
        await store_b.close()


class TestUnassignAgentTasks:
    """Blockers #5 — cascade revoke must release claimed tasks via the store's
    real release path (claimed -> open), not a non-existent in_progress/assignee
    vocabulary."""

    @pytest.mark.asyncio
    async def test_unassign_releases_claimed_task(self, tmp_path):
        from tinyagentos.delegation_handler import _unassign_agent_tasks
        from tinyagentos.projects.task_store import ProjectTaskStore

        store = ProjectTaskStore(tmp_path / "tasks.db")
        await store.init()

        task = await store.create_task("prj-1", "Fix bug", "alice")
        tid = task["id"]
        claimed = await store.claim_task(tid, "agent-1")
        assert claimed is True

        count = await _unassign_agent_tasks(store, "agent-1", project_id="prj-1")
        assert count == 1

        fetched = await store.get_task(tid)
        assert fetched["status"] == "open"
        assert fetched["claimed_by"] is None
        await store.close()


class TestPinFreeInviteIdEntropy:
    """Blockers #6 — PIN-free invite IDs must be high-entropy, not 6 digits."""

    @pytest.mark.asyncio
    async def test_pin_free_invite_id_is_high_entropy(self, tmp_path):
        from tinyagentos.projects.invite_store import ProjectInviteStore

        store = ProjectInviteStore(tmp_path / "invites.db")
        await store.init()

        result = await store.mint(
            project_id="prj-1",
            scopes=["a2a_send"],
            approval_mode="auto",
            check_interval_secs=1800,
            created_by="u",
            pin_required=False,
        )
        invite_id = result["record"]["invite_id"]
        # token_urlsafe-class id, not a guessable 6-digit numeric credential.
        assert len(invite_id) >= 20
        assert not invite_id.isdigit()
        await store.close()

