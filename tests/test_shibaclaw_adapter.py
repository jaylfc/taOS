"""Tests for the ShibaClaw adapter: health endpoint, message proxying,
retry behaviour, and error handling."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

import tinyagentos.adapters.shibaclaw_adapter as sc_mod
from tinyagentos.adapters.shibaclaw_adapter import app


@pytest.fixture(autouse=True)
def _instant_retry_backoff(monkeypatch):
    # with_retry backs off with asyncio.sleep between attempts. The retry tests
    # exhaust all 7 attempts, so without this each would sleep ~31s of real time.
    # Make the backoff instant; the retry COUNT is unchanged.
    import asyncio

    async def _no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


# ---------------------------------------------------------------------------
# health endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_returns_ok():
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["framework"] == "shibaclaw"


# ---------------------------------------------------------------------------
# handle_message — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_message_returns_content_on_200(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"content": "hello from shibaclaw"}

    async def _mock_post(url, json):
        return mock_resp

    monkeypatch.setattr(sc_mod, "_controller_post", _mock_post)
    result = await sc_mod.handle_message({"text": "hi"})
    assert result["content"] == "hello from shibaclaw"


@pytest.mark.asyncio
async def test_handle_message_sends_text_to_correct_url(monkeypatch):
    sent = {}

    async def _mock_post(url, json):
        sent["url"] = url
        sent["json"] = json
        return MagicMock(status_code=200, json=MagicMock(return_value={"content": "ok"}))

    monkeypatch.setattr(sc_mod, "_controller_post", _mock_post)
    monkeypatch.setenv("SHIBACLAW_URL", "http://shibaclaw:19999")
    await sc_mod.handle_message({"text": "ping"})
    assert sent["url"] == "http://shibaclaw:19999/api/message"
    assert sent["json"] == {"text": "ping"}


# ---------------------------------------------------------------------------
# handle_message — non-200 status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_message_returns_status_text_on_non_200(monkeypatch):
    call_count = 0

    async def _mock_post(url, json):
        nonlocal call_count
        call_count += 1
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setattr(sc_mod, "_controller_post", _mock_post)
    result = await sc_mod.handle_message({"text": "hi"})
    assert "503" in result["content"]
    assert call_count == 7  # with_retry(max_attempts=7) exhausts all retries


# ---------------------------------------------------------------------------
# handle_message — exception handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_message_returns_error_on_exception(monkeypatch):
    call_count = 0

    async def _mock_post(url, json):
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(sc_mod, "_controller_post", _mock_post)
    result = await sc_mod.handle_message({"text": "hi"})
    assert "ShibaClaw not available" in result["content"]
    assert "refused" in result["content"]
    assert call_count == 7  # with_retry(max_attempts=7) exhausts all retries


@pytest.mark.asyncio
async def test_handle_message_includes_agent_name_in_error(monkeypatch):
    call_count = 0

    async def _mock_post(url, json):
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("down")

    monkeypatch.setattr(sc_mod, "_controller_post", _mock_post)
    monkeypatch.setenv("TAOS_AGENT_NAME", "my-agent")
    result = await sc_mod.handle_message({"text": "hi"})
    assert "[my-agent]" in result["content"]
    assert call_count == 7


# ---------------------------------------------------------------------------
# handle_message — edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_message_empty_text(monkeypatch):
    sent = {}

    async def _mock_post(url, json):
        sent["json"] = json
        return MagicMock(status_code=200, json=MagicMock(return_value={"content": "ack"}))

    monkeypatch.setattr(sc_mod, "_controller_post", _mock_post)
    result = await sc_mod.handle_message({})
    assert sent["json"]["text"] == ""
    assert result["content"] == "ack"


@pytest.mark.asyncio
async def test_handle_message_uses_default_url_when_env_not_set(monkeypatch):
    sent = {}

    async def _mock_post(url, json):
        sent["url"] = url
        return MagicMock(status_code=200, json=MagicMock(return_value={"content": "ok"}))

    monkeypatch.setattr(sc_mod, "_controller_post", _mock_post)
    monkeypatch.delenv("SHIBACLAW_URL", raising=False)
    await sc_mod.handle_message({"text": "x"})
    assert sent["url"] == "http://localhost:19999/api/message"


@pytest.mark.asyncio
async def test_handle_message_falls_back_to_resp_text_when_no_content_key(monkeypatch):
    async def _mock_post(url, json):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        resp.text = "raw text response"
        return resp

    monkeypatch.setattr(sc_mod, "_controller_post", _mock_post)
    result = await sc_mod.handle_message({"text": "hi"})
    assert result["content"] == "raw text response"
