# tinyagentos/routes/guides.py
from __future__ import annotations

import logging
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter()

# Cached in memory after first load. Restart to pick up edits.
_guides: dict | None = None
_cached_data_dir: Path | None = None


def _load_guides(data_dir: Path) -> dict:
    """Load guides YAML from disk, with in-memory caching per data_dir."""
    global _guides, _cached_data_dir
    if _guides is not None and _cached_data_dir == data_dir:
        return _guides
    path = data_dir / "guides.yaml"
    if not path.exists():
        logger.warning("guides.yaml not found at %s", path)
        _guides = {}
        _cached_data_dir = data_dir
        return _guides
    try:
        _guides = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        logger.exception("failed to parse guides.yaml")
        _guides = {}
    _cached_data_dir = data_dir
    return _guides


@router.get("/api/guides/recommendations")
async def get_recommendations(
    request: Request,
    hardware: str = Query(..., description="Hardware tier: pi-16gb, nvidia-12gb, cpu-only"),
    use_case: str = Query(..., description="Use case: chat, coding, embedding, vision, voice"),
):
    """Return curated model recommendations for a hardware tier and use case."""
    data_dir: Path = request.app.state.data_dir
    guides = _load_guides(data_dir)

    recs = guides.get("recommendations", {})
    tier_recs = recs.get(hardware)
    if tier_recs is None:
        raise HTTPException(
            status_code=404,
            detail=f"Hardware tier '{hardware}' not found. Available: {sorted(recs.keys())}",
        )

    case_recs = tier_recs.get(use_case)
    if case_recs is None:
        raise HTTPException(
            status_code=404,
            detail=f"Use case '{use_case}' not found for tier '{hardware}'. "
            f"Available: {sorted(tier_recs.keys())}",
        )

    return {"hardware": hardware, "use_case": use_case, "recommendations": case_recs}


@router.get("/api/guides/tiers")
async def list_tiers(request: Request):
    """List all hardware tiers with labels and descriptions."""
    data_dir: Path = request.app.state.data_dir
    guides = _load_guides(data_dir)
    return {"tiers": guides.get("hardware_tiers", {})}


@router.get("/api/guides/use-cases")
async def list_use_cases(request: Request):
    """List all use cases with labels and descriptions."""
    data_dir: Path = request.app.state.data_dir
    guides = _load_guides(data_dir)
    return {"use_cases": guides.get("use_cases", {})}
