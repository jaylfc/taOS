"""Canonical provider type definitions — single source of truth.

Adding a new provider type means touching this file ONLY.

  ALL_TYPES          — every valid backend type
  CLOUD_TYPES        — cloud providers (require API key, serve multiple models)
  LOCAL_TYPES        — everything that isn't cloud (derived: ALL_TYPES - CLOUD_TYPES)
  BACKEND_TYPE_MAP   — TinyAgentOS type → LiteLLM model prefix
  CHAT_BACKEND_TYPE_MAP — variant for chat routing (ollama_chat vs ollama)

The frontend fetches these from GET /api/providers/types at boot.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Type sets
# ---------------------------------------------------------------------------

ALL_TYPES: set[str] = {
    # -- local LLM backends --
    "rkllama",
    "hailo-ollama",
    "ollama",
    "llama-cpp",
    "llama-swap",
    "vllm",
    "exo",
    "mlx",
    "lmstudio",
    # -- cloud LLM providers --
    "openai",
    "anthropic",
    "openrouter",
    "kilocode",
    "deepseek",
    "nous",
    "openai-compatible",
    # -- local image-generation backends --
    "sd-cpp",
    # -- local image-editing backends (erase/inpaint/outpaint/bg/upscale) --
    "iopaint",
    "flux-fill",
}

CLOUD_TYPES: set[str] = {
    "openai",
    "anthropic",
    "openrouter",
    "kilocode",
    "deepseek",
    "nous",
    "openai-compatible",
}

LOCAL_TYPES: set[str] = ALL_TYPES - CLOUD_TYPES

# Backends where LiteLLM must receive an explicit api_base (self-hosted or
# user-supplied endpoints). Cloud providers discover their base URL from the
# LiteLLM provider registry.
IMAGE_GEN_TYPES: set[str] = {"sd-cpp", "iopaint", "flux-fill"}
NEEDS_API_BASE_TYPES: set[str] = (LOCAL_TYPES - IMAGE_GEN_TYPES) | {"openai-compatible"}

# Inference base path per backend type. LiteLLM's ``openai`` provider appends
# ``/chat/completions`` to whatever api_base it is given, and the taOS gateway
# appends ``/chat/completions`` / ``/embeddings`` the same way, so a backend
# whose OpenAI surface lives under a version prefix must carry that prefix in
# api_base. Measured against a live LM Studio on :1234: POST /chat/completions
# answers HTTP 200 with {"error":"Unexpected endpoint or method"} while
# /v1/chat/completions is the real endpoint, so a bare base fails silently
# rather than with a 404. The worker's *probe* URL stays unversioned: it builds
# ``<url>/v1/models`` itself.
INFERENCE_BASE_SUFFIX: dict[str, str] = {
    "lmstudio": "/v1",
}


def inference_api_base(backend_type: str, url: str) -> str:
    """api_base for *backend_type* at *url*, with any required version prefix.

    Idempotent: a url that already ends with the suffix is returned unchanged.
    """
    base = (url or "").rstrip("/")
    suffix = INFERENCE_BASE_SUFFIX.get(backend_type, "")
    if suffix and not base.endswith(suffix):
        return f"{base}{suffix}"
    return base


# ---------------------------------------------------------------------------
# LiteLLM routing maps
# ---------------------------------------------------------------------------

BACKEND_TYPE_MAP: dict[str, str] = {
    "ollama": "ollama",
    "rkllama": "ollama",  # rkllama is ollama-compatible on /api/embed too
    "hailo-ollama": "ollama",  # hailo-ollama is ollama-compatible on /api/embed too
    "llama-cpp": "openai",
    "llama-swap": "openai",
    "vllm": "openai",
    "exo": "openai",
    "mlx": "openai",
    "openai": "openai",
    "anthropic": "anthropic",
    "openrouter": "openrouter",
    "kilocode": "openai",  # kilocode is OpenAI-compatible; api_base set explicitly
    "deepseek": "deepseek",  # native LiteLLM provider; api_base set to official base
    "nous": "openai",  # Nous Portal is OpenAI-compatible; api_base set explicitly
    "openai-compatible": "openai",  # user-supplied OpenAI-compatible endpoint
    "lmstudio": "openai",  # LM Studio serves an OpenAI-compatible API on :1234
}

# Chat prefix is different from the embedding prefix for ollama-compat
# backends: ollama_chat uses /api/chat, plain ollama uses /api/generate and
# /api/embed. LiteLLM needs the right one to route requests correctly.
CHAT_BACKEND_TYPE_MAP: dict[str, str] = {
    **BACKEND_TYPE_MAP,
    "ollama": "ollama_chat",
    "rkllama": "ollama_chat",
    "hailo-ollama": "ollama_chat",
}
