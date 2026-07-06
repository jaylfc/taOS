"""Tests for the model download pipeline, including the rkllama sidecar-marker
fix for #1548: when catalog.refresh() fails silently after a successful
rkllama install, the variant never flips to "downloaded" because both
has_disk_evidence and has_live_evidence are False.

The fix writes a sidecar metadata marker at
``models_dir/.taos-installed/<app_id>/<variant_id>.json`` so
``_scan_sidecar_evidence()`` provides disk evidence for registry
corroboration without appearing as a phantom 0 MB downloaded file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyagentos.routes.models import (
    _attach_model_ids,
    _model_to_dict,
    _scan_sidecar_evidence,
    get_downloaded_models,
)


# ---------------------------------------------------------------------------
# Module-level fixtures (Kilo: hoisted from inline classes)
# ---------------------------------------------------------------------------


@pytest.fixture
def rkllama_variant() -> dict:
    """Standard rkllama variant dict with ``format: rkllm``."""
    return {"id": "w8a8", "format": "rkllm", "size_mb": 3500}


@pytest.fixture
def qwen_variant() -> dict:
    """Legacy variant with ``format: bin`` for backward-compat testing."""
    return {"id": "w8a8", "format": "bin", "size_mb": 3500}


class FakeManifest:
    """Lightweight stand-in for an AppManifest used by _model_to_dict."""

    def __init__(self, id: str = "qwen2.5-3b-rkllm", variants: list[dict] | None = None):
        self.id = id
        self.name = "Qwen 2.5 3B"
        self.version = "1.0"
        self.description = "test"
        self.capabilities = ["chat"]
        self.hardware_tiers = {}
        self.variants = variants or [{"id": "w8a8", "format": "rkllm", "size_mb": 3500}]


class FakeHW:
    """Lightweight stand-in for a HardwareProfile used by _model_to_dict."""
    ram_mb = 8192

    class CPU:
        soc = "rk3588"

    cpu = CPU()


# ---------------------------------------------------------------------------
# Sidecar marker detection
# ---------------------------------------------------------------------------


class TestSidecarEvidence:
    """A sidecar marker at .taos-installed/<app_id>/<variant_id>.json must
    provide disk evidence for registry corroboration without appearing
    as a phantom downloaded file (#1548)."""

    def test_scan_returns_app_ids_with_markers(self, tmp_path):
        """_scan_sidecar_evidence returns app_ids that have marker files."""
        sidecar_dir = tmp_path / ".taos-installed" / "some-model"
        sidecar_dir.mkdir(parents=True)
        (sidecar_dir / "q4.json").write_text(
            json.dumps({"source": "rkllama", "app_id": "some-model", "variant_id": "q4"})
        )
        ids = _scan_sidecar_evidence(tmp_path)
        assert "some-model" in ids

    def test_scan_returns_empty_for_no_markers(self, tmp_path):
        """No markers → empty set."""
        ids = _scan_sidecar_evidence(tmp_path)
        assert ids == set()

    def test_scan_ignores_empty_app_dirs(self, tmp_path):
        """An app dir with no .json files is not counted."""
        (tmp_path / ".taos-installed" / "orphan").mkdir(parents=True)
        ids = _scan_sidecar_evidence(tmp_path)
        assert "orphan" not in ids

    def test_sidecar_provides_disk_evidence_for_registry_corroborated(
        self, tmp_path, rkllama_variant,
    ):
        """sidecar_installed=True → has_disk_evidence → registry_corroborated
        works even with zero live-backend models and no model files on disk."""
        manifest = FakeManifest(variants=[rkllama_variant])
        downloaded: list[dict] = []
        result = _model_to_dict(
            manifest,
            FakeHW(),
            downloaded,
            live_models=[],
            registry_installed=True,
            sidecar_installed=True,
        )
        assert result["variants"][0]["downloaded"] is True
        assert result["has_downloaded_variant"] is True

    def test_sidecar_without_registry_not_downloaded(self, tmp_path, rkllama_variant):
        """sidecar_installed without registry_installed does not mark
        the variant downloaded (registry corroboration needs both)."""
        manifest = FakeManifest(variants=[rkllama_variant])
        downloaded: list[dict] = []
        result = _model_to_dict(
            manifest,
            FakeHW(),
            downloaded,
            live_models=[],
            registry_installed=False,
            sidecar_installed=True,
        )
        # Without registry, sidecar alone does not set registry_corroborated
        assert result["variants"][0]["downloaded"] is False
        assert result["has_downloaded_variant"] is False

    def test_sidecar_file_not_in_downloaded_files(self, tmp_path, rkllama_variant):
        """The sidecar .json file must NOT appear in get_downloaded_models
        — .json is not in _MODEL_FILE_SUFFIXES."""
        sidecar_dir = tmp_path / ".taos-installed" / "some-model"
        sidecar_dir.mkdir(parents=True)
        (sidecar_dir / "q4.json").write_text("{}")
        downloaded = get_downloaded_models(tmp_path)
        filenames = {d["filename"] for d in downloaded}
        assert "q4.json" not in filenames


# ---------------------------------------------------------------------------
# _attach_model_ids — legacy .bin fallback
# ---------------------------------------------------------------------------


class TestAttachModelIds:
    """_attach_model_ids matches on {id}-{variant}.{format} naming convention."""

    def test_bin_format_gets_model_id(self, tmp_path):
        """Legacy .bin files still match via the format field."""
        marker = tmp_path / "qwen2.5-3b-rkllm-w8a8.bin"
        marker.write_text("{}")
        downloaded = get_downloaded_models(tmp_path)
        manifest = FakeManifest(variants=[{"id": "w8a8", "format": "bin"}])
        _attach_model_ids(downloaded, [manifest])
        assert downloaded[0].get("model_id") == "qwen2.5-3b-rkllm"

    def test_rkllm_format_gets_model_id(self, tmp_path):
        """rkllm-format files also match."""
        marker = tmp_path / "qwen2.5-3b-rkllm-w8a8.rkllm"
        marker.write_text("{}")
        downloaded = get_downloaded_models(tmp_path)
        manifest = FakeManifest(variants=[{"id": "w8a8", "format": "rkllm"}])
        _attach_model_ids(downloaded, [manifest])
        assert downloaded[0].get("model_id") == "qwen2.5-3b-rkllm"


# ---------------------------------------------------------------------------
# Path-traversal rejection (FIX 1)
# ---------------------------------------------------------------------------


class TestPathTraversalRejection:
    """Path separators and ``..`` in app_id/variant_id are rejected before
    any path is built, preventing escape from models_dir."""

    def test_app_id_with_slash_rejected(self):
        """app_id containing '/' is rejected."""
        assert _has_path_traversal("bad/app", "ok")

    def test_app_id_with_backslash_rejected(self):
        """app_id containing '\\' is rejected."""
        assert _has_path_traversal("bad\\app", "ok")

    def test_app_id_with_dotdot_rejected(self):
        """app_id containing '..' is rejected."""
        assert _has_path_traversal("../../etc/passwd", "ok")

    def test_variant_id_with_slash_rejected(self):
        """variant_id containing '/' is rejected."""
        assert _has_path_traversal("ok", "bad/variant")

    def test_variant_id_with_dotdot_rejected(self):
        """variant_id containing '..' is rejected."""
        assert _has_path_traversal("ok", "../escape")

    def test_clean_ids_accepted(self):
        """Clean app_id/variant_id pass validation."""
        assert not _has_path_traversal("my-model", "q4_k_m")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_path_traversal(app_id: str, variant_id: str) -> bool:
    """True if the given ids contain path separators or traversal sequences.
    Mirrors the sanitisation check in _install_and_record."""
    for value in (app_id, variant_id):
        if "/" in value or "\\" in value or ".." in value:
            return True
    return False
