"""Tests for agent_debugger GET endpoints."""

from __future__ import annotations

import asyncio

import pytest

from tinyagentos.routes import agent_debugger as agent_debugger_mod


class TestDebuggerUI:
    @pytest.mark.asyncio
    async def test_get_debugger_ui_returns_html(self, client, monkeypatch):
        monkeypatch.setattr(agent_debugger_mod, "_get_template", lambda name: "<html>debugger</html>")
        resp = await client.get("/agent/test-agent/debug")
        assert resp.status_code == 200
        assert resp.text == "<html>debugger</html>"
        assert "text/html" in resp.headers["content-type"]


class TestDebuggerStatus:
    @pytest.mark.asyncio
    async def test_status_no_events(self, client, monkeypatch):
        monkeypatch.setattr(agent_debugger_mod, "_traces", {})
        monkeypatch.setattr(agent_debugger_mod, "_positions", {})
        monkeypatch.setattr(agent_debugger_mod, "_queues", {})
        resp = await client.get("/agent/unknown-agent/debug/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent_id"] == "unknown-agent"
        assert body["total_events"] == 0
        assert body["position"] == 0
        assert body["has_listener"] is False

    @pytest.mark.asyncio
    async def test_status_with_events(self, client, monkeypatch):
        monkeypatch.setattr(
            agent_debugger_mod,
            "_traces",
            {"my-agent": [{"type": "tool_call"}, {"type": "tool_result"}]},
        )
        monkeypatch.setattr(agent_debugger_mod, "_positions", {"my-agent": 1})
        monkeypatch.setattr(agent_debugger_mod, "_queues", {"my-agent": []})
        resp = await client.get("/agent/my-agent/debug/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent_id"] == "my-agent"
        assert body["total_events"] == 2
        assert body["position"] == 1
        assert body["has_listener"] is False

    @pytest.mark.asyncio
    async def test_status_with_listener(self, client, monkeypatch):
        monkeypatch.setattr(
            agent_debugger_mod,
            "_traces",
            {"listened": [{"type": "prompt"}]},
        )
        monkeypatch.setattr(agent_debugger_mod, "_positions", {"listened": 0})
        queue = asyncio.Queue()
        monkeypatch.setattr(agent_debugger_mod, "_queues", {"listened": [queue]})
        resp = await client.get("/agent/listened/debug/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_listener"] is True
        assert body["total_events"] == 1


class TestDebuggerEvents:
    @pytest.mark.asyncio
    async def test_events_yields_position_for_empty_trace(self, monkeypatch):
        from collections import defaultdict as _defaultdict
        from unittest.mock import MagicMock, AsyncMock

        monkeypatch.setattr(agent_debugger_mod, "_traces", {})
        monkeypatch.setattr(agent_debugger_mod, "_positions", {})
        monkeypatch.setattr(agent_debugger_mod, "_queues", _defaultdict(list))

        request = MagicMock()
        request.is_disconnected = AsyncMock(return_value=True)

        resp = await agent_debugger_mod.debugger_events("fresh-agent", request)
        gen = resp.body_iterator
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert "position" in chunk

    @pytest.mark.asyncio
    async def test_events_replays_existing_events(self, monkeypatch):
        from collections import defaultdict as _defaultdict
        from unittest.mock import MagicMock, AsyncMock

        monkeypatch.setattr(
            agent_debugger_mod,
            "_traces",
            {"replay-agent": [{"type": "tool_call", "data": {"fn": "search"}}]},
        )
        monkeypatch.setattr(agent_debugger_mod, "_positions", {"replay-agent": 0})
        monkeypatch.setattr(agent_debugger_mod, "_queues", _defaultdict(list))

        request = MagicMock()
        request.is_disconnected = AsyncMock(side_effect=[False, True])
        resp = await agent_debugger_mod.debugger_events("replay-agent", request)
        gen = resp.body_iterator
        chunks = []
        while True:
            try:
                chunk = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
            except StopAsyncIteration:
                break
            chunks.append(chunk)
        assert len(chunks) == 2
        assert "position" in chunks[0]
        assert "tool_call" in chunks[1]
