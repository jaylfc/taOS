"""MCP tool definitions for agent image generation via any OpenAI-compatible backend."""
from __future__ import annotations

# MCP tool schema — agents can call this to generate images
IMAGE_GENERATION_TOOL = {
    "name": "generate_image",
    "description": "Generate an image from a text prompt using your local AI backend (rkllama, ollama, or standalone SD server). Returns the image as a base64-encoded PNG.",
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Text description of the image to generate",
            },
            "size": {
                "type": "string",
                "enum": ["256x256", "384x384", "512x512"],
                "description": "Image dimensions (default 512x512)",
                "default": "512x512",
            },
            "steps": {
                "type": "integer",
                "description": "Number of inference steps (1-8, default 4 for LCM)",
                "default": 4,
                "minimum": 1,
                "maximum": 8,
            },
            "seed": {
                "type": "integer",
                "description": "Random seed for reproducibility (omit for random)",
            },
            "model": {
                "type": "string",
                "description": "Pick from list_image_models output. Omit to let the controller's scheduler pick a model based on the host's installed backends — works on any hardware (Pi NPU, NVIDIA GPU, CPU).",
            },
            "guidance_scale": {
                "type": "number",
                "description": "Classifier-free guidance scale. Higher values follow the prompt more strictly; 7.5 is balanced. Lower (1-4) for artistic flexibility, higher (10-15) for strict adherence.",
                "default": 7.5,
                "minimum": 1.0,
                "maximum": 20.0,
            },
            "negative_prompt": {
                "type": "string",
                "description": "Things you want to AVOID in the image. Comma-separated tokens, e.g. 'blurry, low quality, deformed hands'.",
                "default": "",
            },
        },
        "required": ["prompt"],
    },
}

# MCP tool schema — agents call this to discover installed image-gen models
LIST_IMAGE_MODELS_TOOL = {
    "name": "list_image_models",
    "description": "List installed image-generation models the agent can pick from. Returns each model's name, backend type (sd-cpp / etc.), whether it's currently loaded, and any model metadata that can help with model choice.",
    "input_schema": {"type": "object", "properties": {}},
}


