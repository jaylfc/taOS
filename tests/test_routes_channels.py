import pytest
from tinyagentos.channels import ChannelStore
from tinyagentos.agent_messages import AgentMessageStore


class TestChannelStore:
    @pytest.mark.asyncio
    async def test_add_and_list(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            row_id = await store.add("agent-1", "telegram", {"bot_token_secret": "tok"})
            assert row_id > 0
            channels = await store.list_for_agent("agent-1")
            assert len(channels) == 1
            assert channels[0]["type"] == "telegram"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_by_id(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            row_id = await store.add("agent-1", "discord", {"guild_id": "123"})
            ch = await store.get(row_id)
            assert ch is not None
            assert ch["type"] == "discord"
            assert ch["config"]["guild_id"] == "123"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            ch = await store.get(999)
            assert ch is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_update_config(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            row_id = await store.add("agent-1", "slack", {"channel": "general"})
            await store.update(row_id, {"channel": "random", "extra": "val"})
            ch = await store.get(row_id)
            assert ch["config"]["channel"] == "random"
            assert ch["config"]["extra"] == "val"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_toggle(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            row_id = await store.add("agent-1", "email", {})
            await store.toggle(row_id, False)
            ch = await store.get(row_id)
            assert ch["enabled"] is False
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_remove(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            await store.add("agent-1", "telegram", {})
            removed = await store.remove("agent-1", "telegram")
            assert removed is True
            channels = await store.list_for_agent("agent-1")
            assert len(channels) == 0
        finally:
            await store.close()


class TestAgentMessageStoreExtras:
    @pytest.mark.asyncio
    async def test_delete_message(self, tmp_path):
        store = AgentMessageStore(tmp_path / "msg.db")
        await store.init()
        try:
            msg_id = await store.send("a", "b", "hello")
            deleted = await store.delete(msg_id)
            assert deleted is True
            msgs = await store.get_messages("a")
            assert len(msgs) == 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, tmp_path):
        store = AgentMessageStore(tmp_path / "msg.db")
        await store.init()
        try:
            deleted = await store.delete(999)
            assert deleted is False
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_search_messages(self, tmp_path):
        store = AgentMessageStore(tmp_path / "msg.db")
        await store.init()
        try:
            await store.send("a", "b", "hello world")
            await store.send("a", "b", "goodbye world")
            await store.send("c", "d", "something else")
            results = await store.search("hello", agent_name="a")
            assert len(results) == 1
            assert "hello" in results[0]["message"]
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_search_all_agents(self, tmp_path):
        store = AgentMessageStore(tmp_path / "msg.db")
        await store.init()
        try:
            await store.send("a", "b", "world peace")
            await store.send("c", "d", "world war")
            results = await store.search("world")
            assert len(results) == 2
        finally:
            await store.close()
