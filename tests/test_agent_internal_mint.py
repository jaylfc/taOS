"""Internal driver-agent identity minting (taosctl agents mint / seed-internal).

Covers the store-level get_by_handle helper and the admin-only mint-internal /
seed-internal routes: idempotency by handle (no duplicate rows), grant assertion,
token issuance, the admin gate, and a full end-to-end check that a minted token
reads the A2A bus.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import AgentRegistryStore
from taos_test_csrf import csrf_event_hooks


def test_allowed_scopes_matches_canonical_valid_scopes():
    """The mint allowlist must not drift from the consent-flow VALID_SCOPES."""
    from tinyagentos.routes.agent_auth_requests import VALID_SCOPES
    from tinyagentos.routes.agent_registry import _ALLOWED_SCOPES
    assert set(_ALLOWED_SCOPES) == set(VALID_SCOPES)


# ---------------------------------------------------------------------------
# Store: get_by_handle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetByHandle:

    async def _store(self, db_path):
        s = AgentRegistryStore(db_path)
        await s.init()
        return s

    async def test_returns_active_match(self, tmp_path):
        s = await self._store(tmp_path / "reg.db")
        try:
            rec = await s.register(framework="taos-internal", display_name="taos-dev", handle="@taOS-dev", allow_reserved=True)
            got = await s.get_by_handle("@taOS-dev")
            assert got is not None
            assert got["canonical_id"] == rec["canonical_id"]
        finally:
            await s.close()

    async def test_returns_none_for_unknown_handle(self, tmp_path):
        s = await self._store(tmp_path / "reg.db")
        try:
            assert await s.get_by_handle("@nobody") is None
        finally:
            await s.close()

    async def test_ignores_non_active_when_status_active(self, tmp_path):
        s = await self._store(tmp_path / "reg.db")
        try:
            rec = await s.register(framework="taos-internal", display_name="x", handle="@gone")
            await s.revoke(rec["canonical_id"])
            assert await s.get_by_handle("@gone") is None
            assert await s.get_by_handle("@gone", status=None) is not None
        finally:
            await s.close()



# ---------------------------------------------------------------------------
# Routes: mint-internal / seed-internal
# ---------------------------------------------------------------------------

def _mock_bus_client(json_payload: dict):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = json_payload
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


@pytest_asyncio.fixture
async def mint_client(app, tmp_data_dir):
    for attr in ("agent_registry", "agent_grants", "metrics"):
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
        event_hooks=csrf_event_hooks(),
    ) as c:
        c._app = app
        yield c

    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


@pytest.mark.asyncio
class TestMintInternalRoute:

    async def test_mint_creates_agent_grants_and_token(self, mint_client):
        resp = await mint_client.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "@taOS-dev", "slug": "taos-dev", "scopes": ["a2a_send", "a2a_receive"]},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["handle"] == "@taOS-dev"
        assert data["created"] is True
        assert data["canonical_id"].startswith("taos-dev-")
        assert data["token"]

        # The agent is active and holds both grants.
        rec = await mint_client._app.state.agent_registry.get(data["canonical_id"])
        assert rec["status"] == "active"
        grants = await mint_client._app.state.agent_grants.list_grants(data["canonical_id"])
        assert {g["scope"] for g in grants} == {"a2a_send", "a2a_receive"}

    async def test_concurrent_mint_same_handle_makes_one_row(self, mint_client):
        """The mint lock makes concurrent check-then-register atomic: two
        in-flight mints for the same handle yield one row + one canonical_id."""
        import asyncio
        body = {"handle": "@taOS-dev", "slug": "taos-dev", "scopes": ["a2a_receive"]}
        r1, r2 = await asyncio.gather(
            mint_client.post("/api/agents/registry/mint-internal", json=body),
            mint_client.post("/api/agents/registry/mint-internal", json=body),
        )
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["canonical_id"] == r2.json()["canonical_id"]
        rows = await mint_client._app.state.agent_registry.list_all()
        assert sum(1 for r in rows if r["handle"] == "@taOS-dev") == 1

    async def test_mint_is_idempotent_by_handle(self, mint_client):
        first = await mint_client.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "@taOS-dev", "slug": "taos-dev", "scopes": ["a2a_receive"]},
        )
        second = await mint_client.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "@taOS-dev", "slug": "taos-dev", "scopes": ["a2a_receive"]},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["canonical_id"] == second.json()["canonical_id"]
        assert first.json()["created"] is True
        assert second.json()["created"] is False
        # Exactly one registry row for the handle.
        rows = await mint_client._app.state.agent_registry.list_all()
        assert sum(1 for r in rows if r["handle"] == "@taOS-dev") == 1

    async def test_mint_rejects_empty_handle(self, mint_client):
        resp = await mint_client.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "  ", "slug": "x", "scopes": []},
        )
        assert resp.status_code == 422

    async def test_mint_rejects_unknown_scope(self, mint_client):
        """The mint route is not a back door for arbitrary scopes."""
        resp = await mint_client.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "@x", "slug": "x", "scopes": ["a2a_send", "root_everything"]},
        )
        assert resp.status_code == 422

    async def test_mint_rejects_non_internal_handle_owner(self, mint_client):
        """If an active agent from another origin already owns the handle, the
        mint must NOT hand it internal scopes + a token -- it 409s instead."""
        await mint_client._app.state.agent_registry.register(
            framework="claude", display_name="ext", origin="taos-deployed", handle="@Hermes",
        )
        resp = await mint_client.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "@Hermes", "slug": "hermes", "scopes": ["a2a_send"]},
        )
        assert resp.status_code == 409
        # No grant leaked to the external agent that owns the handle.
        rows = await mint_client._app.state.agent_registry.list_all()
        ext = next(r for r in rows if r["handle"] == "@Hermes")
        grants = await mint_client._app.state.agent_grants.list_grants(ext["canonical_id"])
        assert grants == []

    async def test_mint_adopt_vouches_for_preexisting_agent(self, mint_client):
        """With adopt=true the admin vouches for a pre-existing non-internal
        agent (e.g. a driver that self-joined): it reuses that identity, grants
        the scopes, and mints a token -- no new row, origin left untouched."""
        rec = await mint_client._app.state.agent_registry.register(
            framework="claude-code", display_name="taos-dev",
            origin="external-selfjoin", handle="@taOS-dev",
            # Simulates a row minted BEFORE the reserved-prefix era; the guard
            # (rightly) blocks creating this via register() today.
            allow_reserved=True,
        )
        # external-selfjoin starts 'pending'; the real @taOS-dev was approved -> active.
        await mint_client._app.state.agent_registry.set_status(rec["canonical_id"], "active")
        resp = await mint_client.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "@taOS-dev", "slug": "taos-dev",
                  "scopes": ["a2a_send", "a2a_receive"], "adopt": True},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["canonical_id"] == rec["canonical_id"]   # reused, not a new row
        assert data["created"] is False and data["adopted"] is True
        assert data["token"]
        grants = await mint_client._app.state.agent_grants.list_grants(rec["canonical_id"])
        assert {g["scope"] for g in grants} == {"a2a_send", "a2a_receive"}
        # Origin is preserved (not silently rewritten to internal).
        got = await mint_client._app.state.agent_registry.get(rec["canonical_id"])
        assert got["origin"] == "external-selfjoin"
        # Exactly one row for the handle.
        rows = await mint_client._app.state.agent_registry.list_all()
        assert sum(1 for r in rows if r["handle"] == "@taOS-dev") == 1

    async def test_adopt_finds_selfjoined_driver_stored_under_slugified_handle(self, mint_client):
        """A driver that self-joined is stored under the SLUGIFIED handle.

        The consent approve path registers with ``_slugify(identity_claim)``, so
        `@taOSmd-dev` lands in the registry as `taosmd-dev` -- while
        ``_INTERNAL_AGENTS`` carries the display spelling. ``get_by_handle`` is an
        exact SQL match, so a mint that looks up only the display spelling misses
        the existing row and registers a SECOND one: the duplicate receives the
        driver scopes and a token while the original keeps the project grants, and
        the agent silently owns two identities with the wrong one credentialed.

        Note the sibling adopt tests seed the fixture with the '@' spelling, which
        is not what the real approve path writes -- that mismatch is why this went
        unnoticed. This test reproduces what is actually in the live registry.
        """
        registry = mint_client._app.state.agent_registry
        rec = await registry.register(
            framework="claude-code", display_name="taosmd-dev",
            origin="external-selfjoin", handle="taosmd-dev",
        )
        await registry.set_status(rec["canonical_id"], "active")

        resp = await mint_client.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "@taOSmd-dev", "slug": "taosmd-dev",
                  "scopes": ["a2a_send", "a2a_receive"], "adopt": True},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["canonical_id"] == rec["canonical_id"], "forked a second identity"
        assert data["created"] is False and data["adopted"] is True

        # The scopes must land on the ORIGINAL identity, not on a duplicate.
        grants = await mint_client._app.state.agent_grants.list_grants(rec["canonical_id"])
        assert {g["scope"] for g in grants} == {"a2a_send", "a2a_receive"}

        # Exactly one row for this driver under EITHER spelling.
        rows = await registry.list_all()
        assert sum(1 for r in rows if r["handle"] in ("taosmd-dev", "@taOSmd-dev")) == 1

    async def test_mint_finds_row_stored_under_display_spelling_when_given_the_slug(
        self, mint_client
    ):
        """The spelling mismatch forks a row in BOTH directions.

        The sibling test above covers the direction we hit in production (row
        stored slugified, mint given the display spelling). This is the reverse:
        ``handle`` arrives from a request body, so an admin can just as easily
        pass ``taosmd-dev`` while the registry holds ``@taOSmd-dev``. String
        surgery cannot close this one -- slugifying discards case, so
        ``taosmd-dev`` can never be turned back into ``@taOSmd-dev``. Only
        comparing slugs on both sides works, which is why the lookup normalises
        rather than trying a second hand-written spelling.
        """
        registry = mint_client._app.state.agent_registry
        rec = await registry.register(
            framework="claude-code", display_name="taosmd-dev",
            origin="external-selfjoin", handle="@taOSmd-dev",
        )
        await registry.set_status(rec["canonical_id"], "active")

        resp = await mint_client.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "taosmd-dev", "slug": "taosmd-dev",
                  "scopes": ["a2a_send", "a2a_receive"], "adopt": True},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["canonical_id"] == rec["canonical_id"], "forked a second identity"
        assert data["created"] is False

        rows = await registry.list_all()
        assert sum(1 for r in rows if r["handle"] in ("taosmd-dev", "@taOSmd-dev")) == 1

    async def test_mint_without_adopt_rejects_slugified_selfjoined_driver(
        self, mint_client
    ):
        """The normalised lookup must not become an adoption back door.

        Finding the row under its other spelling is only half the contract: the
        non-internal owner check has to fire on the row we found. Without this,
        widening the lookup would silently hand driver scopes and a token to an
        identity the admin never vouched for -- the exact impostor case the 409
        exists for, reachable through the spelling the existing 409 tests do not
        seed.
        """
        registry = mint_client._app.state.agent_registry
        rec = await registry.register(
            framework="claude-code", display_name="taosmd-dev",
            origin="external-selfjoin", handle="taosmd-dev",
        )
        await registry.set_status(rec["canonical_id"], "active")

        resp = await mint_client.post(
            "/api/agents/registry/mint-internal",
            json={"handle": "@taOSmd-dev", "slug": "taosmd-dev",
                  "scopes": ["a2a_send", "a2a_receive"]},
        )
        assert resp.status_code == 409, resp.text

        # And it stayed uncredentialed: no grants, no second row.
        grants = await mint_client._app.state.agent_grants.list_grants(rec["canonical_id"])
        assert grants == []
        rows = await registry.list_all()
        assert sum(1 for r in rows if r["handle"] in ("taosmd-dev", "@taOSmd-dev")) == 1

    async def test_adopt_writes_governance_audit(self, mint_client):
        """Adopting a pre-existing identity is trust-changing -- it must leave a
        governance audit event (action='adopt') for a forensic trail."""
        rec = await mint_client._app.state.agent_registry.register(
            framework="claude-code", display_name="taos-dev",
            origin="external-selfjoin", handle="@taOS-dev",
            # Simulates a row minted BEFORE the reserved-prefix era; the guard
            # (rightly) blocks creating this via register() today.
            allow_reserved=True,
        )
        await mint_client._app.state.agent_registry.set_status(rec["canonical_id"], "active")
        with patch("tinyagentos.routes.agent_registry._audit_governance",
                   new_callable=AsyncMock) as audit:
            resp = await mint_client.post(
                "/api/agents/registry/mint-internal",
                json={"handle": "@taOS-dev", "slug": "taos-dev",
                      "scopes": ["a2a_receive"], "adopt": True},
            )
        assert resp.status_code == 200, resp.text
        audit.assert_awaited_once()
        kwargs = audit.await_args.kwargs
        assert kwargs["action"] == "adopt"
        assert kwargs["canonical_id"] == rec["canonical_id"]
        assert kwargs["actor_user_id"]

    async def test_seed_internal_adopt_handles_preexisting(self, mint_client):
        """seed-internal?adopt=@handle vouches for THAT pre-existing driver
        handle explicitly and still seeds the rest."""
        rec = await mint_client._app.state.agent_registry.register(
            framework="claude-code", display_name="taos-dev",
            origin="external-selfjoin", handle="@taOS-dev",
            # Simulates a row minted BEFORE the reserved-prefix era; the guard
            # (rightly) blocks creating this via register() today.
            allow_reserved=True,
        )
        await mint_client._app.state.agent_registry.set_status(rec["canonical_id"], "active")
        resp = await mint_client.post(
            "/api/agents/registry/seed-internal?adopt=@taOS-dev"
        )
        assert resp.status_code == 200, resp.text
        seeded = {s["handle"]: s for s in resp.json()["seeded"]}
        assert seeded["@taOS-dev"]["adopted"] is True and seeded["@taOS-dev"]["created"] is False
        assert seeded["@Hermes"]["created"] is True and seeded["@Hermes"]["adopted"] is False
        # Without adopt it would have 409d on @taOS-dev:
        resp2 = await mint_client.post("/api/agents/registry/seed-internal")
        assert resp2.status_code == 409

    async def test_seed_internal_adopt_multiple_listed(self, mint_client):
        """Multiple handles (with copy-paste spacing and a duplicate) adopt in
        one call; each named row comes back adopted."""
        reg = mint_client._app.state.agent_registry
        for handle, name in (("@taOS-dev", "taos-dev"), ("@Hermes", "hermes")):
            rec = await reg.register(
                framework="claude-code", display_name=name,
                origin="external-selfjoin", handle=handle,
                # Pre-reserved-prefix-era row; see the adopt tests above.
                allow_reserved=True,
            )
            await reg.set_status(rec["canonical_id"], "active")
        resp = await mint_client.post(
            "/api/agents/registry/seed-internal"
            "?adopt=@taOS-dev,%20@Hermes,@taOS-dev"
        )
        assert resp.status_code == 200, resp.text
        seeded = {s["handle"]: s for s in resp.json()["seeded"]}
        assert seeded["@taOS-dev"]["adopted"] is True
        assert seeded["@Hermes"]["adopted"] is True
        assert seeded["@taOSmd-dev"]["created"] is True

    async def test_seed_internal_rejects_blanket_adopt(self, mint_client):
        """A bare adopt=true (or any non-handle value) is a 400: adoption
        grants driver scopes + a token to a pre-existing identity, so each
        handle must be vouched for explicitly, never all four at once."""
        resp = await mint_client.post(
            "/api/agents/registry/seed-internal?adopt=true"
        )
        assert resp.status_code == 400
        assert "explicitly" in resp.json()["detail"]

    async def test_seed_internal_adopt_only_covers_listed_handles(self, mint_client):
        """A pre-existing handle NOT named in adopt still 409s even when
        another handle is being adopted."""
        reg = mint_client._app.state.agent_registry
        for handle, name in (("@taOS-dev", "taos-dev"), ("@Hermes", "hermes")):
            rec = await reg.register(
                framework="claude-code", display_name=name,
                origin="external-selfjoin", handle=handle,
                # Pre-reserved-prefix-era row; see the adopt tests above.
                allow_reserved=True,
            )
            await reg.set_status(rec["canonical_id"], "active")
        resp = await mint_client.post(
            "/api/agents/registry/seed-internal?adopt=@taOS-dev"
        )
        assert resp.status_code == 409

    async def test_mint_requires_auth(self, mint_client):
        """An unauthenticated caller cannot mint (route is admin-only and not on
        the agent-token allowlist)."""
        async with AsyncClient(
            transport=ASGITransport(app=mint_client._app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                "/api/agents/registry/mint-internal",
                json={"handle": "@x", "slug": "x", "scopes": []},
            )
        assert resp.status_code in (401, 403)

    async def test_seed_internal_seeds_four_idempotently(self, mint_client):
        r1 = await mint_client.post("/api/agents/registry/seed-internal")
        assert r1.status_code == 200, r1.text
        seeded = r1.json()["seeded"]
        assert len(seeded) == 4
        handles = {s["handle"] for s in seeded}
        assert handles == {"@taOS-dev", "@taOS-website-dev", "@taOSmd-dev", "@Hermes"}
        for s in seeded:
            assert s["created"] is True
            assert s["token"]
            if s["handle"] == "@taOS-dev":
                # Lead curator: gets project_tasks + project_tasks_update +
                # canvas on prj-5y722y in addition to the baseline a2a scopes
                # (#1968 C1). project_tasks_update is what authorizes PATCH
                # field mutation on the lead's own board (tsk-b6ugu5).
                assert set(s["scopes"]) == {
                    "a2a_send", "a2a_receive",
                    "project_tasks", "project_tasks_update",
                    "canvas_read", "canvas_write",
                }
                # Grants should be project-scoped.
                grants = await mint_client._app.state.agent_grants.list_grants(
                    s["canonical_id"]
                )
                for g in grants:
                    if g["scope"] in ("project_tasks", "project_tasks_update",
                                      "canvas_read", "canvas_write"):
                        assert g.get("project_id") == "prj-5y722y", (
                            f"scope {g['scope']!r} not bound to prj-5y722y"
                        )
            else:
                # Baseline agents: only a2a comms.
                assert set(s["scopes"]) == {"a2a_send", "a2a_receive"}

        # Re-run: no duplicate rows, all created=False.
        r2 = await mint_client.post("/api/agents/registry/seed-internal")
        assert r2.status_code == 200
        assert all(s["created"] is False for s in r2.json()["seeded"])
        rows = await mint_client._app.state.agent_registry.list_all()
        internal = [r for r in rows if r["origin"] == "taos-internal"]
        assert len(internal) == 4

    async def test_seed_internal_reseed_reasserts_lead_grant(self, mint_client):
        """seed-internal is idempotent per handle: re-seeding an EXISTING lead
        identity does not create a duplicate row, and re-asserts the lead's
        grants (add_grant is idempotent). After a second seed, @taOS-dev must
        still hold project_tasks_update bound to prj-5y722y (tsk-b6ugu5)."""
        r1 = await mint_client.post("/api/agents/registry/seed-internal")
        assert r1.status_code == 200, r1.text
        lead1 = next(s for s in r1.json()["seeded"] if s["handle"] == "@taOS-dev")
        cid = lead1["canonical_id"]

        # Re-seed: same canonical_id, created=False.
        r2 = await mint_client.post("/api/agents/registry/seed-internal")
        assert r2.status_code == 200
        lead2 = next(s for s in r2.json()["seeded"] if s["handle"] == "@taOS-dev")
        assert lead2["canonical_id"] == cid
        assert lead2["created"] is False

        # The project_tasks_update grant survives re-seed (re-asserted by add_grant).
        grants = await mint_client._app.state.agent_grants.list_grants(cid)
        scopes = {g["scope"] for g in grants}
        assert "project_tasks_update" in scopes
        update_grant = next(
            g for g in grants if g["scope"] == "project_tasks_update"
        )
        assert update_grant.get("project_id") == "prj-5y722y"

    async def test_minted_token_reads_the_bus(self, mint_client):
        """End to end: a token from seed-internal reads the A2A bus (a2a_receive)."""
        seeded = (await mint_client.post("/api/agents/registry/seed-internal")).json()["seeded"]
        token = next(s["token"] for s in seeded if s["handle"] == "@Hermes")
        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=_mock_bus_client({"channels": []}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=mint_client._app), base_url="http://test"
            ) as bare:
                resp = await bare.get(
                    "/api/a2a/bus/channels",
                    headers={"Authorization": f"Bearer {token}"},
                )
        assert resp.status_code == 200
        assert resp.json()["available"] is True
