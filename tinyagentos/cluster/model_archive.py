"""Archived model store and promotion engine.

When a model is downloaded and no current worker can run it
(PR #325 force=True "Archive anyway"), it lands in

    ~/taos/archive/models/<model_id>.json  + files under
    ~/taos/archive/models/<model_id>/

This module scans that directory on worker join and promotes
any model that the new worker can now run — moving it into the
active models tree via :func:`~tinyagentos.installers.model_paths.models_root`.

Consumed by:
- :meth:`~tinyagentos.cluster.manager.ClusterManager.register_worker`
  (automatic promotion on worker join)
- ``GET /api/cluster/promote-archived`` (manual trigger)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _archive_root() -> Path:
    """Root directory for archived models. Override with TAOS_ARCHIVE_ROOT env."""
    override = os.environ.get("TAOS_ARCHIVE_ROOT")
    return Path(override) if override else Path.home() / "taos" / "archive" / "models"


def _active_models_root() -> Path:
    """The active models tree — same as all backend installers write into."""
    from tinyagentos.installers.model_paths import models_root
    return models_root()


def list_archived_models(archive_dir: Path | None = None) -> list[dict]:
    """Scan the archive directory and return every archived model's manifest.

    Each manifest is a JSON file ``<model_id>.json`` in the archive root.
    Returns a list of dicts with keys: ``model_id``, ``files``,
    ``requirements``, ``archived_at``, ``backend``, ``manifest_path``.
    """
    root = archive_dir or _archive_root()
    if not root.is_dir():
        return []
    models: list[dict] = []
    for manifest_path in sorted(root.glob("*.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("model_archive: skipping unreadable manifest %s", manifest_path)
            continue
        data["manifest_path"] = str(manifest_path)
        models.append(data)
    return models


def _worker_can_run(worker_hardware: dict, requirements: dict) -> bool:
    """True if the worker's hardware meets the model's minimum requirements.

    Checks: VRAM, RAM, GPU type (cuda/rocm/vulkan/apple/npu), architecture.

    Requirements shape (all keys optional; missing means no constraint)::

        {
            "min_vram_mb": 8192,
            "min_ram_mb": 4096,
            "gpu_type": "nvidia",       # or "amd" / "apple" / ""
            "gpu_accel": "cuda",        # or "rocm" / "vulkan" / "mlx" / ""
            "npu_type": "rknpu",        # or ""
            "arch": "x86_64"            # or "aarch64" / ""
        }
    """
    if not requirements:
        return True  # No requirements = runs anywhere

    hw_gpu = worker_hardware.get("gpu") or {}
    hw_npu = worker_hardware.get("npu") or {}
    hw_cpu_raw = worker_hardware.get("cpu") or {}
    hw_cpu: dict = hw_cpu_raw if isinstance(hw_cpu_raw, dict) else {}
    hw_ram = worker_hardware.get("ram_mb", 0)

    # VRAM check
    min_vram = requirements.get("min_vram_mb")
    if min_vram:
        worker_vram = hw_gpu.get("vram_mb", 0) or 0
        # Apple Silicon unified memory counts
        if hw_gpu.get("type") == "apple":
            worker_vram = max(worker_vram, hw_ram)
        if worker_vram < min_vram:
            return False

    # RAM check
    min_ram = requirements.get("min_ram_mb")
    if min_ram and hw_ram < min_ram:
        return False

    # GPU type / accelerator check
    req_gpu_type = requirements.get("gpu_type")
    req_gpu_accel = requirements.get("gpu_accel")
    worker_gpu_type = hw_gpu.get("type", "none") or "none"

    if req_gpu_type:
        if req_gpu_type == "nvidia" and worker_gpu_type != "nvidia":
            return False
        if req_gpu_type == "amd" and worker_gpu_type != "amd":
            return False
        if req_gpu_type == "apple" and worker_gpu_type != "apple":
            return False

    if req_gpu_accel:
        if req_gpu_accel == "cuda" and not hw_gpu.get("cuda"):
            return False
        if req_gpu_accel == "rocm" and not hw_gpu.get("rocm"):
            return False
        if req_gpu_accel == "vulkan" and not hw_gpu.get("vulkan"):
            return False
        if req_gpu_accel == "mlx" and worker_gpu_type != "apple":
            return False

    # NPU check
    req_npu = requirements.get("npu_type")
    if req_npu:
        worker_npu_type = hw_npu.get("type", "none") or "none"
        if worker_npu_type != req_npu and worker_npu_type not in (req_npu,):
            return False

    # Architecture check
    req_arch = requirements.get("arch")
    if req_arch:
        worker_arch = hw_cpu.get("arch", "")
        if not worker_arch or worker_arch != req_arch:
            return False

    return True


def find_promotable(
    worker_hardware: dict,
    worker_name: str,
    archive_dir: Path | None = None,
) -> list[dict]:
    """Return archived models that *this* worker can now run.

    Each entry is the model manifest dict with an extra ``worker_name`` key.
    Does NOT move files — call :func:`promote_model` for each.
    """
    promotable: list[dict] = []
    for model in list_archived_models(archive_dir):
        reqs = model.get("requirements") or {}
        if _worker_can_run(worker_hardware, reqs):
            model["worker_name"] = worker_name
            promotable.append(model)
    return promotable


def promote_model(model: dict) -> bool:
    """Move one archived model into the active models tree.

    Args:
        model: A manifest dict from :func:`list_archived_models`.

    Returns True on success, False if the move fails (model stays archived).
    """
    model_id = model.get("model_id", "")
    manifest_path_str = model.get("manifest_path", "")
    if not model_id or not manifest_path_str:
        logger.warning("model_archive: cannot promote — missing model_id or manifest_path")
        return False

    manifest_path = Path(manifest_path_str)
    archive_root_path = manifest_path.parent
    model_files_dir = archive_root_path / model_id

    # Resolve target directory in the active models tree.
    # Use the backend from the manifest if present; otherwise guess.
    backend = model.get("backend", "uncategorised")
    # Build a target path: ~/models/<backend>/<family>/<model_id>/
    # The family is derived from the model_id's first token or from the manifest.
    family = model.get("family", model_id.split("-", 1)[0] if "-" in model_id else model_id)
    target_dir = _active_models_root() / backend / family / model_id

    if not model_files_dir.is_dir():
        logger.warning(
            "model_archive: model files directory %s not found — "
            "promotion requires both the manifest and the files dir",
            model_files_dir,
        )
        return False

    try:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        # If target already exists, don't overwrite — but still remove
        # the archive manifest so we don't keep trying.
        if target_dir.exists():
            logger.info(
                "model_archive: target %s already exists; removing archive entry, skipping move",
                target_dir,
            )
            manifest_path.unlink(missing_ok=True)
            # Clean up empty model files dir if possible
            if model_files_dir.is_dir():
                try:
                    model_files_dir.rmdir()
                except OSError:
                    pass
            return True

        shutil.move(str(model_files_dir), str(target_dir))
        # Remove the archive manifest after successful move
        manifest_path.unlink(missing_ok=True)
        logger.info("model_archive: promoted %s -> %s", model_id, target_dir)
        return True
    except (OSError, shutil.Error) as exc:
        logger.error("model_archive: failed to promote %s: %s", model_id, exc)
        return False


async def promote_compatible_models(
    worker_hardware: dict,
    worker_name: str,
    archive_dir: Path | None = None,
    notifications=None,
) -> list[str]:
    """Scan archive, promote every model compatible with this worker.

    Called by :meth:`ClusterManager.register_worker` when a new worker joins.
    Sends a notification for each promoted model.

    Returns the list of promoted model IDs.
    """
    promotable = find_promotable(worker_hardware, worker_name, archive_dir)
    promoted: list[str] = []
    for model in promotable:
        model_id = model.get("model_id", "?")
        if promote_model(model):
            promoted.append(model_id)
            if notifications:
                try:
                    await notifications.emit_event(
                        "model.promoted",
                        f"Archived model '{model_id}' promoted",
                        f"Worker '{worker_name}' can now run '{model_id}'. "
                        f"Moved from archive to active models.",
                        level="info",
                    )
                except Exception:
                    logger.exception(
                        "model_archive: notification emit failed for promoted model '%s'",
                        model_id,
                    )
    if promoted:
        logger.info(
            "model_archive: worker '%s' promoted %d model(s): %s",
            worker_name, len(promoted), ", ".join(promoted),
        )
    return promoted
