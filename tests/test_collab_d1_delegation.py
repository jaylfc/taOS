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

        granted, denied = validate_delegation_scopes(
            ["a2a_send", "files_write", "project_tasks"]
        )
        assert "files_write" in denied
        assert "files_write" not in granted
        assert "a2a_send" in granted
        assert "project_tasks" in granted

    def test_hard_denies_decisions_write(self):
        from tinyagentos.delegation_handler import validate_delegation_scopes

        granted, denied = validate_delegation_scopes(
            ["decisions_write", "canvas_read"]
        )
        assert "decisions_write" in denied
        assert "decisions_write" not in granted
        assert "canvas_read" in granted

    def test_allows_default_scopes(self):
        from tinyagentos.delegation_handler import validate_delegation_scopes
        from tinyagentos.delegation_handler import SPONSORED_DEFAULT_SCOPES

        granted, denied = validate_delegation_scopes(list(SPONSORED_DEFAULT_SCOPES))
        assert len(denied) == 0
        assert set(granted) == SPONSORED_DEFAULT_SCOPES

    def test_empty_request_returns_no_scopes(self):
        from tinyagentos.delegation_handler import validate_delegation_scopes

        granted, denied = validate_delegation_scopes([])
        assert granted == []
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
        from tinyagentos.agent_registry_store import _migration_v5_add_sponsor_contact_id

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
        await _migration_v5_add_sponsor_contact_id(conn)

        # Verify column exists
        cols = {row[1] for row in await (await conn.execute(
            "PRAGMA table_info(agent_registry)"
        )).fetchall()}
        assert "sponsor_contact_id" in cols

        # Idempotent
        await _migration_v5_add_sponsor_contact_id(conn)

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
