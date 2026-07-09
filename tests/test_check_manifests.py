"""Tests for the managed-service manifest contract lint
(scripts/check_manifests.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_SPEC = importlib.util.spec_from_file_location(
    "check_manifests",
    Path(__file__).resolve().parent.parent / "scripts" / "check_manifests.py",
)
check_manifests = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_manifests)  # type: ignore[union-attr]


def _write(root: Path, sid: str, manifest: dict) -> None:
    d = root / "services" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(yaml.safe_dump(manifest))


def _managed_ok(sid: str = "rkllama") -> dict:
    return {
        "id": sid,
        "type": "service",
        "category": "llm-runtime",
        "lifecycle": {
            "backend_type": "rkllama",
            "auto_manage": True,
            "unit": f"{sid}.service",
            "scope": "system",
            "health": {"url": "http://localhost:7833/api/tags", "expect": '"models"'},
        },
    }


def test_valid_managed_manifest_passes(tmp_path: Path) -> None:
    _write(tmp_path, "rkllama", _managed_ok())
    assert check_manifests.lint_managed(tmp_path) == []


def test_auto_manage_without_unit_fails(tmp_path: Path) -> None:
    m = _managed_ok()
    del m["lifecycle"]["unit"]
    _write(tmp_path, "rkllama", m)
    errors = check_manifests.lint_managed(tmp_path)
    assert len(errors) == 1
    assert "lifecycle.unit" in errors[0]


def test_auto_manage_without_health_fails(tmp_path: Path) -> None:
    m = _managed_ok()
    del m["lifecycle"]["health"]
    _write(tmp_path, "rkllama", m)
    errors = check_manifests.lint_managed(tmp_path)
    assert "lifecycle.health" in errors[0]


def test_invalid_scope_fails(tmp_path: Path) -> None:
    m = _managed_ok()
    m["lifecycle"]["scope"] = "root"
    _write(tmp_path, "rkllama", m)
    errors = check_manifests.lint_managed(tmp_path)
    assert any("scope is 'root'" in e for e in errors)


def test_non_managed_service_is_ignored(tmp_path: Path) -> None:
    # auto_manage false -> not claiming managed, no unit/health required
    m = {
        "id": "rk-llama-cpp",
        "type": "service",
        "category": "llm-runtime",
        "lifecycle": {"backend_type": "openai-compatible", "auto_manage": False},
    }
    _write(tmp_path, "rk-llama-cpp", m)
    assert check_manifests.lint_managed(tmp_path) == []


def test_service_without_lifecycle_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "ollama", {"id": "ollama", "type": "service"})
    assert check_manifests.lint_managed(tmp_path) == []


def test_grandfathered_service_is_skipped(tmp_path: Path, monkeypatch) -> None:
    m = _managed_ok("legacy-backend")
    del m["lifecycle"]["unit"]  # would normally fail
    _write(tmp_path, "legacy-backend", m)
    monkeypatch.setattr(
        check_manifests, "GRANDFATHER", {"legacy-backend": "pre-systemd, tracked in #NNNN"}
    )
    assert check_manifests.lint_managed(tmp_path) == []


def test_non_bool_auto_manage_is_flagged_and_enforced(tmp_path: Path) -> None:
    # A quoted "true" must not slip past the identity check.
    m = _managed_ok()
    m["lifecycle"]["auto_manage"] = "true"
    _write(tmp_path, "rkllama", m)
    errors = check_manifests.lint_managed(tmp_path)
    assert any("must be a boolean true" in e for e in errors)


def test_unparseable_yaml_is_an_error(tmp_path: Path) -> None:
    d = tmp_path / "services" / "broken"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text("id: broken\nlifecycle: {: bad")
    errors = check_manifests.lint_managed(tmp_path)
    assert any("not valid YAML" in e for e in errors)


def test_unit_without_service_suffix_is_flagged(tmp_path: Path) -> None:
    m = _managed_ok()
    m["lifecycle"]["unit"] = "rkllama"  # missing .service
    _write(tmp_path, "rkllama", m)
    errors = check_manifests.lint_managed(tmp_path)
    assert any("ending in .service" in e for e in errors)


def test_non_string_expect_is_flagged(tmp_path: Path) -> None:
    m = _managed_ok()
    m["lifecycle"]["health"]["expect"] = ["models"]
    _write(tmp_path, "rkllama", m)
    errors = check_manifests.lint_managed(tmp_path)
    assert any("expect must be a string" in e for e in errors)


def test_real_catalog_is_clean() -> None:
    """The shipped app-catalog must pass the managed-service lint."""
    root = Path(__file__).resolve().parent.parent / "app-catalog"
    errors = check_manifests.lint_managed(root)
    assert errors == [], "real catalog failed managed-lint:\n" + "\n".join(errors)
