import pytest
import pytest_asyncio

from tinyagentos.channels import CHANNEL_TYPES, ChannelStore


@pytest_asyncio.fixture
async def channel_store(tmp_path):
    store = ChannelStore(tmp_path / "channels.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestChannelStore:
    async def test_add_and_list(self, channel_store):
        cid = await channel_store.add("naira", "telegram", {"bot_token_secret": "tok"})
        assert cid is not None
        channels = await channel_store.list_for_agent("naira")
        assert len(channels) == 1
        assert channels[0]["type"] == "telegram"
        assert channels[0]["config"] == {"bot_token_secret": "tok"}
        assert channels[0]["enabled"] is True

    async def test_add_no_config(self, channel_store):
        await channel_store.add("naira", "web-chat")
        channels = await channel_store.list_for_agent("naira")
        assert len(channels) == 1
        assert channels[0]["config"] == {}

    async def test_list_all(self, channel_store):
        await channel_store.add("naira", "telegram", {"bot_token_secret": "t1"})
        await channel_store.add("kira", "discord", {"bot_token_secret": "t2"})
        all_ch = await channel_store.list_all()
        assert len(all_ch) == 2
        agents = {c["agent_name"] for c in all_ch}
        assert agents == {"naira", "kira"}

    async def test_remove(self, channel_store):
        await channel_store.add("naira", "telegram")
        removed = await channel_store.remove("naira", "telegram")
        assert removed is True
        channels = await channel_store.list_for_agent("naira")
        assert len(channels) == 0

    async def test_remove_nonexistent(self, channel_store):
        removed = await channel_store.remove("naira", "telegram")
        assert removed is False

    async def test_toggle(self, channel_store):
        cid = await channel_store.add("naira", "telegram")
        await channel_store.toggle(cid, False)
        channels = await channel_store.list_for_agent("naira")
        assert channels[0]["enabled"] is False
        await channel_store.toggle(cid, True)
        channels = await channel_store.list_for_agent("naira")
        assert channels[0]["enabled"] is True

    async def test_replace_on_duplicate(self, channel_store):
        await channel_store.add("naira", "telegram", {"bot_token_secret": "old"})
        await channel_store.add("naira", "telegram", {"bot_token_secret": "new"})
        channels = await channel_store.list_for_agent("naira")
        assert len(channels) == 1
        assert channels[0]["config"]["bot_token_secret"] == "new"

    async def test_list_for_agent_empty(self, channel_store):
        channels = await channel_store.list_for_agent("nobody")
        assert channels == []

    async def test_list_enriches_with_type_info(self, channel_store):
        await channel_store.add("naira", "telegram")
        channels = await channel_store.list_for_agent("naira")
        assert channels[0]["name"] == "Telegram"
        assert channels[0]["difficulty"] == "easy"


class TestChannelTypes:
    def test_all_types_have_required_keys(self):
        for key, info in CHANNEL_TYPES.items():
            assert "name" in info, f"{key} missing name"
            assert "difficulty" in info, f"{key} missing difficulty"
            assert "description" in info, f"{key} missing description"
            assert "config_fields" in info, f"{key} missing config_fields"
            assert info["difficulty"] in ("easy", "advanced"), f"{key} has invalid difficulty"

    def test_easy_and_advanced_present(self):
        easy = [k for k, v in CHANNEL_TYPES.items() if v["difficulty"] == "easy"]
        advanced = [k for k, v in CHANNEL_TYPES.items() if v["difficulty"] == "advanced"]
        assert len(easy) >= 3
        assert len(advanced) >= 3


# --- Route tests ---

@pytest.mark.asyncio
async def test_api_channel_types(client):
    resp = await client.get("/api/channels/types")
    assert resp.status_code == 200
    data = resp.json()
    assert "telegram" in data
    assert "web-chat" in data
    assert data["telegram"]["difficulty"] == "easy"


@pytest.mark.asyncio
async def test_api_add_and_list_channels(client):
    resp = await client.post("/api/channels", json={
        "agent_name": "test-agent",
        "type": "telegram",
        "config": {"bot_token_secret": "my-secret"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "added"

    resp = await client.get("/api/channels")
    assert resp.status_code == 200
    channels = resp.json()
    assert len(channels) == 1
    assert channels[0]["type"] == "telegram"
    assert channels[0]["agent_name"] == "test-agent"


@pytest.mark.asyncio
async def test_api_agent_channels(client):
    await client.post("/api/channels", json={
        "agent_name": "test-agent", "type": "web-chat", "config": {},
    })
    resp = await client.get("/api/channels/agent/test-agent")
    assert resp.status_code == 200
    channels = resp.json()
    assert len(channels) == 1
    assert channels[0]["type"] == "web-chat"


@pytest.mark.asyncio
async def test_api_remove_channel(client):
    await client.post("/api/channels", json={
        "agent_name": "test-agent", "type": "telegram", "config": {},
    })
    resp = await client.request("DELETE", "/api/channels/test-agent/telegram")
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"

    resp = await client.get("/api/channels/agent/test-agent")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_api_remove_nonexistent(client):
    resp = await client.request("DELETE", "/api/channels/test-agent/telegram")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_toggle_channel(client):
    add_resp = await client.post("/api/channels", json={
        "agent_name": "test-agent", "type": "telegram", "config": {},
    })
    channel_id = add_resp.json()["id"]
    resp = await client.post(f"/api/channels/{channel_id}/toggle", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_api_add_unknown_type(client):
    resp = await client.post("/api/channels", json={
        "agent_name": "test-agent", "type": "fax-machine", "config": {},
    })
    assert resp.status_code == 400
    assert "Unknown channel type" in resp.json()["error"]
