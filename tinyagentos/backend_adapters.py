from __future__ import annotations

import time
from abc import ABC, abstractmethod

import httpx


class BackendAdapter(ABC):
    @abstractmethod
    async def health(self, client: httpx.AsyncClient, url: str) -> dict:
        ...


class OllamaCompatAdapter(BackendAdapter):
    """Adapter for Ollama-compatible APIs (rkllama, ollama).

    Uses GET /api/tags to list models and check health.
    """

    async def health(self, client: httpx.AsyncClient, url: str) -> dict:
        start = time.monotonic()
        try:
            resp = await client.get(f"{url.rstrip('/')}/api/tags", timeout=10)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            resp.raise_for_status()
            data = resp.json()
            models = [
                {"name": m.get("name", ""), "size_mb": m.get("size", 0) // 1_000_000}
                for m in data.get("models", [])
            ]
            return {"status": "ok", "response_ms": elapsed_ms, "models": models}
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"status": "error", "response_ms": elapsed_ms, "models": []}


class StableDiffusionCppAdapter(BackendAdapter):
    """Adapter for leejet/stable-diffusion.cpp sd-server.

    sd-server exposes an A1111-compatible /sdapi/v1/txt2img endpoint and no
    /health or /v1/models. We probe /sdapi/v1/options and /sdapi/v1/sd-models
    to confirm it's alive and list loaded weights.
    """

    async def health(self, client: httpx.AsyncClient, url: str) -> dict:
        start = time.monotonic()
        base = url.rstrip("/")
        try:
            # Probe /sdapi/v1/options — responds even with no model loaded.
            resp = await client.get(f"{base}/sdapi/v1/options", timeout=10)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            resp.raise_for_status()
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"status": "error", "response_ms": elapsed_ms, "models": []}

        # Server is alive — fetch model list best-effort; empty list is fine.
        models = []
        try:
            mr = await client.get(f"{base}/sdapi/v1/sd-models", timeout=10)
            if mr.status_code == 200:
                data = mr.json()
                models = [
                    {"name": m.get("title", m.get("model_name", "")), "size_mb": 0}
                    for m in (data if isinstance(data, list) else [])
                ]
        except Exception:
            pass

        return {"status": "ok", "response_ms": elapsed_ms, "models": models}


class IOPaintAdapter(BackendAdapter):
    """Adapter for IOPaint (https://github.com/Sanster/IOPaint), the
    lama-cleaner successor used as the self-hosted image-editing backend.

    IOPaint has no /health route; GET /api/v1/server-config responds once the
    server is up and reports the enabled plugins. There is no "model list" in
    the catalog sense (the LaMa erase model + plugins are server-fixed), so we
    surface the loaded model name as a single entry for visibility.
    """

    async def health(self, client: httpx.AsyncClient, url: str) -> dict:
        start = time.monotonic()
        base = url.rstrip("/")
        try:
            resp = await client.get(f"{base}/api/v1/server-config", timeout=10)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            resp.raise_for_status()
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"status": "error", "response_ms": elapsed_ms, "models": []}

        models = []
        try:
            mr = await client.get(f"{base}/api/v1/model", timeout=10)
            if mr.status_code == 200:
                data = mr.json()
                name = data.get("name") if isinstance(data, dict) else None
                if name:
                    models = [{"name": name, "size_mb": 0}]
        except Exception:
            pass

        return {"status": "ok", "response_ms": elapsed_ms, "models": models}


class OpenAICompatAdapter(BackendAdapter):
    """Adapter for OpenAI-compatible APIs (llama.cpp, vLLM).

    Uses GET /health for status, GET /v1/models for model list.
    """

    async def health(self, client: httpx.AsyncClient, url: str) -> dict:
        start = time.monotonic()
        base = url.rstrip("/")
        try:
            resp = await client.get(f"{base}/health", timeout=10)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            resp.raise_for_status()
            models = []
            try:
                model_resp = await client.get(f"{base}/v1/models", timeout=10)
                if model_resp.status_code == 200:
                    models = [
                        {"name": m.get("id", ""), "size_mb": 0}
                        for m in model_resp.json().get("data", [])
                    ]
            except Exception:
                pass
            return {"status": "ok", "response_ms": elapsed_ms, "models": models}
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"status": "error", "response_ms": elapsed_ms, "models": []}


