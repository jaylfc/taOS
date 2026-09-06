# tests/test_registry.py
import json
import pytest
import yaml
from tinyagentos.registry import AppManifest, AppRegistry, AppState


@pytest.fixture
def catalog_dir(tmp_path):
    """Create a test catalog with sample manifests."""
    agents = tmp_path / "agents" / "smolagents"
    agents.mkdir(parents=True)
    (agents / "manifest.yaml").write_text(yaml.dump({
        "id": "smolagents",
        "name": "SmolAgents",
        "type": "agent-framework",
        "version": "1.0.0",
        "description": "Code-based agent framework",
        "requires": {"ram_mb": 256},
        "install": {"method": "pip", "package": "smolagents"},
        "hardware_tiers": {"arm-npu-16gb": "full", "cpu-only": "full"},
    }))
    models = tmp_path / "models" / "qwen3-8b"
    models.mkdir(parents=True)
    (models / "manifest.yaml").write_text(yaml.dump({
        "id": "qwen3-8b",
        "name": "Qwen 3 8B",
        "type": "model",
        "version": "3.0.0",
        "description": "General-purpose chat model",
        "variants": [
            {"id": "q4_k_m", "name": "Q4_K_M", "format": "gguf", "size_mb": 4800,
             "min_ram_mb": 6144, "download_url": "https://example.com/qwen3-8b.gguf",
             "backend": ["ollama", "llama-cpp"]},
        ],
        "hardware_tiers": {"arm-npu-16gb": {"recommended": "q4_k_m"}, "cpu-only": {"recommended": "q4_k_m"}},
    }))
    services = tmp_path / "services" / "gitea"
    services.mkdir(parents=True)
    (services / "manifest.yaml").write_text(yaml.dump({
        "id": "gitea",
        "name": "Gitea",
        "type": "service",
        "version": "1.22.0",
        "description": "Self-hosted Git server",
        "requires": {"ram_mb": 256, "ports": [3000]},
        "install": {"method": "docker", "image": "gitea/gitea:1.22",
                    "volumes": ["data:/data"], "env": {"ROOT_URL": "http://localhost:3000"}},
        "hardware_tiers": {"arm-npu-16gb": "full", "cpu-only": "full"},
    }))
    return tmp_path


@pytest.fixture
def registry(catalog_dir, tmp_path):
    installed_path = tmp_path / "installed.json"
    return AppRegistry(catalog_dir=catalog_dir, installed_path=installed_path)


class TestAppManifest:
    def test_load_agent_manifest(self, catalog_dir):
        m = AppManifest.from_file(catalog_dir / "agents" / "smolagents" / "manifest.yaml")
        assert m.id == "smolagents"
        assert m.type == "agent-framework"
        assert m.install["method"] == "pip"

    def test_load_model_manifest(self, catalog_dir):
        m = AppManifest.from_file(catalog_dir / "models" / "qwen3-8b" / "manifest.yaml")
        assert m.id == "qwen3-8b"
        assert m.type == "model"
        assert len(m.variants) == 1

    def test_load_service_manifest(self, catalog_dir):
        m = AppManifest.from_file(catalog_dir / "services" / "gitea" / "manifest.yaml")
        assert m.id == "gitea"
        assert m.type == "service"
        assert m.install["method"] == "docker"

    def test_compatible_with_tier(self, catalog_dir):
        m = AppManifest.from_file(catalog_dir / "agents" / "smolagents" / "manifest.yaml")
        assert m.is_compatible("arm-npu-16gb")
        assert m.is_compatible("cpu-only")
        assert not m.is_compatible("nonexistent-tier")

    def test_weights_license_fields_default_empty(self, catalog_dir):
        """A manifest with no weights_license/license_class (e.g. gitea)
        must not silently inherit a non-empty default."""
        m = AppManifest.from_file(catalog_dir / "services" / "gitea" / "manifest.yaml")
        assert m.weights_license == ""
        assert m.license_class == ""

    def test_weights_license_fields_load_when_present(self, tmp_path):
        d = tmp_path / "services" / "nc-service"
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(yaml.dump({
            "id": "nc-service",
            "name": "NC Service",
            "type": "service",
            "version": "1.0.0",
            "license": "MIT",
            "weights_license": "CC-BY-NC 4.0",
            "license_class": "non-commercial",
        }))
        m = AppManifest.from_file(d / "manifest.yaml")
        assert m.license == "MIT"
        assert m.weights_license == "CC-BY-NC 4.0"
        assert m.license_class == "non-commercial"

    def test_context_window_survives_when_present(self, tmp_path):
        """A model manifest declaring ``context_window`` must surface it on the
        loaded AppManifest object, not silently drop it to 0 (#1740 loader bug)."""
        d = tmp_path / "models" / "rkllm-model"
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(yaml.dump({
            "id": "rkllm-model",
            "name": "RKLLM Model",
            "type": "model",
            "version": "1.0.0",
            "variants": [{"id": "w8a8", "format": "rkllm", "size_mb": 2040,
                          "download_url": "https://example.com/x.rkllm"}],
            "context_window": 4096,
        }))
        m = AppManifest.from_file(d / "manifest.yaml")
        assert m.context_window == 4096

    def test_context_window_defaults_to_zero_when_absent(self, catalog_dir):
        """A manifest without ``context_window`` (e.g. a service) must report 0,
        preserving the unknown-window semantics consumers rely on."""
        m = AppManifest.from_file(catalog_dir / "services" / "gitea" / "manifest.yaml")
        assert m.context_window == 0


