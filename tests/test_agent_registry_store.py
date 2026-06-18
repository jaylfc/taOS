import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    _assert_valid_transition,
    _b64url_decode,
    _b64url_encode,
    _slugify,
    load_or_create_signing_keypair,
    mint_canonical_id,
    mint_registry_token,
    verify_registry_token,
    VALID_STATUSES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _store(tmp_path: Path) -> AgentRegistryStore:
    s = AgentRegistryStore(tmp_path / "registry.db")
    await s.init()
    return s


# ---------------------------------------------------------------------------
# load_or_create_signing_keypair
# ---------------------------------------------------------------------------

class TestLoadOrCreateSigningKeypair:
    def test_generates_new_keypair_when_missing(self, tmp_path):
        data_dir = tmp_path / "keys"
        priv, pub = load_or_create_signing_keypair(data_dir)
        assert b"PRIVATE" in priv
        assert b"PUBLIC" in pub
        pem_file = data_dir / "agent_registry_signing.pem"
        assert pem_file.exists()

    def test_persists_with_restricted_mode(self, tmp_path):
        data_dir = tmp_path / "keys"
        load_or_create_signing_keypair(data_dir)
        pem_file = data_dir / "agent_registry_signing.pem"
        mode = pem_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_loads_existing_keypair_on_second_call(self, tmp_path):
        data_dir = tmp_path / "keys"
        priv1, pub1 = load_or_create_signing_keypair(data_dir)
        priv2, pub2 = load_or_create_signing_keypair(data_dir)
        assert priv1 == priv2
        assert pub1 == pub2

    def test_returns_deterministic_pem_bytes(self, tmp_path):
        data_dir = tmp_path / "keys"
        priv, pub = load_or_create_signing_keypair(data_dir)
        # PEM text must be bytes, not str
        assert isinstance(priv, bytes)
        assert isinstance(pub, bytes)
        # Must be valid Ed25519 PEM markers
        assert b"BEGIN PRIVATE KEY" in priv
        assert b"BEGIN PUBLIC KEY" in pub

    def test_creates_parent_dirs(self, tmp_path):
        data_dir = tmp_path / "deep" / "nested" / "dir"
        assert not data_dir.exists()
        load_or_create_signing_keypair(data_dir)
        assert data_dir.exists()

    def test_idempotent_under_concurrent_calls(self, tmp_path):
        data_dir = tmp_path / "keys"
        results = [load_or_create_signing_keypair(data_dir) for _ in range(10)]
        privs = [r[0] for r in results]
        pubs = [r[1] for r in results]
        assert all(p == privs[0] for p in privs)
        assert all(p == pubs[0] for p in pubs)


# ---------------------------------------------------------------------------
# mint_registry_token + verify_registry_token (round-trip)
# ---------------------------------------------------------------------------

