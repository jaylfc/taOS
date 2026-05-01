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
                "description": "Pick from list_image_models output. When supplied, routes through the controller's scheduler which picks the right backend (NPU first, CPU fallback). Omit to call the default backend directly with lcm-dreamshaper-v7 (legacy path; bypasses the scheduler).",
            },
            "guidance_scale": {
                "type": "number",
                "description": "Classifier-free guidance scale. Higher values follow the prompt more strictly; 7.5 is balanced. Lower (1–4) for artistic flexibility, higher (10–15) for strict adherence.",
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
    "description": "List installed image-generation models the agent can pick from. Returns each model's name, backend type (rknn-sd / sd-cpp / etc.), whether it's currently loaded, and any model metadata that can help with model choice.",
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
    or if any variant declares a backend in ("rknn-sd", "sd-cpp").
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
                image_backends = {"rknn-sd", "sd-cpp"}
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
    """Execute image generation via an OpenAI-compatible endpoint.

    When a model is specified, routes through the controller's scheduler
    (/api/images/generate) which knows how to find the right backend.
    When no model is given, falls back to hitting the backend_url directly.

    Returns dict with 'success', 'image_b64' (base64 PNG), and 'error' if failed.
    """
    import httpx
    import random

    if seed is None:
        seed = random.randint(1, 999999)

    # If a model is specified, go through the scheduler so it can route to
    # the right backend. The scheduler endpoint accepts the full GenerateRequest.
    if model is not None:
        effective_model = model
        target_url = f"{controller_url.rstrip('/')}/api/images/generate"
        payload: dict = {
            "prompt": prompt,
            "model": effective_model,
            "size": size,
            "steps": steps,
            "seed": seed,
            "guidance_scale": guidance_scale,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(target_url, json=payload)
                resp.raise_for_status()
                # /api/images/generate returns raw PNG bytes
                image_bytes = resp.content
                import base64
                return {
                    "success": True,
                    "image_b64": base64.b64encode(image_bytes).decode(),
                    "seed": seed,
                    "model": effective_model,
                    "size": size,
                }
        except httpx.ConnectError:
            return {"success": False, "error": f"Cannot connect to controller at {controller_url}"}
        except httpx.TimeoutException:
            return {"success": False, "error": "Image generation timed out (>120s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # No model specified — direct call to the backend's OpenAI-compatible endpoint.
    effective_model = "lcm-dreamshaper-v7"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            body: dict = {
                "prompt": prompt,
                "model": effective_model,
                "size": size,
                "n": 1,
                "num_inference_steps": steps,
                "seed": seed,
                "guidance_scale": guidance_scale,
                "response_format": "b64_json",
            }
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
                    "model": effective_model,
                    "size": size,
                }
            return {"success": False, "error": "No image data in response"}
    except httpx.ConnectError:
        return {"success": False, "error": f"Cannot connect to image backend at {backend_url}"}
    except httpx.TimeoutException:
        return {"success": False, "error": "Image generation timed out (>120s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}
