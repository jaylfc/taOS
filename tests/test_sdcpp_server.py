"""Tests for tinyagentos.services.sdcpp_server.

Covers:
- health: returns ok when model loaded, 503 when not
- list_models: returns the configured model id
- generate: happy path with valid request
- generate: rejects n > 1
- generate: rejects invalid size format
- generate: returns 503 when model not loaded
- generate: handles inference failure
- generate: handles empty image list from backend
- GenerateRequest: default values and field validation
- main: calls uvicorn.run with configured host/port
"""

from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import tinyagentos.services.sdcpp_server as mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_sd():
    """Return a mock StableDiffusion that returns one fake image."""
    mock = MagicMock()
    fake_img = Image.new("RGB", (4, 4), color=(255, 0, 0))
    mock.txt_to_img.return_value = [fake_img]
    return mock


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Ensure each test starts with a clean module-level _sd / _load_error."""
    mod._sd = None
    mod._load_error = None
    mod.MODEL_NAME = "dreamshaper-8-lcm"
    yield
    mod._sd = None
    mod._load_error = None
    mod.MODEL_NAME = "dreamshaper-8-lcm"


def _make_client(sd_mock=_make_mock_sd(), load_error=None, model_name="test-model"):
    """Build a TestClient with controlled module-level state.

    Pass sd_mock=False to keep _sd as None (model-not-loaded scenario).
    """
    if sd_mock is False:
        sd_mock = None
    mod._sd = sd_mock
    mod._load_error = load_error
    mod.MODEL_NAME = model_name
    from tinyagentos.services.sdcpp_server import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_ok_when_model_loaded(self):
        client = _make_client()
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["model"] == "test-model"
        assert body["backend"] == "stable-diffusion.cpp"

    def test_health_503_when_model_not_loaded(self):
        client = _make_client(sd_mock=False, load_error="Model not found: /fake/model.gguf")
        resp = client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "error"
        assert "Model not found" in body["error"]


# ---------------------------------------------------------------------------
# list_models
# ---------------------------------------------------------------------------


class TestListModels:
    def test_returns_configured_model(self):
        client = _make_client(model_name="my-sd-model")
        resp = client.get("/v1/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "list"
        assert len(body["data"]) == 1
        model_entry = body["data"][0]
        assert model_entry["id"] == "my-sd-model"
        assert model_entry["object"] == "model"
        assert model_entry["owned_by"] == "tinyagentos"
        assert isinstance(model_entry["created"], int)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


class TestGenerate:
    def test_happy_path(self):
        mock_sd = _make_mock_sd()
        client = _make_client(sd_mock=mock_sd)
        resp = client.post(
            "/v1/images/generations",
            json={
                "prompt": "a red barn",
                "size": "512x512",
                "steps": 4,
                "guidance_scale": 1.0,
                "seed": 42,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["model"] == "test-model"
        assert body["data"][0]["revised_prompt"] == "a red barn"
        assert "b64_json" in body["data"][0]
        decoded = base64.b64decode(body["data"][0]["b64_json"])
        assert len(decoded) > 0
        assert "usage" in body
        assert body["usage"]["seed"] == 42
        assert isinstance(body["usage"]["elapsed_seconds"], float)
        mock_sd.txt_to_img.assert_called_once_with(
            prompt="a red barn",
            negative_prompt="",
            width=512,
            height=512,
            sample_steps=4,
            cfg_scale=1.0,
            seed=42,
            sample_method="euler_a",
        )

    def test_rejects_n_greater_than_one(self):
        client = _make_client()
        resp = client.post(
            "/v1/images/generations",
            json={"prompt": "test", "n": 2},
        )
        assert resp.status_code == 400
        assert "n > 1" in resp.json()["detail"]

    def test_rejects_invalid_size(self):
        client = _make_client()
        resp = client.post(
            "/v1/images/generations",
            json={"prompt": "test", "size": "not_a_size"},
        )
        assert resp.status_code == 400
        assert "invalid size" in resp.json()["detail"]

    def test_503_when_model_not_loaded(self):
        client = _make_client(sd_mock=False, load_error="model not loaded")
        resp = client.post(
            "/v1/images/generations",
            json={"prompt": "test"},
        )
        assert resp.status_code == 503
        assert "model not loaded" in resp.json()["detail"]

    def test_500_on_inference_failure(self):
        mock_sd = MagicMock()
        mock_sd.txt_to_img.side_effect = RuntimeError("out of memory")
        client = _make_client(sd_mock=mock_sd)
        resp = client.post(
            "/v1/images/generations",
            json={"prompt": "test"},
        )
        assert resp.status_code == 500
        assert "inference failed" in resp.json()["detail"]

    def test_500_on_empty_images(self):
        mock_sd = MagicMock()
        mock_sd.txt_to_img.return_value = []
        client = _make_client(sd_mock=mock_sd)
        resp = client.post(
            "/v1/images/generations",
            json={"prompt": "test"},
        )
        assert resp.status_code == 500
        assert "no images" in resp.json()["detail"]

    def test_negative_prompt_forwarded(self):
        mock_sd = _make_mock_sd()
        client = _make_client(sd_mock=mock_sd)
        resp = client.post(
            "/v1/images/generations",
            json={
                "prompt": "a cat",
                "negative_prompt": "blurry, low quality",
                "size": "256x256",
                "seed": 0,
            },
        )
        assert resp.status_code == 200
        mock_sd.txt_to_img.assert_called_once_with(
            prompt="a cat",
            negative_prompt="blurry, low quality",
            width=256,
            height=256,
            sample_steps=4,
            cfg_scale=1.0,
            seed=0,
            sample_method="euler_a",
        )

    def test_random_seed_when_not_provided(self):
        mock_sd = _make_mock_sd()
        client = _make_client(sd_mock=mock_sd)
        with patch("tinyagentos.services.sdcpp_server.random.randint", return_value=999):
            resp = client.post(
                "/v1/images/generations",
                json={"prompt": "test"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["usage"]["seed"] == 999


# ---------------------------------------------------------------------------
# GenerateRequest model validation
# ---------------------------------------------------------------------------


class TestGenerateRequest:
    def test_defaults(self):
        from tinyagentos.services.sdcpp_server import GenerateRequest

        req = GenerateRequest(prompt="hello")
        assert req.negative_prompt == ""
        assert req.model is None
        assert req.size == "512x512"
        assert req.n == 1
        assert req.response_format == "b64_json"
        assert req.seed is None
        assert req.steps == 4
        assert req.guidance_scale == 1.0

    def test_invalid_response_format_rejected(self):
        from pydantic import ValidationError
        from tinyagentos.services.sdcpp_server import GenerateRequest

        with pytest.raises(ValidationError):
            GenerateRequest(prompt="hello", response_format="xml")

    def test_steps_out_of_range_rejected(self):
        from pydantic import ValidationError
        from tinyagentos.services.sdcpp_server import GenerateRequest

        with pytest.raises(ValidationError):
            GenerateRequest(prompt="hello", steps=0)
        with pytest.raises(ValidationError):
            GenerateRequest(prompt="hello", steps=51)

    def test_guidance_scale_out_of_range_rejected(self):
        from pydantic import ValidationError
        from tinyagentos.services.sdcpp_server import GenerateRequest

        with pytest.raises(ValidationError):
            GenerateRequest(prompt="hello", guidance_scale=-0.1)
        with pytest.raises(ValidationError):
            GenerateRequest(prompt="hello", guidance_scale=20.1)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_calls_uvicorn_run(self):
        with patch("tinyagentos.services.sdcpp_server.uvicorn") as mock_uvicorn, \
             patch("tinyagentos.services.sdcpp_server.HOST", "127.0.0.1"), \
             patch("tinyagentos.services.sdcpp_server.PORT", "9999"):
            from tinyagentos.services.sdcpp_server import main
            main()
        mock_uvicorn.run.assert_called_once()
        call_kwargs = mock_uvicorn.run.call_args
        from tinyagentos.services.sdcpp_server import app
        assert call_kwargs[0][0] is app
        assert call_kwargs[1]["host"] == "127.0.0.1"
        assert call_kwargs[1]["port"] == "9999"
        assert call_kwargs[1]["log_level"] == "info"