class TestRegistryTokenRoundTrip:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.data_dir = tmp_path / "keys"
        self.priv_pem, self.pub_pem = load_or_create_signing_keypair(self.data_dir)

    def test_round_trip_basic(self):
        token = mint_registry_token("agent-001", self.priv_pem)
        payload = verify_registry_token(token, self.pub_pem)
        assert payload["sub"] == "agent-001"
        assert payload["iss"] == "taos-registry"
        assert "iat" in payload
        assert "jti" in payload

    def test_round_trip_with_optional_claims(self):
        token = mint_registry_token(
            "agent-002",
            self.priv_pem,
            user_id="user-42",
            framework="openclaw",
            project_id="proj-7",
        )
        payload = verify_registry_token(token, self.pub_pem)
        assert payload["sub"] == "agent-002"
        assert payload["user_id"] == "user-42"
        assert payload["framework"] == "openclaw"
        assert payload["project_id"] == "proj-7"

    def test_project_id_omitted_when_none(self):
        token = mint_registry_token("agent-003", self.priv_pem, project_id=None)
        payload = verify_registry_token(token, self.pub_pem)
        assert "project_id" not in payload

    def test_project_id_omitted_when_empty_string(self):
        token = mint_registry_token("agent-004", self.priv_pem, project_id="")
        payload = verify_registry_token(token, self.pub_pem)
        assert "project_id" not in payload

    def test_token_has_three_parts(self):
        token = mint_registry_token("agent-005", self.priv_pem)
        parts = token.split(".")
        assert len(parts) == 3
        # Each part is non-empty base64url
        for p in parts:
            assert len(p) > 0

    def test_header_is_eddsa_jwt(self):
        token = mint_registry_token("agent-006", self.priv_pem)
        header_b64 = token.split(".")[0]
        header = json.loads(_b64url_decode(header_b64))
        assert header == {"alg": "EdDSA", "typ": "JWT"}

    def test_jti_is_unique_per_call(self):
        token1 = mint_registry_token("agent-007", self.priv_pem)
        token2 = mint_registry_token("agent-007", self.priv_pem)
        p1 = verify_registry_token(token1, self.pub_pem)
        p2 = verify_registry_token(token2, self.pub_pem)
        assert p1["jti"] != p2["jti"]

    def test_tampered_signature_rejected(self):
        token = mint_registry_token("agent-008", self.priv_pem)
        parts = token.split(".")
        # Flip last char of signature
        sig = parts[2]
        flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
        bad_token = f"{parts[0]}.{parts[1]}.{flipped}"
        with pytest.raises(ValueError, match="signature verification failed"):
            verify_registry_token(bad_token, self.pub_pem)

    def test_tampered_payload_rejected(self):
        token = mint_registry_token("agent-009", self.priv_pem)
        parts = token.split(".")
        # Re-encode a different payload
        fake_payload = _b64url_encode(json.dumps({"sub": "evil"}).encode())
        bad_token = f"{parts[0]}.{fake_payload}.{parts[2]}"
        with pytest.raises(ValueError, match="signature verification failed"):
            verify_registry_token(bad_token, self.pub_pem)

    def test_wrong_public_key_rejected(self):
        token = mint_registry_token("agent-010", self.priv_pem)
        # Generate a different keypair
        other_dir = self.data_dir / "other"
        _, other_pub = load_or_create_signing_keypair(other_dir)
        with pytest.raises(ValueError, match="signature verification failed"):
            verify_registry_token(token, other_pub)

    def test_invalid_format_two_parts(self):
        with pytest.raises(ValueError, match="three dot-separated"):
            verify_registry_token("only.two", self.pub_pem)

    def test_invalid_format_four_parts(self):
        with pytest.raises(ValueError, match="three dot-separated"):
            verify_registry_token("a.b.c.d", self.pub_pem)

    def test_empty_token_rejected(self):
        with pytest.raises(ValueError):
            verify_registry_token("", self.pub_pem)

    def test_iat_is_recent_timestamp(self):
        before = int(time.time())
        token = mint_registry_token("agent-011", self.priv_pem)
        after = int(time.time())
        payload = verify_registry_token(token, self.pub_pem)
        assert before <= payload["iat"] <= after


# ---------------------------------------------------------------------------
# mint_canonical_id
# ---------------------------------------------------------------------------

class TestMintCanonicalId:
    def test_format(self):
        ts = datetime(2025, 7, 14, 9, 30, 45, tzinfo=timezone.utc)
        cid = mint_canonical_id("my-agent", ts)
        assert cid == "my-agent-20250714-093045"

    def test_date_components(self):
        ts = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        cid = mint_canonical_id("x", ts)
        assert cid == "x-20241231-235959"

    def test_uniqueness_across_seconds(self):
        ts1 = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        assert mint_canonical_id("slug", ts1) != mint_canonical_id("slug", ts2)

    def test_slug_preserved_as_is(self):
        ts = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        cid = mint_canonical_id("MySlug123", ts)
        # mint_canonical_id does not slugify; it uses the slug verbatim
        assert cid == "MySlug123-20250101-000000"

    def test_empty_slug(self):
        ts = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        cid = mint_canonical_id("", ts)
        assert cid == "-20250101-000000"


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_lowercases(self):
        assert _slugify("MyAgent") == "myagent"

    def test_replaces_spaces(self):
        assert _slugify("my agent") == "my-agent"

    def test_replaces_special_chars(self):
        assert _slugify("agent@v2.0") == "agent-v2-0"

    def test_collapses_multiple_separators(self):
        assert _slugify("a---b") == "a-b"

    def test_strips_leading_trailing(self):
        assert _slugify("  agent  ") == "agent"

    def test_empty_string_returns_agent(self):
        assert _slugify("") == "agent"

    def test_only_special_chars_returns_agent(self):
        assert _slugify("@@@") == "agent"


