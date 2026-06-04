"""Tests for GET/PATCH /api/agents/{slug}/librarian endpoints."""
import pytest

import taosmd.agents as tm_agents
from taosmd.agents import AgentNotFoundError


_DEFAULT_LIBRARIAN = {
    "enabled": True,
    "tasks": {t: True for t in tm_agents.LIBRARIAN_TASKS},
    "fanout": {"default": "low", "auto_scale": True},
}


@pytest.mark.asyncio
class TestGetLibrarian:
    async def test_get_librarian_returns_upstream_config(self, client, monkeypatch):
        monkeypatch.setattr(tm_agents, "get_librarian", lambda name: dict(_DEFAULT_LIBRARIAN))

        resp = await client.get("/api/agents/atlas/librarian")

        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert "tasks" in data
        assert "fanout" in data

    async def test_get_librarian_404_for_unknown_agent(self, client, monkeypatch):
        def _raise(name):
            raise AgentNotFoundError(f"agent {name!r} is not registered")

        monkeypatch.setattr(tm_agents, "get_librarian", _raise)

        resp = await client.get("/api/agents/no-such/librarian")

        assert resp.status_code == 404


@pytest.mark.asyncio
class TestPatchLibrarian:
    async def test_patch_librarian_forwards_kwargs(self, client, monkeypatch):
        received: dict = {}

        def _set(name, **kwargs):
            received["name"] = name
            received["kwargs"] = kwargs
            lib = dict(_DEFAULT_LIBRARIAN)
            lib["enabled"] = kwargs.get("enabled", lib["enabled"])
            return lib

        monkeypatch.setattr(tm_agents, "set_librarian", _set)

        resp = await client.patch(
            "/api/agents/atlas/librarian",
            json={"enabled": False},
        )

        assert resp.status_code == 200
        assert received["name"] == "atlas"
        assert received["kwargs"]["enabled"] is False
        # model/clear_model are no longer forwarded (dropped fields)
        assert "model" not in received["kwargs"]
        assert "clear_model" not in received["kwargs"]

    async def test_patch_librarian_404_for_unknown_agent(self, client, monkeypatch):
        def _raise(name, **kwargs):
            raise AgentNotFoundError(f"agent {name!r} is not registered")

        monkeypatch.setattr(tm_agents, "set_librarian", _raise)

        resp = await client.patch("/api/agents/ghost/librarian", json={"enabled": True})

        assert resp.status_code == 404

    async def test_patch_librarian_400_for_invalid_fanout(self, client, monkeypatch):
        def _raise(name, **kwargs):
            raise ValueError(f"unknown fanout level 'ultra'. Valid levels: ...")

        monkeypatch.setattr(tm_agents, "set_librarian", _raise)

        resp = await client.patch(
            "/api/agents/atlas/librarian",
            json={"fanout": "ultra"},
        )

        assert resp.status_code == 400
        assert "fanout" in resp.json()["detail"]
