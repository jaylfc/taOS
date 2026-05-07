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

    # Run the install in the background without blocking the response.
    asyncio.create_task(
        _run_setup(tasks, task_id, body.device_id, body.tier, tier_cfg)
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
) -> None:
    """Pull each model listed in the tier via Ollama (best-effort).

    Progress is reported coarsely: pending → downloading (per model) →
    installing → done / failed.
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

    try:
        from tinyagentos.installers.ollama_installer import OllamaInstaller

        installer = OllamaInstaller()

        for idx, model_name in enumerate(models):
            base_pct = int(idx / total * 90)
            _update(
                "downloading",
                base_pct,
                f"Downloading {model_name} ({idx + 1}/{total})…",
            )
            result = await installer.install(
                app_id=model_name,
                install_config={},
                variant={"ollama_name": model_name},
            )
            if not result.get("success"):
                err = result.get("error", "unknown error")
                _update("failed", base_pct, f"Failed: {model_name}", err)
                return

        _update("installing", 95, "Finalising…")
        # Brief pause to let any daemon-side work settle.
        await asyncio.sleep(1)
        _update("done", 100, "Memory layer ready.")

    except Exception as exc:  # noqa: BLE001
        logger.exception("taosmd setup task %s failed", task_id)
        _update("failed", 0, "Setup failed.", str(exc))
