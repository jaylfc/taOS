"""RED tests for Q2-8 catalog and build nits."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to load scripts as modules
# ---------------------------------------------------------------------------

_AUDIT_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "audit-manifests.py"
_IGNORE_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_dependency_audit_ignores.py"


def _load_audit():
    spec = importlib.util.spec_from_file_location("audit_manifests", _AUDIT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_ignore():
    spec = importlib.util.spec_from_file_location("check_dependency_audit_ignores", _IGNORE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Test 1: registry loads hailo-ollama
# ---------------------------------------------------------------------------

class TestRegistryLoadsHailoOllama:
    def test_hailo_ollama_has_version(self):
        """hailo-ollama must declare a version so the registry does not drop it."""
        from tinyagentos.registry import AppManifest
        import yaml

        manifest_path = Path(__file__).resolve().parent.parent.parent / "app-catalog" / "services" / "hailo-ollama" / "manifest.yaml"
        data = yaml.safe_load(manifest_path.read_text())
        assert "version" in data, "hailo-ollama manifest is missing version"
        assert data["version"], "hailo-ollama manifest version must be non-empty"

    def test_hailo_ollama_loads_in_registry(self):
        """AppRegistry must successfully load hailo-ollama."""
        from tinyagentos.registry import AppRegistry

        catalog_dir = Path(__file__).resolve().parent.parent.parent / "app-catalog"
        installed_path = catalog_dir / "_test_installed.json"
        registry = AppRegistry(catalog_dir=catalog_dir, installed_path=installed_path)
        registry.reload()
        manifest = registry.get("hailo-ollama")
        assert manifest is not None, "registry did not load hailo-ollama"
        assert manifest.id == "hailo-ollama"


# ---------------------------------------------------------------------------
# Test 2: supervisor resolves a start command for a fixture plugin
# ---------------------------------------------------------------------------

class TestSupervisorResolvesStartCommand:
    def test_resolve_cmd_uses_real_accessor(self):
        """_resolve_cmd must use AppRegistry.get, not the nonexistent get_manifest."""
        from tinyagentos.mcp.supervisor import MCPSupervisor

        catalog = MagicMock()
        manifest = MagicMock()
        manifest.lifecycle = {"start": ["my-plugin", "--serve"]}
        catalog.get = MagicMock(return_value=manifest)

        sup = MCPSupervisor(store=MagicMock(), catalog=catalog, notif_store=None)
        cmd = sup._resolve_cmd("my-plugin", {"config": {}})
        assert cmd == ["my-plugin", "--serve"], f"expected start command, got {cmd!r}"
        catalog.get.assert_called_once_with("my-plugin")


# ---------------------------------------------------------------------------
# Test 3: audit-manifests.py fails on duplicate id and missing version
# ---------------------------------------------------------------------------

class TestAuditManifestsDrift:
    def test_fails_on_manifest_without_version(self, tmp_path: Path):
        """A manifest without a version must cause audit-manifests.py to fail."""
        mod = _load_audit()
        root = tmp_path / "catalog"
        root.mkdir()
        (root / "services").mkdir()
        svc = root / "services" / "no-version"
        svc.mkdir()
        (svc / "manifest.yaml").write_text(
            "id: no-version\nname: No Version\ntype: service\n"
        )
        rc = mod.audit(root)
        assert rc == 1, "audit-manifests.py should fail on a manifest without version"

    def test_fails_on_duplicate_id(self, tmp_path: Path):
        """Duplicate manifest IDs must cause audit-manifests.py to fail."""
        mod = _load_audit()
        root = tmp_path / "catalog"
        root.mkdir()
        (root / "services").mkdir()
        svc1 = root / "services" / "dup-id"
        svc1.mkdir()
        (svc1 / "manifest.yaml").write_text(
            "id: dup-id\nname: First\ntype: service\nversion: 1.0.0\n"
        )
        svc2 = root / "services" / "dup-id-2"
        svc2.mkdir()
        (svc2 / "manifest.yaml").write_text(
            "id: dup-id\nname: Second\ntype: service\nversion: 1.0.0\n"
        )
        rc = mod.audit(root)
        assert rc == 1, "audit-manifests.py should fail on duplicate id"


# ---------------------------------------------------------------------------
# Test 5: first-party tag naming check
# ---------------------------------------------------------------------------

def _unpublished_first_party_pins(root: Path) -> list[str]:
    import re

    workflow_image_tags = set()
    workflow_dir = root / ".github" / "workflows"
    if workflow_dir.exists():
        for wf in workflow_dir.glob("*.y*ml"):
            content = wf.read_text()
            for line in content.splitlines():
                m = re.search(r'-t\s+ghcr\.io/jaylfc/([^\s:]+):(.+?)\\?\s*$', line)
                if m:
                    image = m.group(1).strip()
                    tag = m.group(2).strip()
                    if tag.startswith("${"):
                        continue
                    workflow_image_tags.add((image, tag))

    bad_pins = []
    app_catalog = root / "app-catalog"
    if not app_catalog.exists():
        return bad_pins
    for df in app_catalog.rglob("Dockerfile*"):
        content = df.read_text()
        for line in content.splitlines():
            m = re.match(r'FROM\s+(?:--platform=\S+\s+)?ghcr\.io/jaylfc/([^\s@:]+)(?:[@:](.+))?', line)
            if m:
                image_part = m.group(1)
                tag_or_digest = m.group(2) or "latest"
                if tag_or_digest.startswith("sha256:"):
                    continue
                if (image_part, tag_or_digest) not in workflow_image_tags:
                    bad_pins.append(f"{df}: {image_part}:{tag_or_digest}")
    return bad_pins


# ---------------------------------------------------------------------------
# Test 5: first-party tag naming check
# ---------------------------------------------------------------------------

class TestFirstPartyTagNaming:
    def test_every_fp_tag_has_corresponding_workflow(self):
        """Check the repo's declared publish list, NOT the registry.

        Every FROM ghcr.io/jaylfc/... pin in app-catalog/ must name a tag
        that the repo's own workflows actually publish, or be a @sha256: digest.
        A green here means the pin appears in the workflow YAML - it is NOT
        proof the image resolves in the registry.
        """
        bad_pins = _unpublished_first_party_pins(
            Path(__file__).resolve().parent.parent.parent
        )
        assert not bad_pins, f"Pins not covered by workflow tags: {bad_pins}"

    def test_tag_pin_forms_parameterised(self, tmp_path: Path):
        """All four tag-pin arms plus --platform: drives off a fixture tree."""
        workflow_dir = tmp_path / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        (workflow_dir / "publish.yaml").write_text(
            "steps:\n"
            "  - run: docker build \\\n"
            "    -t ghcr.io/jaylfc/taos-neko-cdp:latest \\\n"
            "    -t ghcr.io/jaylfc/taos-neko-cdp:2.4.0\n"
        )

        app_catalog = tmp_path / "app-catalog" / "streaming" / "neko-browser"
        app_catalog.mkdir(parents=True)

        cases = [
            ("FROM ghcr.io/jaylfc/taos-neko-cdp:2.4.0\n", False, "published tag for same image"),
            ("FROM ghcr.io/jaylfc/taos-neko-cdp:latest\n", False, "latest published"),
            ("FROM ghcr.io/jaylfc/other-image:2.4.0\n", True, "same tag published for different image"),
            ("FROM ghcr.io/jaylfc/taos-neko-cdp:9.9.9-nope\n", True, "unpublished tag"),
            ("FROM ghcr.io/jaylfc/taos-neko-cdp@sha256:abc123\n", False, "digest pin"),
            ("FROM --platform=linux/arm64 ghcr.io/jaylfc/taos-neko-cdp:2.4.0\n", False, "platform-prefixed published tag"),
        ]

        for df_content, expect_failure, desc in cases:
            (app_catalog / "Dockerfile.tmp").write_text(df_content)
            bad_pins = _unpublished_first_party_pins(tmp_path)
            if expect_failure:
                assert bad_pins, f"Expected failure for {desc}, but passed"
            else:
                assert not bad_pins, f"Expected pass for {desc}, but got: {bad_pins}"

class TestStaleIgnoreCheck:
    def test_fails_when_ignored_cve_no_longer_reported(self, tmp_path: Path, capsys: pytest.CaptureFixture):
        """If pip-audit does not report an ignored CVE, the ignore list is stale and the check must fail."""
        mod = _load_ignore()
        sec = tmp_path / "security"
        sec.mkdir()
        (tmp_path / "uv.lock").write_text("")
        ignore = sec / "pip-audit-ignore.toml"
        ignore.write_text(
            '[[ignore]]\npackage = "pip"\nid = "CVE-2026-3219"\ncheck_upgrade = false\n'
        )
        with patch.object(mod.shutil, "which", return_value="/usr/bin/uv"):
            with patch.object(mod, "run_pip_audit", return_value=([], "")):
                rc = mod.main(["--ignore-file", str(ignore)])
        captured = capsys.readouterr()
        assert rc == 1, "stale ignore check should fail when CVE is no longer reported"
        assert "CVE-2026-3219" in captured.out or "stale" in captured.out.lower()
