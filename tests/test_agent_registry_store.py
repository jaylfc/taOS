import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    _assert_valid_transition,
    _b64url_decode,
    _b64url_encode,
    _migration_v2_strip_at_display_name,
    _row_to_dict,
    _slugify,
    load_or_create_signing_keypair,
    mint_canonical_id,
    mint_registry_token,
    verify_registry_token,
    VALID_STATUSES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store(tmp_path):
    """Fresh AgentRegistryStore backed by a temp sqlite file."""
    s = AgentRegistryStore(tmp_path / "agent_registry.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def signing_keypair(tmp_path):
    """Generate an Ed25519 keypair via the store helper."""
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys")
    return priv, pub


# ---------------------------------------------------------------------------
# Module-level pure-function tests
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        assert _slugify("My Agent") == "my-agent"

    def test_mixed_case(self):
        assert _slugify("TaOS Agent") == "taos-agent"

    def test_special_chars(self):
        assert _slugify("agent@v2.0!") == "agent-v2-0"

    def test_empty_string(self):
        assert _slugify("") == "agent"

    def test_only_special_chars(self):
        assert _slugify("!@#$%") == "agent"

    def test_leading_trailing_dashes(self):
        assert _slugify("  hello  ") == "hello"

    def test_multiple_spaces(self):
        assert _slugify("a  b  c") == "a-b-c"


class TestMintCanonicalId:
    def test_format(self):
        ts = datetime(2026, 3, 15, 14, 30, 45, tzinfo=timezone.utc)
        result = mint_canonical_id("my-agent", ts)
        assert result == "my-agent-20260315-143045"

    def test_different_slug(self):
        ts = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert mint_canonical_id("bot", ts) == "bot-20260101-000000"


class TestB64url:
    def test_encode_roundtrip(self):
        raw = b'{"alg":"EdDSA","typ":"JWT"}'
        encoded = _b64url_encode(raw)
        assert _b64url_decode(encoded) == raw

    def test_no_padding(self):
        encoded = _b64url_encode(b"test")
        assert "=" not in encoded

    def test_empty(self):
        assert _b64url_encode(b"") == ""
        assert _b64url_decode("") == b""


class TestAssertValidTransition:
    def test_pending_to_active(self):
        _assert_valid_transition("pending", "active")

    def test_pending_to_rejected(self):
        _assert_valid_transition("pending", "rejected")

    def test_active_to_suspended(self):
        _assert_valid_transition("active", "suspended")

    def test_suspended_to_active(self):
        _assert_valid_transition("suspended", "active")

    def test_active_to_revoked(self):
        _assert_valid_transition("active", "revoked")

    def test_suspended_to_revoked(self):
        _assert_valid_transition("suspended", "revoked")

    def test_pending_to_revoked(self):
        _assert_valid_transition("pending", "revoked")

    def test_rejected_to_revoked(self):
        _assert_valid_transition("rejected", "revoked")

    def test_rejected_to_pending(self):
        _assert_valid_transition("rejected", "pending")

    def test_rejected_to_active(self):
        _assert_valid_transition("rejected", "active")

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="unknown status"):
            _assert_valid_transition("active", "nonexistent")

    def test_invalid_transition_raises(self):
        with pytest.raises(ValueError, match="invalid lifecycle transition"):
            _assert_valid_transition("active", "pending")

    def test_revoked_is_terminal(self):
        with pytest.raises(ValueError, match="invalid lifecycle transition"):
            _assert_valid_transition("revoked", "active")

    def test_valid_statuses_frozen(self):
        assert "active" in VALID_STATUSES
        assert "pending" in VALID_STATUSES
        assert "suspended" in VALID_STATUSES
        assert "revoked" in VALID_STATUSES
        assert "rejected" in VALID_STATUSES


class TestRowToDict:
    def _make_row(self, data):
        """Create a minimal row-like object with dict-access and .keys()."""
        return _FakeRow(data)

    def test_basic_conversion(self):
        row = self._make_row({"id": 1, "canonical_id": "test-1", "capabilities": '["a","b"]'})
        result = _row_to_dict(row)
        assert result["capabilities"] == ["a", "b"]

    def test_empty_capabilities(self):
        row = self._make_row({"id": 1, "capabilities": "[]"})
        result = _row_to_dict(row)
        assert result["capabilities"] == []

    def test_null_capabilities(self):
        row = self._make_row({"id": 1, "capabilities": None})
        result = _row_to_dict(row)
        assert result["capabilities"] == []

    def test_invalid_json_capabilities(self):
        row = self._make_row({"id": 1, "capabilities": "not-json"})
        result = _row_to_dict(row)
        assert result["capabilities"] == []

    def test_preserves_other_fields(self):
        row = self._make_row({"id": 1, "display_name": "My Agent", "capabilities": "[]"})
        result = _row_to_dict(row)
        assert result["display_name"] == "My Agent"


