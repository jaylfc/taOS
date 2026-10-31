"""Tests for the managed-service manifest contract lint
(scripts/check_manifests.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
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
        "name": sid,
        "type": "service",
        "version": "1.0.0",
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
        "name": "rk-llama-cpp",
        "type": "service",
        "version": "1.0.0",
        "category": "llm-runtime",
        "lifecycle": {"backend_type": "openai-compatible", "auto_manage": False},
    }
    _write(tmp_path, "rk-llama-cpp", m)
    assert check_manifests.lint_managed(tmp_path) == []


def test_service_without_lifecycle_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "ollama", {
        "id": "ollama", "name": "ollama", "type": "service", "version": "1.0.0",
    })
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


def test_requires_string_reports_schema_validation_failure(tmp_path: Path) -> None:
    """A string ``requires`` field fails the same AppManifest schema the
    runtime loads with and is reported as a schema validation failure."""
    _write(tmp_path, "bad-requires", {
        "id": "bad-requires",
        "name": "bad",
        "type": "service",
        "version": "1.0.0",
        "requires": "ollama",
    })
    errors = check_manifests.lint_managed(tmp_path)
    assert len(errors) == 1
    assert "bad-requires" in errors[0]
    assert "failed schema validation" in errors[0]


def test_real_catalog_is_clean() -> None:
    """The shipped app-catalog must pass the managed-service lint."""
    root = Path(__file__).resolve().parent.parent / "app-catalog"
    errors = check_manifests.lint_managed(root)
    assert errors == [], "real catalog failed managed-lint:\n" + "\n".join(errors)


# --- #148 Phase 0: model capability-field lint (warn-only) ---

def _model_ok(mid: str = "qwen3-8b") -> dict:
    return {
        "id": mid,
        "name": mid,
        "type": "model",
        "version": "1.0.0",
        "family": "qwen3",
        "min_context": 4096,
        "max_context": 131072,
        "variants": [
            {
                "id": "q4",
                "format": "gguf",
                "size_mb": 5000,
                "vram_floor_mb": 6000,
                "approx_tok_s_by_profile": {"gaming-pc-nvidia": 45.0},
            }
        ],
    }


def _write_model(root: Path, mid: str, manifest: dict) -> None:
    d = root / "models" / mid
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(yaml.safe_dump(manifest))


def test_model_fields_complete_passes(tmp_path: Path) -> None:
    _write_model(tmp_path, "qwen3-8b", _model_ok())
    assert check_manifests.lint_model_fields(tmp_path) == []


def test_model_fields_missing_family_warns(tmp_path: Path) -> None:
    m = _model_ok()
    del m["family"]
    _write_model(tmp_path, "qwen3-8b", m)
    warns = check_manifests.lint_model_fields(tmp_path)
    assert any("family" in w for w in warns)


def test_model_fields_missing_context_warns(tmp_path: Path) -> None:
    m = _model_ok()
    del m["min_context"]
    del m["max_context"]
    _write_model(tmp_path, "qwen3-8b", m)
    warns = check_manifests.lint_model_fields(tmp_path)
    assert any("min_context" in w for w in warns)


def test_model_fields_missing_envelope_warns(tmp_path: Path) -> None:
    m = _model_ok()
    del m["variants"][0]["vram_floor_mb"]
    del m["variants"][0]["approx_tok_s_by_profile"]
    _write_model(tmp_path, "qwen3-8b", m)
    warns = check_manifests.lint_model_fields(tmp_path)
    assert any("envelope" in w for w in warns)


def test_model_fields_non_model_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "rkllama", _managed_ok())
    # lint_model_fields only looks under models/, so the service manifest
    # is invisible to it.
    assert check_manifests.lint_model_fields(tmp_path) == []


# --- Gate-integrity red proof (review lead request) ---
#
# These tests drive the real CLI entry point (main), not just the helper
# functions, so deleting either new check from the gate makes them go red:
#   * the AppManifest boundary validation in lint_managed, and
#   * the #148 capability-field warnings in lint_model_fields.

class TestGateRedProof:
    """RED PROOF: the gate refuses a bad manifest and reports the new
    capability fields; remove either check and these fail."""

    def test_gate_refuses_manifest_failing_runtime_schema(self, tmp_path, capsys):
        """A manifest the runtime's AppManifest model rejects must exit 1."""
        _write(tmp_path, "bad-requires", {
            "id": "bad-requires",
            "name": "bad",
            "type": "service",
            "version": "1.0.0",
            "requires": "ollama",
        })
        rc = check_manifests.main(["managed-lint", "--root", str(tmp_path)])
        captured = capsys.readouterr()
        assert rc == 1
        assert "failed schema validation" in captured.err
        assert "bad-requires" in captured.err

    @pytest.mark.parametrize(
        ("drop", "expected"),
        [
            ("family", "family"),
            ("context", "min_context"),
            ("envelope", "envelope"),
        ],
    )
    def test_gate_warns_on_each_new_model_field(
        self, tmp_path, capsys, drop, expected,
    ):
        """Each #148 capability check must reach stderr through main()."""
        m = _model_ok()
        if drop == "family":
            del m["family"]
        elif drop == "context":
            del m["min_context"]
            del m["max_context"]
        else:
            del m["variants"][0]["vram_floor_mb"]
            del m["variants"][0]["approx_tok_s_by_profile"]
        _write_model(tmp_path, "qwen3-8b", m)

        rc = check_manifests.main(["managed-lint", "--root", str(tmp_path)])
        err = capsys.readouterr().err
        assert rc == 0  # warnings are warn-only, they do not fail the gate
        assert "warn:" in err
        assert expected in err
