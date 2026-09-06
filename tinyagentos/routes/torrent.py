"""Torrent mesh settings API — opt-out, upload cap, seed count."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tinyagentos.torrent_downloader import TORRENT_AVAILABLE
from tinyagentos.torrent_settings import TorrentSettings

router = APIRouter()


class TorrentSettingsBody(BaseModel):
    seed_enabled: bool = Field(True, description="Seed downloaded models back to the swarm")
    upload_rate_limit_kbps: int = Field(5000, ge=0, le=10_000_000, description="Upload cap in KB/s (0 = unlimited)")
    max_active_seeds: int = Field(20, ge=0, le=1000, description="How many torrents to actively seed at once")


@router.get("/api/torrent/settings")
async def get_torrent_settings(request: Request):
    """Current torrent mesh settings."""
    store = getattr(request.app.state, "torrent_settings_store", None)
    if store is None:
        return JSONResponse({"error": "torrent settings store not initialised"}, status_code=503)
    settings = store.load()
    return {
        "libtorrent_available": TORRENT_AVAILABLE,
        "seed_enabled": settings.seed_enabled,
        "upload_rate_limit_kbps": settings.upload_rate_limit_kbps,
        "max_active_seeds": settings.max_active_seeds,
    }


@router.put("/api/torrent/settings")
async def put_torrent_settings(request: Request, body: TorrentSettingsBody):
    """Update torrent settings — hot-applied to the running libtorrent
    session if one is active, otherwise read on next session start."""
    store = getattr(request.app.state, "torrent_settings_store", None)
    if store is None:
        return JSONResponse({"error": "torrent settings store not initialised"}, status_code=503)

    new_settings = TorrentSettings(
        seed_enabled=body.seed_enabled,
        upload_rate_limit_kbps=body.upload_rate_limit_kbps,
        max_active_seeds=body.max_active_seeds,
    )
    store.save(new_settings)

    # Hot-apply to the running session so the new cap takes effect
    # immediately without requiring a service restart.
    dm = getattr(request.app.state, "download_manager", None)
    if dm is not None:
        try:
            dm.apply_torrent_settings(new_settings)
        except Exception:
            pass  # Settings are saved; next session start will read them

    return {
        "status": "saved",
        "seed_enabled": new_settings.seed_enabled,
        "upload_rate_limit_kbps": new_settings.upload_rate_limit_kbps,
        "max_active_seeds": new_settings.max_active_seeds,
    }
