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
    "ollama",
    "llama-cpp",
    "vllm",
    "exo",
    "mlx",
    # -- cloud LLM providers --
    "openai",
    "anthropic",
    "openrouter",
    "kilocode",
    "deepseek",
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
    "openai-compatible",
}

LOCAL_TYPES: set[str] = ALL_TYPES - CLOUD_TYPES

# Backends where LiteLLM must receive an explicit api_base (self-hosted or
# user-supplied endpoints). Cloud providers discover their base URL from the
# LiteLLM provider registry.
IMAGE_GEN_TYPES: set[str] = {"sd-cpp", "iopaint", "flux-fill"}
NEEDS_API_BASE_TYPES: set[str] = (LOCAL_TYPES - IMAGE_GEN_TYPES) | {"openai-compatible"}

# ---------------------------------------------------------------------------
# LiteLLM routing maps
# ---------------------------------------------------------------------------

BACKEND_TYPE_MAP: dict[str, str] = {
    "ollama": "ollama",
    "rkllama": "ollama",  # rkllama is ollama-compatible on /api/embed too
    "llama-cpp": "openai",
    "vllm": "openai",
    "exo": "openai",
    "mlx": "openai",
    "openai": "openai",
    "anthropic": "anthropic",
    "openrouter": "openrouter",
    "kilocode": "openai",  # kilocode is OpenAI-compatible; api_base set explicitly
    "deepseek": "deepseek",  # native LiteLLM provider; api_base set to official base
    "openai-compatible": "openai",  # user-supplied OpenAI-compatible endpoint
}

# Chat prefix is different from the embedding prefix for ollama-compat
# backends: ollama_chat uses /api/chat, plain ollama uses /api/generate and
# /api/embed. LiteLLM needs the right one to route requests correctly.
CHAT_BACKEND_TYPE_MAP: dict[str, str] = {
    **BACKEND_TYPE_MAP,
    "ollama": "ollama_chat",
    "rkllama": "ollama_chat",
}
