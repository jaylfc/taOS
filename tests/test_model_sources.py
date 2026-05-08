"""Tests for model_sources module and live search endpoints."""
import pytest
import pytest_asyncio
import yaml
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport, Response

from tinyagentos.app import create_app
from tinyagentos.model_sources import (
    _parse_quantization,
    estimate_ram_mb,
    get_compatibility,
    search_huggingface,
    search_ollama,
    get_huggingface_model_files,
    _cache,
)


# --- Unit tests for helper functions ---

class TestParseQuantization:
    def test_q4_k_m(self):
        assert _parse_quantization("model-q4_k_m.gguf") == "Q4_K_M"

    def test_q8_0(self):
        assert _parse_quantization("some-model.Q8_0.gguf") == "Q8_0"

    def test_f16(self):
        assert _parse_quantization("model-f16.gguf") == "F16"

    def test_q2_k(self):
        assert _parse_quantization("tiny-q2_k.gguf") == "Q2_K"

    def test_q5_k_s(self):
        assert _parse_quantization("model.q5_k_s.gguf") == "Q5_K_S"

    def test_unknown(self):
        assert _parse_quantization("model.gguf") == "unknown"

    def test_case_insensitive(self):
        assert _parse_quantization("Model-Q4_K_M.gguf") == "Q4_K_M"


class TestEstimateRamMb:
    def test_basic(self):
        assert estimate_ram_mb(1000) == 1200

    def test_zero(self):
        assert estimate_ram_mb(0) == 0

    def test_small(self):
        assert estimate_ram_mb(100) == 120


class TestGetCompatibility:
    def test_compatible(self):
        # 1000 MB model, 16000 MB RAM -> 1000 <= 9600 -> compatible
        assert get_compatibility(1000, 16000) == "compatible"

    def test_tight(self):
        # 10000 MB model, 16000 MB RAM -> 10000 > 9600, 10000 <= 13600 -> tight
        assert get_compatibility(10000, 16000) == "tight"

    def test_incompatible(self):
        # 15000 MB model, 16000 MB RAM -> 15000 > 13600 -> incompatible
        assert get_compatibility(15000, 16000) == "incompatible"

    def test_zero_ram(self):
        assert get_compatibility(1000, 0) == "incompatible"

    def test_exact_boundary_compatible(self):
        # 6000 <= 10000 * 0.6 = 6000 -> compatible
        assert get_compatibility(6000, 10000) == "compatible"

    def test_exact_boundary_tight(self):
        # 8500 <= 10000 * 0.85 = 8500 -> tight
        assert get_compatibility(8500, 10000) == "tight"


# --- Tests for search functions with mocked HTTP ---