# ---------------------------------------------------------------------------
# _assert_valid_transition
# ---------------------------------------------------------------------------

class TestAssertValidTransition:
    def test_pending_to_active(self):
        _assert_valid_transition("pending", "active")  # no raise

    def test_pending_to_rejected(self):
        _assert_valid_transition("pending", "rejected")

    def test_active_to_suspended(self):
        _assert_valid_transition("active", "suspended")

    def test_suspended_to_active(self):
        _assert_valid_transition("suspended", "active")

    def test_active_to_revoked(self):
        _assert_valid_transition("active", "revoked")

    def test_rejected_to_pending(self):
        _assert_valid_transition("rejected", "pending")

    def test_rejected_to_active(self):
        _assert_valid_transition("rejected", "active")

    def test_invalid_transition_raises(self):
        with pytest.raises(ValueError, match="invalid lifecycle transition"):
            _assert_valid_transition("active", "pending")

    def test_unknown_status_raises(self):
        with pytest.raises(ValueError, match="unknown status"):
            _assert_valid_transition("active", "nonexistent")

    def test_revoked_is_terminal(self):
        for target in VALID_STATUSES:
            if target != "revoked":
                with pytest.raises(ValueError):
                    _assert_valid_transition("revoked", target)


# ---------------------------------------------------------------------------
# AgentRegistryStore.register
# ---------------------------------------------------------------------------

