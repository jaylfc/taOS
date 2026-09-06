"""Endpoint tests for tinyagentos/routes/skill_exec.py (GET / read-only)."""

from unittest.mock import AsyncMock

import pytest


class TestSkillExecTools:
    @pytest.mark.asyncio
    async def test_list_tools_returns_assigned_skills(self, client, app, monkeypatch):
        mock_store = AsyncMock()
        mock_store.get_agent_skills.return_value = [
            {
                "id": "memory_search",
                "name": "Memory Search",
                "category": "search",
                "description": "Search the agent's knowledge base",
                "tool_schema": {
                    "name": "memory_search",
                    "description": "Search stored documents and memories",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer", "default": 10},
                        },
                        "required": ["query"],
                    },
                },
                "frameworks": {},
                "agent_config": {},
            },
            {
                "id": "file_read",
                "name": "File Read",
                "category": "files",
                "description": "Read files from the agent workspace",
                "tool_schema": {
                    "name": "file_read",
                    "description": "Read a file from the workspace",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                        },
                        "required": ["path"],
                    },
                },
                "frameworks": {},
                "agent_config": {},
            },
        ]
        monkeypatch.setattr(app.state, "skills", mock_store)

        resp = await client.get(
            "/api/skill-exec/tools",
            params={"agent_name": "test-agent"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "tools" in body
        assert len(body["tools"]) == 2
        tool = body["tools"][0]
        assert tool["type"] == "function"
        assert "function" in tool
        assert "name" in tool["function"]
        assert "description" in tool["function"]
        assert "parameters" in tool["function"]
        assert "skill_id" in tool
        assert "exec_url" in tool
        assert tool["exec_url"].startswith("/api/skill-exec/")
        assert tool["exec_url"].endswith("/call")

    @pytest.mark.asyncio
    async def test_list_tools_empty_for_unknown_agent(self, client, app, monkeypatch):
        mock_store = AsyncMock()
        mock_store.get_agent_skills.return_value = []
        monkeypatch.setattr(app.state, "skills", mock_store)

        resp = await client.get(
            "/api/skill-exec/tools",
            params={"agent_name": "no-such-agent"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"tools": []}

    @pytest.mark.asyncio
    async def test_list_tools_requires_agent_name(self, client):
        resp = await client.get("/api/skill-exec/tools")
        assert resp.status_code == 422
