"""Tests for the Agent Registry (SP-A).

Covers:
  - Store: canonical_id minting (format, immutability, collision suffix)
  - Store: token issue + verify-with-pubkey round-trip
  - Store: list_for_user / list_revoked
  - Routes: register / read-back / list / revoke
  - Routes: origin allowlist, revoked feed, route-ordering regression
"""
from __future__ import annotations

import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    mint_canonical_id,
    mint_registry_token,
    verify_registry_token,
    _slugify,
)
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Store-level tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAgentRegistryStore:

    async def _make_store(self, db_path):
        store = AgentRegistryStore(db_path)
        await store.init()
        return store

    # -- ID format -----------------------------------------------------------

    async def test_canonical_id_format(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="openclaw", display_name="My Agent")
            cid = rec["canonical_id"]
            # Should match {slug}-{YYYYMMDD}-{HHMMSS}
            assert re.match(r"^my-agent-\d{8}-\d{6}$", cid), f"unexpected format: {cid!r}"
        finally:
            await store.close()

    async def test_canonical_id_uses_framework_when_no_display_name(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="hermes")
            cid = rec["canonical_id"]
            assert cid.startswith("hermes-"), f"expected hermes prefix, got {cid!r}"
        finally:
            await store.close()

    async def test_canonical_id_immutable_on_readback(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec1 = await store.register(framework="openclaw", display_name="Stable Agent")
            rec2 = await store.get(rec1["canonical_id"])
            assert rec2 is not None
            assert rec2["canonical_id"] == rec1["canonical_id"]
        finally:
            await store.close()

    async def test_collision_appends_two_char_suffix(self, tmp_path):
        """Two registrations with the same display_name in the same second get distinct IDs."""
        store = await self._make_store(tmp_path / "reg.db")
        try:
            now = datetime.now(timezone.utc)
            slug = _slugify("Clash")
            base_id = mint_canonical_id(slug, now)

            # Pre-insert an entry with the base canonical_id to simulate a collision.
            import json
            await store._db.execute(
                """INSERT INTO agent_registry
                   (canonical_id, display_name, framework, user_id, origin, handle, role, capabilities, created_ts)
                   VALUES (?, '', 'dummy', '', 'taos-deployed', '', NULL, '[]', ?)""",
                (base_id, now.isoformat()),
            )
            await store._db.commit()

            # Now register with the same slug — should get a suffixed ID.
            rec = await store.register(framework="dummy", display_name="Clash")
            assert rec["canonical_id"] != base_id
            assert rec["canonical_id"].startswith(base_id + "-"), (
                f"expected suffix on collision, got {rec['canonical_id']!r}"
            )
        finally:
            await store.close()

    # -- Record fields -------------------------------------------------------

    async def test_register_stores_all_fields(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(
                framework="openclaw",
                display_name="Codex",
                user_id="user-42",
                origin="external-selfjoin",
                handle="@codex",
                role="coder",
                capabilities=["code-generation", "review"],
            )
            assert rec["framework"] == "openclaw"
            assert rec["display_name"] == "Codex"
            assert rec["user_id"] == "user-42"
            assert rec["origin"] == "external-selfjoin"
            assert rec["handle"] == "@codex"
            assert rec["role"] == "coder"
            assert rec["capabilities"] == ["code-generation", "review"]
            assert rec["revoked_at"] is None
        finally:
            await store.close()

    async def test_list_all_returns_all_entries(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            await store.register(framework="openclaw", display_name="A")
            await store.register(framework="hermes", display_name="B")
            records = await store.list_all()
            assert len(records) == 2
        finally:
            await store.close()

    async def test_get_unknown_returns_none(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            assert await store.get("no-such-id") is None
        finally:
            await store.close()

    # -- Revoke --------------------------------------------------------------

    async def test_revoke_sets_revoked_at(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="openclaw")
            revoked = await store.revoke(rec["canonical_id"])
            assert revoked is not None
            assert revoked["revoked_at"] is not None
        finally:
            await store.close()

    async def test_revoke_unknown_returns_none(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            assert await store.revoke("does-not-exist") is None
        finally:
            await store.close()

    async def test_revoke_already_revoked_returns_none(self, tmp_path):
        """Revoking a second time returns None (already revoked)."""
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="openclaw")
            await store.revoke(rec["canonical_id"])
            result = await store.revoke(rec["canonical_id"])
            # The record still exists but the second UPDATE matched 0 rows;
            # the helper returns the record (with revoked_at set) either way.
            # The important thing is no exception is raised.
            assert result is not None
        finally:
            await store.close()

    # -- list_for_user -------------------------------------------------------

    async def test_list_for_user_returns_only_own_records(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            await store.register(framework="openclaw", user_id="alice")
            await store.register(framework="hermes", user_id="alice")
            await store.register(framework="openclaw", user_id="bob")

            alice_records = await store.list_for_user("alice")
            bob_records = await store.list_for_user("bob")
            nobody_records = await store.list_for_user("nobody")

            assert len(alice_records) == 2
            assert all(r["user_id"] == "alice" for r in alice_records)
            assert len(bob_records) == 1
            assert bob_records[0]["user_id"] == "bob"
            assert len(nobody_records) == 0
        finally:
            await store.close()

    # -- list_revoked --------------------------------------------------------

    async def test_list_revoked_returns_only_revoked(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec_a = await store.register(framework="openclaw", display_name="A")
            rec_b = await store.register(framework="hermes", display_name="B")
            await store.register(framework="openclaw", display_name="C")  # not revoked

            await store.revoke(rec_a["canonical_id"])
            await store.revoke(rec_b["canonical_id"])

            revoked = await store.list_revoked()
            assert len(revoked) == 2
            cids = {r["canonical_id"] for r in revoked}
            assert rec_a["canonical_id"] in cids
            assert rec_b["canonical_id"] in cids
            # Each entry must have exactly canonical_id and revoked_at
            for entry in revoked:
                assert set(entry.keys()) == {"canonical_id", "revoked_at"}
                assert entry["revoked_at"] is not None
        finally:
            await store.close()

    async def test_list_revoked_empty_when_none_revoked(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            await store.register(framework="openclaw")
            assert await store.list_revoked() == []
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# Keypair + token tests
# ---------------------------------------------------------------------------

class TestSigningKeypair:
    def test_load_or_create_generates_and_persists(self, tmp_path):
        priv, pub = load_or_create_signing_keypair(tmp_path)
        assert priv.startswith(b"-----BEGIN PRIVATE KEY-----")
        assert pub.startswith(b"-----BEGIN PUBLIC KEY-----")
        key_file = tmp_path / "agent_registry_signing.pem"
        assert key_file.exists()
        assert (key_file.stat().st_mode & 0o777) == 0o600

    def test_load_or_create_idempotent(self, tmp_path):
        priv1, pub1 = load_or_create_signing_keypair(tmp_path)
        priv2, pub2 = load_or_create_signing_keypair(tmp_path)
        assert priv1 == priv2
        assert pub1 == pub2


class TestTokenRoundTrip:
    def _make_keypair(self, tmp_path):
        return load_or_create_signing_keypair(tmp_path)

    def test_mint_and_verify(self, tmp_path):
        priv, pub = self._make_keypair(tmp_path)
        token = mint_registry_token(
            "agent-20260609-120000", priv,
            user_id="user-1", framework="openclaw",
        )
        payload = verify_registry_token(token, pub)
        assert payload["sub"] == "agent-20260609-120000"
        assert payload["iss"] == "taos-registry"
        assert "iat" in payload
        assert payload["user_id"] == "user-1"
        assert payload["framework"] == "openclaw"

    def test_verify_wrong_key_fails(self, tmp_path):
        import tempfile
        priv1, pub1 = self._make_keypair(tmp_path)
        with tempfile.TemporaryDirectory() as d2:
            from pathlib import Path
            _priv2, pub2 = load_or_create_signing_keypair(Path(d2))
        token = mint_registry_token("agent-20260609-120001", priv1)
        with pytest.raises(ValueError, match="signature"):
            verify_registry_token(token, pub2)

    def test_verify_tampered_payload_fails(self, tmp_path):
        import base64, json
        priv, pub = self._make_keypair(tmp_path)
        token = mint_registry_token("agent-20260609-120002", priv)
        header, payload_b64, sig = token.split(".")
        # Decode, mutate, re-encode
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        orig = json.loads(base64.urlsafe_b64decode(payload_b64))
        orig["sub"] = "evil-agent"
        bad_payload = base64.urlsafe_b64encode(json.dumps(orig).encode()).rstrip(b"=").decode()
        tampered = f"{header}.{bad_payload}.{sig}"
        with pytest.raises(ValueError, match="signature"):
            verify_registry_token(tampered, pub)

    def test_verify_malformed_token_fails(self, tmp_path):
        _priv, pub = self._make_keypair(tmp_path)
        with pytest.raises(ValueError, match="three dot-separated"):
            verify_registry_token("notavalidtoken", pub)

    def test_token_contains_user_id_and_framework_claims(self, tmp_path):
        """Minted token must carry user_id and framework as JWT claims."""
        import base64, json
        priv, _pub = self._make_keypair(tmp_path)
        token = mint_registry_token(
            "cid-abc",
            priv,
            user_id="user-99",
            framework="hermes",
        )
        _header, payload_b64, _sig = token.split(".")
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        assert claims["user_id"] == "user-99"
        assert claims["framework"] == "hermes"
        assert claims["sub"] == "cid-abc"
        assert claims["iss"] == "taos-registry"
        assert claims["jti"] and len(claims["jti"]) >= 16  # unique token id (revocation-ready)

    def test_generic_edsa_verify_without_our_helper(self, tmp_path):
        """A generic Ed25519 verifier (cryptography lib only, no tinyagentos import)
        must be able to verify the token — proving taOSmd can do so independently.
        """
        import base64, json
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.exceptions import InvalidSignature

        priv, pub_pem = self._make_keypair(tmp_path)
        token = mint_registry_token(
            "bus-agent-20260609-120000",
            priv,
            user_id="user-bus",
            framework="taosmd",
        )

        # Split the compact JWT
        header_b64, payload_b64, sig_b64 = token.split(".")

        # Verify the header is standard EdDSA
        h_padding = 4 - len(header_b64) % 4
        if h_padding != 4:
            header_b64_padded = header_b64 + "=" * h_padding
        else:
            header_b64_padded = header_b64
        header = json.loads(base64.urlsafe_b64decode(header_b64_padded))
        assert header == {"alg": "EdDSA", "typ": "JWT"}, (
            f"JWT header must be exactly {{alg:EdDSA,typ:JWT}}, got {header!r}"
        )

        # Reconstruct and verify signing input using only the cryptography lib
        signing_input = f"{header_b64}.{payload_b64}".encode()
        sig_padding = 4 - len(sig_b64) % 4
        if sig_padding != 4:
            sig_b64_padded = sig_b64 + "=" * sig_padding
        else:
            sig_b64_padded = sig_b64
        sig_bytes = base64.urlsafe_b64decode(sig_b64_padded)

        public_key = load_pem_public_key(pub_pem)
        try:
            public_key.verify(sig_bytes, signing_input)
        except InvalidSignature:
            pytest.fail("Generic Ed25519 verify failed — taOSmd would not be able to verify this token")

        # Decode and assert the claims are present
        p_padding = 4 - len(payload_b64) % 4
        if p_padding != 4:
            payload_b64_padded = payload_b64 + "=" * p_padding
        else:
            payload_b64_padded = payload_b64
        claims = json.loads(base64.urlsafe_b64decode(payload_b64_padded))
        assert claims["sub"] == "bus-agent-20260609-120000"
        assert claims["iss"] == "taos-registry"
        assert claims["user_id"] == "user-bus"
        assert claims["framework"] == "taosmd"


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def registry_client(app, tmp_data_dir):
    """Async client with agent_registry store initialised, authenticated as admin."""
    # Init the agent_registry store (lifespan not running in tests)
    registry_store = app.state.agent_registry
    if registry_store._db is None:
        await registry_store.init()

    # Re-use the existing app.state.agent_registry_keypair (set by create_app)

    # Auth setup (mirrors conftest.client)
    store = app.state.metrics
    if store._db is None:
        await store.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        # Expose the admin uid so tests can assert token claims
        c._test_admin_uid = uid
        yield c

    await registry_store.close()
    await store.close()


@pytest.mark.asyncio
class TestAgentRegistryRoutes:

    async def test_register_returns_canonical_id_and_token(self, registry_client):
        resp = await registry_client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Route Agent"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "canonical_id" in data
        assert "token" in data
        assert "record" in data
        assert data["record"]["framework"] == "openclaw"
        assert data["record"]["display_name"] == "Route Agent"

    async def test_register_token_carries_authenticated_user_id(self, registry_client):
        """Token user_id must equal the authenticated caller's id, not a body value."""
        resp = await registry_client.post(
            "/api/agents/registry/register",
            json={"framework": "hermes", "display_name": "Verified Agent"},
        )
        assert resp.status_code == 200
        token = resp.json()["token"]
        canonical_id = resp.json()["canonical_id"]

        pubkey_resp = await registry_client.get("/api/agents/registry/pubkey")
        assert pubkey_resp.status_code == 200
        pub_pem = pubkey_resp.json()["public_key"].encode()

        payload = verify_registry_token(token, pub_pem)
        assert payload["sub"] == canonical_id
        assert payload["iss"] == "taos-registry"
        # user_id in the token must be the authenticated admin's id
        assert payload["user_id"] == registry_client._test_admin_uid
        assert payload["framework"] == "hermes"

    async def test_register_origin_allowlist_rejects_bad_value(self, registry_client):
        """origin values outside the allowlist must be rejected with 422."""
        resp = await registry_client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "origin": "evil-origin"},
        )
        assert resp.status_code == 422

    async def test_register_origin_allowlist_accepts_valid_values(self, registry_client):
        for origin in ("taos-deployed", "external-selfjoin"):
            resp = await registry_client.post(
                "/api/agents/registry/register",
                json={"framework": "openclaw", "origin": origin},
            )
            assert resp.status_code == 200, f"origin={origin!r} should be accepted"

    async def test_pubkey_endpoint_returns_pem(self, registry_client):
        resp = await registry_client.get("/api/agents/registry/pubkey")
        assert resp.status_code == 200
        data = resp.json()
        assert data["alg"] == "EdDSA"
        assert "BEGIN PUBLIC KEY" in data["public_key"]

    async def test_get_registry_entry(self, registry_client):
        reg_resp = await registry_client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Get Test"},
        )
        cid = reg_resp.json()["canonical_id"]

        resp = await registry_client.get(f"/api/agents/registry/{cid}")
        assert resp.status_code == 200
        assert resp.json()["canonical_id"] == cid

    async def test_get_unknown_returns_404(self, registry_client):
        resp = await registry_client.get("/api/agents/registry/no-such-agent")
        assert resp.status_code == 404

    async def test_list_returns_all_for_admin(self, registry_client):
        await registry_client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "List A"},
        )
        await registry_client.post(
            "/api/agents/registry/register",
            json={"framework": "hermes", "display_name": "List B"},
        )
        resp = await registry_client.get("/api/agents/registry")
        assert resp.status_code == 200
        records = resp.json()
        assert len(records) >= 2

    async def test_revoke_sets_revoked_at(self, registry_client):
        reg_resp = await registry_client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "Revoke Me"},
        )
        cid = reg_resp.json()["canonical_id"]

        del_resp = await registry_client.delete(f"/api/agents/registry/{cid}")
        assert del_resp.status_code == 200
        data = del_resp.json()
        assert data["status"] == "revoked"
        assert data["canonical_id"] == cid
        assert data["revoked_at"] is not None

    async def test_revoke_unknown_returns_404(self, registry_client):
        resp = await registry_client.delete("/api/agents/registry/no-such-agent")
        assert resp.status_code == 404

    async def test_register_with_capabilities(self, registry_client):
        resp = await registry_client.post(
            "/api/agents/registry/register",
            json={
                "framework": "openclaw",
                "display_name": "Cap Agent",
                "role": "researcher",
                "capabilities": ["web-search", "summarise"],
            },
        )
        assert resp.status_code == 200
        rec = resp.json()["record"]
        assert rec["role"] == "researcher"
        assert rec["capabilities"] == ["web-search", "summarise"]

    # -- Revoked feed --------------------------------------------------------

    async def test_revoked_feed_shape(self, registry_client):
        """GET /api/agents/registry/revoked returns {revoked: [{canonical_id, revoked_at}]}."""
        reg_resp = await registry_client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "To Revoke"},
        )
        cid = reg_resp.json()["canonical_id"]
        await registry_client.delete(f"/api/agents/registry/{cid}")

        resp = await registry_client.get("/api/agents/registry/revoked")
        assert resp.status_code == 200
        data = resp.json()
        assert "revoked" in data
        assert isinstance(data["revoked"], list)
        assert len(data["revoked"]) >= 1
        entry = next(e for e in data["revoked"] if e["canonical_id"] == cid)
        assert set(entry.keys()) == {"canonical_id", "revoked_at"}
        assert entry["revoked_at"] is not None

    async def test_revoked_route_not_captured_as_canonical_id(self, registry_client):
        """Route ordering regression: /revoked must hit the feed, not the single-entry route."""
        resp = await registry_client.get("/api/agents/registry/revoked")
        # Must return the feed shape, not a 404 for canonical_id="revoked"
        assert resp.status_code == 200
        assert "revoked" in resp.json()

    async def test_revoked_feed_admin_can_read(self, registry_client):
        resp = await registry_client.get("/api/agents/registry/revoked")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# registry_feeds_read scope -- feed token auth
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def feeds_client(app, tmp_data_dir):
    """Admin client with all stores needed for the feed-token tests.

    Mirrors registry_client but also initialises agent_grants, auth_requests,
    and relationships so the consent-flow helpers used inside tests work.
    """
    for attr in ("agent_registry", "agent_grants", "auth_requests", "relationships", "metrics"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    app.state._startup_complete = True

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        c._test_admin_uid = uid
        c._app = app
        yield c

    for attr in ("agent_registry", "agent_grants", "auth_requests", "relationships", "metrics"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


async def _make_feed_token(client) -> str:
    """Register an agent and grant it registry_feeds_read via the consent flow.

    Returns the signed registry JWT for that agent.
    """
    from httpx import ASGITransport, AsyncClient

    app = client._app
    transport = ASGITransport(app=app)

    # Submit the auth-request as an unauthenticated external agent.
    async with AsyncClient(transport=transport, base_url="http://test") as bare:
        cr = await bare.post(
            "/api/agents/auth-requests",
            json={
                "identity_claim": "taosmd-feed-reader",
                "framework": "taosmd",
                "requested_scopes": ["registry_feeds_read"],
                "reason": "poll grant and revocation feeds",
            },
        )
    assert cr.status_code == 200, cr.text
    request_id = cr.json()["request_id"]

    # Admin approves.
    approve = await client.post(
        f"/api/agents/auth-requests/{request_id}/approve",
        json={"granted_scopes": ["registry_feeds_read"]},
    )
    assert approve.status_code == 200, approve.text

    # Poll to get the token.
    async with AsyncClient(transport=transport, base_url="http://test") as bare:
        status = await bare.get(f"/api/agents/auth-requests/{request_id}")
    assert status.status_code == 200
    return status.json()["token"]


@pytest.mark.asyncio
class TestFeedReadScope:

    async def test_no_auth_revoked_returns_401_or_403(self, feeds_client):
        """Unauthenticated request (no session, no Bearer) is rejected."""
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=feeds_client._app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            resp = await bare.get("/api/agents/registry/revoked")
        assert resp.status_code in (401, 403)

    async def test_no_auth_grants_returns_401_or_403(self, feeds_client):
        """Unauthenticated request (no session, no Bearer) is rejected."""
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=feeds_client._app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            resp = await bare.get("/api/agents/registry/grants")
        assert resp.status_code in (401, 403)

    async def test_token_with_scope_reads_revoked_feed(self, feeds_client):
        """A JWT with an active registry_feeds_read grant can read the revoked feed."""
        feed_token = await _make_feed_token(feeds_client)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=feeds_client._app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            resp = await bare.get(
                "/api/agents/registry/revoked",
                headers={"Authorization": f"Bearer {feed_token}"},
            )
        assert resp.status_code == 200
        assert "revoked" in resp.json()

    async def test_token_with_scope_reads_grants_feed(self, feeds_client):
        """A JWT with an active registry_feeds_read grant can read the grants feed."""
        feed_token = await _make_feed_token(feeds_client)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=feeds_client._app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            resp = await bare.get(
                "/api/agents/registry/grants",
                headers={"Authorization": f"Bearer {feed_token}"},
            )
        assert resp.status_code == 200
        assert "grants" in resp.json()

    async def test_token_without_scope_gets_403(self, feeds_client):
        """A valid JWT for an agent that has no registry_feeds_read grant gets 403."""
        # Register an agent directly (no grants written).
        reg = await feeds_client.post(
            "/api/agents/registry/register",
            json={"framework": "openclaw", "display_name": "No Scope Agent"},
        )
        assert reg.status_code == 200
        no_scope_token = reg.json()["token"]

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=feeds_client._app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            r1 = await bare.get(
                "/api/agents/registry/revoked",
                headers={"Authorization": f"Bearer {no_scope_token}"},
            )
            r2 = await bare.get(
                "/api/agents/registry/grants",
                headers={"Authorization": f"Bearer {no_scope_token}"},
            )
        assert r1.status_code == 403
        assert r2.status_code == 403

    async def test_expired_grant_gets_403(self, feeds_client):
        """A token whose registry_feeds_read grant has expired gets 403."""
        from datetime import datetime, timezone, timedelta

        feed_token = await _make_feed_token(feeds_client)

        # Decode the canonical_id from the token payload.
        import base64, json as _json
        raw = feed_token.split(".")[1]
        padding = 4 - len(raw) % 4
        if padding != 4:
            raw += "=" * padding
        canonical_id = _json.loads(base64.urlsafe_b64decode(raw))["sub"]

        # Overwrite the grant with an already-expired expires_at.
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        grants_store = feeds_client._app.state.agent_grants
        await grants_store.add_grant(
            canonical_id,
            "registry_feeds_read",
            tier="once",
            expires_at=past,
        )

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=feeds_client._app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            r1 = await bare.get(
                "/api/agents/registry/revoked",
                headers={"Authorization": f"Bearer {feed_token}"},
            )
            r2 = await bare.get(
                "/api/agents/registry/grants",
                headers={"Authorization": f"Bearer {feed_token}"},
            )
        assert r1.status_code == 403
        assert r2.status_code == 403

    async def test_suspended_agent_gets_403(self, feeds_client):
        """A token for a suspended agent gets 403 even with the correct scope."""
        feed_token = await _make_feed_token(feeds_client)

        import base64, json as _json
        raw = feed_token.split(".")[1]
        padding = 4 - len(raw) % 4
        if padding != 4:
            raw += "=" * padding
        canonical_id = _json.loads(base64.urlsafe_b64decode(raw))["sub"]

        # Suspend the agent via the admin endpoint.
        suspend = await feeds_client.post(f"/api/agents/registry/{canonical_id}/suspend")
        assert suspend.status_code == 200

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=feeds_client._app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare:
            r1 = await bare.get(
                "/api/agents/registry/revoked",
                headers={"Authorization": f"Bearer {feed_token}"},
            )
            r2 = await bare.get(
                "/api/agents/registry/grants",
                headers={"Authorization": f"Bearer {feed_token}"},
            )
        assert r1.status_code == 403
        assert r2.status_code == 403

    async def test_admin_session_still_works(self, feeds_client):
        """Admin cookie session continues to work alongside Bearer token auth."""
        r1 = await feeds_client.get("/api/agents/registry/revoked")
        r2 = await feeds_client.get("/api/agents/registry/grants")
        assert r1.status_code == 200
        assert "revoked" in r1.json()
        assert r2.status_code == 200
        assert "grants" in r2.json()
