from __future__ import annotations

from fastapi import APIRouter

from tinyagentos.adapters import list_frameworks

router = APIRouter(prefix="/api/frameworks", tags=["frameworks"])


@router.get("")
async def list_all() -> list[dict]:
    """Return every registered agent framework with its verification status."""
    return list_frameworks()
