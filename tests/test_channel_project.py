import pytest
import pytest_asyncio

from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app


@pytest_asyncio.fixture
async def app(tmp_path):
    import yaml
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    _app = create_app(data_dir=tmp_path)
    chat_channels = _app.state.chat_channels
    if chat_channels._db is not None:
        await chat_channels.close()
    await chat_channels.init()
    return _app


@pytest_asyncio.fixture
async def client(app):
    _app = app
    _app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _record = _app.state.auth.find_user("admin")
    _uid = _record["id"] if _record else ""
    _token = _app.state.auth.create_session(user_id=_uid, long_lived=True)
    _app.state._startup_complete = True
    transport = ASGITransport(app=_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": _token},
    ) as c:
        yield c


@pytest.mark.asyncio
class TestSetProject:
    async def test_set_project_updates_row(self, client):
        create_resp = await client.post("/api/chat/channels", json={
            "name": "test-channel",
            "type": "topic",
            "created_by": "user",
        })
        assert create_resp.status_code == 200
        channel_id = create_resp.json()["id"]

        set_resp = await client.put(
            f"/api/chat/channels/{channel_id}/project",
            json={"project_id": "proj-123"},
        )
        assert set_resp.status_code == 200
        assert set_resp.json()["status"] == "updated"

        get_resp = await client.get(f"/api/chat/channels/{channel_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["project_id"] == "proj-123"

    async def test_set_project_to_empty(self, client):
        create_resp = await client.post("/api/chat/channels", json={
            "name": "proj-channel",
            "type": "topic",
            "created_by": "user",
            "project_id": "initial-proj",
        })
        channel_id = create_resp.json()["id"]

        set_resp = await client.put(
            f"/api/chat/channels/{channel_id}/project",
            json={"project_id": ""},
        )
        assert set_resp.status_code == 200

        get_resp = await client.get(f"/api/chat/channels/{channel_id}")
        assert get_resp.json()["project_id"] == ""

    async def test_set_project_not_found(self, client):
        resp = await client.put(
            "/api/chat/channels/nonexistent/project",
            json={"project_id": "proj-123"},
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestListChannelsByProject:
    async def test_filter_by_project(self, client):
        await client.post("/api/chat/channels", json={
            "name": "ch-proj-a",
            "type": "topic",
            "created_by": "user",
            "project_id": "proj-A",
        })
        await client.post("/api/chat/channels", json={
            "name": "ch-proj-b",
            "type": "topic",
            "created_by": "user",
            "project_id": "proj-B",
        })
        await client.post("/api/chat/channels", json={
            "name": "ch-no-proj",
            "type": "topic",
            "created_by": "user",
        })

        resp = await client.get("/api/chat/channels", params={"project_id": "proj-A"})
        assert resp.status_code == 200
        channels = resp.json()["channels"]
        assert len(channels) == 1
        assert channels[0]["name"] == "ch-proj-a"
        assert channels[0]["project_id"] == "proj-A"

    async def test_no_filter_returns_all(self, client):
        await client.post("/api/chat/channels", json={
            "name": "ch-1",
            "type": "topic",
            "created_by": "user",
            "project_id": "proj-X",
        })
        await client.post("/api/chat/channels", json={
            "name": "ch-2",
            "type": "topic",
            "created_by": "user",
        })

        resp = await client.get("/api/chat/channels")
        assert resp.status_code == 200
        channels = resp.json()["channels"]
        assert len(channels) == 2
        names = {c["name"] for c in channels}
        assert names == {"ch-1", "ch-2"}

    async def test_empty_project_id_returns_rootless_only(self, client):
        # An empty project_id filters to rootless channels (no project), not all.
        # Omitting the param entirely is what returns all channels.
        await client.post("/api/chat/channels", json={
            "name": "ch-assigned",
            "type": "topic",
            "created_by": "user",
            "project_id": "some-proj",
        })
        await client.post("/api/chat/channels", json={
            "name": "ch-rootless",
            "type": "topic",
            "created_by": "user",
        })

        resp = await client.get("/api/chat/channels", params={"project_id": ""})
        assert resp.status_code == 200
        channels = resp.json()["channels"]
        assert len(channels) == 1
        assert channels[0]["name"] == "ch-rootless"
