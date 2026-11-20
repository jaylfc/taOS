from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from tinyagentos.auth_context import require_admin
from tinyagentos.desktop_settings import DesktopSettingsStore

logger = logging.getLogger(__name__)

HELPER_PATH = Path(os.getenv("TAOS_LLM_HELPER", "/usr/local/sbin/taos-llm"))
METADATA_PATH = Path("/usr/share/taos/models/lfm3-1.7b.json")
PREF_NAMESPACE = "offline_model"

router = APIRouter()


def _is_available() -> bool:
    return HELPER_PATH.exists() and METADATA_PATH.exists()


def _read_metadata() -> dict:
    try:
        with METADATA_PATH.open() as f:
            return json.load(f)
    except Exception:
        return {}


def _build_offline_model_entry() -> dict | None:
    """Build a routing table entry for the offline model if available and enabled.

    This is called from llm_gateway.resolve.routing_table to inject the
    offline model into the routing table when the user has enabled it.
    """
    if not _is_available():
        return None
    metadata = _read_metadata()
    name = metadata.get("name", "LFM3 1.7B")
    display_name = f"{name} (on this phone, offline)"
    return {
        "model_name": display_name,
        "litellm_params": {
            "model": "openai/offline-model",
            "api_base": "http://127.0.0.1:7835/v1",
        },
        "metadata": {
            "priority": 10,
            "backend_name": "offline-model",
            "backend_type": "offline",
            "offline": True,
            "enable_thinking": False,
        },
    }


async def _run_helper(args: list[str], timeout: float = 70.0) -> tuple[int, str]:
    """Run the taos-llm helper with the given args. Never uses shell."""
    try:
        proc = await asyncio.create_subprocess_exec(
            str(HELPER_PATH), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, (out or b"").decode(errors="replace").strip()
    except FileNotFoundError:
        return 127, "helper not found"
    except asyncio.TimeoutError:
        return 124, "helper timed out"
    except Exception as exc:
        return 1, str(exc)


async def _get_status() -> dict:
    rc, out = await _run_helper(["status"], timeout=10.0)
    if rc != 0:
        return {"running": False, "healthy": False, "model": "", "port": 7835}
    try:
        return json.loads(out)
    except Exception:
        return {"running": False, "healthy": False, "model": "", "port": 7835}


@router.get("/api/system/offline-model")
async def get_offline_model(request: Request):
    """Return offline model availability, preference, and runtime status."""
    available = _is_available()
    if not available:
        return {
            "available": False,
            "enabled": False,
            "running": False,
            "healthy": False,
            "model": "",
            "licence": "",
            "licence_note": "",
            "performance_note": "",
        }

    metadata = _read_metadata()
    status = await _get_status()

    store = getattr(request.app.state, "desktop_settings", None)
    enabled = False
    if store is not None:
        try:
            prefs = await store.get_preference("user", PREF_NAMESPACE) or {}
            enabled = bool(prefs.get("enabled", False))
        except Exception:
            pass

    model_name = metadata.get("name", "LFM3 1.7B")
    display_name = f"{model_name} (on this phone, offline)"

    return {
        "available": True,
        "enabled": enabled,
        "running": status.get("running", False),
        "healthy": status.get("healthy", False),
        "model": display_name,
        "licence": metadata.get("licence", ""),
        "licence_note": metadata.get("licence_note", ""),
        "performance_note": metadata.get("performance_note", ""),
    }


@router.put("/api/system/offline-model", dependencies=[Depends(require_admin)])
async def set_offline_model(request: Request):
    """Enable or disable the offline model. Admin or host local token only."""
    available = _is_available()
    if not available:
        return JSONResponse({"error": "offline model not available on this device"}, status_code=404)

    body = await request.json() if await request.body() else {}
    enabled = bool(body.get("enabled", False))

    store = getattr(request.app.state, "desktop_settings", None)
    if store is None:
        return JSONResponse({"error": "settings store unavailable"}, status_code=503)

    if enabled:
        rc, out = await _run_helper(["start"], timeout=70.0)
        if rc != 0:
            return JSONResponse({"error": out or "failed to start offline model"}, status_code=502)
        try:
            status = json.loads(out)
        except Exception:
            status = {}
        await store.save_preference("user", PREF_NAMESPACE, {"enabled": True})
        return {
            "status": "enabled",
            "running": status.get("running", True),
            "healthy": status.get("healthy", False),
            "model": status.get("model", "LFM3 1.7B"),
        }
    else:
        rc, out = await _run_helper(["stop"], timeout=30.0)
        await store.save_preference("user", PREF_NAMESPACE, {"enabled": False})
        try:
            status = json.loads(out) if out else {}
        except Exception:
            status = {}
        return {
            "status": "disabled",
            "running": status.get("running", False),
            "healthy": status.get("healthy", False),
            "model": "",
        }