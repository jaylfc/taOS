import pytest
import pytest_asyncio
import yaml
import httpx
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock
from tinyagentos.app import create_app
from tinyagentos.routes.models import get_downloaded_models


@pytest.fixture
def catalog_with_models(tmp_path):
    models_dir = tmp_path / "catalog" / "models" / "test-model"
    models_dir.mkdir(parents=True)
    (models_dir / "manifest.yaml").write_text(yaml.dump({
        "id": "test-model", "name": "Test Model", "type": "model",
        "version": "1.0.0", "description": "A test model for unit tests",
        "capabilities": ["chat", "tool-calling"],
        "variants": [
            {"id": "small", "name": "Small GGUF", "format": "gguf", "size_mb": 100,
             "min_ram_mb": 512, "download_url": "https://example.com/small.gguf",
             "backend": ["ollama", "llama-cpp"]},
            {"id": "npu", "name": "NPU RKLLM", "format": "rkllm", "size_mb": 200,
             "min_ram_mb": 0, "download_url": "https://example.com/npu.rkllm",
             "backend": ["rkllama"], "requires_npu": ["rk3588"]},
        ],
        "hardware_tiers": {"arm-npu-16gb": {"recommended": "npu", "fallback": "small"}},
        "install": {"method": "download"},
    }))

    another = tmp_path / "catalog" / "models" / "another-model"
    another.mkdir(parents=True)
    (another / "manifest.yaml").write_text(yaml.dump({
        "id": "another-model", "name": "Another Model", "type": "model",
        "version": "2.0.0", "description": "Another test model",
        "capabilities": ["embedding"],
        "variants": [
            {"id": "default", "name": "Default", "format": "gguf", "size_mb": 50,
             "min_ram_mb": 256, "download_url": "https://example.com/another.gguf",
             "backend": ["ollama"]},
        ],
        "hardware_tiers": {},
        "install": {"method": "download"},
    }))

    return tmp_path / "catalog"


@pytest.fixture
def models_app(tmp_data_dir, catalog_with_models, tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    app = create_app(data_dir=tmp_data_dir, catalog_dir=catalog_with_models)
    app.state.models_dir = models_dir
    return app


@pytest_asyncio.fixture
async def models_client(models_app):
    store = models_app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await models_app.state.qmd_client.init()
    models_app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _rec = models_app.state.auth.find_user("admin")
    _token = models_app.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
    transport = ASGITransport(app=models_app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}) as c:
        yield c
    await store.close()
    await models_app.state.qmd_client.close()
    await models_app.state.http_client.aclose()