class _FakeRow:
    """Minimal stand-in for aiosqlite.Row."""
    def __init__(self, data):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]


# ---------------------------------------------------------------------------
# Token minting / verification
# ---------------------------------------------------------------------------


class TestTokenMinting:
    def test_mint_returns_three_parts(self, signing_keypair):
        priv, pub = signing_keypair
        token = mint_registry_token("agent-001", priv)
        parts = token.split(".")
        assert len(parts) == 3

    def test_mint_and_verify_roundtrip(self, signing_keypair):
        priv, pub = signing_keypair
        token = mint_registry_token("agent-002", priv, user_id="user-1", framework="openclaw")
        payload = verify_registry_token(token, pub)
        assert payload["sub"] == "agent-002"
        assert payload["iss"] == "taos-registry"
        assert payload["user_id"] == "user-1"
        assert payload["framework"] == "openclaw"

    def test_mint_with_project_id(self, signing_keypair):
        priv, pub = signing_keypair
        token = mint_registry_token("agent-003", priv, project_id="proj-99")
        payload = verify_registry_token(token, pub)
        assert payload["project_id"] == "proj-99"

    def test_mint_without_project_id_omits_claim(self, signing_keypair):
        priv, pub = signing_keypair
        token = mint_registry_token("agent-004", priv)
        payload = verify_registry_token(token, pub)
        assert "project_id" not in payload

    def test_verify_bad_signature_raises(self, signing_keypair, tmp_path):
        priv, _ = signing_keypair
        # Generate a different keypair for verification
        _, wrong_pub = load_or_create_signing_keypair(tmp_path / "other_keys")
        token = mint_registry_token("agent-005", priv)
        with pytest.raises(ValueError, match="signature verification failed"):
            verify_registry_token(token, wrong_pub)

    def test_verify_malformed_token_raises(self, signing_keypair):
        _, pub = signing_keypair
        with pytest.raises(ValueError, match="three dot-separated parts"):
            verify_registry_token("only.two", pub)

    def test_verify_truncated_token_raises(self, signing_keypair):
        _, pub = signing_keypair
        with pytest.raises(ValueError):
            verify_registry_token("one", pub)

    def test_token_has_jti(self, signing_keypair):
        priv, pub = signing_keypair
        token = mint_registry_token("agent-006", priv)
        payload = verify_registry_token(token, pub)
        assert "jti" in payload
        assert len(payload["jti"]) == 32  # uuid4 hex

    def test_token_has_iat(self, signing_keypair):
        priv, pub = signing_keypair
        token = mint_registry_token("agent-007", priv)
        payload = verify_registry_token(token, pub)
        assert "iat" in payload
        assert isinstance(payload["iat"], int)


# ---------------------------------------------------------------------------
# Signing keypair persistence
# ---------------------------------------------------------------------------


class TestSigningKeypair:
    def test_creates_keypair(self, tmp_path):
        d = tmp_path / "keys"
        priv, pub = load_or_create_signing_keypair(d)
        assert b"PRIVATE" in priv
        assert b"PUBLIC" in pub

    def test_idempotent(self, tmp_path):
        d = tmp_path / "keys"
        priv1, pub1 = load_or_create_signing_keypair(d)
        priv2, pub2 = load_or_create_signing_keypair(d)
        assert priv1 == priv2
        assert pub1 == pub2

    def test_pem_file_created(self, tmp_path):
        d = tmp_path / "keys"
        load_or_create_signing_keypair(d)
        pem_file = d / "agent_registry_signing.pem"
        assert pem_file.exists()


# ---------------------------------------------------------------------------
# AgentRegistryStore: registration
# ---------------------------------------------------------------------------


