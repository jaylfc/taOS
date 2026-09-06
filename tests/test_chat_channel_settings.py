import pytest
from tinyagentos.chat.channel_store import ChatChannelStore, PHASE1_DEFAULT_SETTINGS


@pytest.mark.asyncio
async def test_get_channel_backfills_phase1_defaults(tmp_path):
    store = ChatChannelStore(tmp_path / "chat.db")
    await store.init()
    ch = await store.create_channel(
        name="test", type="group", description="", topic="",
        members=["user", "tom"], settings={}, created_by="user",
    )
    ch = await store.get_channel(ch["id"])
    for key, default in PHASE1_DEFAULT_SETTINGS.items():
        assert ch["settings"][key] == default
    await store.close()


@pytest.mark.asyncio
async def test_set_response_mode_persists(tmp_path):
    store = ChatChannelStore(tmp_path / "chat.db")
    await store.init()
    ch = await store.create_channel(
        name="t", type="group", description="", topic="",
        members=["user", "tom"], settings={}, created_by="user",
    )
    await store.set_response_mode(ch["id"], "lively")
    ch = await store.get_channel(ch["id"])
    assert ch["settings"]["response_mode"] == "lively"
    await store.close()


@pytest.mark.asyncio
async def test_mute_and_unmute_agent(tmp_path):
    store = ChatChannelStore(tmp_path / "chat.db")
    await store.init()
    ch = await store.create_channel(
        name="t", type="group", description="", topic="",
        members=["user", "tom", "don"], settings={}, created_by="user",
    )
    await store.mute_agent(ch["id"], "tom")
    ch = await store.get_channel(ch["id"])
    assert "tom" in ch["settings"]["muted"]
    await store.unmute_agent(ch["id"], "tom")
    ch = await store.get_channel(ch["id"])
    assert "tom" not in ch["settings"]["muted"]
    await store.close()
