import types

import pytest


class TestMemoryModelEndpoint:
    @pytest.mark.asyncio
    async def test_get_memory_model_returns_200_with_keys(self, client, monkeypatch):
        fake = types.SimpleNamespace(get_memory_model=lambda: "qwen3-8b")
        monkeypatch.setattr("tinyagentos.routes.librarian.taosmd", fake)
        resp = await client.get("/api/memory/model")
        assert resp.status_code == 200
        data = resp.json()
        assert "model" in data
        assert "supported" in data
        assert data["supported"] is True
        assert data["model"] == "qwen3-8b"

    @pytest.mark.asyncio
    async def test_get_memory_model_unsupported_returns_none(self, client, monkeypatch):
        fake = types.SimpleNamespace()
        monkeypatch.setattr("tinyagentos.routes.librarian.taosmd", fake)
        resp = await client.get("/api/memory/model")
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] is None
        assert data["supported"] is False
