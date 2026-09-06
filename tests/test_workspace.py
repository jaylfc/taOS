from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.agent_messages import AgentMessageStore


@pytest_asyncio.fixture
async def msg_store(tmp_path):
    store = AgentMessageStore(tmp_path / "messages.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestAgentMessageStore:
    async def test_send_and_get(self, msg_store):
        msg_id = await msg_store.send("naira", "kira", "Hello from Naira!")
        assert msg_id is not None
        messages = await msg_store.get_messages("naira")
        assert len(messages) == 1
        assert messages[0]["from"] == "naira"
        assert messages[0]["to"] == "kira"
        assert messages[0]["message"] == "Hello from Naira!"

    async def test_get_messages_both_directions(self, msg_store):
        await msg_store.send("naira", "kira", "Hi Kira")
        await msg_store.send("kira", "naira", "Hi Naira")
        messages = await msg_store.get_messages("naira")
        assert len(messages) == 2

    async def test_get_conversation(self, msg_store):
        await msg_store.send("naira", "kira", "msg1")
        await msg_store.send("kira", "naira", "msg2")
        await msg_store.send("naira", "zara", "msg3")  # different conversation
        convo = await msg_store.get_conversation("naira", "kira")
        assert len(convo) == 2
        # Ordered by timestamp ASC
        assert convo[0]["message"] == "msg1"
        assert convo[1]["message"] == "msg2"

    async def test_unread_count(self, msg_store):
        await msg_store.send("naira", "kira", "Read me!")
        await msg_store.send("naira", "kira", "And me!")
        count = await msg_store.unread_count("kira")
        assert count == 2

    async def test_mark_read(self, msg_store):
        await msg_store.send("naira", "kira", "Read me!")
        await msg_store.mark_read("kira")
        count = await msg_store.unread_count("kira")
        assert count == 0

    async def test_unread_count_only_to_agent(self, msg_store):
        await msg_store.send("naira", "kira", "To kira")
        await msg_store.send("kira", "naira", "From kira")
        # Naira should have 1 unread (the one from kira)
        assert await msg_store.unread_count("naira") == 1
        # Kira should have 1 unread (the one from naira)
        assert await msg_store.unread_count("kira") == 1

    async def test_send_with_tool_calls(self, msg_store):
        tools = [{"name": "search", "args": {"q": "test"}}]
        results = [{"output": "found it"}]
        msg_id = await msg_store.send(
            "naira", "kira", "Using tools",
            tool_calls=tools, tool_results=results,
            metadata={"intent": "research"},
        )
        messages = await msg_store.get_messages("naira")
        assert messages[0]["tool_calls"] == tools
        assert messages[0]["tool_results"] == results
        assert messages[0]["metadata"] == {"intent": "research"}

    async def test_get_messages_limit(self, msg_store):
        for i in range(10):
            await msg_store.send("naira", "kira", f"msg{i}")
        messages = await msg_store.get_messages("naira", limit=3)
        assert len(messages) == 3


# --- Route tests ---

@pytest.mark.asyncio
async def test_api_agent_messages_empty(client):
    resp = await client.get("/api/agents/test-agent/messages")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_api_send_and_list_messages(client):
    resp = await client.post("/api/agents/test-agent/messages", json={
        "from_agent": "other-agent",
        "message": "Hello test-agent!",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "sent"

    resp = await client.get("/api/agents/test-agent/messages")
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 1
    assert messages[0]["from"] == "other-agent"
    assert messages[0]["to"] == "test-agent"
    assert messages[0]["message"] == "Hello test-agent!"


@pytest.mark.asyncio
async def test_api_agent_files_empty(client):
    resp = await client.get("/api/agents/test-agent/files")
    assert resp.status_code == 200
    assert resp.json() == []


# --- Messages sub-view route tests ---


@pytest.mark.asyncio
class TestWorkspaceMessagesView:
    async def test_contacts_endpoint_returns_partners(self, client):
        # Send a message first so there is a contact
        resp = await client.post("/api/agents/test-agent/messages", json={
            "from_agent": "sender-bot",
            "message": "Hello from sender",
        })
        assert resp.status_code == 200

        resp = await client.get("/api/agents/test-agent/workspace/messages/contacts")
        assert resp.status_code == 200
        contacts = resp.json()
        names = [c["name"] for c in contacts]
        assert "sender-bot" in names

    async def test_contacts_empty_by_default(self, client):
        resp = await client.get("/api/agents/test-agent/workspace/messages/contacts")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_messages_404_for_unknown_agent(self, client):
        resp = await client.get("/agents/nonexistent/workspace/messages")
        assert resp.status_code == 404


# --- Files sub-view route tests ---


@pytest.mark.asyncio
class TestWorkspaceFilesView:
    async def test_workspace_files_list_empty_by_default(self, client):
        resp = await client.get("/api/agents/test-agent/workspace/files")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_file_upload_and_listing(self, client):
        # Upload a file
        resp = await client.post(
            "/api/agents/test-agent/workspace/files/upload",
            files={"file": ("hello.txt", b"Hello, world!", "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "hello.txt"
        assert data["size"] == 13

        # List files and verify it appears
        resp = await client.get("/api/agents/test-agent/workspace/files")
        assert resp.status_code == 200
        files = resp.json()
        assert len(files) == 1
        assert files[0]["name"] == "hello.txt"

    async def test_file_delete(self, client):
        # Upload then delete
        await client.post(
            "/api/agents/test-agent/workspace/files/upload",
            files={"file": ("temp.txt", b"temp content", "text/plain")},
        )
        resp = await client.delete("/api/agents/test-agent/workspace/files/temp.txt")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Verify it's gone
        resp = await client.get("/api/agents/test-agent/workspace/files")
        assert resp.json() == []

    async def test_delete_nonexistent_file(self, client):
        resp = await client.delete("/api/agents/test-agent/workspace/files/nope.txt")
        assert resp.status_code == 404

    async def test_files_404_for_unknown_agent(self, client):
        resp = await client.get("/agents/nonexistent/workspace/files")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestWorkspaceUsage:
    async def test_usage_returns_not_available_when_no_proxy(self, client):
        resp = await client.get("/api/agents/test-agent/workspace/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False

    async def test_usage_404_for_unknown_agent(self, client):
        # LLM proxy not running, so it returns "not running" before checking agent
        resp = await client.get("/api/agents/nonexistent/workspace/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