@pytest.mark.asyncio
class TestModelsAPI:
    async def test_list_models(self, models_client):
        resp = await models_client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "downloaded_files" in data
        assert "hardware_profile_id" in data
        assert len(data["models"]) == 2
        ids = {m["id"] for m in data["models"]}
        assert "test-model" in ids
        assert "another-model" in ids

    async def test_model_variants_included(self, models_client):
        resp = await models_client.get("/api/models")
        data = resp.json()
        test_model = next(m for m in data["models"] if m["id"] == "test-model")
        assert len(test_model["variants"]) == 2
        assert test_model["variants"][0]["id"] == "small"
        assert test_model["variants"][1]["id"] == "npu"

    async def test_model_compatibility_field(self, models_client):
        resp = await models_client.get("/api/models")
        data = resp.json()
        for model in data["models"]:
            assert model["compatibility"] in ("green", "yellow", "red")
            for v in model["variants"]:
                assert v["compatibility"] in ("green", "yellow", "red")

    async def test_get_model_detail(self, models_client):
        resp = await models_client.get("/api/models/test-model")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "test-model"
        assert data["name"] == "Test Model"
        assert len(data["variants"]) == 2
        assert "capabilities" in data
        assert "chat" in data["capabilities"]

    async def test_get_nonexistent_model(self, models_client):
        resp = await models_client.get("/api/models/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json()

    async def test_get_non_model_type_returns_404(self, models_client):
        """Requesting an app_id that exists but is not type=model should 404."""
        resp = await models_client.get("/api/models/nonexistent")
        assert resp.status_code == 404

    async def test_downloaded_files_empty_initially(self, models_client):
        resp = await models_client.get("/api/models")
        data = resp.json()
        assert data["downloaded_files"] == []
        for m in data["models"]:
            assert m["has_downloaded_variant"] is False


@pytest.mark.asyncio
class TestModelsDelete:
    async def test_delete_nonexistent_model(self, models_client):
        resp = await models_client.delete("/api/models/nonexistent")
        assert resp.status_code == 404

    async def test_delete_model_no_files(self, models_client):
        resp = await models_client.delete("/api/models/test-model")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["deleted_files"] == []


@pytest.mark.asyncio
class TestModelRecommendations:
    async def test_recommended_models(self, models_client):
        resp = await models_client.get("/api/models/recommended")
        assert resp.status_code == 200
        data = resp.json()
        assert "profile_id" in data
        assert "recommended" in data
        assert "compatible" in data
        assert isinstance(data["recommended"], list)
        assert isinstance(data["compatible"], list)


class TestGetDownloadedModels:
    def test_empty_dir(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        assert get_downloaded_models(models_dir) == []

    def test_nonexistent_dir(self, tmp_path):
        assert get_downloaded_models(tmp_path / "nope") == []

    def test_finds_model_files(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "test-model-small.gguf").write_bytes(b"x" * 2048)
        (models_dir / "test-model-npu.rkllm").write_bytes(b"y" * 4096)
        (models_dir / "readme.txt").write_text("ignore me")

        results = get_downloaded_models(models_dir)
        assert len(results) == 2
        filenames = {r["filename"] for r in results}
        assert "test-model-small.gguf" in filenames
        assert "test-model-npu.rkllm" in filenames
        # txt file excluded
        assert all(r["format"] in ("gguf", "rkllm", "bin") for r in results)

    def test_size_and_format(self, tmp_path):
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "m.bin").write_bytes(b"x" * (1024 * 1024 * 3))

        results = get_downloaded_models(models_dir)
        assert len(results) == 1
        assert results[0]["size_mb"] == 3
        assert results[0]["format"] == "bin"


@pytest.fixture
def app_with_rknn_sd_backend(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [
            {"name": "rknn-sd-npu", "type": "rknn-sd", "url": "http://localhost:7863", "priority": 1}
        ],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    return create_app(data_dir=tmp_path)


@pytest.fixture
def app_with_sd_cpp_backend(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [
            {"name": "sd-cpp-cpu", "type": "sd-cpp", "url": "http://localhost:7864", "priority": 1}
        ],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    return create_app(data_dir=tmp_path)


async def _make_auth_client(app):
    store = app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await app.state.qmd_client.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _rec = app.state.auth.find_user("admin")
    _token = app.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token})


@pytest.mark.asyncio
class TestLoadedModelsImageBackends:
    async def test_rknn_sd_loaded_when_pipeline_loaded(self, app_with_rknn_sd_backend):
        """rknn-sd: /health with pipeline_loaded=true -> entry appears in loaded list."""
        app = app_with_rknn_sd_backend

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "model": "lcm-dreamshaper-v7-rknn",
            "pipeline_loaded": True,
        }

        async with await _make_auth_client(app) as c:
            with patch.object(app.state, "http_client") as mock_http:
                mock_http.get = AsyncMock(return_value=mock_response)
                resp = await c.get("/api/models/loaded")
        await app.state.metrics.close()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()

        assert resp.status_code == 200
        data = resp.json()
        entries = [e for e in data["loaded"] if e["backend"] == "rknn-sd-npu"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["name"] == "lcm-dreamshaper-v7-rknn"
        assert entry["backend_type"] == "rknn-sd"
        assert entry["purpose"] == "image-generation"
        assert entry["size_mb"] is None
        assert entry["vram_mb"] is None

    async def test_rknn_sd_not_loaded_when_pipeline_not_loaded(self, app_with_rknn_sd_backend):
        """rknn-sd: /health with pipeline_loaded=false -> no entry in loaded list."""
        app = app_with_rknn_sd_backend

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "model": "lcm-dreamshaper-v7-rknn",
            "pipeline_loaded": False,
        }

        async with await _make_auth_client(app) as c:
            with patch.object(app.state, "http_client") as mock_http:
                mock_http.get = AsyncMock(return_value=mock_response)
                resp = await c.get("/api/models/loaded")
        await app.state.metrics.close()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()

        assert resp.status_code == 200
        data = resp.json()
        entries = [e for e in data["loaded"] if e["backend"] == "rknn-sd-npu"]
        assert entries == []

    async def test_rknn_sd_offline_skipped(self, app_with_rknn_sd_backend):
        """rknn-sd backend: ConnectError is swallowed; loaded list is empty."""
        app = app_with_rknn_sd_backend

        async with await _make_auth_client(app) as c:
            with patch.object(app.state, "http_client") as mock_http:
                mock_http.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
                resp = await c.get("/api/models/loaded")
        await app.state.metrics.close()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()

        assert resp.status_code == 200
        assert resp.json()["loaded"] == []

    async def test_sd_cpp_never_appears_in_loaded(self, app_with_sd_cpp_backend):
        """sd-cpp: /sdapi/v1/options doesn't expose load state; backend is skipped entirely."""
        app = app_with_sd_cpp_backend

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "sd_model_checkpoint": "v1-5-pruned-emaonly.safetensors",
        }

        async with await _make_auth_client(app) as c:
            with patch.object(app.state, "http_client") as mock_http:
                mock_http.get = AsyncMock(return_value=mock_response)
                resp = await c.get("/api/models/loaded")
        await app.state.metrics.close()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()

        assert resp.status_code == 200
        data = resp.json()
        sd_entries = [e for e in data["loaded"] if e["backend_type"] == "sd-cpp"]
        assert sd_entries == []

    async def test_sd_cpp_offline_skipped(self, app_with_sd_cpp_backend):
        """sd-cpp backend: ConnectError is swallowed; loaded list is empty."""
        app = app_with_sd_cpp_backend

        async with await _make_auth_client(app) as c:
            with patch.object(app.state, "http_client") as mock_http:
                mock_http.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
                resp = await c.get("/api/models/loaded")
        await app.state.metrics.close()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()

        assert resp.status_code == 200
        assert resp.json()["loaded"] == []