@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the module-level cache before each test."""
    _cache.clear()
    yield
    _cache.clear()


def _mock_hf_response():
    return [
        {
            "modelId": "TheBloke/Llama-2-7B-GGUF",
            "downloads": 50000,
            "likes": 200,
            "tags": ["gguf", "llama"],
        },
        {
            "modelId": "bartowski/Qwen3-8B-GGUF",
            "downloads": 30000,
            "likes": 100,
            "tags": ["gguf", "qwen"],
        },
    ]


def _mock_ollama_response():
    return {
        "models": [
            {"name": "llama3.2", "description": "Meta Llama 3.2", "tags": ["chat"]},
            {"name": "qwen3", "description": "Qwen 3 by Alibaba", "tags": ["chat", "tool-use"]},
        ]
    }


def _mock_hf_model_detail():
    return {
        "modelId": "TheBloke/Llama-2-7B-GGUF",
        "siblings": [
            {"rfilename": "llama-2-7b.Q4_K_M.gguf", "size": 4_000_000_000},
            {"rfilename": "llama-2-7b.Q8_0.gguf", "size": 7_000_000_000},
            {"rfilename": "README.md", "size": 5000},
            {"rfilename": "config.json", "size": 200},
        ],
    }


@pytest.mark.asyncio
class TestSearchHuggingFace:
    async def test_search_returns_results(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _mock_hf_response()
        mock_resp.raise_for_status = MagicMock()

        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            results = await search_huggingface("llama")
            assert len(results) == 2
            assert results[0]["id"] == "TheBloke/Llama-2-7B-GGUF"
            assert results[0]["source"] == "huggingface"
            assert results[0]["author"] == "TheBloke"
            assert results[0]["name"] == "Llama-2-7B-GGUF"
            assert results[0]["downloads"] == 50000

    async def test_search_handles_error(self):
        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.side_effect = Exception("Connection timeout")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            results = await search_huggingface("llama")
            assert results == []

    async def test_search_caches_results(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _mock_hf_response()
        mock_resp.raise_for_status = MagicMock()

        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            r1 = await search_huggingface("llama")
            r2 = await search_huggingface("llama")
            assert r1 == r2
            # Only one actual HTTP call due to caching
            assert instance.get.call_count == 1


@pytest.mark.asyncio
class TestSearchOllama:
    async def test_search_returns_results(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _mock_ollama_response()
        mock_resp.status_code = 200

        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            results = await search_ollama("llama")
            assert len(results) == 2
            assert results[0]["id"] == "llama3.2"
            assert results[0]["source"] == "ollama"
            assert results[0]["author"] == "ollama"

    async def test_search_handles_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            results = await search_ollama("llama")
            assert results == []

    async def test_search_handles_error(self):
        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.side_effect = Exception("timeout")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            results = await search_ollama("llama")
            assert results == []


@pytest.mark.asyncio
class TestGetHuggingFaceModelFiles:
    async def test_returns_gguf_files_only(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _mock_hf_model_detail()
        mock_resp.raise_for_status = MagicMock()

        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            files = await get_huggingface_model_files("TheBloke/Llama-2-7B-GGUF")
            assert len(files) == 2  # Only .gguf files
            assert files[0]["filename"] == "llama-2-7b.Q4_K_M.gguf"
            assert files[0]["quantization"] == "Q4_K_M"
            assert files[0]["size_mb"] == 3814  # 4GB -> ~3814 MB
            assert files[1]["filename"] == "llama-2-7b.Q8_0.gguf"

    async def test_sorted_by_size(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _mock_hf_model_detail()
        mock_resp.raise_for_status = MagicMock()

        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            files = await get_huggingface_model_files("TheBloke/Llama-2-7B-GGUF")
            sizes = [f["size_mb"] for f in files]
            assert sizes == sorted(sizes)

    async def test_handles_error(self):
        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.side_effect = Exception("not found")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            files = await get_huggingface_model_files("nonexistent/model")
            assert files == []


# --- Tests for search API endpoints ---

@pytest.fixture
def catalog_with_models(tmp_path):
    models_dir = tmp_path / "catalog" / "models" / "test-model"
    models_dir.mkdir(parents=True)
    (models_dir / "manifest.yaml").write_text(yaml.dump({
        "id": "test-model", "name": "Test Model", "type": "model",
        "version": "1.0.0", "description": "A test model for unit tests",
        "capabilities": ["chat"],
        "variants": [
            {"id": "small", "name": "Small", "format": "gguf", "size_mb": 100,
             "min_ram_mb": 512, "download_url": "https://example.com/small.gguf",
             "backend": ["ollama"]},
        ],
        "hardware_tiers": {},
        "install": {"method": "download"},
    }))
    return tmp_path / "catalog"


@pytest.fixture
def search_app(tmp_data_dir, catalog_with_models, tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    shared_root = tmp_path / "models-shared"
    monkeypatch.setenv("TAOS_MODELS_ROOT", str(shared_root))
    app = create_app(data_dir=tmp_data_dir, catalog_dir=catalog_with_models)
    app.state.models_dir = models_dir
    app.state.models_root = shared_root
    return app


@pytest_asyncio.fixture
async def search_client(search_app):
    store = search_app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await search_app.state.qmd_client.init()
    search_app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _rec = search_app.state.auth.find_user("admin")
    _token = search_app.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
    transport = ASGITransport(app=search_app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}) as c:
        yield c
    await store.close()
    await search_app.state.qmd_client.close()
    await search_app.state.http_client.aclose()


@pytest.mark.asyncio
class TestSearchEndpoints:
    async def test_search_empty_query(self, search_client):
        resp = await search_client.get("/api/models/search", params={"q": ""})
        assert resp.status_code == 200

    async def test_search_catalog_source(self, search_client):
        resp = await search_client.get(
            "/api/models/search",
            params={"q": "test", "source": "catalog"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "catalog"
        # Should find "test-model" from catalog
        assert len(data["results"]) >= 1
        assert data["results"][0]["source"] == "catalog"
        assert data["results"][0]["name"] == "Test Model"

    async def test_search_huggingface_endpoint(self, search_client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _mock_hf_response()
        mock_resp.raise_for_status = MagicMock()

        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            resp = await search_client.get(
                "/api/models/search/huggingface",
                params={"q": "llama"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["source"] == "huggingface"
            assert len(data["results"]) == 2

    async def test_search_ollama_endpoint(self, search_client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _mock_ollama_response()
        mock_resp.status_code = 200

        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            resp = await search_client.get(
                "/api/models/search/ollama",
                params={"q": "llama"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["source"] == "ollama"
            assert len(data["results"]) == 2

    async def test_files_endpoint(self, search_client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _mock_hf_model_detail()
        mock_resp.raise_for_status = MagicMock()

        with patch("tinyagentos.model_sources.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            resp = await search_client.get("/api/models/files/TheBloke/Llama-2-7B-GGUF")
            assert resp.status_code == 200
            data = resp.json()
            assert data["model_id"] == "TheBloke/Llama-2-7B-GGUF"
            assert len(data["files"]) == 2
            assert "compatibility" in data["files"][0]
            assert "ram_estimate_mb" in data["files"][0]
            assert "ram_available_mb" in data

    async def test_pull_endpoint_no_model(self, search_client):
        resp = await search_client.post(
            "/api/models/pull",
            json={"model_name": ""},
        )
        assert resp.status_code == 400