async def execute_list_image_models(
    controller_url: str = "http://localhost:6969",
) -> dict:
    """Read installed image-gen models via the controller's models API.

    Joins /api/models (installed) + /api/models/loaded (in memory) so the
    agent can prefer loaded models for low latency.

    /api/models returns:
      {"models": [{"id", "name", "capabilities": [...], "variants": [{"backend": [...], ...}], ...}], ...}

    An entry is image-gen if "image-generation" is in its capabilities list,
    or if any variant declares a backend in ("sd-cpp",).
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            installed_resp = await client.get(
                f"{controller_url.rstrip('/')}/api/models"
            )
            installed_resp.raise_for_status()
            installed = installed_resp.json()

            loaded_set: set[str] = set()
            loaded_resp = await client.get(
                f"{controller_url.rstrip('/')}/api/models/loaded"
            )
            if loaded_resp.status_code == 200:
                loaded_data = loaded_resp.json()
                loaded_set = {
                    m.get("name", "")
                    for m in (loaded_data.get("loaded") or [])
                    if m.get("purpose") == "image-generation"
                }

        raw_models = (
            installed.get("models")
            if isinstance(installed, dict)
            else (installed or [])
        ) or []

        models = []
        for m in raw_models:
            capabilities = m.get("capabilities") or []
            variants = m.get("variants") or []

            # Check capabilities list for image-generation purpose
            is_image = "image-generation" in capabilities

            # Also check if any variant declares an image-gen backend type
            if not is_image:
                image_backends = {"sd-cpp"}
                for v in variants:
                    backends = v.get("backend") or []
                    if isinstance(backends, str):
                        backends = [backends]
                    if any(b in image_backends for b in backends):
                        is_image = True
                        break

            if not is_image:
                continue

            # Derive a display name from the first downloaded variant if present
            backend_type = ""
            for v in variants:
                backends = v.get("backend") or []
                if isinstance(backends, str):
                    backends = [backends]
                if backends:
                    backend_type = backends[0]
                    break

            name = m.get("name") or m.get("id", "")
            models.append({
                "name": name,
                "id": m.get("id", ""),
                "backend": backend_type,
                "backend_type": backend_type,
                "loaded": name in loaded_set or m.get("id", "") in loaded_set,
                "size_mb": None,
                "metadata": {
                    "description": m.get("description", ""),
                    "capabilities": capabilities,
                    "has_downloaded_variant": m.get("has_downloaded_variant", False),
                },
            })

        return {"success": True, "models": models}
    except Exception as e:
        return {"success": False, "error": str(e), "models": []}


async def execute_image_generation(
    prompt: str,
    backend_url: str = "http://localhost:8080",
    model: str | None = None,
    size: str = "512x512",
    steps: int = 4,
    seed: int | None = None,
    guidance_scale: float = 7.5,
    negative_prompt: str = "",
    controller_url: str = "http://localhost:6969",
) -> dict:
    """Execute image generation, preferring the controller's scheduler.

    Always tries the controller's ``/api/images/generate`` first so the
    scheduler can pick a backend (Pi NPU, NVIDIA GPU, CPU, …) using
    whatever's installed on the host. Falls back to a direct call to
    ``backend_url`` only when the controller is unreachable — and the
    fallback omits the model field so the local backend uses whatever
    checkpoint it has loaded rather than a pinned model name.

    Returns dict with 'success', 'image_ref' (the saved filename, usable by
    canvas_add_image), 'url' (web path to the PNG), and 'error' if failed.
    """
    import httpx
    import random

    if seed is None:
        seed = random.randint(1, 999999)

    # 1. Scheduler-routed path. Works for any hardware. If no model is
    #    supplied, the scheduler's GenerateRequest defaults the model to
    #    empty and the chosen backend uses its loaded checkpoint.
    target_url = f"{controller_url.rstrip('/')}/api/images/generate"
    payload: dict = {
        "prompt": prompt,
        "size": size,
        "steps": steps,
        "seed": seed,
        "guidance_scale": guidance_scale,
    }
    if isinstance(model, str) and model.strip():
        payload["model"] = model.strip()
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt

    controller_unreachable = False
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(target_url, json=payload)
            resp.raise_for_status()
            # The scheduler route saves the PNG and returns JSON metadata.
            data = resp.json()
            filename = data.get("filename")
            if not filename:
                return {"success": False, "error": f"image backend returned no filename: {str(data)[:200]}"}
            return {
                "success": True,
                "image_ref": filename,
                "url": data.get("path", ""),
                "seed": data.get("seed", seed),
                "model": data.get("model", model or ""),
                "size": data.get("size", size),
            }
    except httpx.ConnectError:
        controller_unreachable = True  # fall through to direct path below
    except httpx.TimeoutException:
        return {"success": False, "error": "Image generation timed out (>120s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    if not controller_unreachable:
        # Defensive guard — shouldn't reach here without a return above.
        return {"success": False, "error": "Image generation failed unexpectedly"}

    # 2. Connect-failure fallback: direct call to backend_url. Used when
    #    the caller's network can't see the controller (e.g. an LXC agent
    #    where localhost:6969 is not the controller). We do NOT pin a
    #    model name here — the local backend uses its own loaded model.
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            body: dict = {
                "prompt": prompt,
                "size": size,
                "n": 1,
                "num_inference_steps": steps,
                "seed": seed,
                "guidance_scale": guidance_scale,
                "response_format": "b64_json",
            }
            if isinstance(model, str) and model.strip():
                body["model"] = model.strip()
            if negative_prompt:
                body["negative_prompt"] = negative_prompt
            resp = await client.post(
                f"{backend_url.rstrip('/')}/v1/images/generations",
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            if "data" in data and len(data["data"]) > 0:
                return {
                    "success": True,
                    "image_b64": data["data"][0].get("b64_json", ""),
                    "seed": seed,
                    "model": model or "",
                    "size": size,
                }
            return {"success": False, "error": "No image data in response"}
    except httpx.ConnectError:
        return {"success": False, "error": f"Cannot connect to controller at {controller_url} or backend at {backend_url}"}
    except httpx.TimeoutException:
        return {"success": False, "error": "Image generation timed out (>120s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}
