"""Tests for the model archive promotion engine."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tinyagentos.cluster.model_archive import (
    _archive_root,
    _active_models_root,
    _worker_can_run,
    find_promotable,
    list_archived_models,
    promote_model,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_worker_hw(
    ram_mb: int = 8192,
    gpu_type: str = "nvidia",
    gpu_cuda: bool = True,
    vram_mb: int = 8192,
    arch: str = "x86_64",
) -> dict:
    return {
        "ram_mb": ram_mb,
        "gpu": {
            "type": gpu_type,
            "cuda": gpu_cuda,
            "vram_mb": vram_mb,
        },
        "cpu": {"arch": arch},
        "npu": {"type": "none"},
    }


def _write_archive_manifest(
    archive_dir: Path,
    model_id: str,
    requirements: dict | None = None,
    files: list[str] | None = None,
    backend: str = "llama-cpp",
    family: str = "qwen3",
) -> dict:
    """Write a manifest AND create a dummy model-files dir so
    :func:`promote_model` has something to move.
    """
    manifest = {
        "model_id": model_id,
        "backend": backend,
        "family": family,
        "files": files or [f"{model_id}-Q4_K_M.gguf"],
        "requirements": requirements or {},
        "archived_at": 1700000000.0,
    }
    manifest_path = archive_dir / f"{model_id}.json"
    manifest_path.write_text(json.dumps(manifest))
    # Create the accompanying model files directory
    files_dir = archive_dir / model_id
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / f"{model_id}-Q4_K_M.gguf").write_text("fake-model-data")
    return manifest


# ---------------------------------------------------------------------------
# _worker_can_run
# ---------------------------------------------------------------------------


class TestWorkerCanRun:
    def test_empty_requirements(self):
        assert _worker_can_run(_make_worker_hw(), {}) is True

    def test_vram_met(self):
        assert _worker_can_run(
            _make_worker_hw(vram_mb=8192),
            {"min_vram_mb": 4096},
        ) is True

    def test_vram_not_met(self):
        assert _worker_can_run(
            _make_worker_hw(vram_mb=4096),
            {"min_vram_mb": 8192},
        ) is False

    def test_ram_met(self):
        assert _worker_can_run(
            _make_worker_hw(ram_mb=16384),
            {"min_ram_mb": 8192},
        ) is True

    def test_ram_not_met(self):
        assert _worker_can_run(
            _make_worker_hw(ram_mb=4096),
            {"min_ram_mb": 8192},
        ) is False

    def test_gpu_type_nvidia_match(self):
        assert _worker_can_run(
            _make_worker_hw(gpu_type="nvidia"),
            {"gpu_type": "nvidia"},
        ) is True

    def test_gpu_type_nvidia_mismatch(self):
        assert _worker_can_run(
            _make_worker_hw(gpu_type="amd"),
            {"gpu_type": "nvidia"},
        ) is False

    def test_gpu_accel_cuda_match(self):
        assert _worker_can_run(
            _make_worker_hw(gpu_type="nvidia", gpu_cuda=True),
            {"gpu_accel": "cuda"},
        ) is True

    def test_gpu_accel_cuda_mismatch(self):
        assert _worker_can_run(
            _make_worker_hw(gpu_type="nvidia", gpu_cuda=False),
            {"gpu_accel": "cuda"},
        ) is False

    def test_arch_match(self):
        assert _worker_can_run(
            _make_worker_hw(arch="x86_64"),
            {"arch": "x86_64"},
        ) is True

    def test_arch_mismatch(self):
        assert _worker_can_run(
            _make_worker_hw(arch="aarch64"),
            {"arch": "x86_64"},
        ) is False

    def test_apple_silicon_unified_memory(self):
        worker_hw = {
            "ram_mb": 16384,
            "gpu": {"type": "apple", "vulkan": True, "vram_mb": 16384},
            "cpu": {"arch": "aarch64"},
            "npu": {"type": "none"},
        }
        assert _worker_can_run(worker_hw, {"min_vram_mb": 8192}) is True

    def test_all_requirements_met(self):
        worker = _make_worker_hw(vram_mb=12288, ram_mb=16384, gpu_type="nvidia", gpu_cuda=True)
        reqs = {
            "min_vram_mb": 8192,
            "min_ram_mb": 8192,
            "gpu_type": "nvidia",
            "gpu_accel": "cuda",
            "arch": "x86_64",
        }
        assert _worker_can_run(worker, reqs) is True

    def test_one_requirement_fails_whole_thing_fails(self):
        worker = _make_worker_hw(vram_mb=4096, ram_mb=16384, gpu_type="nvidia", gpu_cuda=True)
        reqs = {
            "min_vram_mb": 8192,
            "min_ram_mb": 8192,
            "gpu_type": "nvidia",
            "gpu_accel": "cuda",
        }
        assert _worker_can_run(worker, reqs) is False


# ---------------------------------------------------------------------------
# list_archived_models
# ---------------------------------------------------------------------------


class TestListArchivedModels:
    def test_empty_dir(self, tmp_path: Path):
        assert list_archived_models(tmp_path) == []

    def test_nonexistent_dir(self, tmp_path: Path):
        assert list_archived_models(tmp_path / "nonexistent") == []

    def test_single_manifest(self, tmp_path: Path):
        _write_archive_manifest(tmp_path, "qwen3.5-4b")
        result = list_archived_models(tmp_path)
        assert len(result) == 1
        assert result[0]["model_id"] == "qwen3.5-4b"
        assert "manifest_path" in result[0]

    def test_skips_corrupt_manifest(self, tmp_path: Path):
        (tmp_path / "bad.json").write_text("not json")
        _write_archive_manifest(tmp_path, "gemma-4-e2b")
        result = list_archived_models(tmp_path)
        assert len(result) == 1
        assert result[0]["model_id"] == "gemma-4-e2b"

    def test_multiple_manifests_sorted(self, tmp_path: Path):
        _write_archive_manifest(tmp_path, "llama3-8b")
        _write_archive_manifest(tmp_path, "gemma-4-e2b")
        _write_archive_manifest(tmp_path, "qwen3.5-4b")
        result = list_archived_models(tmp_path)
        ids = [m["model_id"] for m in result]
        # Sorted alphabetically by filename
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# find_promotable
# ---------------------------------------------------------------------------


class TestFindPromotable:
    def test_no_archive(self, tmp_path: Path):
        worker = _make_worker_hw()
        assert find_promotable(worker, "test-worker", tmp_path) == []

    def test_no_compatible(self, tmp_path: Path):
        # Archive a model requiring 16GB VRAM; worker has 8GB
        _write_archive_manifest(
            tmp_path, "big-model",
            requirements={"min_vram_mb": 16384},
        )
        worker = _make_worker_hw(vram_mb=8192)
        assert find_promotable(worker, "test-worker", tmp_path) == []

    def test_compatible_found(self, tmp_path: Path):
        _write_archive_manifest(
            tmp_path, "qwen3.5-4b",
            requirements={"min_vram_mb": 4096, "gpu_accel": "cuda"},
        )
        worker = _make_worker_hw(vram_mb=8192)
        result = find_promotable(worker, "test-worker", tmp_path)
        assert len(result) == 1
        assert result[0]["model_id"] == "qwen3.5-4b"
        assert result[0]["worker_name"] == "test-worker"

    def test_mixed_compatible_and_incompatible(self, tmp_path: Path):
        _write_archive_manifest(
            tmp_path, "qwen3.5-4b",
            requirements={"min_vram_mb": 4096},
        )
        _write_archive_manifest(
            tmp_path, "big-model",
            requirements={"min_vram_mb": 32768},
        )
        worker = _make_worker_hw(vram_mb=8192)
        result = find_promotable(worker, "test-worker", tmp_path)
        assert len(result) == 1
        assert result[0]["model_id"] == "qwen3.5-4b"

    def test_no_requirements_always_promotable(self, tmp_path: Path):
        _write_archive_manifest(tmp_path, "no-reqs-model", requirements={})
        worker = _make_worker_hw(vram_mb=128)  # very weak
        result = find_promotable(worker, "test-worker", tmp_path)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# promote_model
# ---------------------------------------------------------------------------


class TestPromoteModel:
    def test_promote_moves_files_and_removes_manifest(self, tmp_path: Path, monkeypatch):
        archive_dir = tmp_path / "archive"
        active_dir = tmp_path / "active"
        archive_dir.mkdir(parents=True, exist_ok=True)
        active_dir.mkdir(parents=True, exist_ok=True)

        # Override paths for test isolation
        monkeypatch.setattr(
            "tinyagentos.cluster.model_archive._archive_root",
            lambda: archive_dir,
        )
        monkeypatch.setattr(
            "tinyagentos.cluster.model_archive._active_models_root",
            lambda: active_dir,
        )

        _write_archive_manifest(
            archive_dir, "qwen3.5-4b",
            requirements={"min_vram_mb": 4096},
            backend="llama-cpp",
            family="qwen3.5",
        )
        models = list_archived_models(archive_dir)
        assert len(models) == 1

        ok = promote_model(models[0])
        assert ok is True

        # Manifest removed
        assert not (archive_dir / "qwen3.5-4b.json").exists()
        # Files dir moved
        assert not (archive_dir / "qwen3.5-4b").is_dir()
        target = active_dir / "llama-cpp" / "qwen3.5" / "qwen3.5-4b"
        assert target.is_dir()
        assert (target / "qwen3.5-4b-Q4_K_M.gguf").read_text() == "fake-model-data"

    def test_promote_skips_when_target_exists(self, tmp_path: Path, monkeypatch):
        archive_dir = tmp_path / "archive"
        active_dir = tmp_path / "active"
        archive_dir.mkdir(parents=True, exist_ok=True)
        active_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            "tinyagentos.cluster.model_archive._archive_root",
            lambda: archive_dir,
        )
        monkeypatch.setattr(
            "tinyagentos.cluster.model_archive._active_models_root",
            lambda: active_dir,
        )

        _write_archive_manifest(
            archive_dir, "qwen3.5-4b",
            backend="llama-cpp",
            family="qwen3.5",
        )
        # Pre-create target dir
        target_dir = active_dir / "llama-cpp" / "qwen3.5" / "qwen3.5-4b"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "existing.gguf").write_text("preexisting")

        models = list_archived_models(archive_dir)
        ok = promote_model(models[0])
        assert ok is True
        # Manifest removed, but existing target untouched
        assert not (archive_dir / "qwen3.5-4b.json").exists()
        assert (target_dir / "existing.gguf").read_text() == "preexisting"

    def test_promote_fails_without_files_dir(self, tmp_path: Path, monkeypatch):
        archive_dir = tmp_path / "archive"
        active_dir = tmp_path / "active"
        archive_dir.mkdir(parents=True, exist_ok=True)
        active_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(
            "tinyagentos.cluster.model_archive._archive_root",
            lambda: archive_dir,
        )
        monkeypatch.setattr(
            "tinyagentos.cluster.model_archive._active_models_root",
            lambda: active_dir,
        )

        # Create manifest without files dir
        manifest = {
            "model_id": "orphan-model",
            "backend": "llama-cpp",
            "family": "orphan",
            "files": ["orphan.gguf"],
            "requirements": {},
            "archived_at": 1700000000.0,
        }
        (archive_dir / "orphan-model.json").write_text(json.dumps(manifest))
        # No files dir — don't create it

        models = list_archived_models(archive_dir)
        assert len(models) == 1
        ok = promote_model(models[0])
        assert ok is False
        # Manifest remains
        assert (archive_dir / "orphan-model.json").exists()


# ---------------------------------------------------------------------------
# Archive root env var override
# ---------------------------------------------------------------------------


class TestArchiveRootOverride:
    def test_env_var_override(self, tmp_path: Path, monkeypatch):
        custom = tmp_path / "custom-archive"
        monkeypatch.setenv("TAOS_ARCHIVE_ROOT", str(custom))
        assert _archive_root() == custom

    def test_default_is_home_taos(self, monkeypatch):
        monkeypatch.delenv("TAOS_ARCHIVE_ROOT", raising=False)
        root = _archive_root()
        assert root.name == "models"
        assert "taos" in str(root)
        assert "archive" in str(root)
