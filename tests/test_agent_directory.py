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


async def _register_agent(app, *, display_name, handle="", role=None, user_id="owner-1"):
    """Register an active agent directly; return (canonical_id, registry JWT)."""
    registry = app.state.agent_registry
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="taosmd",
        display_name=display_name,
        user_id=user_id,
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
        assert all(e["bio_untrusted"] is True for e in entries.values())
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
        admin_uid = directory_client._test_admin_uid
        cid_a, _ = await _register_agent(
            app, display_name="PA Alice", handle="@paalice", user_id=admin_uid
        )
        cid_b, _ = await _register_agent(app, display_name="Other Bob", handle="@otherbob")

        settings = app.state.desktop_settings
        await settings.save_preference(
            admin_uid, "assistant_studio", {"pa_canonical_id": cid_a}
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


# ---------------------------------------------------------------------------
# Sanitising beyond C0/C1: Unicode categories + NFKC (#2119 review round)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBioSanitisingCategories:
    async def _stored_bio(self, tmp_path, raw):
        store = AgentRegistryStore(tmp_path / "reg.db")
        await store.init()
        try:
            rec = await store.register(framework="hermes", display_name="San Agent")
            updated = await store.set_bio(rec["canonical_id"], raw)
            assert updated is not None
            return updated["bio"]
        finally:
            await store.close()

    async def test_bidi_override_cf_stripped(self, tmp_path):
        # U+202E RIGHT-TO-LEFT OVERRIDE is category Cf.
        assert await self._stored_bio(tmp_path, "ab\u202ecd") == "abcd"

    async def test_zero_width_space_cf_stripped(self, tmp_path):
        # U+200B ZERO WIDTH SPACE is category Cf.
        assert await self._stored_bio(tmp_path, "ab\u200bcd") == "abcd"

    async def test_isolates_cf_stripped(self, tmp_path):
        # U+2066 LEFT-TO-RIGHT ISOLATE / U+2069 POP DIRECTIONAL ISOLATE (Cf).
        assert await self._stored_bio(tmp_path, "ab\u2066cd\u2069ef") == "abcdef"

    async def test_line_separator_zl_stripped(self, tmp_path):
        # U+2028 LINE SEPARATOR is category Zl.
        assert await self._stored_bio(tmp_path, "ab\u2028cd") == "abcd"

    async def test_paragraph_separator_zp_stripped(self, tmp_path):
        # U+2029 PARAGRAPH SEPARATOR is category Zp.
        assert await self._stored_bio(tmp_path, "ab\u2029cd") == "abcd"

    async def test_lone_surrogate_cs_stripped(self, tmp_path):
        # A lone surrogate is category Cs.
        assert await self._stored_bio(tmp_path, "ab\ud800cd") == "abcd"

    async def test_nfkc_normalisation_applied(self, tmp_path):
        # Full-width letters and the fi ligature fold under NFKC.
        assert await self._stored_bio(tmp_path, "ＡＢＣ ﬁ") == "ABC fi"

    async def test_cap_applies_after_nfkc(self, tmp_path):
        store = AgentRegistryStore(tmp_path / "reg.db")
        await store.init()
        try:
            rec = await store.register(framework="hermes", display_name="Cap2 Agent")
            with pytest.raises(ValueError):
                # 101 ligatures: 101 chars before NFKC, 202 after.
                await store.set_bio(rec["canonical_id"], "ﬁ" * 101)
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# Directory visibility scoping + per-owner PA (#2119 review round)
# ---------------------------------------------------------------------------

async def _member_client(app, username="member2", password="memberpass12345"):
    """Non-admin member session client; exposes ``._member_uid``."""
    auth_mgr = app.state.auth
    if not auth_mgr.find_user(username):
        invite_code = auth_mgr.add_user_invite(username, "admin")
        auth_mgr.complete_invite(username, invite_code, "Test Member", "", password)
    record = auth_mgr.find_user(username)
    uid = record["id"] if record else ""
    token = auth_mgr.create_session(user_id=uid, long_lived=True)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )
    client._member_uid = uid
    return client


