# tinyagentos/routes/images_edit.py
"""Tier-aware AI image-editing endpoints for the Images Studio.

Mirrors ``tinyagentos/routes/images.py``: source images are resolved from the
workspace generated-images dir, results are saved back there as PNGs, and a
url + image_ref is returned. Routing goes through the backend catalog by
capability, with a fast|quality tier preference over backend type.

Backends:
  ``iopaint``   — LaMa erase/inpaint, rembg RemoveBG plugin, RealESRGAN upscale
                  (https://github.com/Sanster/IOPaint). The fast/CPU/NPU tier.
  ``flux-fill`` — FLUX.1-Fill inpaint/outpaint behind an A1111-style server.
                  The quality/GPU tier (image-editing only).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

import httpx
from PIL import Image
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.routes.images import _image_url_path, _images_dir

logger = logging.getLogger(__name__)

router = APIRouter()

# Tier → backend-type preference per capability. Lower index = more preferred.
# quality prefers the GPU diffusion tier (flux-fill) then falls back to iopaint;
# fast prefers iopaint (CPU/NPU, the LaMa eraser). Background-removal / upscale
# are iopaint-only capabilities, so both tiers resolve to iopaint there.
_TIER_PREFERENCE: dict[str, dict[str, list[str]]] = {
    "image-editing": {
        "quality": ["flux-fill", "iopaint"],
        "fast": ["iopaint", "flux-fill"],
    },
    "background-removal": {
        "quality": ["iopaint"],
        "fast": ["iopaint"],
    },
    "upscale": {
        "quality": ["iopaint"],
        "fast": ["iopaint"],
    },
}


# --------------------------------------------------------------------------- #
#  Request models                                                             #
# --------------------------------------------------------------------------- #
class EditRequest(BaseModel):
    image_ref: str
    op: Literal["erase", "inpaint", "outpaint"]
    mask: str  # base64 png (raw or data-URI; both backends strip the prefix)
    prompt: str = ""
    tier: Literal["fast", "quality"] = "fast"


class RemoveBgRequest(BaseModel):
    image_ref: str


class UpscaleRequest(BaseModel):
    image_ref: str
    scale: Literal[2, 4] = 2


# --------------------------------------------------------------------------- #
#  Backend routing                                                            #
# --------------------------------------------------------------------------- #
def _get_edit_backend(
    request: Request, capability: str, tier: str
) -> Optional[tuple[str, str, Optional[str]]]:
    """Pick a healthy backend for *capability*, ordered by the *tier* type
    preference. Returns (url, type, name) or None when none is available.

    Mirrors ``images._get_image_backend``: capability-driven via the live
    catalog, health/enabled/running already filtered by
    ``backends_with_capability``.
    """
    catalog = getattr(request.app.state, "backend_catalog", None)
    if catalog is None:
        return None

    healthy = catalog.backends_with_capability(capability)
    if not healthy:
        return None

    pref = _TIER_PREFERENCE.get(capability, {}).get(tier, [])

    def rank(backend) -> tuple[int, int]:
        try:
            type_rank = pref.index(backend.type)
        except ValueError:
            type_rank = len(pref)  # types not listed go last
        return (type_rank, backend.priority)

    healthy = sorted(healthy, key=rank)
    chosen = healthy[0]
    return chosen.url, chosen.type, chosen.name


def _resolve_source(request: Request, image_ref: str) -> Optional[Path]:
    """Resolve an image_ref (filename) to a path in the generated dir,
    guarding against traversal. Returns None if not found / invalid."""
    if "/" in image_ref or "\\" in image_ref or ".." in image_ref:
        return None
    path = _images_dir(request) / image_ref
    return path if path.exists() else None


def _save_result(
    request: Request,
    image_bytes: bytes,
    *,
    source_ref: str,
    op: str,
    backend_type: Optional[str] = None,
    degraded: bool = False,
) -> dict:
    """Save result PNG into the generated dir, copying the source's prompt
    metadata so the new image is browsable. Returns {url, image_ref, ...}.

    ``backend_type`` and ``degraded`` surface the backend that actually ran so
    callers can tell when a requested tier was silently downgraded (e.g. the
    quality/diffusion tier fell back to the iopaint LaMa eraser, which ignores
    the prompt).
    """
    images_dir = _images_dir(request)
    # Unique stem so two edits of the same op within one second don't collide
    # (int(time.time()) alone overwrote the earlier output).
    stem = f"{int(time.time())}_{op}_{uuid4().hex[:8]}"
    filename = f"{stem}.png"

    # The host disk can fail (full / permission) independently of the backend.
    # Keep that distinct from "backend unreachable" so the frontend can label it.
    try:
        (images_dir / filename).write_bytes(image_bytes)

        # Carry forward source metadata where available.
        metadata = {"prompt": "", "model": op, "size": "", "steps": 0, "seed": 0, "guidance_scale": 0}
        src_meta = (images_dir / source_ref).with_suffix(".json")
        if src_meta.exists():
            try:
                prev = json.loads(src_meta.read_text())
                metadata["prompt"] = prev.get("prompt", "")
            except (json.JSONDecodeError, OSError):
                pass
        metadata["model"] = f"edit:{op}"
        (images_dir / f"{stem}.json").write_text(json.dumps(metadata, indent=2))
    except OSError as exc:
        logger.exception("failed to save edit result")
        raise RuntimeError(f"Could not save result: {exc}") from exc

    return {
        "status": "edited",
        "filename": filename,
        "image_ref": filename,
        "url": _image_url_path(filename),
        "path": _image_url_path(filename),
        "backend": backend_type,
        "degraded": degraded,
    }


# --------------------------------------------------------------------------- #
#  IOPaint adapter (async httpx client)                                        #
# --------------------------------------------------------------------------- #
class IOPaintClient:
    """Thin async client for the IOPaint HTTP API.

    Endpoints (IOPaint >= 1.x, github.com/Sanster/IOPaint):
      POST /api/v1/inpaint           — erase/inpaint/outpaint (LaMa or diffusion)
      POST /api/v1/run_plugin_gen_image — RemoveBG / RealESRGAN plugins
    Both take base64 image(s) in JSON and return raw image bytes (image/png).
    """

    def __init__(self, base_url: str, *, timeout: float = 300.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    async def inpaint(
        self,
        image_b64: str,
        mask_b64: str,
        *,
        prompt: str = "",
        outpaint: bool = False,
    ) -> bytes:
        payload: dict = {
            # Strip any data-URI prefix so a data-URI mask/image works here too,
            # matching FluxFillClient (EditRequest.mask is raw or data-URI).
            "image": _strip_data_uri(image_b64),
            "mask": _strip_data_uri(mask_b64),
            "prompt": prompt,
            "sd_seed": -1,
        }
        if outpaint:
            # enable_extender grows the canvas before diffusion outpaint.
            payload["enable_extender"] = True
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base}/api/v1/inpaint", json=payload)
            resp.raise_for_status()
            return _require_image(resp)

    async def run_plugin(self, name: str, image_b64: str, *, scale: float = 2.0) -> bytes:
        payload = {
            "name": name,
            "image": _strip_data_uri(image_b64),
            "scale": float(scale),
            "clicks": [],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base}/api/v1/run_plugin_gen_image", json=payload
            )
            resp.raise_for_status()
            return _require_image(resp)


# --------------------------------------------------------------------------- #
#  FLUX Fill adapter (A1111-style sd.cpp server, async httpx client)           #
# --------------------------------------------------------------------------- #
def _strip_data_uri(b64: str) -> str:
    """Drop a leading ``data:image/...;base64,`` prefix if present."""
    if b64.startswith("data:") and "," in b64:
        return b64.split(",", 1)[1]
    return b64


def _looks_like_image(data: bytes) -> bool:
    """True if *data* starts with a known image magic signature."""
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")  # PNG
        or data.startswith(b"\xff\xd8\xff")  # JPEG
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")  # WEBP
        or data.startswith(b"GIF8")  # GIF
    )


def _require_image(resp: httpx.Response) -> bytes:
    """Return the response body only if it is actually an image.

    IOPaint can answer HTTP 200 with a JSON/text error body (missing plugin,
    bad args, model-load failure). Those bytes would otherwise be saved as a
    ``.png`` and reported as success, handing the user a broken image. Reject
    such a response so it routes into ``_backend_error_response``.

    Some valid backends return image bytes without an ``image/*`` content-type
    (or with ``application/octet-stream``), so accept the body when either the
    content-type is an image or the bytes carry an image magic signature. Only
    raise when it is clearly not an image (e.g. a JSON/text error body).
    """
    content = resp.content
    is_image_type = resp.headers.get("content-type", "").startswith("image/")
    if is_image_type or _looks_like_image(content):
        return content
    raise RuntimeError(f"IOPaint returned non-image response: {resp.text[:300]}")


# Fraction of the image's own size to grow each side by when outpainting.
_OUTPAINT_MARGIN = 0.25


class FluxFillClient:
    """Thin async client for a FLUX.1-Fill server behind an A1111-style sd.cpp
    HTTP API (same family as the ``sd-cpp`` image-generation backend).

    Inpaint/outpaint go through ``POST /sdapi/v1/img2img`` with an A1111 inpaint
    payload: the source goes in ``init_images``, the mask in ``mask``, and the
    response carries a base64 PNG in ``images[0]``.

    The mirror of :class:`IOPaintClient.inpaint` so ``edit_image`` can call
    either with identical arguments.
    """

    def __init__(self, base_url: str, *, timeout: float = 300.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    @staticmethod
    def _pad_for_outpaint(image_b64: str, mask_b64: str) -> tuple[str, str]:
        """Grow the canvas for outpaint: pad the source with edge-replicated
        borders and build a mask whose new border is white (to be painted) and
        whose original region is black (to be kept). Returns (image_b64, mask_b64).

        sd.cpp img2img does not natively extend the canvas, so (consistent with
        the IOPaint extender path) we pre-pad before sending.
        """
        src = Image.open(io.BytesIO(base64.b64decode(_strip_data_uri(image_b64)))).convert("RGB")
        w, h = src.size
        mx = max(1, int(round(w * _OUTPAINT_MARGIN)))
        my = max(1, int(round(h * _OUTPAINT_MARGIN)))
        new_w, new_h = w + 2 * mx, h + 2 * my

        # Edge-replicate the source into the larger canvas so diffusion has
        # plausible context to extend from.
        padded = Image.new("RGB", (new_w, new_h))
        padded.paste(src, (mx, my))
        left = src.crop((0, 0, 1, h)).resize((mx, h))
        right = src.crop((w - 1, 0, w, h)).resize((mx, h))
        padded.paste(left, (0, my))
        padded.paste(right, (mx + w, my))
        top = padded.crop((0, my, new_w, my + 1)).resize((new_w, my))
        bottom = padded.crop((0, my + h - 1, new_w, my + h)).resize((new_w, my))
        padded.paste(top, (0, 0))
        padded.paste(bottom, (0, my + h))

        # White border = paint, black centre = keep.
        mask = Image.new("L", (new_w, new_h), 255)
        mask.paste(0, (mx, my, mx + w, my + h))

        def _png_b64(img: Image.Image) -> str:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode()

        return _png_b64(padded), _png_b64(mask)

    async def inpaint(
        self,
        image_b64: str,
        mask_b64: str,
        *,
        prompt: str = "",
        outpaint: bool = False,
        steps: int = 28,
        guidance: float = 30.0,
        denoising_strength: float = 0.85,
    ) -> bytes:
        if outpaint:
            image_b64, mask_b64 = self._pad_for_outpaint(image_b64, mask_b64)
        else:
            mask_b64 = _strip_data_uri(mask_b64)

        # A1111-compatible servers (sd.cpp sd-server) fall back to a default
        # 512x512 size when width/height are omitted, which wrongly resizes any
        # non-512 image. Pin them to the dimensions of the image actually sent in
        # ``init_images`` (the padded canvas in the outpaint case).
        src_w, src_h = Image.open(
            io.BytesIO(base64.b64decode(_strip_data_uri(image_b64)))
        ).size

        payload: dict = {
            "init_images": [image_b64],
            "mask": mask_b64,
            "width": src_w,
            "height": src_h,
            "prompt": prompt,
            "steps": steps,
            "cfg_scale": guidance,
            "denoising_strength": denoising_strength,
            # 1 = fill the masked region (vs. original/latent noise/nothing).
            "inpainting_fill": 1,
            # Process the masked region at full resolution for sharper fills.
            "inpaint_full_res": True,
            "inpaint_full_res_padding": 32,
            # 0 = inpaint the white (masked) area, matching our mask convention.
            "inpainting_mask_invert": 0,
            "sampler_name": "euler_a",
            "seed": -1,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base}/sdapi/v1/img2img", json=payload)
            resp.raise_for_status()
            data = resp.json()
        images = data.get("images") if isinstance(data, dict) else None
        if not images:
            raise RuntimeError("FLUX Fill server returned no images")
        return base64.b64decode(_strip_data_uri(images[0]))


# --------------------------------------------------------------------------- #
#  Error helpers                                                               #
# --------------------------------------------------------------------------- #
_NO_BACKEND = {
    "error": "No image-editing backend installed. Install IOPaint from the Store."
}


def _backend_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, httpx.ConnectError):
        return JSONResponse(
            {"error": "Cannot connect to the editing backend. Is it running?"},
            status_code=503,
        )
    if isinstance(exc, httpx.TimeoutException):
        return JSONResponse(
            {"error": "Image edit timed out. The backend may be busy."},
            status_code=504,
        )
    if isinstance(exc, httpx.HTTPStatusError):
        return JSONResponse(
            {"error": f"Editing backend returned error: {exc.response.status_code}"},
            status_code=502,
        )
    # A failed disk write (full / permission) is a host problem, not the backend
    # being unreachable; surface it distinctly so the frontend doesn't mislabel
    # it as "could not reach the editing backend".
    if isinstance(exc, RuntimeError) and str(exc).startswith("Could not save result"):
        return JSONResponse({"error": str(exc)}, status_code=500)
    logger.exception("image edit failed")
    return JSONResponse({"error": f"Unexpected error: {exc}"}, status_code=500)


# --------------------------------------------------------------------------- #
#  Endpoints                                                                   #
# --------------------------------------------------------------------------- #
@router.post("/api/images/edit")
async def edit_image(request: Request, body: EditRequest):
    """Erase / inpaint / outpaint a region of an image via the editing backend."""
    source = _resolve_source(request, body.image_ref)
    if source is None:
        return JSONResponse({"error": f"Image '{body.image_ref}' not found"}, status_code=404)

    backend = _get_edit_backend(request, "image-editing", body.tier)
    if backend is None:
        return JSONResponse(_NO_BACKEND, status_code=503)
    url, backend_type, _name = backend

    image_b64 = base64.b64encode(source.read_bytes()).decode()
    # flux-fill = the quality GPU diffusion tier (A1111 img2img inpaint); iopaint
    # = the fast CPU/NPU tier (LaMa). _get_edit_backend falls back to iopaint
    # when no flux-fill backend is healthy, so anything else is unexpected.
    if backend_type == "flux-fill":
        client: IOPaintClient | FluxFillClient = FluxFillClient(url)
    elif backend_type == "iopaint":
        client = IOPaintClient(url)
    else:
        return JSONResponse(
            {"error": f"Edit backend '{backend_type}' has no client wired yet."},
            status_code=503,
        )

    # Only a meaningful downgrade is flagged: a quality request that did NOT get
    # the quality primary (flux-fill) fell back to the fast iopaint eraser, which
    # ignores the prompt. Falling between fast-tier preferences is not a
    # meaningful degrade, so fast requests (and quality served by flux-fill) are
    # never flagged. The chosen backend type is surfaced in the response either way.
    quality_primary = (
        _TIER_PREFERENCE.get("image-editing", {}).get("quality") or [None]
    )[0]
    degraded = body.tier == "quality" and backend_type != quality_primary

    try:
        result_bytes = await client.inpaint(
            image_b64,
            body.mask,
            prompt=body.prompt,
            outpaint=body.op == "outpaint",
        )
        return _save_result(
            request,
            result_bytes,
            source_ref=body.image_ref,
            op=body.op,
            backend_type=backend_type,
            degraded=degraded,
        )
    except Exception as exc:  # noqa: BLE001 — mapped to a clean HTTP response
        return _backend_error_response(exc)


@router.post("/api/images/remove-bg")
async def remove_background(request: Request, body: RemoveBgRequest):
    """Remove the background of an image via the rembg (RemoveBG) plugin."""
    source = _resolve_source(request, body.image_ref)
    if source is None:
        return JSONResponse({"error": f"Image '{body.image_ref}' not found"}, status_code=404)

    backend = _get_edit_backend(request, "background-removal", "fast")
    if backend is None:
        return JSONResponse(_NO_BACKEND, status_code=503)
    url, _backend_type, _name = backend

    image_b64 = base64.b64encode(source.read_bytes()).decode()
    client = IOPaintClient(url)
    try:
        result_bytes = await client.run_plugin("RemoveBG", image_b64)
        return _save_result(
            request, result_bytes, source_ref=body.image_ref, op="removebg",
            backend_type=_backend_type,
        )
    except Exception as exc:  # noqa: BLE001
        return _backend_error_response(exc)


@router.post("/api/images/upscale")
async def upscale_image(request: Request, body: UpscaleRequest):
    """Upscale an image 2x or 4x via the RealESRGAN plugin."""
    source = _resolve_source(request, body.image_ref)
    if source is None:
        return JSONResponse({"error": f"Image '{body.image_ref}' not found"}, status_code=404)

    backend = _get_edit_backend(request, "upscale", "fast")
    if backend is None:
        return JSONResponse(_NO_BACKEND, status_code=503)
    url, _backend_type, _name = backend

    image_b64 = base64.b64encode(source.read_bytes()).decode()
    client = IOPaintClient(url)
    try:
        result_bytes = await client.run_plugin("RealESRGAN", image_b64, scale=float(body.scale))
        return _save_result(
            request, result_bytes, source_ref=body.image_ref, op="upscale",
            backend_type=_backend_type,
        )
    except Exception as exc:  # noqa: BLE001
        return _backend_error_response(exc)


@router.get("/api/images/edit/capabilities")
async def edit_capabilities(request: Request):
    """Report which editing capabilities currently have a healthy backend,
    so the frontend can gate ops.

    ``image_editing_tiers`` additionally reports per-tier health for
    image-editing so the UI can disable a tier the router can't honestly
    serve, rather than offering one that silently downgrades. Health mirrors
    what ``_get_edit_backend`` + ``edit_image`` actually do:

    - ``quality`` is healthy only when the router resolves its primary
      backend (flux-fill). If it would fall back to iopaint, that's the
      silent, prompt-ignoring downgrade we're preventing, so Quality reports
      unhealthy (matching ``edit_image``'s ``degraded`` flag).
    - ``fast`` is healthy whenever the router resolves *any* backend for the
      tier. Fast's preference falls through to flux-fill when iopaint is
      absent, and serving a fast request on flux-fill still honours the
      prompt (never flagged ``degraded``) — so Fast must NOT be disabled just
      because iopaint is missing, or the UI would gate a tier the router
      would happily serve.
    """
    catalog = getattr(request.app.state, "backend_catalog", None)

    def has(capability: str) -> bool:
        if catalog is None:
            return False
        return bool(catalog.backends_with_capability(capability))

    def tier_healthy(capability: str, tier: str) -> bool:
        """True if the router can serve *tier* without a silent downgrade.

        Resolves the backend the same way ``edit_image`` does (via
        ``_get_edit_backend``) so the probe and the router never diverge.
        """
        resolved = _get_edit_backend(request, capability, tier)
        if resolved is None:
            return False
        _url, backend_type, _name = resolved
        # Only "quality" carries a downgrade risk: served by anything other
        # than its primary (flux-fill) means the prompt is ignored. This is
        # exactly ``edit_image``'s degraded condition, inverted.
        quality_primary = (
            _TIER_PREFERENCE.get(capability, {}).get("quality") or [None]
        )[0]
        if tier == "quality":
            return backend_type == quality_primary
        return True

    return {
        "image_editing": has("image-editing"),
        "background_removal": has("background-removal"),
        "upscale": has("upscale"),
        "image_editing_tiers": {
            "quality": tier_healthy("image-editing", "quality"),
            "fast": tier_healthy("image-editing", "fast"),
        },
    }
