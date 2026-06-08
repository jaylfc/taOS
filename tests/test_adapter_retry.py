"""Tests for #209 — exponential-backoff retry on controller calls in adapters."""
from __future__ import annotations

import asyncio
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock


# ---------------------------------------------------------------------------
# openclaw_adapter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openclaw_retries_on_connect_error():
    """openclaw_adapter handle_message must retry on ConnectError."""
    call_count = {"n": 0}

    async def _mock_post(url, json):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise httpx.ConnectError("simulated connection refused")
        return MagicMock(status_code=200, json=MagicMock(return_value={"response": "hi"}))

    import tinyagentos.adapters.openclaw_adapter as oc_mod
    with patch.object(oc_mod, "_controller_post", side_effect=_mock_post):
        result = await oc_mod.handle_message({"text": "hello"})

    assert result["content"] == "hi"
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_openclaw_retries_on_503():
    """openclaw_adapter handle_message must retry on 503."""
    call_count = {"n": 0}

    async def _mock_post(url, json):
        call_count["n"] += 1
        if call_count["n"] < 2:
            req = httpx.Request("POST", url)
            raise httpx.HTTPStatusError("Service Unavailable", request=req,
                                        response=httpx.Response(503, request=req))
        return MagicMock(status_code=200, json=MagicMock(return_value={"response": "ok"}))

    import tinyagentos.adapters.openclaw_adapter as oc_mod
    with patch.object(oc_mod, "_controller_post", side_effect=_mock_post):
        result = await oc_mod.handle_message({"text": "hello"})

    assert call_count["n"] == 2
    assert result["content"] == "ok"


@pytest.mark.asyncio
async def test_openclaw_retries_on_502():
    """openclaw_adapter must retry on 502 and succeed."""
    call_count = {"n": 0}

    async def _mock_post(url, json):
        call_count["n"] += 1
        if call_count["n"] == 1:
            req = httpx.Request("POST", url)
            raise httpx.HTTPStatusError("Bad Gateway", request=req,
                                        response=httpx.Response(502, request=req))
        return MagicMock(status_code=200, json=MagicMock(return_value={"response": "recovered"}))

    import tinyagentos.adapters.openclaw_adapter as oc_mod
    with patch.object(oc_mod, "_controller_post", side_effect=_mock_post):
        result = await oc_mod.handle_message({"text": "test"})

    assert call_count["n"] == 2
    assert "recovered" in result["content"]


@pytest.mark.asyncio
async def test_openclaw_does_not_retry_on_404():
    """openclaw_adapter must NOT retry on 404 — client errors propagate."""
    call_count = {"n": 0}

    async def _mock_post(url, json):
        call_count["n"] += 1
        req = httpx.Request("POST", url)
        raise httpx.HTTPStatusError("Not Found", request=req,
                                    response=httpx.Response(404, request=req))

    import tinyagentos.adapters.openclaw_adapter as oc_mod
    with patch.object(oc_mod, "_controller_post", side_effect=_mock_post):
        result = await oc_mod.handle_message({"text": "test"})

    # Only one attempt — 404 is not retried
    assert call_count["n"] == 1
    # The outer except Exception catches and returns an error message
    assert "Error" in result["content"] or "not reachable" in result["content"]


# ---------------------------------------------------------------------------
# hermes_adapter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hermes_retries_on_connect_error():
    """hermes_adapter handle_message must retry on ConnectError."""
    call_count = {"n": 0}

    async def _mock_post(url, json, headers):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise httpx.ConnectError("connection refused")
        return MagicMock(
            status_code=200,
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "hello"}}]
            })
        )

    import tinyagentos.adapters.hermes_adapter as hm_mod
    with patch.object(hm_mod, "_controller_post", side_effect=_mock_post):
        result = await hm_mod.handle_message({"text": "hi"})

    assert call_count["n"] == 2
    assert result["content"] == "hello"


# ---------------------------------------------------------------------------
# moltis_adapter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_moltis_retries_on_connect_error():
    """moltis_adapter handle_message must retry on ConnectError."""
    call_count = {"n": 0}

    async def _mock_post(url, json):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise httpx.ConnectError("connection refused")
        return MagicMock(
            status_code=200,
            json=MagicMock(return_value={"content": "done"}),
            text="done",
        )

    import tinyagentos.adapters.moltis_adapter as mt_mod
    with patch.object(mt_mod, "_controller_post", side_effect=_mock_post):
        result = await mt_mod.handle_message({"text": "hi"})

    assert call_count["n"] == 2
    assert result["content"] == "done"


# ---------------------------------------------------------------------------
# with_retry contract — 4xx propagates immediately, 5xx retries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_with_retry_propagates_4xx_immediately():
    """with_retry must not retry 4xx status errors — they propagate on attempt 1."""
    from tinyagentos.clients.retry import with_retry

    call_count = {"n": 0}

    async def _factory():
        call_count["n"] += 1
        req = httpx.Request("POST", "http://x/y")
        raise httpx.HTTPStatusError("Bad Request", request=req,
                                    response=httpx.Response(400, request=req))

    with pytest.raises(httpx.HTTPStatusError):
        await with_retry(_factory, max_attempts=5, base_delay=0.001, multiplier=2.0, max_delay=0.01)

    assert call_count["n"] == 1, "4xx must not be retried"


@pytest.mark.asyncio
async def test_with_retry_retries_connect_error():
    """with_retry must retry ConnectError up to max_attempts."""
    from tinyagentos.clients.retry import with_retry

    call_count = {"n": 0}

    async def _factory():
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise httpx.ConnectError("refused")
        return MagicMock(status_code=200)

    result = await with_retry(
        _factory, max_attempts=5, base_delay=0.001, multiplier=2.0, max_delay=0.01
    )
    assert result.status_code == 200
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_with_retry_exhausts_all_attempts():
    """with_retry must raise after exhausting max_attempts."""
    from tinyagentos.clients.retry import with_retry

    call_count = {"n": 0}

    async def _factory():
        call_count["n"] += 1
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        await with_retry(
            _factory, max_attempts=3, base_delay=0.001, multiplier=2.0, max_delay=0.01
        )

    assert call_count["n"] == 3