async def _visibility_world(app, member_uid):
    """Two session people + a third owner, three agents, three projects.

    - admin owns agent_admin (projects: shared, admin-only)
    - member owns agent_member (projects: shared); member is a human
      member of the shared project only
    - person-x (no session) owns agent_outsider (projects: outsider-only)
    """
    registry = app.state.agent_registry
    pstore = app.state.project_store
    if pstore._db is None:
        await pstore.init()
    admin_uid = app.state.auth.find_user("admin")["id"]

    shared = await pstore.create_project(
        "Shared Proj", "shared-proj", admin_uid, user_id=admin_uid
    )
    admin_only = await pstore.create_project(
        "Admin Only Proj", "admin-only-proj", admin_uid, user_id=admin_uid
    )
    outsider_only = await pstore.create_project(
        "Outsider Proj", "outsider-proj", "person-x", user_id="person-x"
    )
    await pstore.add_member(shared["id"], member_uid, "human")

    cid_admin, _ = await _register_agent(
        app, display_name="Keeper Agent", handle="@keeperagent", user_id=admin_uid
    )
    cid_member, token_member = await _register_agent(
        app, display_name="Helper Agent", handle="@helperagent", user_id=member_uid
    )
    cid_outsider, _ = await _register_agent(
        app, display_name="Remote Agent", handle="@remoteagent", user_id="person-x"
    )
    await registry.set_bio(cid_admin, "admin agent bio")
    await registry.set_bio(cid_member, "member agent bio")
    await registry.set_bio(cid_outsider, "outsider secret bio")
    await registry.set_sponsorship(cid_admin, shared["id"], "contact-a")
    await registry.set_sponsorship(cid_admin, admin_only["id"], "contact-a")
    await registry.set_sponsorship(cid_member, shared["id"], "contact-m")
    await registry.set_sponsorship(cid_outsider, outsider_only["id"], "contact-x")
    return {
        "admin_uid": admin_uid,
        "shared": shared["id"],
        "admin_only": admin_only["id"],
        "outsider_only": outsider_only["id"],
        "cid_admin": cid_admin,
        "cid_member": cid_member,
        "cid_outsider": cid_outsider,
        "token_member": token_member,
    }


@pytest.mark.asyncio
class TestDirectoryVisibility:
    async def test_member_sees_own_and_shared_not_outsider(self, directory_client):
        app = directory_client._app
        async with await _member_client(app) as member:
            world = await _visibility_world(app, member._member_uid)
            resp = await member.get("/api/agents/directory")
        assert resp.status_code == 200, resp.text
        entries = {e["canonical_id"]: e for e in resp.json()["agents"]}
        # Own agent + the admin's agent shared via the shared project.
        assert set(entries) == {world["cid_member"], world["cid_admin"]}
        # Project membership leaks only for projects the member is in.
        assert entries[world["cid_admin"]]["projects"] == [world["shared"]]
        assert entries[world["cid_member"]]["projects"] == [world["shared"]]
        # The outsider is absent through every field, not just the id list.
        assert world["cid_outsider"] not in entries
        assert "outsider secret bio" not in resp.text
        assert "Remote Agent" not in resp.text
        assert world["outsider_only"] not in resp.text
        assert world["admin_only"] not in resp.text
        assert all(e["bio_untrusted"] is True for e in entries.values())

    async def test_agent_caller_sees_owner_agents_and_project_sharers(
        self, directory_client
    ):
        app = directory_client._app
        member = await _member_client(app)
        world = await _visibility_world(app, member._member_uid)
        async with _bare(app) as bare:
            resp = await bare.get(
                "/api/agents/directory",
                headers={"Authorization": f"Bearer {world['token_member']}"},
            )
        assert resp.status_code == 200, resp.text
        entries = {e["canonical_id"]: e for e in resp.json()["agents"]}
        # Its owner's agents (itself) + agents on projects it shares.
        assert set(entries) == {world["cid_member"], world["cid_admin"]}
        assert entries[world["cid_admin"]]["projects"] == [world["shared"]]
        assert "outsider secret bio" not in resp.text

    async def test_admin_sees_all_agents_and_projects(self, directory_client):
        app = directory_client._app
        member = await _member_client(app)
        world = await _visibility_world(app, member._member_uid)
        resp = await directory_client.get("/api/agents/directory")
        assert resp.status_code == 200, resp.text
        entries = {e["canonical_id"]: e for e in resp.json()["agents"]}
        assert set(entries) == {
            world["cid_admin"],
            world["cid_member"],
            world["cid_outsider"],
        }
        assert sorted(entries[world["cid_admin"]]["projects"]) == sorted(
            [world["shared"], world["admin_only"]]
        )
        assert entries[world["cid_outsider"]]["bio"] == "outsider secret bio"