class CloudAPIAdapter(BackendAdapter):
    """Adapter for hosted cloud AI APIs (OpenAI, Anthropic, OpenRouter, Kilo).

    Cloud APIs have no /health endpoint. We probe GET /models without auth:
    - 2xx  = online (public model list)
    - 401/403 = online (API is responding, just needs a key)
    - anything else = error

    ``url`` must include the versioned path prefix (e.g. ``https://api.openai.com/v1``).
    The wizard pre-fills correct URLs from ``DEFAULT_URLS``; bare base URLs will 404.
    """

    async def health(self, client: httpx.AsyncClient, url: str) -> dict:
        start = time.monotonic()
        base = url.rstrip("/")
        try:
            resp = await client.get(f"{base}/models", timeout=10)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if resp.status_code in (200, 401, 403):
                models = []
                if resp.status_code == 200:
                    try:
                        models = [
                            {"name": m.get("id", ""), "size_mb": 0}
                            for m in resp.json().get("data", [])
                        ]
                    except Exception:
                        pass
                return {"status": "ok", "response_ms": elapsed_ms, "models": models}
            return {"status": "error", "response_ms": elapsed_ms, "models": []}
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"status": "error", "response_ms": elapsed_ms, "models": []}


# Type aliases for backwards compatibility with tests
RkLlamaAdapter = OllamaCompatAdapter
OllamaAdapter = OllamaCompatAdapter
LlamaCppAdapter = OpenAICompatAdapter
VllmAdapter = OpenAICompatAdapter

ExoAdapter = OpenAICompatAdapter  # Exo exposes OpenAI-compatible API

_ADAPTERS: dict[str, BackendAdapter] = {
    "rkllama": OllamaCompatAdapter(),
    "ollama": OllamaCompatAdapter(),
    "llama-cpp": OpenAICompatAdapter(),
    "vllm": OpenAICompatAdapter(),
    "exo": OpenAICompatAdapter(),
    "mlx": OpenAICompatAdapter(),
    "openai": CloudAPIAdapter(),
    "anthropic": CloudAPIAdapter(),
    "openrouter": CloudAPIAdapter(),
    "kilocode": CloudAPIAdapter(),
    "deepseek": CloudAPIAdapter(),
    "openai-compatible": CloudAPIAdapter(),
    "sd-cpp": StableDiffusionCppAdapter(),
    "iopaint": IOPaintAdapter(),
    # flux-fill is served behind an A1111-compatible image server in practice;
    # reuse the sd-cpp probe shape (it answers /sdapi/v1/options).
    "flux-fill": StableDiffusionCppAdapter(),
}


def get_adapter(backend_type: str) -> BackendAdapter:
    adapter = _ADAPTERS.get(backend_type)
    if not adapter:
        raise ValueError(f"Unknown backend type: '{backend_type}'")
    return adapter


async def check_backend_health(client: httpx.AsyncClient, backend: dict) -> dict:
    """Health-check a single backend, never raising.

    A misconfigured backend (unknown type, missing url, adapter that
    raises) must not be allowed to take down the entire /api/backends
    response. Return a structured error envelope so the caller can
    aggregate and the UI can show the bad entry as offline with a
    reason rather than 500-ing the whole endpoint.
    """
    backend_type = backend.get("type", "")
    try:
        adapter = get_adapter(backend_type)
    except ValueError as exc:
        return {
            "healthy": False,
            "status": "unsupported",
            "error": str(exc),
            "name": backend.get("name", ""),
            "type": backend_type,
            "priority": backend.get("priority", 99),
            "models": [],
        }
    try:
        result = await adapter.health(client, backend["url"])
    except Exception as exc:  # noqa: BLE001 — adapter integrity is best-effort
        return {
            "healthy": False,
            "status": "error",
            "error": str(exc),
            "name": backend.get("name", ""),
            "type": backend_type,
            "priority": backend.get("priority", 99),
            "models": [],
        }
    return {**result, "name": backend.get("name", ""), "type": backend_type, "priority": backend.get("priority", 99)}
