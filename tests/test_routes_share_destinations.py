import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.device_store import DeviceStore
from tinyagentos.agent_registry_store import AgentRegistryStore


@pytest.fixture
def app(tmp_data_dir):
    return create_app(data_dir=tmp_data_dir)


@pytest.mark.asyncio
class TestShareDestinations:
    async def test_unauthenticated_returns_401(self, app, tmp_data_dir):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/share/destinations")
            assert resp.status_code == 401

    async def test_library_always_present_for_paired_device(self, client, app):
        reg = await client.post("/api/devices/register", json={"platform": "ios"})
        assert reg.status_code == 200
        token = reg.json()["scoped_token"]

        resp = await client.get(
            "/api/share/destinations",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "destinations" in body
        kinds = {d["kind"] for d in body["destinations"]}
        assert "library" in kinds
        lib = next(d for d in body["destinations"] if d["kind"] == "library")
        assert lib["id"] == "library"
        assert lib["label"] == "Library"

    async def test_project_filtering_excludes_other_user_projects(self, client, app):
        await app.state.project_store.create_project(
            name="Other Project",
            slug="other-project",
            created_by="other-user",
            user_id="other-user",
        )

        reg = await client.post("/api/devices/register", json={"platform": "ios"})
        token = reg.json()["scoped_token"]

        resp = await client.get(
            "/api/share/destinations",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        slugs = {d["id"] for d in body["destinations"] if d["kind"] == "project_files"}
        assert "other-project" not in slugs

    async def test_response_shape_and_correct_kinds(self, client, app):
        reg = await client.post("/api/devices/register", json={"platform": "ios"})
        token = reg.json()["scoped_token"]

        resp = await client.get(
            "/api/share/destinations",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "destinations" in body
        assert isinstance(body["destinations"], list)

        for entry in body["destinations"]:
            assert "kind" in entry
            assert "id" in entry
            assert "label" in entry
            assert entry["kind"] in ("library", "project_files", "agent_chat")

    async def test_agent_chat_includes_active_agents_in_user_channels(self, client, app):
        registry = app.state.agent_registry
        if registry._db is not None:
            await registry.close()
        await registry.init()

        try:
            primary = app.state.auth.get_primary_user()
            user_id = primary["id"] if primary else "admin"

            agent_record = await registry.register(
                display_name="Test Agent",
                framework="test",
                user_id=user_id,
            )
            canonical_id = agent_record["canonical_id"]

            await app.state.chat_channels.create_channel(
                name="dm",
                type="dm",
                created_by=user_id,
                members=[user_id, canonical_id],
            )

            reg = await client.post("/api/devices/register", json={"platform": "ios"})
            token = reg.json()["scoped_token"]

            resp = await client.get(
                "/api/share/destinations",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            agents = [d for d in body["destinations"] if d["kind"] == "agent_chat"]
            agent_ids = {a["id"] for a in agents}
            assert canonical_id in agent_ids
        finally:
            await registry.close()
            app.state.agent_registry = registry

    async def _prep_registry_and_device(self, app, client):
        """Init a fresh registry and register a paired device for *app*.

        Returns (registry, user_id, device_token). Shared by the bare-slug
        regression tests so they can seed a DM channel member stored as a slug.
        """
        registry = app.state.agent_registry
        if registry._db is not None:
            await registry.close()
        await registry.init()
        primary = app.state.auth.get_primary_user()
        user_id = primary["id"] if primary else "admin"
        reg = await client.post("/api/devices/register", json={"platform": "ios"})
        assert reg.status_code == 200
        token = reg.json()["scoped_token"]
        return registry, user_id, token

    async def test_agent_chat_resolves_bare_slug_member(self, client, app):
        """A DM member stored as a bare slug (not canonical_id) still resolves to
        an active-agent destination. Storing the canonical_id would make
        ``registry.get(member)`` succeed and never exercise the ``get_by_slug``
        fallback, so this pins the fallback path itself."""
        registry, user_id, token = await self._prep_registry_and_device(app, client)
        try:
            agent = await registry.register(
                framework="openclaw", display_name="Alpha", user_id=user_id
            )
            slug = "alpha"
            assert agent["canonical_id"].startswith(f"{slug}-")

            await app.state.chat_channels.create_channel(
                name="dm",
                type="dm",
                created_by=user_id,
                members=[user_id, slug],
            )

            resp = await client.get(
                "/api/share/destinations",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            chats = [d for d in body["destinations"] if d["kind"] == "agent_chat"]
            ids = {d["id"] for d in chats}
            assert slug in ids
            assert chats[0]["label"] == "Alpha"
        finally:
            await registry.close()

    async def test_agent_chat_bare_slug_does_not_match_longer_slug(
        self, client, app
    ):
        """Negative boundary: member ``alpha`` must NOT match a record slotted
        ``alpha-beta-YYYYMMDD-HHMMSS``. ``get_by_slug('alpha')`` must not
        prefix-match the longer slug."""
        registry, user_id, token = await self._prep_registry_and_device(app, client)
        try:
            wider = await registry.register(
                framework="openclaw", display_name="Alpha Beta", user_id=user_id
            )
            assert wider["canonical_id"].startswith("alpha-beta-")

            await app.state.chat_channels.create_channel(
                name="dm2",
                type="dm",
                created_by=user_id,
                members=[user_id, "alpha"],
            )

            resp = await client.get(
                "/api/share/destinations",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            chats = [d for d in body["destinations"] if d["kind"] == "agent_chat"]
            ids = {d["id"] for d in chats}
            assert "alpha" not in ids
            assert all(d["id"] != wider["canonical_id"] for d in chats)
        finally:
            await registry.close()
