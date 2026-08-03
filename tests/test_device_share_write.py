"""Tests proving a device token can complete share writes and cannot write elsewhere."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app


@pytest.fixture
def app(tmp_data_dir):
    return create_app(data_dir=tmp_data_dir)


@pytest.mark.asyncio
class TestDeviceShareWritePassthrough:
    """Device bearer tokens may write to the three share destinations
    advertised by /api/share/destinations and cannot reach any other route.
    """

    async def _register_device(self, client) -> str:
        reg = await client.post("/api/devices/register", json={"platform": "ios"})
        assert reg.status_code == 200
        return reg.json()["scoped_token"]

    async def _device_client(self, app, token):
        transport = ASGITransport(app=app)
        return AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        )

    async def _user_id(self, app) -> str:
        primary = app.state.auth.get_primary_user()
        return primary["id"] if primary else "admin"

    # -- Positive: device token can write to each share destination --

    async def test_device_can_ingest_library_url(self, client, app, tmp_data_dir):
        token = await self._register_device(client)
        async with await self._device_client(app, token) as dc:
            resp = await dc.post("/api/library/ingest", data={"url": "https://example.com/page"})
            assert resp.status_code == 202
            body = resp.json()
            assert "item_id" in body
            assert body["status"] == "pending"

    async def test_device_can_post_chat_message(self, client, app, tmp_data_dir):
        token = await self._register_device(client)
        uid = await self._user_id(app)
        async with await self._device_client(app, token) as dc:
            resp = await dc.post(
                "/api/chat/messages",
                json={
                    "channel_id": "general",
                    "content": "hello from device",
                    "author_id": uid,
                    "author_type": "agent",
                },
            )
            assert resp.status_code == 200
            assert resp.json()["content"] == "hello from device"

    async def test_device_can_upload_project_file(self, client, app, tmp_data_dir):
        token = await self._register_device(client)
        uid = await self._user_id(app)
        await app.state.project_store.create_project(
            name="Device Project",
            slug="device-project",
            created_by=uid,
            user_id=uid,
        )
        async with await self._device_client(app, token) as dc:
            resp = await dc.post(
                "/api/projects/device-project/files/upload",
                files={"file": ("note.txt", b"shared content", "text/plain")},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["name"] == "note.txt"
            assert body["status"] == "uploaded"

    # -- Negative: device token cannot write to non-share endpoints --

    async def test_device_cannot_list_library_items(self, client, app):
        token = await self._register_device(client)
        async with await self._device_client(app, token) as dc:
            resp = await dc.get("/api/library/items")
            assert resp.status_code == 401

    async def test_device_cannot_delete_library_item(self, client, app):
        token = await self._register_device(client)
        async with await self._device_client(app, token) as dc:
            resp = await dc.delete("/api/library/items/nonexistent")
            assert resp.status_code == 401

    async def test_device_cannot_reprocess_library_item(self, client, app):
        token = await self._register_device(client)
        async with await self._device_client(app, token) as dc:
            resp = await dc.post("/api/library/items/abc/reprocess")
            assert resp.status_code == 401

    async def test_device_cannot_list_chat_channels(self, client, app):
        token = await self._register_device(client)
        async with await self._device_client(app, token) as dc:
            resp = await dc.get("/api/chat/channels")
            assert resp.status_code == 401

    async def test_device_cannot_access_others_project_files(self, client, app):
        token = await self._register_device(client)
        await app.state.project_store.create_project(
            name="Other Project",
            slug="other-project",
            created_by="other-user",
            user_id="other-user",
        )
        async with await self._device_client(app, token) as dc:
            resp = await dc.get("/api/projects/other-project/files")
            assert resp.status_code == 401

    async def test_device_cannot_mkdir_others_project(self, client, app):
        token = await self._register_device(client)
        await app.state.project_store.create_project(
            name="Other Project",
            slug="other-project",
            created_by="other-user",
            user_id="other-user",
        )
        async with await self._device_client(app, token) as dc:
            resp = await dc.post("/api/projects/other-project/mkdir", json={"path": "sub"})
            assert resp.status_code == 401

    async def test_device_cannot_delete_others_project_file(self, client, app):
        token = await self._register_device(client)
        await app.state.project_store.create_project(
            name="Other Project",
            slug="other-project",
            created_by="other-user",
            user_id="other-user",
        )
        async with await self._device_client(app, token) as dc:
            resp = await dc.delete("/api/projects/other-project/files/note.txt")
            assert resp.status_code == 401

    # -- Chat author_id guard: device must not impersonate another user --

    async def test_device_chat_rejects_mismatched_author_id(self, client, app):
        token = await self._register_device(client)
        async with await self._device_client(app, token) as dc:
            resp = await dc.post(
                "/api/chat/messages",
                json={
                    "channel_id": "general",
                    "content": "impersonation attempt",
                    "author_id": "some-other-user",
                    "author_type": "agent",
                },
            )
            assert resp.status_code == 403
            assert "author_id must match device owner" in resp.json()["error"]

    async def test_device_chat_accepts_own_author_id(self, client, app, tmp_data_dir):
        token = await self._register_device(client)
        uid = await self._user_id(app)
        async with await self._device_client(app, token) as dc:
            resp = await dc.post(
                "/api/chat/messages",
                json={
                    "channel_id": "general",
                    "content": "from my device",
                    "author_id": uid,
                    "author_type": "agent",
                },
            )
            assert resp.status_code == 200