class TestRegister:
    @pytest.mark.asyncio
    async def test_basic_registration(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="My Agent",
            user_id="user-1",
        )
        assert row["framework"] == "openclaw"
        assert row["display_name"] == "My Agent"
        assert row["user_id"] == "user-1"
        assert row["status"] == "active"
        assert row["canonical_id"].startswith("my-agent-")
        assert row["capabilities"] == []

    @pytest.mark.asyncio
    async def test_registration_with_capabilities(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="Cap Agent",
            capabilities=["read", "write"],
        )
        assert row["capabilities"] == ["read", "write"]

    @pytest.mark.asyncio
    async def test_registration_with_handle_and_role(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="Handled",
            handle="@handler",
            role="worker",
        )
        assert row["handle"] == "@handler"
        assert row["role"] == "worker"

    @pytest.mark.asyncio
    async def test_external_selfjoin_is_pending(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="Ext",
            origin="external-selfjoin",
        )
        assert row["status"] == "pending"

    @pytest.mark.asyncio
    async def test_default_origin_is_active(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="Default",
        )
        assert row["status"] == "active"

    @pytest.mark.asyncio
    async def test_empty_display_name_uses_framework_slug(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="",
        )
        assert row["canonical_id"].startswith("openclaw-")

    @pytest.mark.asyncio
    async def test_canonical_id_is_unique(self, store):
        r1 = await store.register(framework="openclaw", display_name="Same")
        r2 = await store.register(framework="openclaw", display_name="Same")
        assert r1["canonical_id"] != r2["canonical_id"]

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self, tmp_path):
        s = AgentRegistryStore(tmp_path / "not_init.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await s.register(framework="openclaw")


# ---------------------------------------------------------------------------
# AgentRegistryStore: get
# ---------------------------------------------------------------------------


class TestGet:
    @pytest.mark.asyncio
    async def test_get_existing(self, store):
        registered = await store.register(framework="openclaw", display_name="Get Me")
        fetched = await store.get(registered["canonical_id"])
        assert fetched is not None
        assert fetched["canonical_id"] == registered["canonical_id"]
        assert fetched["display_name"] == "Get Me"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store):
        result = await store.get("does-not-exist-20260101-000000")
        assert result is None

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self, tmp_path):
        s = AgentRegistryStore(tmp_path / "not_init.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await s.get("anything")


# ---------------------------------------------------------------------------
# AgentRegistryStore: list_all
# ---------------------------------------------------------------------------


class TestListAll:
    @pytest.mark.asyncio
    async def test_empty(self, store):
        assert await store.list_all() == []

    @pytest.mark.asyncio
    async def test_lists_all(self, store):
        await store.register(framework="openclaw", display_name="A")
        await store.register(framework="openclaw", display_name="B")
        rows = await store.list_all()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_filter_by_status(self, store):
        await store.register(framework="openclaw", display_name="Active One")
        r2 = await store.register(
            framework="openclaw",
            display_name="Pending One",
            origin="external-selfjoin",
        )
        active_rows = await store.list_all(status="active")
        pending_rows = await store.list_all(status="pending")
        assert len(active_rows) == 1
        assert len(pending_rows) == 1
        assert pending_rows[0]["canonical_id"] == r2["canonical_id"]

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self, tmp_path):
        s = AgentRegistryStore(tmp_path / "not_init.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await s.list_all()


# ---------------------------------------------------------------------------
# AgentRegistryStore: list_for_user
# ---------------------------------------------------------------------------


class TestListForUser:
    @pytest.mark.asyncio
    async def test_filters_by_user(self, store):
        await store.register(framework="openclaw", display_name="U1", user_id="user-1")
        await store.register(framework="openclaw", display_name="U2", user_id="user-2")
        await store.register(framework="openclaw", display_name="U3", user_id="user-1")
        rows = await store.list_for_user("user-1")
        assert len(rows) == 2
        assert all(r["user_id"] == "user-1" for r in rows)

    @pytest.mark.asyncio
    async def test_user_no_agents(self, store):
        await store.register(framework="openclaw", display_name="Other", user_id="user-1")
        rows = await store.list_for_user("user-empty")
        assert rows == []

    @pytest.mark.asyncio
    async def test_filter_by_user_and_status(self, store):
        await store.register(framework="openclaw", display_name="UA", user_id="user-a")
        r2 = await store.register(
            framework="openclaw",
            display_name="UP",
            user_id="user-a",
            origin="external-selfjoin",
        )
        pending = await store.list_for_user("user-a", status="pending")
        assert len(pending) == 1
        assert pending[0]["canonical_id"] == r2["canonical_id"]

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self, tmp_path):
        s = AgentRegistryStore(tmp_path / "not_init.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await s.list_for_user("anyone")


# ---------------------------------------------------------------------------
# AgentRegistryStore: list_revoked
# ---------------------------------------------------------------------------


class TestListRevoked:
    @pytest.mark.asyncio
    async def test_empty_when_none_revoked(self, store):
        await store.register(framework="openclaw", display_name="Active")
        assert await store.list_revoked() == []

    @pytest.mark.asyncio
    async def test_lists_revoked(self, store):
        r1 = await store.register(framework="openclaw", display_name="To Revoke")
        await store.revoke(r1["canonical_id"])
        revoked = await store.list_revoked()
        assert len(revoked) == 1
        assert revoked[0]["canonical_id"] == r1["canonical_id"]
        assert revoked[0]["revoked_at"] is not None

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self, tmp_path):
        s = AgentRegistryStore(tmp_path / "not_init.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await s.list_revoked()


# ---------------------------------------------------------------------------
# AgentRegistryStore: list_inactive
# ---------------------------------------------------------------------------


class TestListInactive:
    @pytest.mark.asyncio
    async def test_empty_when_all_active(self, store):
        await store.register(framework="openclaw", display_name="Active")
        assert await store.list_inactive() == []

    @pytest.mark.asyncio
    async def test_lists_non_active(self, store):
        r1 = await store.register(
            framework="openclaw",
            display_name="Pending",
            origin="external-selfjoin",
        )
        inactive = await store.list_inactive()
        assert len(inactive) == 1
        assert inactive[0]["canonical_id"] == r1["canonical_id"]
        assert inactive[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self, tmp_path):
        s = AgentRegistryStore(tmp_path / "not_init.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await s.list_inactive()


# ---------------------------------------------------------------------------
# AgentRegistryStore: set_status (lifecycle transitions)
# ---------------------------------------------------------------------------


class TestSetStatus:
    @pytest.mark.asyncio
    async def test_pending_to_active(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="Promote",
            origin="external-selfjoin",
        )
        assert row["status"] == "pending"
        updated = await store.set_status(row["canonical_id"], "active")
        assert updated["status"] == "active"

    @pytest.mark.asyncio
    async def test_pending_to_rejected(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="Reject Me",
            origin="external-selfjoin",
        )
        updated = await store.set_status(row["canonical_id"], "rejected")
        assert updated["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_active_to_suspended(self, store):
        row = await store.register(framework="openclaw", display_name="Suspend Me")
        updated = await store.set_status(row["canonical_id"], "suspended")
        assert updated["status"] == "suspended"

    @pytest.mark.asyncio
    async def test_suspended_to_active(self, store):
        row = await store.register(framework="openclaw", display_name="Reactivate")
        await store.set_status(row["canonical_id"], "suspended")
        updated = await store.set_status(row["canonical_id"], "active")
        assert updated["status"] == "active"

    @pytest.mark.asyncio
    async def test_active_to_revoked(self, store):
        row = await store.register(framework="openclaw", display_name="Revoke Me")
        updated = await store.set_status(row["canonical_id"], "revoked")
        assert updated["status"] == "revoked"
        assert updated["revoked_at"] is not None

    @pytest.mark.asyncio
    async def test_rejected_to_pending(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="Reopen",
            origin="external-selfjoin",
        )
        await store.set_status(row["canonical_id"], "rejected")
        updated = await store.set_status(row["canonical_id"], "pending")
        assert updated["status"] == "pending"

    @pytest.mark.asyncio
    async def test_rejected_to_active(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="Direct Approve",
            origin="external-selfjoin",
        )
        await store.set_status(row["canonical_id"], "rejected")
        updated = await store.set_status(row["canonical_id"], "active")
        assert updated["status"] == "active"

    @pytest.mark.asyncio
    async def test_nonexistent_raises_key_error(self, store):
        with pytest.raises(KeyError):
            await store.set_status("no-such-id-20260101-000000", "active")

    @pytest.mark.asyncio
    async def test_invalid_status_raises_value_error(self, store):
        row = await store.register(framework="openclaw", display_name="Bad Status")
        with pytest.raises(ValueError, match="unknown status"):
            await store.set_status(row["canonical_id"], "garbage")

    @pytest.mark.asyncio
    async def test_invalid_transition_raises_value_error(self, store):
        row = await store.register(framework="openclaw", display_name="Bad Trans")
        with pytest.raises(ValueError, match="invalid lifecycle transition"):
            await store.set_status(row["canonical_id"], "pending")

    @pytest.mark.asyncio
    async def test_revoked_is_terminal(self, store):
        row = await store.register(framework="openclaw", display_name="Terminal")
        await store.set_status(row["canonical_id"], "revoked")
        with pytest.raises(ValueError, match="invalid lifecycle transition"):
            await store.set_status(row["canonical_id"], "active")

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self, tmp_path):
        s = AgentRegistryStore(tmp_path / "not_init.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await s.set_status("anything", "active")


# ---------------------------------------------------------------------------
# AgentRegistryStore: update
# ---------------------------------------------------------------------------


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_display_name(self, store):
        row = await store.register(framework="openclaw", display_name="Old Name")
        updated = await store.update(row["canonical_id"], display_name="New Name")
        assert updated["display_name"] == "New Name"

    @pytest.mark.asyncio
    async def test_update_handle(self, store):
        row = await store.register(framework="openclaw", display_name="Handled")
        updated = await store.update(row["canonical_id"], handle="@newhandle")
        assert updated["handle"] == "@newhandle"

    @pytest.mark.asyncio
    async def test_update_role(self, store):
        row = await store.register(framework="openclaw", display_name="Roled")
        updated = await store.update(row["canonical_id"], role="manager")
        assert updated["role"] == "manager"

    @pytest.mark.asyncio
    async def test_update_capabilities(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="Capped",
            capabilities=["read"],
        )
        updated = await store.update(row["canonical_id"], capabilities=["read", "write"])
        assert updated["capabilities"] == ["read", "write"]

    @pytest.mark.asyncio
    async def test_update_multiple_fields(self, store):
        row = await store.register(framework="openclaw", display_name="Multi")
        updated = await store.update(
            row["canonical_id"],
            display_name="Multi Updated",
            handle="@multi",
            capabilities=["admin"],
        )
        assert updated["display_name"] == "Multi Updated"
        assert updated["handle"] == "@multi"
        assert updated["capabilities"] == ["admin"]

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_none(self, store):
        result = await store.update("no-such-20260101-000000", display_name="X")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_no_fields_returns_unchanged(self, store):
        row = await store.register(framework="openclaw", display_name="Noop")
        updated = await store.update(row["canonical_id"])
        assert updated["display_name"] == "Noop"

    @pytest.mark.asyncio
    async def test_immutable_fields_not_changed(self, store):
        row = await store.register(
            framework="openclaw",
            display_name="Immutable",
            user_id="user-1",
        )
        updated = await store.update(row["canonical_id"], display_name="Changed")
        assert updated["user_id"] == "user-1"
        assert updated["framework"] == "openclaw"
        assert updated["canonical_id"] == row["canonical_id"]

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self, tmp_path):
        s = AgentRegistryStore(tmp_path / "not_init.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await s.update("anything", display_name="X")


# ---------------------------------------------------------------------------
# AgentRegistryStore: revoke
# ---------------------------------------------------------------------------


class TestRevoke:
    @pytest.mark.asyncio
    async def test_revoke_sets_revoked_at(self, store):
        row = await store.register(framework="openclaw", display_name="Revoke Me")
        assert row["revoked_at"] is None
        updated = await store.revoke(row["canonical_id"])
        assert updated["revoked_at"] is not None
        assert updated["status"] == "revoked"

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_returns_none(self, store):
        result = await store.revoke("no-such-20260101-000000")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_idempotent(self, store):
        row = await store.register(framework="openclaw", display_name="Idem")
        first = await store.revoke(row["canonical_id"])
        second = await store.revoke(row["canonical_id"])
        assert first["revoked_at"] == second["revoked_at"]
        assert first["status"] == "revoked"
        assert second["status"] == "revoked"

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self, tmp_path):
        s = AgentRegistryStore(tmp_path / "not_init.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await s.revoke("anything")


# ---------------------------------------------------------------------------
# Full lifecycle round-trip
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_register_get_update_revoke(self, store):
        registered = await store.register(
            framework="openclaw",
            display_name="Lifecycle Agent",
            user_id="user-lc",
            capabilities=["read"],
        )
        cid = registered["canonical_id"]
        assert registered["status"] == "active"

        fetched = await store.get(cid)
        assert fetched["display_name"] == "Lifecycle Agent"

        updated = await store.update(cid, capabilities=["read", "write"])
        assert updated["capabilities"] == ["read", "write"]

        revoked = await store.revoke(cid)
        assert revoked["status"] == "revoked"
        assert revoked["revoked_at"] is not None

    @pytest.mark.asyncio
    async def test_external_selfjoin_full_flow(self, store):
        registered = await store.register(
            framework="openclaw",
            display_name="Ext Flow",
            origin="external-selfjoin",
        )
        cid = registered["canonical_id"]
        assert registered["status"] == "pending"

        approved = await store.set_status(cid, "active")
        assert approved["status"] == "active"

        suspended = await store.set_status(cid, "suspended")
        assert suspended["status"] == "suspended"

        reactivated = await store.set_status(cid, "active")
        assert reactivated["status"] == "active"

        revoked = await store.set_status(cid, "revoked")
        assert revoked["status"] == "revoked"
        assert revoked["revoked_at"] is not None

    @pytest.mark.asyncio
    async def test_list_filters_after_transitions(self, store):
        r1 = await store.register(framework="openclaw", display_name="A1")
        r2 = await store.register(
            framework="openclaw",
            display_name="P1",
            origin="external-selfjoin",
        )
        r3 = await store.register(framework="openclaw", display_name="S1")

        # Suspend r3
        await store.set_status(r3["canonical_id"], "suspended")

        # r2 stays pending
        active = await store.list_all(status="active")
        pending = await store.list_all(status="pending")
        suspended = await store.list_all(status="suspended")

        active_ids = {r["canonical_id"] for r in active}
        assert r1["canonical_id"] in active_ids
        assert r2["canonical_id"] not in active_ids
        assert r3["canonical_id"] not in active_ids

        assert len(pending) == 1
        assert pending[0]["canonical_id"] == r2["canonical_id"]

        assert len(suspended) == 1
        assert suspended[0]["canonical_id"] == r3["canonical_id"]


# ---------------------------------------------------------------------------
# Migration v2: strip leading '@' from display_name
# ---------------------------------------------------------------------------


class TestMigrationV2StripAtDisplayName:
    @pytest.mark.asyncio
    async def test_strips_leading_at_from_existing_rows(self, store):
        """Rows inserted with a leading '@' are cleaned up by the migration."""
        row = await store.register(framework="openclaw", display_name="normal-agent")
        # Manually inject a row with a leading '@' to simulate pre-fix data.
        await store._db.execute(
            "UPDATE agent_registry SET display_name = '@tainted' WHERE canonical_id = ?",
            (row["canonical_id"],),
        )
        await store._db.commit()

        # Verify the '@' is present before the migration runs.
        raw = await store.get(row["canonical_id"])
        assert raw["display_name"] == "@tainted"

        # Run the migration.
        await _migration_v2_strip_at_display_name(store._db)

        cleaned = await store.get(row["canonical_id"])
        assert cleaned["display_name"] == "tainted"
        assert not cleaned["display_name"].startswith("@")

    @pytest.mark.asyncio
    async def test_leaves_names_without_at_unchanged(self, store):
        row = await store.register(framework="openclaw", display_name="clean-name")
        await _migration_v2_strip_at_display_name(store._db)
        after = await store.get(row["canonical_id"])
        assert after["display_name"] == "clean-name"

    @pytest.mark.asyncio
    async def test_idempotent_on_multiple_runs(self, store):
        row = await store.register(framework="openclaw", display_name="normal-agent")
        await store._db.execute(
            "UPDATE agent_registry SET display_name = '@once' WHERE canonical_id = ?",
            (row["canonical_id"],),
        )
        await store._db.commit()

        await _migration_v2_strip_at_display_name(store._db)
        await _migration_v2_strip_at_display_name(store._db)  # second run is a no-op

        after = await store.get(row["canonical_id"])
        assert after["display_name"] == "once"

    @pytest.mark.asyncio
    async def test_strips_at_on_store_init(self, tmp_path):
        """AgentRegistryStore._post_init runs the migration automatically."""
        s = AgentRegistryStore(tmp_path / "migr_test.db")
        await s.init()

        row = await s.register(framework="openclaw", display_name="normal-agent")
        await s._db.execute(
            "UPDATE agent_registry SET display_name = '@auto-migrated' WHERE canonical_id = ?",
            (row["canonical_id"],),
        )
        await s._db.commit()

        # Re-opening the store triggers _post_init which runs the migration.
        await s.close()
        s2 = AgentRegistryStore(tmp_path / "migr_test.db")
        await s2.init()

        after = await s2.get(row["canonical_id"])
        assert after["display_name"] == "auto-migrated"
        await s2.close()
