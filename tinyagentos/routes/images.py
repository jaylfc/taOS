# tinyagentos/routes/images.py
from __future__ import annotations

import base64
import json
import logging
import random
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tinyagentos.scheduler import (
    Capability,
    NoResourceAvailableError,
    Priority,
    Resource,
    ResourceRef,
    Task,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class GenerateRequest(BaseModel):
    prompt: str
    # Empty string means "let the scheduler pick a model based on what's
    # installed on the host". Hardcoding a Pi-specific name here was a
    # footgun for users on different hardware (e.g. NVIDIA GPU users
    # who don't have lcm-dreamshaper-v7 installed at all).
    model: str = ""
    size: str = "512x512"
    steps: int = 4
    seed: int | None = None
    guidance_scale: float = 7.5


def _images_dir(request: Request) -> Path:
    """Return the workspace/images/generated directory, creating it if needed.

    Generated images live under the user's workspace so they can also be
    browsed via the Files app.
    """
    config_path = getattr(request.app.state, "config_path", None)
    if config_path is not None:
        data_dir = Path(config_path).parent
    else:
        # Fall back to project-level data/
        data_dir = Path(__file__).parent.parent.parent / "data"
    d = data_dir / "workspace" / "images" / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _image_url_path(filename: str) -> str:
    """Web path for serving a generated image."""
    return f"/data/workspace/images/generated/{filename}"


def _get_image_backend(
    request: Request, model: str | None = None
) -> tuple[str | None, str, str | None]:
    """Find an image generation backend. Returns (url, backend_type, backend_name).

    backend_name is the ``name`` field from config.backends, or None when the
    backend came from the server.image_backend_url override (which is not a
    registered catalog entry and therefore has no lifecycle state to manage).

    Backend-driven: if a model id is supplied, prefer a backend that is
    *right now* advertising that model via its own live API. Fall back to
    capability-only routing (NPU > CPU > generic) if no backend has the
    requested model loaded or no model was specified.

    Backend types:
      ``openai``    — TinyAgentOS's own rknn_sd_server / any OpenAI-compatible image server
      ``rkllama``   — rkllama-style /v1/images/generations
      ``ollama``    — ollama-style /v1/images/generations
      ``sd-cpp``    — leejet/stable-diffusion.cpp sd-server (A1111-compatible /sdapi/v1/txt2img)
      ``rknn-sd``   — darkbit1001 RKNN LCM server — POST /generate returns raw PNG
    """
    config = request.app.state.config

    image_url = config.server.get("image_backend_url")
    if image_url:
        # Server-level override: no catalog entry, so name is None.
        return image_url, config.server.get("image_backend_type", "openai"), None

    catalog = getattr(request.app.state, "backend_catalog", None)
    image_types = ("rknn-sd", "sd-cpp", "rkllama", "ollama")

    # 1. Backend-driven: find a backend actually serving the requested model.
    if model and catalog is not None:
        for backend in catalog.backends_with_capability("image-generation"):
            if backend.type not in image_types:
                continue
            if backend.has_model(model):
                return backend.url, backend.type, backend.name

    # 2. Capability-only fallback: highest-priority healthy image-gen backend,
    #    respecting the NPU > CPU > generic preference within ties.
    preference = {"rknn-sd": 0, "sd-cpp": 1, "rkllama": 2, "ollama": 3}
    if catalog is not None:
        healthy = [
            b for b in catalog.backends_with_capability("image-generation")
            if b.type in image_types
        ]
        healthy.sort(key=lambda b: (preference.get(b.type, 99), b.priority))
        if healthy:
            return healthy[0].url, healthy[0].type, healthy[0].name

    # 3. No live catalog (scheduler not started yet): use static config.
    npu = [b for b in config.backends if b.get("type") == "rknn-sd"]
    sdcpp = [b for b in config.backends if b.get("type") == "sd-cpp"]
    generic = [b for b in config.backends if b.get("type") in ("rkllama", "ollama")]
    for backend in (
        sorted(npu, key=lambda b: b.get("priority", 99))
        + sorted(sdcpp, key=lambda b: b.get("priority", 99))
        + sorted(generic, key=lambda b: b.get("priority", 99))
    ):
        return backend["url"], backend["type"], backend.get("name")

    return None, "", None


def _list_images(images_dir: Path) -> list[dict]:
    """List generated images with metadata, newest first."""
    results = []
    for png in images_dir.glob("*.png"):
        meta_path = png.with_suffix(".json")
        metadata = {}
        if meta_path.exists():
            try:
                metadata = json.loads(meta_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        results.append({
            "filename": png.name,
            "path": _image_url_path(png.name),
            "size_bytes": png.stat().st_size,
            "prompt": metadata.get("prompt", ""),
            "model": metadata.get("model", ""),
            "size": metadata.get("size", ""),
            "steps": metadata.get("steps", 0),
            "seed": metadata.get("seed", 0),
            "guidance_scale": metadata.get("guidance_scale", 0),
        })
    results.sort(key=lambda x: x["filename"], reverse=True)
    return results


async def _call_image_backend(
    resource: Resource,
    *,
    prompt: str,
    model: str,
    size: str,
    steps: int,
    guidance_scale: float,
    seed: int,
) -> bytes:
    """Scheduler payload: POST to the resource's image-generation backend and
    return the decoded PNG bytes. Shape of the call depends on backend type —
    sd-cpp uses A1111, everyone else uses OpenAI-compatible /v1/images/generations.
    """
    backend_url = resource.backend_url_for("image-generation")
    if not backend_url:
        raise RuntimeError(
            f"{resource.name} has no image-generation backend wired"
        )

    width_s, height_s = size.split("x")
    width, height = int(width_s), int(height_s)

    # Detect backend kind from the URL — the resource knows its own wiring.
    # sd-cpp listens on 7864, rknn-sd on 7863. We could introspect via /health
    # but at phase 1 the URL is enough.
    is_sdcpp = ":7864" in backend_url

    if is_sdcpp:
        endpoint = f"{backend_url.rstrip('/')}/sdapi/v1/txt2img"
        payload = {
            "prompt": prompt,
            "steps": steps,
            "width": width,
            "height": height,
            "cfg_scale": guidance_scale,
            "seed": seed,
            "sampler_name": "euler_a",
        }
    else:
        endpoint = f"{backend_url.rstrip('/')}/v1/images/generations"
        payload = {
            "prompt": prompt,
            "model": model,
            "size": size,
            "response_format": "b64_json",
        }

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(endpoint, json=payload)
        resp.raise_for_status()
        data = resp.json()

    if is_sdcpp:
        b64 = data["images"][0]
    else:
        b64 = data["data"][0]["b64_json"]
    return base64.b64decode(b64)


@router.post("/api/images/generate")
async def generate_image(request: Request, body: GenerateRequest):
    """Generate an image via the resource scheduler.

    Routing is backend-driven: the scheduler picks the first preferred
    resource whose live backend catalog entry can serve the capability.
    Falls back from NPU to CPU automatically.
    """
    scheduler = getattr(request.app.state, "resource_scheduler", None)
    seed = body.seed if body.seed is not None else random.randint(0, 2**32 - 1)

    # Legacy fallback: if the scheduler isn't ready (startup race, test env),
    # fall through to the old direct-backend path.
    if scheduler is None:
        return await _legacy_generate(request, body, seed)

    # Model-steered resource preference: if the user picked a model that's
    # loaded on a specific backend, prefer the matching resource first.
    catalog = getattr(request.app.state, "backend_catalog", None)
    preferred: list[ResourceRef] = []
    if catalog:
        match = catalog.find_backend_for_model("image-generation", body.model)
        if match and match.type in ("rknn-sd", "rkllama"):
            preferred.append(ResourceRef("npu-rk3588"))
        if match and match.type == "sd-cpp":
            preferred.append(ResourceRef("cpu-inference"))
    # Always include both as fallbacks so routing has somewhere to go
    for name in ("npu-rk3588", "cpu-inference"):
        if not any(r.name == name for r in preferred):
            preferred.append(ResourceRef(name))

    async def _payload(resource: Resource) -> bytes:
        return await _call_image_backend(
            resource,
            prompt=body.prompt,
            model=body.model,
            size=body.size,
            steps=body.steps,
            guidance_scale=body.guidance_scale,
            seed=seed,
        )

    task = Task(
        capability=Capability.IMAGE_GENERATION,
        payload=_payload,
        preferred_resources=preferred,
        priority=Priority.INTERACTIVE_USER,
        submitter="images-app",
        estimated_seconds=35.0,
        estimated_memory_mb=0,  # backend is already loaded
    )

    try:
        image_bytes = await scheduler.submit(task)
    except NoResourceAvailableError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except httpx.ConnectError:
        return JSONResponse(
            {"error": "Cannot connect to image backend. Is it running?"},
            status_code=503,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": "Image generation timed out. The backend may be busy."},
            status_code=504,
        )
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            {"error": f"Image backend returned error: {exc.response.status_code}"},
            status_code=502,
        )
    except Exception as exc:
        logger.exception("image generation failed")
        return JSONResponse({"error": f"Unexpected error: {exc}"}, status_code=500)

    # Save to workspace
    images_dir = _images_dir(request)
    timestamp = int(time.time())
    filename = f"{timestamp}_{seed}.png"
    (images_dir / filename).write_bytes(image_bytes)
    metadata = {
        "prompt": body.prompt,
        "model": body.model,
        "size": body.size,
        "steps": body.steps,
        "seed": seed,
        "guidance_scale": body.guidance_scale,
    }
    (images_dir / f"{timestamp}_{seed}.json").write_text(json.dumps(metadata, indent=2))
    return {
        "status": "generated",
        "filename": filename,
        "path": _image_url_path(filename),
        **metadata,
    }


async def _legacy_generate(request: Request, body: GenerateRequest, seed: int):
    """Pre-scheduler direct-backend path. Only used when the scheduler is not
    available (e.g. during startup or in tests without lifespan)."""
    backend_url, backend_type, backend_name = _get_image_backend(request, model=body.model)
    if not backend_url:
        return JSONResponse(
            {"error": "No image generation backend configured."},
            status_code=503,
        )

    lifecycle_mgr = getattr(request.app.state, "lifecycle_manager", None)

    if backend_type == "sd-cpp":
        try:
            width_s, height_s = body.size.split("x")
            width, height = int(width_s), int(height_s)
        except ValueError:
            return JSONResponse({"error": f"invalid size: {body.size}"}, status_code=400)
        payload = {
            "prompt": body.prompt, "steps": body.steps, "width": width, "height": height,
            "cfg_scale": body.guidance_scale, "seed": seed, "sampler_name": "euler_a",
        }
        endpoint = f"{backend_url.rstrip('/')}/sdapi/v1/txt2img"
    else:
        payload = {"prompt": body.prompt, "model": body.model, "size": body.size, "response_format": "b64_json"}
        endpoint = f"{backend_url.rstrip('/')}/v1/images/generations"
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(endpoint, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError:
        return JSONResponse(
            {"error": f"Cannot connect to image backend at {backend_url}. Is it running?"},
            status_code=503,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": "Image generation timed out. The backend may be busy."},
            status_code=504,
        )
    except httpx.HTTPStatusError as e:
        return JSONResponse(
            {"error": f"Image backend returned error: {e.response.status_code}"},
            status_code=502,
        )
    except Exception as e:
        return JSONResponse({"error": f"Unexpected error: {e}"}, status_code=500)
    finally:
        # Notify lifecycle manager so the keep-alive timer resets/arms after
        # each image generation attempt. This is a no-op when:
        #   - backend_name is None (server-level override, not a catalog entry)
        #   - lifecycle_manager is not wired on this app (tests, startup race)
        #   - keep_alive_minutes == 0 (always-on backend)
        # NOTE: chat / embedding traffic goes through LiteLLM and does not hit
        # this route, so its keep-alive timer is NOT reset here. Fixing that
        # requires a LiteLLM callback or proxy hook — tracked separately.
        if backend_name and lifecycle_mgr is not None:
            lifecycle_mgr.notify_task_complete(backend_name)
    image_data = data["images"][0] if backend_type == "sd-cpp" else data["data"][0]["b64_json"]
    images_dir = _images_dir(request)
    timestamp = int(time.time())
    filename = f"{timestamp}_{seed}.png"
    (images_dir / filename).write_bytes(base64.b64decode(image_data))
    metadata = {
        "prompt": body.prompt, "model": body.model, "size": body.size,
        "steps": body.steps, "seed": seed, "guidance_scale": body.guidance_scale,
    }
    (images_dir / f"{timestamp}_{seed}.json").write_text(json.dumps(metadata, indent=2))
    return {"status": "generated", "filename": filename, "path": _image_url_path(filename), **metadata}


@router.get("/api/images")
async def list_images(request: Request):
    """List all generated images with metadata, newest first."""
    images_dir = _images_dir(request)
    return {"images": _list_images(images_dir)}


@router.delete("/api/images/{filename}")
async def delete_image(request: Request, filename: str):
    """Delete a generated image and its metadata sidecar."""
    images_dir = _images_dir(request)

    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)

    image_path = images_dir / filename
    if not image_path.exists():
        return JSONResponse({"error": f"Image '{filename}' not found"}, status_code=404)

    image_path.unlink()
    # Also delete metadata sidecar if it exists
    meta_path = image_path.with_suffix(".json")
    if meta_path.exists():
        meta_path.unlink()

    return {"status": "deleted", "filename": filename}
