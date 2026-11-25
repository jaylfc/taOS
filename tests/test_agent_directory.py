"""Tests for the agent directory with self-maintained bios (#2119).

Covers:
  - Store: bio column migration default, set_bio round-trip, 200-char cap,
    control-character stripping.
  - Routes: GET /api/agents/directory lists active agents with bios, role
    and derived project tags; the PA marker follows the Assistant Studio
    selection synced to the desktop-settings preference store.
  - Routes: PUT /api/agents/registry/{id}/bio accepts only the OWNING
    agent's registry token; a forged update (agent A's token, agent B's id)
    is refused and leaves B's bio unchanged; derived fields are not writable
    through the bio API.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    mint_registry_token,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def directory_client(app, tmp_data_dir):
    """Admin-session client with the registry + settings stores initialised.

    Exposes ``._app`` so tests can register agents / mint tokens directly
    against the stores and drive bare (cookieless) agent requests.
    """
    for attr in ("agent_registry", "metrics", "desktop_settings"):
        store = getattr(app.state, attr, None)
        if store is not None and store._db is None:
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
        c._test_admin_uid = uid
        yield c

    for attr in ("agent_registry", "metrics", "desktop_settings"):
        store = getattr(app.state, attr, None)
        if store is not None and store._db is not None:
            await store.close()


def _bare(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _register_agent(app, *, display_name, handle="", role=None):
    """Register an active agent directly; return (canonical_id, registry JWT)."""
    registry = app.state.agent_registry
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="taosmd",
        display_name=display_name,
        user_id="owner-1",
        origin="taos-deployed",
        handle=handle,
        role=role,
    )
    cid = rec["canonical_id"]
    token = mint_registry_token(cid, priv, user_id="owner-1", framework="taosmd")
    return cid, token


# ---------------------------------------------------------------------------
# Store-level tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBioStore:
    async def _make_store(self, db_path):
        store = AgentRegistryStore(db_path)
        await store.init()
        return store

    async def test_bio_defaults_empty_and_round_trips(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="hermes", display_name="Bio Agent")
            assert rec["bio"] == ""
            updated = await store.set_bio(rec["canonical_id"], "Keeper of the bus.")
            assert updated is not None
            assert updated["bio"] == "Keeper of the bus."
            fetched = await store.get(rec["canonical_id"])
            assert fetched["bio"] == "Keeper of the bus."
        finally:
            await store.close()

    async def test_set_bio_unknown_agent_returns_none(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            assert await store.set_bio("ghost-20260101-000000", "boo") is None
        finally:
            await store.close()

    async def test_bio_cap_is_200_chars(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="hermes", display_name="Cap Agent")
            ok = await store.set_bio(rec["canonical_id"], "x" * 200)
            assert ok is not None and len(ok["bio"]) == 200
            with pytest.raises(ValueError):
                await store.set_bio(rec["canonical_id"], "x" * 201)
        finally:
            await store.close()

    async def test_bio_control_characters_are_stripped(self, tmp_path):
        store = await self._make_store(tmp_path / "reg.db")
        try:
            rec = await store.register(framework="hermes", display_name="Ctrl Agent")
            updated = await store.set_bio(
                rec["canonical_id"], "line one\nline two\x00\x07 end"
            )
            assert updated is not None
            assert "\n" not in updated["bio"]
            assert "\x00" not in updated["bio"]
            assert "\x07" not in updated["bio"]
            assert "line one" in updated["bio"]
            assert "line two" in updated["bio"]
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# Directory route tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAgentDirectoryRoute:
    async def test_directory_lists_agents_with_bios_roles_and_projects(
        self, directory_client
    ):
        app = directory_client._app
        registry = app.state.agent_registry
        cid_a, _ = await _register_agent(
            app, display_name="Directory Alice", handle="@alice", role="lead"
        )
        cid_b, _ = await _register_agent(
            app, display_name="Directory Bob", handle="@bob", role="worker"
        )
        await registry.set_bio(cid_a, "Lead on the keep; owns deploys.")
        await registry.set_bio(cid_b, "Worker; libraries and tests.")
        await registry.set_sponsorship(cid_a, "proj-keep", "contact-1")
        await registry.set_sponsorship(cid_a, "proj-docs", "contact-1")
        await registry.set_sponsorship(cid_b, "proj-keep", "contact-2")

        resp = await directory_client.get("/api/agents/directory")
        assert resp.status_code == 200, resp.text
        entries = {e["canonical_id"]: e for e in resp.json()["agents"]}
        assert set(entries) == {cid_a, cid_b}

        alice = entries[cid_a]
        assert alice["handle"] == "@alice"
        assert alice["display_name"] == "Directory Alice"
        assert alice["bio"] == "Lead on the keep; owns deploys."
        assert alice["role"] == "lead"
        assert sorted(alice["projects"]) == ["proj-docs", "proj-keep"]
        assert alice["is_pa"] is False

        bob = entries[cid_b]
        assert bob["bio"] == "Worker; libraries and tests."
        assert bob["projects"] == ["proj-keep"]
        assert bob["is_pa"] is False

    async def test_directory_marks_pa_from_assistant_studio_selection(
        self, directory_client
    ):
        app = directory_client._app
        cid_a, _ = await _register_agent(app, display_name="PA Alice", handle="@paalice")
        cid_b, _ = await _register_agent(app, display_name="Other Bob", handle="@otherbob")

        settings = app.state.desktop_settings
        await settings.save_preference(
            "user", "assistant_studio", {"pa_canonical_id": cid_a}
        )

        resp = await directory_client.get("/api/agents/directory")
        assert resp.status_code == 200, resp.text
        entries = {e["canonical_id"]: e for e in resp.json()["agents"]}
        assert entries[cid_a]["is_pa"] is True
        assert entries[cid_b]["is_pa"] is False

    async def test_directory_requires_authentication(self, directory_client):
        app = directory_client._app
        await _register_agent(app, display_name="Auth Alice", handle="@authalice")
        async with _bare(app) as bare:
            resp = await bare.get("/api/agents/directory")
        assert resp.status_code == 401

    async def test_agent_can_read_directory_with_registry_token(
        self, directory_client
    ):
        app = directory_client._app
        cid_a, token_a = await _register_agent(
            app, display_name="Reader Alice", handle="@readeralice"
        )
        await _register_agent(app, display_name="Listed Bob", handle="@listedbob")
        async with _bare(app) as bare:
            resp = await bare.get(
                "/api/agents/directory",
                headers={"Authorization": f"Bearer {token_a}"},
            )
        assert resp.status_code == 200, resp.text
        ids = [e["canonical_id"] for e in resp.json()["agents"]]
        assert cid_a in ids


# ---------------------------------------------------------------------------
# Bio update route tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBioUpdateRoute:
    async def test_owner_can_set_own_bio(self, directory_client):
        app = directory_client._app
        registry = app.state.agent_registry
        cid_a, token_a = await _register_agent(
            app, display_name="Owner Alice", handle="@owneralice"
        )
        async with _bare(app) as bare:
            resp = await bare.put(
                f"/api/agents/registry/{cid_a}/bio",
                json={"bio": "I keep the directory honest."},
                headers={"Authorization": f"Bearer {token_a}"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["bio"] == "I keep the directory honest."
        record = await registry.get(cid_a)
        assert record["bio"] == "I keep the directory honest."

    async def test_forged_bio_update_is_refused_and_target_unchanged(
        self, directory_client
    ):
        app = directory_client._app
        registry = app.state.agent_registry
        cid_a, token_a = await _register_agent(
            app, display_name="Forge Alice", handle="@forgealice"
        )
        cid_b, _token_b = await _register_agent(
            app, display_name="Victim Bob", handle="@victimbob"
        )
        await registry.set_bio(cid_b, "Bob's genuine bio.")

        async with _bare(app) as bare:
            resp = await bare.put(
                f"/api/agents/registry/{cid_b}/bio",
                json={"bio": "Forged by Alice."},
                headers={"Authorization": f"Bearer {token_a}"},
            )
        assert resp.status_code == 403, resp.text
        record = await registry.get(cid_b)
        assert record["bio"] == "Bob's genuine bio."

    async def test_bio_update_requires_agent_token(self, directory_client):
        """A human session alone cannot set a bio: the credential is the agent's."""
        app = directory_client._app
        cid_a, _ = await _register_agent(
            app, display_name="Session Alice", handle="@sessionalice"
        )
        resp = await directory_client.put(
            f"/api/agents/registry/{cid_a}/bio",
            json={"bio": "Set by a human session."},
        )
        assert resp.status_code == 401, resp.text
        record = await app.state.agent_registry.get(cid_a)
        assert record["bio"] == ""

    async def test_bio_update_cap_enforced(self, directory_client):
        app = directory_client._app
        cid_a, token_a = await _register_agent(
            app, display_name="Cap Alice", handle="@capalice"
        )
        async with _bare(app) as bare:
            resp = await bare.put(
                f"/api/agents/registry/{cid_a}/bio",
                json={"bio": "y" * 201},
                headers={"Authorization": f"Bearer {token_a}"},
            )
        assert resp.status_code == 422, resp.text
        record = await app.state.agent_registry.get(cid_a)
        assert record["bio"] == ""

    async def test_bio_update_rejects_derived_fields(self, directory_client):
        """Project membership is derived, never writable via the bio API."""
        app = directory_client._app
        cid_a, token_a = await _register_agent(
            app, display_name="Derived Alice", handle="@derivedalice"
        )
        async with _bare(app) as bare:
            resp = await bare.put(
                f"/api/agents/registry/{cid_a}/bio",
                json={"bio": "hi", "projects": ["proj-secret"]},
                headers={"Authorization": f"Bearer {token_a}"},
            )
        assert resp.status_code == 422, resp.text
