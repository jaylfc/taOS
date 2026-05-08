"""Routes for taOSmd memory setup wizard integration.

Provides:
  GET  /api/taosmd/tiers            — static tier → model mapping
  GET  /api/taosmd/default          — user's saved memory default (404 if none)
  PUT  /api/taosmd/default          — save/update the user's default
  POST /api/taosmd/setup            — kick off background install of runtime + model
  GET  /api/taosmd/setup/{task_id}  — poll progress of a setup task
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Single source of truth — tier → model mapping
# Imported by tests and surfaced via GET /api/taosmd/tiers.
# ---------------------------------------------------------------------------

MEMORY_TIERS: dict[str, dict] = {
    "lite": {
        "label": "Lite",
        "description": "Smaller embedder, works on any device",
        "models": ["nomic-embed-text-v1.5"],
        "min_ram_mb": 1024,
        "needs_accel": False,
    },
    "standard": {
        "label": "Standard",
        "description": "Recommended balance for most users",
        "models": ["bge-m3"],
        "min_ram_mb": 4096,
        "needs_accel": False,
    },
    "heavy": {
        "label": "Heavy",
        "description": "Best quality with reranker, needs real acceleration",
        "models": ["bge-m3", "qwen3-reranker-0.6b"],
        "min_ram_mb": 8192,
        "needs_accel": True,
    },
}

# ---------------------------------------------------------------------------
# In-memory task store (keyed by task_id, lives in app.state.taosmd_setup_tasks)
# ---------------------------------------------------------------------------

TaskState = Literal["pending", "downloading", "installing", "done", "failed"]


def _tasks(request: Request) -> dict:
    """Return (creating if needed) the setup task dict on app.state."""
    if not hasattr(request.app.state, "taosmd_setup_tasks"):
        request.app.state.taosmd_setup_tasks = {}
    return request.app.state.taosmd_setup_tasks


# ---------------------------------------------------------------------------
# Default storage helpers (JSON file at data_dir/taosmd_default.json)
# ---------------------------------------------------------------------------

def _default_path(request: Request) -> Path:
    return Path(request.app.state.data_dir) / "taosmd_default.json"


def _read_default(request: Request) -> dict | None:
    import json
    p = _default_path(request)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def _write_default(request: Request, data: dict) -> None:
    import json
    p = _default_path(request)
    p.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/taosmd/tiers")
async def get_tiers():
    """Return the static tier → model mapping for the frontend."""
    return MEMORY_TIERS


@router.get("/api/taosmd/default")
async def get_default(request: Request):
    """Return the user's saved memory default, or 404 if none set."""
    data = _read_default(request)
    if data is None:
        return JSONResponse({"error": "No memory default set"}, status_code=404)
    return data


class DefaultBody(BaseModel):
    device_id: str
    tier_id: str


@router.put("/api/taosmd/default")
async def put_default(request: Request, body: DefaultBody):
    """Save the user's preferred memory device and tier."""
    tier = MEMORY_TIERS.get(body.tier_id)
    tier_label = tier["label"] if tier else body.tier_id
    payload = {
        "device_id": body.device_id,
        "tier_id": body.tier_id,
        "tier_name": tier_label,
    }
    _write_default(request, payload)
    return payload


class SetupBody(BaseModel):
    device_id: str
    tier: Literal["lite", "standard", "heavy"]


@router.post("/api/taosmd/setup")
async def post_setup(request: Request, body: SetupBody):
    """Kick off a background install of the runtime + models for the chosen tier.

    Returns immediately with a task_id for progress polling.
    """
    tier_cfg = MEMORY_TIERS.get(body.tier)
    if tier_cfg is None:
        return JSONResponse({"error": f"Unknown tier '{body.tier}'"}, status_code=400)

    task_id = str(uuid.uuid4())
    tasks = _tasks(request)
    tasks[task_id] = {
        "state": "pending",
        "progress_pct": 0,
        "message": "Queued…",
        "error": None,
    }

    # Capture state on the request thread so the background task isn't
    # tied to the request lifecycle. Resolver needs registry +
    # hardware_profile to pick a backend that's actually available on
    # this controller — johny saw "Ollama not reachable" because the
    # old code path always tried OllamaInstaller regardless of whether
    # Ollama was the appropriate or even installed runtime.
    registry = getattr(request.app.state, "registry", None)
    hardware_profile = getattr(request.app.state, "hardware_profile", None)
    config = getattr(request.app.state, "config", None)
    backends_snapshot = list(getattr(config, "backends", []) or []) if config else []

    asyncio.create_task(
        _run_setup(
            tasks,
            task_id,
            body.device_id,
            body.tier,
            tier_cfg,
            registry=registry,
            hardware_profile=hardware_profile,
            backends=backends_snapshot,
        )
    )

    return {"task_id": task_id}


@router.get("/api/taosmd/setup/{task_id}")
async def get_setup_status(request: Request, task_id: str):
    """Poll the progress of a setup task."""
    tasks = _tasks(request)
    task = tasks.get(task_id)
    if task is None:
        return JSONResponse({"error": f"No setup task '{task_id}'"}, status_code=404)
    return task


# ---------------------------------------------------------------------------
# Background install logic
# ---------------------------------------------------------------------------