@pytest.mark.asyncio
class TestPaPerOwner:
    async def test_each_owner_sees_only_their_own_pa_marked(self, directory_client):
        app = directory_client._app
        member = await _member_client(app)
        world = await _visibility_world(app, member._member_uid)
        settings = app.state.desktop_settings
        await settings.save_preference(
            world["admin_uid"], "assistant_studio",
            {"pa_canonical_id": world["cid_admin"]},
        )
        await settings.save_preference(
            member._member_uid, "assistant_studio",
            {"pa_canonical_id": world["cid_member"]},
        )
        # A legacy install-wide pick naming a third person's agent must
        # mark nothing for anyone.
        await settings.save_preference(
            "user", "assistant_studio",
            {"pa_canonical_id": world["cid_outsider"]},
        )

        resp = await directory_client.get("/api/agents/directory")
        entries = {e["canonical_id"]: e for e in resp.json()["agents"]}
        assert entries[world["cid_admin"]]["is_pa"] is True
        assert entries[world["cid_member"]]["is_pa"] is False
        assert entries[world["cid_outsider"]]["is_pa"] is False

        async with member:
            resp = await member.get("/api/agents/directory")
        entries = {e["canonical_id"]: e for e in resp.json()["agents"]}
        assert entries[world["cid_member"]]["is_pa"] is True
        assert entries[world["cid_admin"]]["is_pa"] is False

    async def test_pick_naming_another_persons_agent_is_not_marked(
        self, directory_client
    ):
        app = directory_client._app
        member = await _member_client(app)
        world = await _visibility_world(app, member._member_uid)
        settings = app.state.desktop_settings
        # The member's pick names the ADMIN's agent: visible via the shared
        # project, but it is not the member's PA and must not be marked.
        await settings.save_preference(
            member._member_uid, "assistant_studio",
            {"pa_canonical_id": world["cid_admin"]},
        )
        async with member:
            resp = await member.get("/api/agents/directory")
        entries = {e["canonical_id"]: e for e in resp.json()["agents"]}
        assert entries[world["cid_admin"]]["is_pa"] is False
        assert entries[world["cid_member"]]["is_pa"] is False

    async def test_studio_sync_writes_the_per_owner_key(self, directory_client):
        """PUT /api/preferences/assistant_studio lands under the caller's id."""
        app = directory_client._app
        admin_uid = directory_client._test_admin_uid
        cid, _ = await _register_agent(
            app, display_name="Sync PA", handle="@syncpa", user_id=admin_uid
        )
        resp = await directory_client.put(
            "/api/preferences/assistant_studio", json={"pa_canonical_id": cid}
        )
        assert resp.status_code == 200, resp.text
        settings = app.state.desktop_settings
        own = await settings.get_preference(admin_uid, "assistant_studio")
        assert own.get("pa_canonical_id") == cid
        legacy = await settings.get_preference("user", "assistant_studio")
        assert legacy.get("pa_canonical_id") != cid
        resp = await directory_client.get("/api/preferences/assistant_studio")
        assert resp.status_code == 200, resp.text
        assert resp.json().get("pa_canonical_id") == cid
