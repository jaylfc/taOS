"""Tests for tinyagentos.tools.image_tool.

Covers:
- execute_list_image_models: filters to image-gen models, sets loaded flag
- execute_list_image_models: handles API failures gracefully
- execute_image_generation: forwards model/guidance_scale/negative_prompt
- execute_image_generation: backward-compat when new params are omitted
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int, body: dict) -> MagicMock:
    """Build a minimal mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# execute_list_image_models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_image_models_filters_to_image_gen():
    """Only image-generation models should appear; loaded flag must be set."""
    from tinyagentos.tools.image_tool import execute_list_image_models

    installed_body = {
        "models": [
            {
                "id": "lcm-dreamshaper-v7",
                "name": "LCM Dreamshaper v7",
                "capabilities": ["image-generation"],
                "variants": [{"id": "rknn", "backend": ["rknn-sd"]}],
                "description": "LCM model for RKNN",
                "has_downloaded_variant": True,
            },
            {
                "id": "llama-3-8b",
                "name": "Llama 3 8B",
                "capabilities": ["chat"],
                "variants": [{"id": "q4", "backend": ["rkllama"]}],
                "description": "Chat model",
                "has_downloaded_variant": False,
            },
        ]
    }
    loaded_body = {
        "loaded": [
            {
                "name": "LCM Dreamshaper v7",
                "purpose": "image-generation",
                "backend_type": "rknn-sd",
            }
        ]
    }

    installed_resp = _make_response(200, installed_body)
    loaded_resp = _make_response(200, loaded_body)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[installed_resp, loaded_resp])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await execute_list_image_models()

    assert result["success"] is True
    models = result["models"]
    assert len(models) == 1
    m = models[0]
    assert m["name"] == "LCM Dreamshaper v7"
    assert m["loaded"] is True


@pytest.mark.asyncio
async def test_list_image_models_backend_type_filter():
    """Models with no image-generation capability but an rknn-sd/sd-cpp variant are included."""
    from tinyagentos.tools.image_tool import execute_list_image_models

    installed_body = {
        "models": [
            {
                "id": "my-sd-model",
                "name": "My SD Model",
                "capabilities": [],  # no capability declared
                "variants": [{"id": "fp16", "backend": ["sd-cpp"]}],
                "description": "",
                "has_downloaded_variant": True,
            },
        ]
    }
    loaded_body = {"loaded": []}

    installed_resp = _make_response(200, installed_body)
    loaded_resp = _make_response(200, loaded_body)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[installed_resp, loaded_resp])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await execute_list_image_models()

    assert result["success"] is True
    assert len(result["models"]) == 1
    assert result["models"][0]["id"] == "my-sd-model"
    assert result["models"][0]["loaded"] is False


@pytest.mark.asyncio
async def test_list_image_models_api_failure():
    """A connection error should return success=False with error text, not raise."""
    from tinyagentos.tools.image_tool import execute_list_image_models

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await execute_list_image_models()

    assert result["success"] is False
    assert "error" in result
    assert result["models"] == []


# ---------------------------------------------------------------------------
# execute_image_generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_generation_forwards_new_params():
    """model/guidance_scale/negative_prompt must appear in the POST body."""
    from tinyagentos.tools.image_tool import execute_image_generation

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # minimal fake PNG bytes

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_png
    mock_resp.raise_for_status = MagicMock()

    captured_payload: dict = {}

    async def fake_post(url, json=None, **kwargs):
        captured_payload.update(json or {})
        return mock_resp

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await execute_image_generation(
            prompt="a majestic elephant",
            model="lcm-dreamshaper-v7",
            guidance_scale=10.0,
            negative_prompt="blurry, low quality",
            seed=42,
        )

    assert result["success"] is True
    assert captured_payload["model"] == "lcm-dreamshaper-v7"
    assert captured_payload["guidance_scale"] == 10.0
    assert captured_payload["negative_prompt"] == "blurry, low quality"
    assert captured_payload["seed"] == 42


@pytest.mark.asyncio
async def test_image_generation_backward_compat():
    """Omitting model/guidance_scale/negative_prompt uses legacy direct path."""
    from tinyagentos.tools.image_tool import execute_image_generation

    response_body = {
        "data": [{"b64_json": base64.b64encode(b"fake-png").decode()}]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = response_body
    mock_resp.raise_for_status = MagicMock()

    captured: dict = {}

    async def fake_post(url, json=None, **kwargs):
        captured["url"] = url
        captured["body"] = json or {}
        return mock_resp

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await execute_image_generation(
            prompt="a red barn",
            backend_url="http://localhost:8080",
            seed=7,
        )

    assert result["success"] is True
    # Must have gone to the direct backend path, not the scheduler
    assert "/v1/images/generations" in captured["url"]
    body = captured["body"]
    assert body["prompt"] == "a red barn"
    assert body["seed"] == 7
    # negative_prompt absent when empty
    assert "negative_prompt" not in body