class TestAppRegistry:
    def test_load_catalog(self, registry):
        apps = registry.list_available()
        assert len(apps) == 3
        ids = {a.id for a in apps}
        assert "smolagents" in ids
        assert "qwen3-8b" in ids
        assert "gitea" in ids

    def test_filter_by_type(self, registry):
        models = registry.list_available(type_filter="model")
        assert len(models) == 1
        assert models[0].id == "qwen3-8b"

    def test_get_app(self, registry):
        app = registry.get("smolagents")
        assert app is not None
        assert app.name == "SmolAgents"

    def test_get_nonexistent(self, registry):
        assert registry.get("nonexistent") is None

    def test_installed_empty_initially(self, registry):
        assert registry.list_installed() == []

    def test_mark_installed(self, registry):
        registry.mark_installed("smolagents", "1.0.0")
        installed = registry.list_installed()
        assert len(installed) == 1
        assert installed[0]["id"] == "smolagents"
        assert installed[0]["state"] == "installed"

    def test_mark_uninstalled(self, registry):
        registry.mark_installed("smolagents", "1.0.0")
        registry.mark_uninstalled("smolagents")
        assert registry.list_installed() == []

    def test_is_installed(self, registry):
        assert not registry.is_installed("smolagents")
        registry.mark_installed("smolagents", "1.0.0")
        assert registry.is_installed("smolagents")

    def test_installed_persists(self, registry, tmp_path):
        registry.mark_installed("gitea", "1.22.0")
        # Create new registry instance pointing at same file
        registry2 = AppRegistry(catalog_dir=registry.catalog_dir, installed_path=registry.installed_path)
        assert registry2.is_installed("gitea")

    def test_catalog_loads_lazily(self, catalog_dir, tmp_path):
        """Construction must not parse manifests; first read triggers load."""
        installed_path = tmp_path / "installed.json"
        r = AppRegistry(catalog_dir=catalog_dir, installed_path=installed_path)
        assert r._catalog is None
        r.list_available()
        assert r._catalog is not None
        assert len(r._catalog) == 3

    def test_concurrent_first_read_does_not_double_load(self, catalog_dir, tmp_path):
        """Two threads racing on a cold registry must not produce a doubled catalog."""
        import threading

        installed_path = tmp_path / "installed.json"
        r = AppRegistry(catalog_dir=catalog_dir, installed_path=installed_path)
        results: list[int] = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(len(r.list_available()))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(n == 3 for n in results)
        assert len(r._catalog) == 3
