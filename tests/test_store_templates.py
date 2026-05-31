import pytest
from pathlib import Path
import yaml

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
