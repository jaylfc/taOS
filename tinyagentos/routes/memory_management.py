"""Memory Management Routes — taOSmd backend integration.

Exposes stats, settings, backend capabilities/schema, per-agent
memory config, and recipe (config-profile) endpoints.  All routes
instantiate TaOSmdBackend with auto-init so the settings DB is created
on first access without a separate startup step.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_SETTINGS_DB = "data/memory-settings.db"


def _backend(request: Request):
    """Return a TaOSmdBackend wired to app-state stores where available."""
    from taosmd import TaOSmdBackend

    data_dir: Path = getattr(request.app.state, "data_dir", Path("data"))
    settings_db_path = data_dir / "memory-settings.db"

    kg = getattr(request.app.state, "knowledge_graph", None)
    archive = getattr(request.app.state, "archive", None)

    return TaOSmdBackend(
        kg=kg,
        archive=archive,
        settings_db_path=settings_db_path,
    )


def _build_device_info(request: Request) -> dict:
    """Build the ``{host, cluster}`` device-info dict expected by recommend().

    ``host`` mirrors the HardwareProfile dict returned by GET /api/hardware.
    ``cluster`` aggregates all online workers: counts, per-worker summaries,
    and a computed ``aggregate`` block (max/total GPU VRAM, NPU presence, total
    cores and RAM across the mesh).
    """
    hp = getattr(request.app.state, "hardware_profile", None)
    host: dict = {}
    if hp is not None:
        host = asdict(hp)
        host["profile_id"] = hp.profile_id

    cluster_manager = getattr(request.app.state, "cluster_manager", None)
    workers_summary: list[dict] = []
    aggregate = {
        "max_gpu_vram_mb": 0,
        "total_gpu_vram_mb": 0,
        "has_npu": False,
        "total_cores": 0,
        "total_ram_mb": 0,
    }

    if cluster_manager is not None:
        online = [w for w in cluster_manager.get_workers() if w.status == "online"]
        for w in online:
            hw = w.hardware or {}
            gpu = hw.get("gpu") or {}
            npu = hw.get("npu") or {}
            cpu = hw.get("cpu") or {}
            ram_mb: int = hw.get("ram_mb", 0) or 0
            vram_mb: int = gpu.get("vram_mb", 0) or 0
            cores: int = cpu.get("cores", 0) or 0
            has_npu: bool = (npu.get("type", "none") or "none") != "none"

            aggregate["total_gpu_vram_mb"] += vram_mb
            if vram_mb > aggregate["max_gpu_vram_mb"]:
                aggregate["max_gpu_vram_mb"] = vram_mb
            if has_npu:
                aggregate["has_npu"] = True
            aggregate["total_cores"] += cores
            aggregate["total_ram_mb"] += ram_mb

            workers_summary.append({
                "hardware": hw,
                "capabilities": list(w.capabilities),
                "tier_id": getattr(w, "tier_id", ""),
                "status": w.status,
            })

    cluster = {
        "online_workers": len(workers_summary),
        "workers": workers_summary,
        "aggregate": aggregate,
    }
    return {"host": host, "cluster": cluster}


# --- Memory Management Routes ---

@router.get("/api/memory/stats")
async def memory_stats(request: Request):
    """Return aggregated stats from all memory stores."""
    try:
        b = _backend(request)
        stats = await b.get_stats()
        return stats
    except Exception as exc:
        logger.warning("memory stats failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/memory/settings")
async def memory_settings_get(request: Request):
    """Return current memory settings."""
    try:
        b = _backend(request)
        settings = await b.get_settings()
        return settings
    except Exception as exc:
        logger.warning("memory settings get failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.put("/api/memory/settings")
async def memory_settings_put(request: Request):
    """Update memory settings from JSON body; returns merged settings."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    try:
        b = _backend(request)
        updated = await b.update_settings(body)
        return updated
    except Exception as exc:
        logger.warning("memory settings update failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/memory/backend/capabilities")
async def memory_backend_capabilities(request: Request):
    """Return backend name, version, and capabilities list."""
    try:
        from taosmd import TaOSmdBackend
        return {
            "name": TaOSmdBackend.name,
            "version": TaOSmdBackend.version,
            "capabilities": TaOSmdBackend.capabilities,
        }
    except Exception as exc:
        logger.warning("memory backend capabilities failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/memory/backend/settings-schema")
async def memory_backend_settings_schema(request: Request):
    """Return JSON Schema for the memory settings form."""
    try:
        b = _backend(request)
        schema = await b.get_settings_schema()
        return schema
    except Exception as exc:
        logger.warning("memory settings schema failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/agents/{name}/memory-config")
async def agent_memory_config_get(request: Request, name: str):
    """Return the memory config for a specific agent."""
    try:
        b = _backend(request)
        config = await b.get_agent_config(name)
        return config
    except Exception as exc:
        logger.warning("agent memory config get failed for %s: %s", name, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.put("/api/agents/{name}/memory-config")
async def agent_memory_config_put(request: Request, name: str):
    """Update a specific agent's memory config from JSON body."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    try:
        b = _backend(request)
        updated = await b.update_agent_config(name, body)
        return updated
    except Exception as exc:
        logger.warning("agent memory config update failed for %s: %s", name, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# --- Recipe Routes (SP4) ---

@router.get("/api/memory/recipes/schema")
async def memory_recipes_schema(request: Request):
    """Return JSON Schema for a recipe config bundle."""
    try:
        b = _backend(request)
        schema = await b.get_recipe_schema()
        return schema
    except Exception as exc:
        logger.warning("memory recipes schema failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/memory/recipes")
async def memory_recipes_list(request: Request):
    """Return all available recipes."""
    try:
        b = _backend(request)
        recipes = await b.list_recipes()
        return recipes
    except Exception as exc:
        logger.warning("memory recipes list failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/memory/recipes/{recipe_id}")
async def memory_recipes_get(request: Request, recipe_id: str):
    """Return a single recipe by id; 404 if unknown."""
    try:
        b = _backend(request)
        recipe = await b.get_recipe(recipe_id)
        if recipe is None:
            return JSONResponse({"error": f"Recipe '{recipe_id}' not found"}, status_code=404)
        return recipe
    except Exception as exc:
        logger.warning("memory recipe get failed for %s: %s", recipe_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/memory/recipes/{recipe_id}/apply")
async def memory_recipes_apply(request: Request, recipe_id: str):
    """Apply a recipe as the global default or to a specific agent.

    Body (all optional): ``{"agent": "<name>"}``
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    agent: str | None = body.get("agent") or None
    try:
        b = _backend(request)
        result = await b.apply_recipe(recipe_id, agent=agent)
        return result
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.warning("memory recipe apply failed for %s: %s", recipe_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/memory/recipes/recommend")
async def memory_recipes_recommend(request: Request):
    """Rank recipes best-first for this device (or provided device_info).

    Body (all optional): ``{"device_info": {...}}``
    When ``device_info`` is absent the controller's own hardware + cluster
    state is used.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    device_info = body.get("device_info") or None
    if device_info is None:
        device_info = _build_device_info(request)
    try:
        b = _backend(request)
        ranked = await b.recommend(device_info=device_info)
        return ranked
    except Exception as exc:
        logger.warning("memory recipes recommend failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/memory/recipes")
async def memory_recipes_create(request: Request):
    """Create a custom recipe from a spec (SP3 — not yet implemented)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    try:
        b = _backend(request)
        recipe = await b.create_recipe(body)
        return recipe
    except NotImplementedError as exc:
        return JSONResponse({"error": str(exc)}, status_code=501)
    except Exception as exc:
        logger.warning("memory recipe create failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
