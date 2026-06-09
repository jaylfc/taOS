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

import httpx
import pytest


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
                "variants": [{"id": "safetensors", "backend": ["sd-cpp"]}],
                "description": "LCM model",
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
                "backend_type": "sd-cpp",
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
    """Models with no image-generation capability but an sd-cpp variant are included."""
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
    """model/guidance_scale/negative_prompt must appear in the POST body
    AND the call must hit the scheduler route — guards against regressing
    to a non-scheduler endpoint."""
    from tinyagentos.tools.image_tool import execute_image_generation

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # minimal fake PNG bytes

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_png
    mock_resp.raise_for_status = MagicMock()

    captured_payload: dict = {}
    captured_url: dict = {}

    async def fake_post(url, json=None, **kwargs):
        captured_url["url"] = url
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
    assert "/api/images/generate" in captured_url["url"]
    assert captured_payload["model"] == "lcm-dreamshaper-v7"
    assert captured_payload["guidance_scale"] == 10.0
    assert captured_payload["negative_prompt"] == "blurry, low quality"
    assert captured_payload["seed"] == 42


@pytest.mark.asyncio
async def test_image_generation_default_routes_via_scheduler():
    """Omitting model still routes via the scheduler (no hardcoded model
    fallback). The model field is left out of the payload so the
    scheduler's GenerateRequest default ("") applies."""
    from tinyagentos.tools.image_tool import execute_image_generation

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_png
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
            seed=7,
        )

    assert result["success"] is True
    assert "/api/images/generate" in captured["url"]
    body = captured["body"]
    assert body["prompt"] == "a red barn"
    assert body["seed"] == 7
    # No hardcoded model — the field is omitted so the server picks
    assert "model" not in body
    # negative_prompt absent when empty
    assert "negative_prompt" not in body


@pytest.mark.asyncio
async def test_image_generation_blank_model_treated_as_omitted():
    """Whitespace-only or empty model strings should not land in the payload."""
    from tinyagentos.tools.image_tool import execute_image_generation

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_png
    mock_resp.raise_for_status = MagicMock()

    captured: dict = {}

    async def fake_post(url, json=None, **kwargs):
        captured["body"] = json or {}
        return mock_resp

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await execute_image_generation(prompt="x", model="   ")

    assert result["success"] is True
    assert "model" not in captured["body"]


@pytest.mark.asyncio
async def test_image_generation_falls_back_on_controller_unreachable():
    """When the controller is unreachable, fall back to the direct
    backend call. The fallback forwards a user-supplied model (their
    explicit choice wins) but does NOT inject a hardcoded Pi-specific
    default — the principle is "don't pin a default", not "drop user
    input".

    Two sub-assertions matter for the regression:

      1. Scheduler is tried first with the user's model.
      2. Fallback is tried second; if the user did NOT supply a model,
         the fallback payload does not include one (so heterogeneous
         hardware doesn't get a Pi-pinned default forwarded).
    """
    from tinyagentos.tools.image_tool import execute_image_generation

    direct_response_body = {"data": [{"b64_json": base64.b64encode(b"fake").decode()}]}

    direct_resp = MagicMock()
    direct_resp.status_code = 200
    direct_resp.json.return_value = direct_response_body
    direct_resp.raise_for_status = MagicMock()

    captured_urls: list = []
    captured_payloads: list = []

    async def fake_post(url, json=None, **kwargs):
        captured_urls.append(url)
        captured_payloads.append(json or {})
        # First call (scheduler) raises; second call (direct backend) succeeds.
        if "/api/images/generate" in url:
            raise httpx.ConnectError("controller down")
        return direct_resp

    mock_client = AsyncMock()
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Case A: user supplied a model — both scheduler and fallback get it.
    with patch("httpx.AsyncClient", return_value=mock_client):
        result_a = await execute_image_generation(
            prompt="a red barn",
            model="lcm-dreamshaper-v7",
            backend_url="http://localhost:8080",
            seed=7,
        )

    assert result_a["success"] is True
    assert any("/api/images/generate" in u for u in captured_urls)
    assert any("/v1/images/generations" in u for u in captured_urls)
    scheduler_payload = next(
        p for u, p in zip(captured_urls, captured_payloads) if "/api/images/generate" in u
    )
    fallback_payload = next(
        p for u, p in zip(captured_urls, captured_payloads) if "/v1/images/generations" in u
    )
    assert scheduler_payload.get("model") == "lcm-dreamshaper-v7"
    assert fallback_payload.get("model") == "lcm-dreamshaper-v7"

    # Case B: no model supplied — neither scheduler nor fallback should
    # add a default. This is the heterogeneous-hardware regression.
    captured_urls.clear()
    captured_payloads.clear()
    with patch("httpx.AsyncClient", return_value=mock_client):
        result_b = await execute_image_generation(
            prompt="a barn at dusk",
            backend_url="http://localhost:8080",
            seed=8,
        )

    assert result_b["success"] is True
    fallback_payload_b = next(
        p for u, p in zip(captured_urls, captured_payloads) if "/v1/images/generations" in u
    )
    assert "model" not in fallback_payload_b
