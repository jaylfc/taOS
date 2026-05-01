"""Tests for the Skill execution runtime.

Verifies that:
- ``GET /api/skill-exec/tools`` returns an empty list for unknown agents.
- Assigned skills show up in the tool-discovery response with properly shaped
  OpenAI/MCP-style schemas.
- Executing a non-existent skill returns a 404.
- The ``file_write`` and ``file_read`` built-ins round-trip successfully.
- ``list_image_models`` skill dispatches correctly via skill-exec.
- ``image_generation`` skill still dispatches correctly (no regression).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from tinyagentos.routes.skill_exec import router
from tinyagentos.skills import SkillStore


@pytest_asyncio.fixture
async def app_with_store(tmp_path):
    app = FastAPI()
    app.include_router(router)
    store = SkillStore(tmp_path / "skills.db")
    await store.init()
    app.state.skills = store
    workspace_root = tmp_path / "agent-workspaces"
    workspace_root.mkdir(parents=True, exist_ok=True)
    app.state.agent_workspaces_dir = workspace_root
    try:
        yield app
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_list_tools_empty(app_with_store):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_store), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/skill-exec/tools?agent_name=no-such-agent"
        )
        assert resp.status_code == 200
        assert resp.json()["tools"] == []


@pytest.mark.asyncio
async def test_list_tools_with_assigned(app_with_store):
    store = app_with_store.state.skills
    await store.assign_skill("agent-alpha", "memory_search")
    await store.assign_skill("agent-alpha", "file_read")

    async with AsyncClient(
        transport=ASGITransport(app=app_with_store), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/skill-exec/tools?agent_name=agent-alpha"
        )
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        assert len(tools) == 2
        names = {t["function"]["name"] for t in tools}
        assert "memory_search" in names
        assert "file_read" in names
        # Each tool should advertise the exec_url so agents can invoke it.
        for tool in tools:
            assert tool["exec_url"].startswith("/api/skill-exec/")
            assert tool["exec_url"].endswith("/call")
            assert "parameters" in tool["function"]


@pytest.mark.asyncio
async def test_execute_unknown_skill(app_with_store):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_store), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/skill-exec/nonexistent/call", json={"args": {}}
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_file_write_and_read(app_with_store):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_store), base_url="http://test"
    ) as client:
        write_resp = await client.post(
            "/api/skill-exec/file_write/call",
            json={"args": {"path": "test.txt", "content": "hello world"}},
        )
        assert write_resp.status_code == 200
        assert write_resp.json().get("status") == "written"

        read_resp = await client.post(
            "/api/skill-exec/file_read/call",
            json={"args": {"path": "test.txt"}},
        )
        assert read_resp.status_code == 200
        assert read_resp.json().get("content") == "hello world"


@pytest.mark.asyncio
async def test_skill_exec_list_image_models(app_with_store):
    """list_image_models skill returns a list of models via skill-exec."""
    store = app_with_store.state.skills
    await store.assign_skill("don", "list_image_models")

    models_payload = {"success": True, "models": [{"name": "LCM", "id": "lcm"}]}

    with patch(
        "tinyagentos.tools.image_tool.execute_list_image_models",
        new_callable=AsyncMock,
        return_value=models_payload,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_store), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/skill-exec/list_image_models/call", json={"args": {}}
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert isinstance(data["models"], list)


@pytest.mark.asyncio
async def test_skill_exec_image_generation_no_regression(app_with_store):
    """Existing image_generation skill still dispatches correctly."""
    store = app_with_store.state.skills
    await store.assign_skill("don", "image_generation")

    gen_payload = {"success": True, "image_b64": "abc", "seed": 1, "model": "lcm", "size": "512x512"}

    with patch(
        "tinyagentos.tools.image_tool.execute_image_generation",
        new_callable=AsyncMock,
        return_value=gen_payload,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_store), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/skill-exec/image_generation/call",
                json={"args": {"prompt": "a red barn"}},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["image_b64"] == "abc"
