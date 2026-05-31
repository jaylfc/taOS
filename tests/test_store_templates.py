import pytest
from pathlib import Path
import yaml
import tempfile
import asyncio

from tinyagentos.routes.store import _STORE_TEMPLATE_DIR


class TestStoreTemplates:
    """Tests for /api/store/templates endpoint and YAML manifest files."""

    def test_template_dir_exists(self):
        assert _STORE_TEMPLATE_DIR.is_dir(), f"Expected {_STORE_TEMPLATE_DIR} to exist"

    def test_at_least_three_templates(self):
        yamls = list(_STORE_TEMPLATE_DIR.glob("*.yaml"))
        assert len(yamls) >= 3, f"Expected >=3 templates, found {len(yamls)}"

    def test_all_templates_have_required_fields(self):
        for tmpl_path in sorted(_STORE_TEMPLATE_DIR.glob("*.yaml")):
            with open(tmpl_path) as f:
                data = yaml.safe_load(f)
            assert data is not None, f"{tmpl_path.name}: invalid YAML"
            assert data.get("type") == "template", f"{tmpl_path.name}: type != template"
            assert data.get("id"), f"{tmpl_path.name}: missing id"
            assert data.get("name"), f"{tmpl_path.name}: missing name"
            assert data.get("hardware_tier"), f"{tmpl_path.name}: missing hardware_tier"
            assert data.get("description"), f"{tmpl_path.name}: missing description"
            assert isinstance(data.get("apps"), list), f"{tmpl_path.name}: apps not a list"
            assert len(data["apps"]) >= 3, f"{tmpl_path.name}: fewer than 3 apps"

    def test_hardware_tiers_are_valid(self):
        valid_tiers = {"arm-npu-16gb", "arm-npu-32gb", "x86-cuda-12gb",
                       "x86-vulkan-8gb", "cpu-only"}
        for tmpl_path in sorted(_STORE_TEMPLATE_DIR.glob("*.yaml")):
            with open(tmpl_path) as f:
                data = yaml.safe_load(f)
            tier = data.get("hardware_tier", "")
            assert tier in valid_tiers, f"{tmpl_path.name}: unknown tier '{tier}'"

    def test_app_ids_are_strings(self):
        for tmpl_path in sorted(_STORE_TEMPLATE_DIR.glob("*.yaml")):
            with open(tmpl_path) as f:
                data = yaml.safe_load(f)
            for app_id in data.get("apps", []):
                assert isinstance(app_id, str), f"{tmpl_path.name}: app '{app_id}' not a string"

    def test_template_ids_are_unique(self):
        ids = []
        for tmpl_path in sorted(_STORE_TEMPLATE_DIR.glob("*.yaml")):
            with open(tmpl_path) as f:
                data = yaml.safe_load(f)
            ids.append(data["id"])
        assert len(ids) == len(set(ids)), f"Duplicate template ids: {ids}"

    def test_templates_route_is_registered(self):
        """Verify /api/store/templates route is mounted on the router."""
        from tinyagentos.routes.store import router
        route_paths = [r.path for r in router.routes]
        assert "/api/store/templates" in route_paths, \
            f"Expected /api/store/templates in routes, got {route_paths}"


class TestStoreTemplatesEndpoint:
    """Function-level tests for template listing logic."""

    def test_malformed_yaml_skipped_gracefully(self, monkeypatch):
        """When a template YAML file is malformed, it's skipped without
        crashing or affecting valid templates."""
        import tinyagentos.routes.store as store_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "good.yaml").write_text(
                "id: good-one\nname: Good\ntype: template\n"
                "hardware_tier: cpu-only\ndescription: ok\napps: []\n"
            )
            (tmp / "bad.yaml").write_text("id: broken\n  dangling indent: [\n{oops\n")
            monkeypatch.setattr(store_mod, "_STORE_TEMPLATE_DIR", tmp)

            result = asyncio.run(store_mod.list_store_templates())
            templates = result["templates"]
            ids = [t["id"] for t in templates]
            assert "good-one" in ids
            assert "broken" not in ids, "malformed YAML should be skipped"

    def test_bad_type_field_skipped(self, monkeypatch):
        """Template YAML without type: template is skipped."""
        import tinyagentos.routes.store as store_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "real.yaml").write_text(
                "id: real\nname: Real\ntype: template\n"
                "hardware_tier: cpu-only\ndescription: ok\napps: []\n"
            )
            (tmp / "not-template.yaml").write_text(
                "id: other\nname: Other\ntype: model\n"
                "hardware_tier: cpu-only\ndescription: N/A\napps: []\n"
            )
            monkeypatch.setattr(store_mod, "_STORE_TEMPLATE_DIR", tmp)

            result = asyncio.run(store_mod.list_store_templates())
            templates = result["templates"]
            ids = [t["id"] for t in templates]
            assert "real" in ids
            assert "other" not in ids, "non-template type should be skipped"

    def test_empty_dir_returns_empty_list(self, monkeypatch):
        """Empty template dir returns empty list, not an error."""
        import tinyagentos.routes.store as store_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setattr(store_mod, "_STORE_TEMPLATE_DIR", Path(tmpdir))
            result = asyncio.run(store_mod.list_store_templates())
            assert result == {"templates": []}

    def test_nonexistent_dir_returns_empty_list(self, monkeypatch):
        """Nonexistent template dir returns empty list, not an error."""
        import tinyagentos.routes.store as store_mod
        monkeypatch.setattr(store_mod, "_STORE_TEMPLATE_DIR", Path("/nonexistent/path"))
        result = asyncio.run(store_mod.list_store_templates())
        assert result == {"templates": []}