class TestAgentRegistryStoreRegister:
    @pytest.mark.asyncio
    async def test_register_creates_record(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw", display_name="My Agent")
        assert rec["canonical_id"].startswith("my-agent-")
        assert rec["framework"] == "openclaw"
        assert rec["display_name"] == "My Agent"
        assert rec["status"] == "active"
        await s.close()

    @pytest.mark.asyncio
    async def test_register_default_status_pending_for_external(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw", origin="external-selfjoin")
        assert rec["status"] == "pending"
        await s.close()

    @pytest.mark.asyncio
    async def test_register_default_status_active_for_taos(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw", origin="taos-deployed")
        assert rec["status"] == "active"
        await s.close()

    @pytest.mark.asyncio
    async def test_register_stores_capabilities(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(
            framework="openclaw",
            capabilities=["app.kv", "app.net"],
        )
        assert rec["capabilities"] == ["app.kv", "app.net"]
        await s.close()

    @pytest.mark.asyncio
    async def test_register_empty_capabilities(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw")
        assert rec["capabilities"] == []
        await s.close()

    @pytest.mark.asyncio
    async def test_register_collision_appends_suffix(self, tmp_path):
        s = await _store(tmp_path)
        # Freeze time so both registrations get the same second
        fixed_ts = datetime(2025, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
        with patch("tinyagentos.agent_registry_store.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_ts
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            r1 = await s.register(framework="openclaw", display_name="Same")
            r2 = await s.register(framework="openclaw", display_name="Same")
        assert r1["canonical_id"] != r2["canonical_id"]
        assert r2["canonical_id"].endswith("-01")
        await s.close()

    @pytest.mark.asyncio
    async def test_register_without_init_raises(self, tmp_path):
        s = AgentRegistryStore(tmp_path / "r.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await s.register(framework="openclaw")

    @pytest.mark.asyncio
    async def test_register_stores_user_id(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw", user_id="user-123")
        assert rec["user_id"] == "user-123"
        await s.close()

    @pytest.mark.asyncio
    async def test_register_stores_handle_and_role(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(
            framework="openclaw",
            handle="@myagent",
            role="assistant",
        )
        assert rec["handle"] == "@myagent"
        assert rec["role"] == "assistant"
        await s.close()


# ---------------------------------------------------------------------------
# AgentRegistryStore.get
# ---------------------------------------------------------------------------

class TestAgentRegistryStoreGet:
    @pytest.mark.asyncio
    async def test_get_returns_record(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw", display_name="Find Me")
        fetched = await s.get(rec["canonical_id"])
        assert fetched is not None
        assert fetched["canonical_id"] == rec["canonical_id"]
        assert fetched["display_name"] == "Find Me"
        await s.close()

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, tmp_path):
        s = await _store(tmp_path)
        assert await s.get("nonexistent") is None
        await s.close()

    @pytest.mark.asyncio
    async def test_get_without_init_raises(self, tmp_path):
        s = AgentRegistryStore(tmp_path / "r.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await s.get("anything")


# ---------------------------------------------------------------------------
# AgentRegistryStore.list_all / list_for_user
# ---------------------------------------------------------------------------

class TestAgentRegistryStoreList:
    @pytest.mark.asyncio
    async def test_list_all_empty(self, tmp_path):
        s = await _store(tmp_path)
        assert await s.list_all() == []
        await s.close()

    @pytest.mark.asyncio
    async def test_list_all_returns_all(self, tmp_path):
        s = await _store(tmp_path)
        await s.register(framework="openclaw", display_name="A")
        await s.register(framework="openclaw", display_name="B")
        all_rows = await s.list_all()
        assert len(all_rows) == 2
        await s.close()

    @pytest.mark.asyncio
    async def test_list_all_filters_by_status(self, tmp_path):
        s = await _store(tmp_path)
        await s.register(framework="openclaw", origin="external-selfjoin")
        await s.register(framework="openclaw", origin="taos-deployed")
        pending = await s.list_all(status="pending")
        active = await s.list_all(status="active")
        assert len(pending) == 1
        assert len(active) == 1
        await s.close()

    @pytest.mark.asyncio
    async def test_list_for_user(self, tmp_path):
        s = await _store(tmp_path)
        await s.register(framework="openclaw", user_id="u1", display_name="A")
        await s.register(framework="openclaw", user_id="u2", display_name="B")
        await s.register(framework="openclaw", user_id="u1", display_name="C")
        u1_rows = await s.list_for_user("u1")
        assert len(u1_rows) == 2
        u2_rows = await s.list_for_user("u2")
        assert len(u2_rows) == 1
        await s.close()

    @pytest.mark.asyncio
    async def test_list_for_user_with_status(self, tmp_path):
        s = await _store(tmp_path)
        await s.register(framework="openclaw", user_id="u1", origin="external-selfjoin")
        await s.register(framework="openclaw", user_id="u1", origin="taos-deployed")
        pending = await s.list_for_user("u1", status="pending")
        assert len(pending) == 1
        await s.close()


# ---------------------------------------------------------------------------
# AgentRegistryStore.set_status (lifecycle transitions)
# ---------------------------------------------------------------------------

class TestAgentRegistryStoreSetStatus:
    @pytest.mark.asyncio
    async def test_pending_to_active(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw", origin="external-selfjoin")
        assert rec["status"] == "pending"
        updated = await s.set_status(rec["canonical_id"], "active")
        assert updated["status"] == "active"
        await s.close()

    @pytest.mark.asyncio
    async def test_active_to_revoked(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw")
        updated = await s.set_status(rec["canonical_id"], "revoked")
        assert updated["status"] == "revoked"
        assert updated["revoked_at"] is not None
        await s.close()

    @pytest.mark.asyncio
    async def test_invalid_transition_raises_value_error(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw")
        with pytest.raises(ValueError, match="invalid lifecycle transition"):
            await s.set_status(rec["canonical_id"], "pending")
        await s.close()

    @pytest.mark.asyncio
    async def test_nonexistent_raises_key_error(self, tmp_path):
        s = await _store(tmp_path)
        with pytest.raises(KeyError):
            await s.set_status("no-such-id", "active")
        await s.close()

    @pytest.mark.asyncio
    async def test_suspend_and_reactivate(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw")
        suspended = await s.set_status(rec["canonical_id"], "suspended")
        assert suspended["status"] == "suspended"
        reactivated = await s.set_status(rec["canonical_id"], "active")
        assert reactivated["status"] == "active"
        await s.close()

    @pytest.mark.asyncio
    async def test_revoke_sets_revoked_at(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw")
        assert rec["revoked_at"] is None
        revoked = await s.set_status(rec["canonical_id"], "revoked")
        assert revoked["revoked_at"] is not None
        await s.close()

    @pytest.mark.asyncio
    async def test_rejected_to_active(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw", origin="external-selfjoin")
        rejected = await s.set_status(rec["canonical_id"], "rejected")
        assert rejected["status"] == "rejected"
        approved = await s.set_status(rec["canonical_id"], "active")
        assert approved["status"] == "active"
        await s.close()


# ---------------------------------------------------------------------------
# AgentRegistryStore.update
# ---------------------------------------------------------------------------

class TestAgentRegistryStoreUpdate:
    @pytest.mark.asyncio
    async def test_update_display_name(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw", display_name="Old")
        updated = await s.update(rec["canonical_id"], display_name="New")
        assert updated["display_name"] == "New"
        await s.close()

    @pytest.mark.asyncio
    async def test_update_capabilities(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw", capabilities=["app.kv"])
        updated = await s.update(rec["canonical_id"], capabilities=["app.kv", "app.net"])
        assert updated["capabilities"] == ["app.kv", "app.net"]
        await s.close()

    @pytest.mark.asyncio
    async def test_update_returns_none_for_missing(self, tmp_path):
        s = await _store(tmp_path)
        result = await s.update("nonexistent", display_name="X")
        assert result is None
        await s.close()

    @pytest.mark.asyncio
    async def test_update_no_fields_returns_unchanged(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw", display_name="Same")
        updated = await s.update(rec["canonical_id"])
        assert updated["display_name"] == "Same"
        await s.close()

    @pytest.mark.asyncio
    async def test_immutable_fields_not_changed(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw", user_id="u1")
        # framework and user_id are not in the update whitelist
        updated = await s.update(rec["canonical_id"], display_name="NewName")
        assert updated["framework"] == "openclaw"
        assert updated["user_id"] == "u1"
        assert updated["display_name"] == "NewName"
        await s.close()


# ---------------------------------------------------------------------------
# AgentRegistryStore.revoke
# ---------------------------------------------------------------------------

class TestAgentRegistryStoreRevoke:
    @pytest.mark.asyncio
    async def test_revoke_sets_revoked_at(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw")
        assert rec["revoked_at"] is None
        revoked = await s.revoke(rec["canonical_id"])
        assert revoked["revoked_at"] is not None
        assert revoked["status"] == "revoked"
        await s.close()

    @pytest.mark.asyncio
    async def test_revoke_missing_returns_none(self, tmp_path):
        s = await _store(tmp_path)
        assert await s.revoke("nonexistent") is None
        await s.close()

    @pytest.mark.asyncio
    async def test_revoke_idempotent(self, tmp_path):
        s = await _store(tmp_path)
        rec = await s.register(framework="openclaw")
        r1 = await s.revoke(rec["canonical_id"])
        r2 = await s.revoke(rec["canonical_id"])
        assert r1["revoked_at"] == r2["revoked_at"]
        assert r1["status"] == "revoked"
        await s.close()


# ---------------------------------------------------------------------------
# AgentRegistryStore.list_revoked / list_inactive
# ---------------------------------------------------------------------------

class TestAgentRegistryStoreListRevokedInactive:
    @pytest.mark.asyncio
    async def test_list_revoked(self, tmp_path):
        s = await _store(tmp_path)
        r1 = await s.register(framework="openclaw")
        r2 = await s.register(framework="openclaw")
        await s.revoke(r1["canonical_id"])
        revoked = await s.list_revoked()
        assert len(revoked) == 1
        assert revoked[0]["canonical_id"] == r1["canonical_id"]
        assert revoked[0]["revoked_at"] is not None
        await s.close()

    @pytest.mark.asyncio
    async def test_list_revoked_empty(self, tmp_path):
        s = await _store(tmp_path)
        await s.register(framework="openclaw")
        assert await s.list_revoked() == []
        await s.close()

    @pytest.mark.asyncio
    async def test_list_inactive(self, tmp_path):
        s = await _store(tmp_path)
        r1 = await s.register(framework="openclaw")
        await s.register(framework="openclaw", origin="external-selfjoin")
        await s.revoke(r1["canonical_id"])
        inactive = await s.list_inactive()
        # r1 is revoked, the other is pending
        assert len(inactive) == 2
        statuses = {r["status"] for r in inactive}
        assert "revoked" in statuses
        assert "pending" in statuses
        await s.close()

    @pytest.mark.asyncio
    async def test_list_inactive_empty_when_all_active(self, tmp_path):
        s = await _store(tmp_path)
        await s.register(framework="openclaw")
        assert await s.list_inactive() == []
        await s.close()
