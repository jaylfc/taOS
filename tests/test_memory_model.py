"""Tests for GET/PUT /api/memory/model endpoints."""
from __future__ import annotations

import pytest

import taosmd
import tinyagentos.routes.librarian as librarian_mod


@pytest.mark.asyncio
class TestGetMemoryModel:
    async def test_get_returns_model(self, client, monkeypatch):
        monkeypatch.setattr(librarian_mod.taosmd, "get_memory_model", lambda: "ollama:qwen3:4b")

        resp = await client.get("/api/memory/model")

        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "ollama:qwen3:4b"
        assert data["supported"] is True

    async def test_get_returns_none_when_no_model_set(self, client, monkeypatch):
        monkeypatch.setattr(librarian_mod.taosmd, "get_memory_model", lambda: None)

        resp = await client.get("/api/memory/model")

        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] is None
        assert data["supported"] is True

    async def test_get_supported_false_when_attr_absent(self, client, monkeypatch):
        monkeypatch.delattr(librarian_mod.taosmd, "get_memory_model", raising=False)

        resp = await client.get("/api/memory/model")

        assert resp.status_code == 200
        data = resp.json()
        assert data["supported"] is False
        assert data["model"] is None


@pytest.mark.asyncio
class TestPutMemoryModel:
    async def test_put_sets_model(self, client, monkeypatch):
        received: dict = {}

        def _set(model: str, clear: bool = False):
            received["model"] = model
            received["clear"] = clear

        monkeypatch.setattr(librarian_mod.taosmd, "set_memory_model", _set)
        monkeypatch.setattr(librarian_mod.taosmd, "get_memory_model", lambda: "ollama:qwen3:4b")

        resp = await client.put("/api/memory/model", json={"model": "ollama:qwen3:4b"})

        assert resp.status_code == 200
        assert received["model"] == "ollama:qwen3:4b"
        assert received["clear"] is False
        assert resp.json()["model"] == "ollama:qwen3:4b"

    async def test_put_clear_true_calls_setter_with_clear(self, client, monkeypatch):
        received: dict = {}

        def _set(model: str, clear: bool = False):
            received["model"] = model
            received["clear"] = clear

        monkeypatch.setattr(librarian_mod.taosmd, "set_memory_model", _set)
        monkeypatch.setattr(librarian_mod.taosmd, "get_memory_model", lambda: None)

        resp = await client.put("/api/memory/model", json={"clear": True})

        assert resp.status_code == 200
        assert received["clear"] is True
        assert resp.json()["model"] is None

    async def test_put_neither_model_nor_clear_returns_400(self, client, monkeypatch):
        monkeypatch.setattr(librarian_mod.taosmd, "set_memory_model", lambda m, clear=False: None)

        resp = await client.put("/api/memory/model", json={})

        assert resp.status_code == 400
        assert "model" in resp.json()["detail"]

    async def test_put_empty_string_model_returns_400(self, client, monkeypatch):
        monkeypatch.setattr(librarian_mod.taosmd, "set_memory_model", lambda m, clear=False: None)

        resp = await client.put("/api/memory/model", json={"model": "   "})

        assert resp.status_code == 400

    async def test_put_returns_501_when_set_memory_model_absent(self, client, monkeypatch):
        monkeypatch.delattr(librarian_mod.taosmd, "set_memory_model", raising=False)

        resp = await client.put("/api/memory/model", json={"model": "ollama:qwen3:4b"})

        assert resp.status_code == 501
        assert "taosmd" in resp.json()["detail"]
