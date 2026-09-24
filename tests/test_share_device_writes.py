"""Tests for device bearer write authorization to share destinations.

Covers the three device-bearer self-service routes plus the share destination
listing:
  - POST /api/library/ingest
  - POST /api/projects/{slug}/files/upload
  - POST /api/chat/messages
  - GET  /api/share/destinations

All tests drive a REAL device bearer through the app with httpx (no patched
auth helpers).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
class TestDeviceBearerLibraryIngest:
    async def test_device_can_ingest_url(self, client, app):
        device = await app.state.device_store.register(
            user_id="lib-user", platform="ios", display_name="test"
        )
        token = device["scoped_token"]

        resp = await client.post(
            "/api/library/ingest",
            headers=_bearer(token),
            data={"url": "https://example.com/test"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "item_id" in body
        assert body["status"] == "pending"

    async def test_device_cannot_ingest_without_token(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as bare_client:
            resp = await bare_client.post(
                "/api/library/ingest",
                data={"url": "https://example.com/test"},
            )
            assert resp.status_code == 401


@pytest.mark.asyncio
class TestDeviceBearerProjectFiles:
    async def test_device_can_upload_to_owned_project(self, client, app):
        user_id = "device-owner"
        project = await app.state.project_store.create_project(
            name="Owned",
            slug="owned",
            created_by=user_id,
            user_id=user_id,
        )
        device = await app.state.device_store.register(
            user_id=user_id, platform="ios", display_name="test"
        )
        token = device["scoped_token"]

        resp = await client.post(
            "/api/projects/owned/files/upload",
            headers=_bearer(token),
            files={"file": ("hello.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "hello.txt"
        assert body["status"] == "uploaded"

    async def test_device_cannot_upload_to_other_project(self, client, app):
        await app.state.project_store.create_project(
            name="Other",
            slug="other",
            created_by="other-user",
            user_id="other-user",
        )
        device = await app.state.device_store.register(
            user_id="device-owner", platform="ios", display_name="test"
        )
        token = device["scoped_token"]

        resp = await client.post(
            "/api/projects/other/files/upload",
            headers=_bearer(token),
            files={"file": ("hello.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code in (403, 404)

    async def test_device_member_with_write_is_refused_like_a_session(self, client, app):
        owner = "project-owner"
        member = "project-member"
        project = await app.state.project_store.create_project(
            name="Shared",
            slug="shared",
            created_by=owner,
            user_id=owner,
        )
        await app.state.project_store.add_member(
            project_id=project["id"],
            member_id=member,
            member_kind="native",
            role="member",
        )
        await app.state.project_store.set_member_canvas(
            project_id=project["id"],
            member_id=member,
            can_write=True,
        )
        device = await app.state.device_store.register(
            user_id=member, platform="ios", display_name="test"
        )
        token = device["scoped_token"]

        resp = await client.post(
            "/api/projects/shared/files/upload",
            headers=_bearer(token),
            files={"file": ("hello.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 403
        body = resp.json()

        # Same member's SESSION upload should also be refused (404 or 403)
        resp2 = await client.post(
            "/api/sessions/upload",
            headers=_bearer(token),
            files={"file": ("session.txt", b"session content", "text/plain")},
        )
        assert resp2.status_code in (403, 404)
        body2 = resp2.json()


@pytest.mark.asyncio
class TestDeviceBearerChatMessages:
    async def test_device_can_post_to_member_channel(self, client, app):
        user_id = "chat-user"
        await app.state.chat_channels.create_channel(
            name="dm",
            type="dm",
            created_by=user_id,
            members=[user_id],
        )
        device = await app.state.device_store.register(
            user_id=user_id, platform="ios", display_name="test"
        )
        token = device["scoped_token"]

        channel = (await app.state.chat_channels.list_channels())[0]
        resp = await client.post(
            "/api/chat/messages",
            headers=_bearer(token),
            json={
                "channel_id": channel["id"],
                "content": "hello from device",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["author_id"] == user_id
        assert body["author_type"] == "user"

    async def test_device_cannot_post_to_non_member_channel(self, client, app):
        await app.state.chat_channels.create_channel(
            name="dm",
            type="dm",
            created_by="other-user",
            members=["other-user"],
        )
        device = await app.state.device_store.register(
            user_id="chat-user", platform="ios", display_name="test"
        )
        token = device["scoped_token"]

        channel = (await app.state.chat_channels.list_channels())[0]
        resp = await client.post(
            "/api/chat/messages",
            headers=_bearer(token),
            json={
                "channel_id": channel["id"],
                "content": "hello from device",
            },
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestShareDestinations:
    async def test_destinations_include_owned_projects(self, client, app):
        user_id = "share-user"
        await app.state.project_store.create_project(
            name="Mine",
            slug="mine",
            created_by=user_id,
            user_id=user_id,
        )
        device = await app.state.device_store.register(
            user_id=user_id, platform="ios", display_name="test"
        )
        token = device["scoped_token"]

        resp = await client.get("/api/share/destinations", headers=_bearer(token))
        assert resp.status_code == 200
        body = resp.json()
        slugs = {d["id"] for d in body["destinations"] if d["kind"] == "project_files"}
        assert "mine" in slugs

    async def test_destinations_exclude_other_user_projects(self, client, app):
        await app.state.project_store.create_project(
            name="Other",
            slug="other",
            created_by="other-user",
            user_id="other-user",
        )
        device = await app.state.device_store.register(
            user_id="share-user", platform="ios", display_name="test"
        )
        token = device["scoped_token"]

        resp = await client.get("/api/share/destinations", headers=_bearer(token))
        assert resp.status_code == 200
        body = resp.json()
        slugs = {d["id"] for d in body["destinations"] if d["kind"] == "project_files"}
        assert "other" not in slugs

    async def test_destinations_include_library(self, client, app):
        device = await app.state.device_store.register(
            user_id="lib-user", platform="ios", display_name="test"
        )
        token = device["scoped_token"]

        resp = await client.get("/api/share/destinations", headers=_bearer(token))
        assert resp.status_code == 200
        body = resp.json()
        kinds = {d["kind"] for d in body["destinations"]}
        assert "library" in kinds
