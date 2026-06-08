"""LiteLLM virtual-key re-scope (update_agent_key) — keystone for synced model management."""
from __future__ import annotations

import pytest
from tinyagentos.llm_proxy import LLMProxy


class _Resp:
    def __init__(self, status): self.status_code = status; self.text = ""

class _Client:
    def __init__(self, status=200, capture=None): self._s = status; self._cap = capture
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, json=None, headers=None):
        if self._cap is not None: self._cap["url"] = url; self._cap["json"] = json
        return _Resp(self._s)


class _FakeProxy:
    """Duck-typed proxy borrowing the real update_agent_key implementation."""
    update_agent_key = LLMProxy.update_agent_key
    def __init__(self, running=True, db=True):
        self.url = "http://127.0.0.1:4000"
        self.database_url = "postgres://x" if db else None
        self._running = running
        self._data_dir = None  # in-memory master key (no disk I/O in tests)
    def is_running(self): return self._running


@pytest.mark.asyncio
async def test_update_agent_key_calls_key_update(monkeypatch):
    cap = {}
    import tinyagentos.llm_proxy as M
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **k: _Client(200, cap))
    assert await _FakeProxy().update_agent_key("sk-x", ["a", "b"]) is True
    assert cap["url"].endswith("/key/update")
    assert cap["json"] == {"key": "sk-x", "models": ["a", "b"]}


@pytest.mark.asyncio
async def test_update_agent_key_noop_without_db():
    assert await _FakeProxy(db=False).update_agent_key("sk-x", ["a"]) is False


@pytest.mark.asyncio
async def test_update_agent_key_false_on_error(monkeypatch):
    import tinyagentos.llm_proxy as M
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **k: _Client(500))
    assert await _FakeProxy().update_agent_key("sk-x", ["a"]) is False


@pytest.mark.asyncio
async def test_update_agent_key_refuses_empty_models(monkeypatch):
    # An empty scope must NOT silently become ["default"] — refuse and never
    # hit /key/update (which would scope the key to a non-existent model).
    cap = {}
    import tinyagentos.llm_proxy as M
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda **k: _Client(200, cap))
    assert await _FakeProxy().update_agent_key("sk-x", []) is False
    assert cap == {}
