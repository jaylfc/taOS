import pytest
import yaml
from pathlib import Path

STREAMING_DIR = Path(__file__).parent.parent / "app-catalog" / "streaming"

class TestStreamingManifests:
    def test_all_manifests_valid_yaml(self):
        manifests = list(STREAMING_DIR.rglob("manifest.yaml"))
        assert len(manifests) >= 12  # 4 Phase 1 + 8 Phase 2
        for path in manifests:
            data = yaml.safe_load(path.read_text())
            assert "id" in data
            assert "type" in data
            assert data["type"] == "streaming-app"

    def test_all_have_required_fields(self):
        required = {"id", "name", "type", "version", "streaming", "mcp", "expert_agent", "hardware_tiers"}
        for path in STREAMING_DIR.rglob("manifest.yaml"):
            data = yaml.safe_load(path.read_text())
            missing = required - set(data.keys())
            assert not missing, f"{path.name} missing: {missing}"

    def test_all_have_expert_agent(self):
        for path in STREAMING_DIR.rglob("manifest.yaml"):
            data = yaml.safe_load(path.read_text())
            expert = data.get("expert_agent", {})
            assert "name" in expert, f"{data['id']} missing expert_agent.name"
            assert "system_prompt" in expert, f"{data['id']} missing expert_agent.system_prompt"

    def test_all_have_dockerfile(self):
        for app_dir in STREAMING_DIR.iterdir():
            if app_dir.is_dir():
                assert (app_dir / "Dockerfile").exists(), f"{app_dir.name} missing Dockerfile"
