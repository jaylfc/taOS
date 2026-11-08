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
    warns, errors = check_manifests.lint_model_fields(tmp_path)
    assert warns == []
    assert errors == []


def test_model_fields_missing_family_warns(tmp_path: Path) -> None:
    m = _model_ok()
    del m["family"]
    _write_model(tmp_path, "qwen3-8b", m)
    warns, errors = check_manifests.lint_model_fields(tmp_path)
    assert any("family" in w for w in warns)
    assert errors == []


def test_model_fields_missing_context_warns(tmp_path: Path) -> None:
    m = _model_ok()
    del m["min_context"]
    del m["max_context"]
    _write_model(tmp_path, "qwen3-8b", m)
    warns, errors = check_manifests.lint_model_fields(tmp_path)
    assert any("min_context" in w for w in warns)
    assert errors == []


def test_model_fields_missing_envelope_warns(tmp_path: Path) -> None:
    m = _model_ok()
    del m["variants"][0]["vram_floor_mb"]
    del m["variants"][0]["approx_tok_s_by_profile"]
    _write_model(tmp_path, "qwen3-8b", m)
    warns, errors = check_manifests.lint_model_fields(tmp_path)
    assert any("envelope" in w for w in warns)
    assert errors == []


def test_model_fields_non_model_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "rkllama", _managed_ok())
    # lint_model_fields only looks under models/, so the service manifest
    # is invisible to it.
    warns, errors = check_manifests.lint_model_fields(tmp_path)
    assert warns == []
    assert errors == []


def test_malformed_model_yaml_is_an_error(tmp_path: Path) -> None:
    """Unparseable model YAML must be reported, not silently skipped.

    lint_managed scans only services/, so a ``continue`` here left the gate
    printing "clean" over a model manifest nothing could read.
    """
    d = tmp_path / "models" / "broken"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text("id: broken\nfamily: {: bad")
    warns, errors = check_manifests.lint_model_fields(tmp_path)
    assert any("not valid YAML" in e for e in errors)
    assert any("broken" in e for e in errors)


def test_non_mapping_model_yaml_is_an_error(tmp_path: Path) -> None:
    d = tmp_path / "models" / "listy"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text("- id: listy\n")
    warns, errors = check_manifests.lint_model_fields(tmp_path)
    assert any("top-level is not a mapping" in e for e in errors)


def test_main_fails_the_gate_on_malformed_model(tmp_path: Path) -> None:
    """The gate must exit non-zero when a model manifest is malformed."""
    d = tmp_path / "models" / "broken"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text("id: broken\nfamily: {: bad")
    assert check_manifests.main(["managed-lint", "--root", str(tmp_path)]) == 1

def test_binary_manifest_unicode_decode_error(tmp_path: Path) -> None:
    """A model manifest with binary bytes b"\xff\xfe" must be reported as unreadable."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "broken-model").mkdir()
    (models_dir / "broken-model" / "manifest.yaml").write_bytes(b"\xff\xfe")
    
    import io
    import sys
    from contextlib import redirect_stderr
    _stderr = io.StringIO()
    with redirect_stderr(_stderr):
        exit_code = check_manifests.main(["managed-lint", "--root", str(tmp_path)])
    stderr = _stderr.getvalue()
    assert exit_code == 1, f"Expected exit code 1, got {exit_code}"
    assert "could not be read (UnicodeDecodeError)" in stderr, f"Expected error message in stderr, got: {stderr}"


def test_fifteen_model_warns_capped(capsys) -> None:
    """15 model manifests missing 'family' print count + first 10 warnings only."""
    from pathlib import Path
    import yaml
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    models_dir = tmp / "models"
    models_dir.mkdir()

    # Create 15 model manifests missing 'family' but with min_context/max_context
    for i in range(15):
        mid = f"model-{i:02d}"
        (m_dir := models_dir / mid).mkdir()
        (m_dir / "manifest.yaml").write_text(yaml.safe_dump({
            "id": mid,
            "name": mid,
            "type": "model",
            "version": "1.0.0",
            "min_context": 4096,
            "max_context": 131072,
        }))

    
    exit_code = check_manifests.main(["managed-lint", "--root", str(tmp)])
    assert exit_code == 0  # exits 0 when warnings are non-fatal

    output = capsys.readouterr().err
    assert "15 model capability field warnings" in output
    # Count warn: lines - should be exactly 10
    warn_lines = [l for l in output.splitlines() if l.startswith("  warn:")]
    assert len(warn_lines) == 10, f"Expected 10 warn lines, got {len(warn_lines)}"
    # Each warning text should appear exactly once
    warning_texts = [l.replace("  warn: ", "") for l in warn_lines]
    assert len(warning_texts) == len(set(warning_texts)), "Duplicate warning texts found"


def test_fifteen_model_warns_verbose(capsys) -> None:
    """Same 15 manifests with --verbose print all 15 warn lines exactly once."""
    from pathlib import Path
    import yaml
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    models_dir = tmp / "models"
    models_dir.mkdir()

    # Create 15 model manifests missing 'family' but with min_context/max_context
    for i in range(15):
        mid = f"model-{i:02d}"
        (m_dir := models_dir / mid).mkdir()
        (m_dir / "manifest.yaml").write_text(yaml.safe_dump({
            "id": mid,
            "name": mid,
            "type": "model",
            "version": "1.0.0",
            "min_context": 4096,
            "max_context": 131072,
        }))

    
    exit_code = check_manifests.main(["managed-lint", "--root", str(tmp), "--verbose"])
    assert exit_code == 0  # exits 0 when warnings are non-fatal

    output = capsys.readouterr().err
    # Count warn: lines - should be exactly 15
    warn_lines = [l for l in output.splitlines() if l.startswith("  warn:")]
    assert len(warn_lines) == 15, f"Expected 15 warn lines, got {len(warn_lines)}"
    # Each warning text should appear exactly once
    warning_texts = [l.replace("  warn: ", "") for l in warn_lines]
    assert len(warning_texts) == len(set(warning_texts)), "Duplicate warning texts found"
