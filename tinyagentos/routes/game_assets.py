"""Tier-aware AI asset generation for the Game Studio (Slice 1: textures/sprites).

A game is a flat file set stored under ``data/games/{id}/`` (see routes/games.py).
This route turns a text prompt into a PNG *inside that same file set* by driving
a ComfyUI backend, so the texture previews and travels with the game — no new
storage is invented.

Tier resolution mirrors the Images Studio (routes/images_edit.py): the concrete
backend is resolved from the live backend catalog first, falling back to the
host hardware profile. A discrete GPU (e.g. the RTX 3060 worker) serves the SDXL
tier; a host with no discrete-GPU texture backend has no capable pipeline (RK
NPU SD tiling is a documented follow-up, not yet wired), so the route returns
``{available: false}`` (HTTP 200) and the UI shows a "needs a GPU worker" state
instead of a crash or an always-failing tier.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.asset_gen.comfyui_client import ComfyUIClient, default_base_url
from tinyagentos.asset_gen.workflows import build_texture_workflow
from tinyagentos.routes.games import (
    _SLUG_RE,
    _games_dir,
    _is_safe_filename,
    _load_meta,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Size bounds. SDXL/latent geometry requires multiples of 8; keep a sane range
# so a texture request can't ask the GPU for an 8K canvas.
_MIN_SIZE = 64
_MAX_SIZE = 2048
_SIZE_MULTIPLE = 8

# The discrete-GPU tier checkpoint. (RK NPU SD is a documented follow-up; there
# is no wired NPU texture backend yet, so it is not resolved here.)
_SDXL_CHECKPOINT = "sd_xl_base_1.0.safetensors"

# Generated PNGs share the game's flat file set. Cap both the per-image size and
# the number of assets so a runaway/loop request can't fill the disk.
_MAX_ASSET_BYTES = 12 * 1024 * 1024  # a 2048x2048 PNG fits comfortably
_MAX_ASSET_FILES = 200


class TextureRequest(BaseModel):
    prompt: str
    width: int = 512
    height: int = 512
    tileable: bool = False
    kind: Literal["texture", "sprite"] = "texture"


@dataclass
class TextureBackend:
    """The resolved backend for a texture request."""

    base_url: str
    tier: str          # "sdxl" | "sd15"
    checkpoint: str
    workflow: str      # workflow template name


def resolve_texture_backend(request: Request) -> Optional[TextureBackend]:
    """Resolve the tier-appropriate ComfyUI backend, or ``None`` when the host
    has no capable accelerator.

    Priority mirrors the Images Studio routing:
      1. A live ComfyUI backend advertised in the catalog (e.g. the RTX 3060
         worker) — the SDXL/GPU tier.
      2. A local discrete GPU (CUDA/ROCm) — SDXL at the local ComfyUI URL.
      3. Otherwise unavailable. (An NPU-only host has no wired texture backend
         yet, so it resolves to unavailable rather than an always-failing tier.)
    """
    catalog = getattr(request.app.state, "backend_catalog", None)
    if catalog is not None:
        try:
            for backend in catalog.backends_with_capability("image-generation"):
                if getattr(backend, "type", "") == "comfyui" and getattr(backend, "url", None):
                    return TextureBackend(backend.url, "sdxl", _SDXL_CHECKPOINT, "texture_sdxl")
        except Exception:  # noqa: BLE001 — a flaky catalog must not crash the route
            logger.warning("backend catalog lookup failed", exc_info=True)

    hw = getattr(request.app.state, "hardware_profile", None)
    gpu = getattr(hw, "gpu", None)
    if gpu is not None and (getattr(gpu, "cuda", False) or getattr(gpu, "rocm", False)):
        return TextureBackend(default_base_url(), "sdxl", _SDXL_CHECKPOINT, "texture_sdxl")

    return None


def _valid_size(value: int) -> bool:
    return _MIN_SIZE <= value <= _MAX_SIZE and value % _SIZE_MULTIPLE == 0


def _asset_filename(kind: str) -> str:
    """A unique, filesystem-safe PNG name for a generated asset."""
    return f"{kind}-{int(time.time())}-{uuid4().hex[:8]}.png"


@router.post("/api/games/{game_id}/assets/texture")
async def generate_texture(request: Request, game_id: str, body: TextureRequest):
    """Generate a texture/sprite PNG via ComfyUI and write it into the game's
    file set. Returns the asset's preview path on success.

    Never raises to the client: an unavailable tier returns 200
    ``{available: false, reason}`` and a backend failure returns a clean 502.
    """
    if not _SLUG_RE.match(game_id):
        return JSONResponse({"error": "invalid game id"}, status_code=400)

    prompt = (body.prompt or "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt must not be empty"}, status_code=400)
    if not _valid_size(body.width) or not _valid_size(body.height):
        return JSONResponse(
            {
                "error": (
                    f"width/height must be a multiple of {_SIZE_MULTIPLE} "
                    f"between {_MIN_SIZE} and {_MAX_SIZE}"
                )
            },
            status_code=400,
        )

    game_dir = _games_dir(request) / game_id
    if _load_meta(game_dir) is None:
        return JSONResponse({"error": f"game '{game_id}' not found"}, status_code=404)

    backend = resolve_texture_backend(request)
    if backend is None:
        # Tier-gated: no capable accelerator. NOT an error — the UI shows a
        # "needs a GPU worker" state, so answer 200 with a clear reason.
        return {
            "available": False,
            "reason": "No GPU worker available for texture generation.",
        }

    # Refuse before spending a GPU call if the game is already at the asset cap.
    try:
        asset_count = sum(1 for p in game_dir.iterdir() if p.is_file() and p.suffix == ".png")
    except OSError:
        asset_count = 0
    if asset_count >= _MAX_ASSET_FILES:
        return JSONResponse(
            {"available": True, "error": f"asset limit reached (max {_MAX_ASSET_FILES})"},
            status_code=409,
        )

    seed = random.randint(0, 2**32 - 1)
    workflow = build_texture_workflow(
        prompt=prompt,
        width=body.width,
        height=body.height,
        seed=seed,
        tileable=body.tileable,
        checkpoint=backend.checkpoint,
        template=backend.workflow,
    )

    client = ComfyUIClient(backend.base_url)
    result = await client.generate(workflow)
    if result is None:
        # Fail-soft: the client swallowed the underlying error and returned
        # None. Surface a clean 502 (not a 500 crash) so the UI can retry.
        return JSONResponse(
            {"error": "Texture generation failed. Is the ComfyUI backend running?"},
            status_code=502,
        )

    if len(result.image_bytes) > _MAX_ASSET_BYTES:
        logger.warning("generated texture exceeded size cap: %d bytes", len(result.image_bytes))
        return JSONResponse(
            {"error": "generated image exceeded the size limit"}, status_code=502
        )

    filename = _asset_filename(body.kind)
    if not _is_safe_filename(filename):  # defensive; the generator is safe
        return JSONResponse({"error": "internal filename error"}, status_code=500)
    try:
        game_dir.mkdir(parents=True, exist_ok=True)
        (game_dir / filename).write_bytes(result.image_bytes)
    except OSError as exc:
        logger.exception("failed to write generated texture")
        return JSONResponse({"error": f"could not save asset: {exc}"}, status_code=500)

    return {
        "available": True,
        "status": "generated",
        "filename": filename,
        "path": f"/api/games/{game_id}/preview/{filename}",
        "kind": body.kind,
        "tier": backend.tier,
        "tileable": body.tileable,
        "seed": seed,
    }
