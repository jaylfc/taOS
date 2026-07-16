"""Unit tests for the ComfyUI client + workflow builder (all HTTP mocked).

ComfyUI is not installed in CI, so every test patches httpx so no real network
call is made — mirroring tests/test_routes_games.py's httpx.AsyncClient mocking.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ConnectError, Request as HttpxRequest, Response

from tinyagentos.asset_gen.comfyui_client import ComfyUIClient, ComfyUIResult
from tinyagentos.asset_gen.workflows import build_texture_workflow

# Minimal valid 1x1 PNG (magic header is what _looks_like_image checks).
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

_PROMPT_ID = "abc123"


def _resp(url: str, *, json=None, content=None) -> Response:
    req = HttpxRequest("GET", url)
    if content is not None:
        return Response(status_code=200, content=content, request=req)
    return Response(status_code=200, json=json, request=req)


def _history_with_image() -> dict:
    return {
        _PROMPT_ID: {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
            }
        }
    }


def _install_mock(monkeypatch_target, mock_instance):
    """Patch the client's httpx.AsyncClient to yield *mock_instance*."""
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    return patch(
        "tinyagentos.asset_gen.comfyui_client.httpx.AsyncClient",
        return_value=mock_instance,
    )


@pytest.mark.asyncio
async def test_generate_success_returns_image_bytes():
    inst = AsyncMock()
    inst.post.return_value = _resp("http://x/prompt", json={"prompt_id": _PROMPT_ID})

    def get_side_effect(url, **kwargs):
        if "/history/" in url:
            return _resp(url, json=_history_with_image())
        return _resp(url, content=PNG_BYTES)  # /view

    inst.get.side_effect = get_side_effect

    with _install_mock(None, inst):
        result = await ComfyUIClient("http://comfy").generate({"1": {}})

    assert isinstance(result, ComfyUIResult)
    assert result.image_bytes == PNG_BYTES
    assert result.prompt_id == _PROMPT_ID
    # /view was called with the ref parsed out of history.
    view_call = [c for c in inst.get.call_args_list if "/view" in c.args[0]][0]
    assert view_call.kwargs["params"]["filename"] == "out.png"


@pytest.mark.asyncio
async def test_generate_no_prompt_id_returns_none():
    inst = AsyncMock()
    inst.post.return_value = _resp("http://x/prompt", json={})
    with _install_mock(None, inst):
        result = await ComfyUIClient("http://comfy").generate({"1": {}})
    assert result is None
    inst.get.assert_not_called()


@pytest.mark.asyncio
async def test_generate_job_finishes_without_image_returns_none():
    inst = AsyncMock()
    inst.post.return_value = _resp("http://x/prompt", json={"prompt_id": _PROMPT_ID})
    inst.get.side_effect = lambda url, **kw: _resp(url, json={_PROMPT_ID: {"outputs": {}}})
    with _install_mock(None, inst):
        result = await ComfyUIClient("http://comfy").generate({"1": {}})
    assert result is None


@pytest.mark.asyncio
async def test_generate_non_image_view_returns_none():
    inst = AsyncMock()
    inst.post.return_value = _resp("http://x/prompt", json={"prompt_id": _PROMPT_ID})

    def get_side_effect(url, **kwargs):
        if "/history/" in url:
            return _resp(url, json=_history_with_image())
        return _resp(url, content=b'{"error":"boom"}')  # /view non-image body

    inst.get.side_effect = get_side_effect
    with _install_mock(None, inst):
        result = await ComfyUIClient("http://comfy").generate({"1": {}})
    assert result is None


@pytest.mark.asyncio
async def test_generate_connect_error_is_fail_soft():
    inst = AsyncMock()
    inst.post.side_effect = ConnectError("refused")
    with _install_mock(None, inst):
        result = await ComfyUIClient("http://comfy").generate({"1": {}})
    assert result is None


@pytest.mark.asyncio
async def test_generate_poll_timeout_returns_none():
    inst = AsyncMock()
    inst.post.return_value = _resp("http://x/prompt", json={"prompt_id": _PROMPT_ID})
    # History never contains the prompt id -> poll spins until the deadline.
    inst.get.side_effect = lambda url, **kw: _resp(url, json={})
    with _install_mock(None, inst):
        client = ComfyUIClient("http://comfy", poll_timeout=0.05, poll_interval=0.01)
        result = await client.generate({"1": {}})
    assert result is None


def test_default_base_url_from_env(monkeypatch):
    monkeypatch.setenv("TAOS_COMFYUI_URL", "http://gpu-box:8188/")
    # Trailing slash is stripped.
    assert ComfyUIClient().base == "http://gpu-box:8188"


def test_default_base_url_fallback(monkeypatch):
    monkeypatch.delenv("TAOS_COMFYUI_URL", raising=False)
    assert ComfyUIClient().base == "http://127.0.0.1:8188"


class TestBuildTextureWorkflow:
    def test_stamps_prompt_size_seed_checkpoint(self):
        wf = build_texture_workflow(
            prompt="mossy stone", width=768, height=512, seed=42,
            checkpoint="my.safetensors",
        )
        assert wf["6"]["inputs"]["text"] == "mossy stone"
        assert wf["5"]["inputs"]["width"] == 768
        assert wf["5"]["inputs"]["height"] == 512
        assert wf["3"]["inputs"]["seed"] == 42
        assert wf["4"]["inputs"]["ckpt_name"] == "my.safetensors"

    def test_tileable_augments_prompt(self):
        wf = build_texture_workflow(prompt="brick wall", tileable=True)
        assert "seamless tileable" in wf["6"]["inputs"]["text"]

    def test_tileable_not_duplicated_when_already_seamless(self):
        wf = build_texture_workflow(prompt="a seamless grass texture", tileable=True)
        assert wf["6"]["inputs"]["text"].count("seamless") == 1

    def test_returns_fresh_copy(self):
        a = build_texture_workflow(prompt="one")
        b = build_texture_workflow(prompt="two")
        assert a["6"]["inputs"]["text"] == "one"
        assert b["6"]["inputs"]["text"] == "two"