async def _run_setup(
    tasks: dict,
    task_id: str,
    device_id: str,
    tier: str,
    tier_cfg: dict,
    *,
    registry=None,
    hardware_profile=None,
    backends: list | None = None,
) -> None:
    """Install each model listed in the tier using the catalog resolver.

    For every model id, looks up its manifest, lets the resolver pick a
    compatible backend based on the controller's hardware + the
    backends in config, then dispatches to the matching installer
    (Ollama / download / hf-multi / rkllama). This replaces the
    previous hardcoded OllamaInstaller path which gave johny the
    "Ollama not reachable" error when he had llama.cpp installed
    instead.

    Progress: pending → downloading (per model) → installing → done / failed.
    """
    models: list[str] = tier_cfg.get("models", [])
    total = len(models)

    def _update(state: str, pct: int, msg: str, error: str | None = None) -> None:
        tasks[task_id] = {
            "state": state,
            "progress_pct": pct,
            "message": msg,
            "error": error,
        }

    _update("pending", 0, "Starting…")

    if registry is None or hardware_profile is None:
        _update(
            "failed",
            0,
            "Setup unavailable.",
            "registry or hardware profile missing — controller startup not complete",
        )
        return

    try:
        from dataclasses import asdict
        from tinyagentos.catalog.resolver import (
            DeviceCapability,
            ResolveErr,
            resolve,
        )
        from tinyagentos.cluster.capabilities import hardware_to_targets
        from tinyagentos.installers.base import get_installer
        from tinyagentos.routes.store_install import _BACKEND_TO_METHOD

        # Build a DeviceCapability — inline because get_device_capability
        # in routes/store_install.py needs a Request, and this background
        # task doesn't have one. HardwareProfile is a flat dataclass;
        # asdict yields {ram_mb, cpu, gpu, npu, disk, os}.
        # Wrap separately so a hardware-detection failure surfaces with
        # a clearer error than the generic outer-except catch — old
        # agent versions and partial profiles can land int() conversion
        # errors here that would otherwise show as "ValueError: invalid
        # literal" in the UI.
        try:
            hw_dict = asdict(hardware_profile)
            ram_mb = int(hw_dict.get("ram_mb", 0) or 0)
            vram_mb = int((hw_dict.get("gpu") or {}).get("vram_mb", 0) or 0)
            free_gb = int((hw_dict.get("disk") or {}).get("free_gb", 0) or 0)
            installed_backends = tuple(
                b.get("type", "")
                for b in (backends or [])
                if b.get("enabled", True) and b.get("type")
            )
            device = DeviceCapability(
                device_id="local",
                targets=tuple(hardware_to_targets(hw_dict)),
                total_ram_mb=ram_mb,
                total_vram_mb=vram_mb,
                free_disk_mb=max(0, free_gb * 1024),
                installed_backends=installed_backends,
            )
        except (TypeError, ValueError, AttributeError) as hw_exc:
            _update(
                "failed",
                0,
                "Hardware detection failed.",
                f"could not derive device capability from hardware profile: {hw_exc}",
            )
            return

        for idx, manifest_id in enumerate(models):
            base_pct = int(idx / total * 90)
            _update(
                "downloading",
                base_pct,
                f"Installing {manifest_id} ({idx + 1}/{total})…",
            )

            manifest = registry.get(manifest_id) if hasattr(registry, "get") else None
            if manifest is None:
                _update(
                    "failed",
                    base_pct,
                    f"Failed: {manifest_id}",
                    f"manifest {manifest_id!r} not found in catalog",
                )
                return

            manifest_dict = {
                "id": manifest.id,
                "type": manifest.type,
                "variants": manifest.variants,
                "context_window": getattr(manifest, "context_window", 0),
            }
            result = resolve(manifest_dict, "auto", device)
            if isinstance(result, ResolveErr):
                _update(
                    "failed",
                    base_pct,
                    f"Failed: {manifest_id}",
                    f"no compatible backend: {result.reason}. "
                    f"Suggestions: {', '.join(result.suggestions)}"
                    if result.suggestions else
                    f"no compatible backend: {result.reason}",
                )
                return

            chosen_variant = next(
                (v for v in manifest.variants
                 if isinstance(v, dict) and v.get("id") == result.variant_id),
                None,
            )
            if chosen_variant is None:
                _update(
                    "failed",
                    base_pct,
                    f"Failed: {manifest_id}",
                    f"variant {result.variant_id!r} not in manifest",
                )
                return

            install_method = _BACKEND_TO_METHOD.get(result.backend_id)
            if install_method is None:
                _update(
                    "failed",
                    base_pct,
                    f"Failed: {manifest_id}",
                    f"backend {result.backend_id!r} has no installer mapping. "
                    "Add to _BACKEND_TO_METHOD in store_install.py.",
                )
                return

            installer = get_installer(install_method)
            install_result = await installer.install(
                app_id=manifest_id,
                install_config={"backend": result.backend_id},
                variant=chosen_variant,
            )
            if not install_result.get("success"):
                err = install_result.get("error", "unknown error")
                _update(
                    "failed",
                    base_pct,
                    f"Failed: {manifest_id}",
                    f"{err} (backend: {result.backend_id})",
                )
                return

            try:
                if hasattr(registry, "mark_installed"):
                    registry.mark_installed(manifest_id, getattr(manifest, "version", ""))
            except Exception:  # noqa: BLE001 — registry write is best-effort
                logger.exception("taosmd setup: mark_installed for %s failed", manifest_id)

        _update("installing", 95, "Finalising…")
        # Brief pause to let any daemon-side work settle.
        await asyncio.sleep(1)
        _update("done", 100, "Memory layer ready.")

    except Exception as exc:  # noqa: BLE001
        logger.exception("taosmd setup task %s failed", task_id)
        _update("failed", 0, "Setup failed.", str(exc))
