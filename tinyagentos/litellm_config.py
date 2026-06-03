"""Pure config-generation logic for the LiteLLM proxy.

Contains the functions that build a LiteLLM configuration dict from the
TinyAgentOS backend list.  Process-management concerns (subprocess, PIDs,
start/stop) live in ``tinyagentos.llm_proxy``; import direction is strictly
one-way: ``llm_proxy`` imports from here, never the reverse.
"""
from __future__ import annotations

import logging

import httpx

from tinyagentos.providers import CHAT_BACKEND_TYPE_MAP, CLOUD_TYPES as CLOUD_BACKEND_TYPES, NEEDS_API_BASE_TYPES

logger = logging.getLogger(__name__)

# Canonical alias the deployer injects into agent containers as
# TAOS_EMBEDDING_MODEL. Agents that want an embedding call this name and
# LiteLLM routes it to whatever concrete embedding model the host has.
# See docs/design/framework-agnostic-runtime.md.
EMBEDDING_ALIAS = "taos-embedding-default"

# Shared master key used for LiteLLM auth. When LiteLLM runs without a
# Postgres DB it cannot issue per-agent virtual keys, so every client
# (openclaw gateway, host-side key admin calls) authenticates with this
# single value. Written into the config yaml AND exported into the
# litellm subprocess env so whichever source LiteLLM reads first agrees.
TAOS_LITELLM_MASTER_KEY = "sk-taos-master"


def _is_embedding_model(name: str) -> bool:
    """Classify a model name as embedding vs chat.

    Heuristic match against the common embedding-model families. LiteLLM
    has no reliable way to ask a backend "is this model an embedder?" so
    we infer from the slug. Known families: anything containing ``embed``
    (nomic-embed, qwen3-embedding, mxbai-embed-large), plus the BGE, GTE,
    E5, and arctic-embed families whose canonical names don't always
    include the word.

    Rerankers include ``rerank`` in the slug and are always excluded —
    they're a different endpoint shape that LiteLLM doesn't front today.
    """
    n = name.lower()
    if "rerank" in n:
        return False
    if "embed" in n:
        return True
    # Known embedding families where the slug doesn't include "embed"
    # verbatim. Match on dash-separated prefixes so "bge-something" hits
    # but a hypothetical "bgem-chat" doesn't.
    for prefix in ("bge-", "gte-", "e5-", "arctic-"):
        if n.startswith(prefix):
            return True
    return False


def _discover_ollama_models(url: str, timeout: float = 2.0) -> list[str]:
    """Probe an ollama-compatible backend for its loaded model names.

    Returns ``[]`` on any failure (backend down, network error, schema
    drift) — the caller treats an empty list as "no models to auto-wire"
    and falls back to the ``default`` alias. Kept sync because
    ``generate_litellm_config`` is called from both sync and async
    contexts and adding a timeout-bounded probe here is simpler than
    plumbing async all the way through the subscriber chain.
    """
    try:
        resp = httpx.get(f"{url.rstrip('/')}/api/tags", timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception as exc:
        logger.debug("ollama model probe at %s failed: %s", url, exc)
        return []


def _local_backend_models_from_registry(
    backend: dict, registry
) -> list[str]:
    """Find every installed model that can run on this local backend.

    auto_register_from_manifest names backends ``local-<service-id>`` (e.g.
    ``local-rk-llama-cpp`` for the rk-llama.cpp service). Each model
    manifest declares ``variants[].requires.backends[].id`` listing the
    runtime ids it supports. Cross-reference the two: every installed
    model whose variants point at this backend's service id is a model
    we can route requests to via this backend's URL.

    Returns a deduplicated list of manifest ids — the LiteLLM model_name
    aliases we want to create per local backend.
    """
    if registry is None:
        return []

    name = backend.get("name", "")
    if not name.startswith("local-"):
        return []
    service_id = name[len("local-"):]
    if not service_id:
        return []

    try:
        installed_rows = registry.list_installed()
    except Exception:  # noqa: BLE001
        return []
    installed_ids = {row.get("id") for row in installed_rows if row.get("id")}

    matched: list[str] = []
    for manifest_id in sorted(installed_ids):
        manifest = registry.get(manifest_id) if hasattr(registry, "get") else None
        if not manifest or getattr(manifest, "type", None) != "model":
            continue
        variants = getattr(manifest, "variants", None) or []
        for v in variants:
            if not isinstance(v, dict):
                continue
            for req in (v.get("requires", {}) or {}).get("backends", []) or []:
                if isinstance(req, dict) and req.get("id") == service_id:
                    matched.append(manifest_id)
                    break
            else:
                continue
            break
    return matched


def generate_litellm_config(
    backends: list[dict],
    default_model: str = "default",
    *,
    registry=None,
) -> dict:
    """Generate LiteLLM config from TinyAgentOS backend list.

    Emits two kinds of model_list entries:

    - Chat entries under the ``default`` alias, routing through each
      ollama-compatible backend. Frameworks using the OpenAI SDK against
      ``OPENAI_BASE_URL`` hit these with ``model="default"``.
    - Embedding entries discovered by probing each ollama-compatible
      backend's ``/api/tags``. The first embedding model found is also
      aliased as ``taos-embedding-default`` so the deployer can inject
      a stable model name into every agent container via
      ``TAOS_EMBEDDING_MODEL``. Entries carry
      ``model_info.mode: embedding`` so LiteLLM routes
      ``/v1/embeddings`` requests to them instead of chat.
    """
    model_list = []
    sorted_backends = sorted(backends, key=lambda b: b.get("priority", 99))
    aliased_embedding_claimed = False

    for backend in sorted_backends:
        backend_type = backend.get("type", "ollama")
        # Loudly flag cloud-type entries missing url/models so the next
        # round of silent drops (the kilocode regression) is visible in
        # logs instead of surfacing only as a broken agent much later.
        if backend_type in CLOUD_BACKEND_TYPES:
            if not backend.get("url") or not backend.get("models"):
                logger.warning(
                    "backend %s skipped — missing url or models (type=%s)",
                    backend.get("name"),
                    backend_type,
                )
        prefix = CHAT_BACKEND_TYPE_MAP.get(backend_type, "openai")
        url = backend.get("url", "").rstrip("/")
        model_name = backend.get("model", "default")

        litellm_params = {
            "model": f"{prefix}/{model_name}",
        }

        # Set api_base for local/self-hosted backends and openai-compatible
        if backend_type in NEEDS_API_BASE_TYPES:
            litellm_params["api_base"] = url

        # API key from secrets reference
        if backend.get("api_key_secret"):
            litellm_params["api_key"] = f"os.environ/{backend['api_key_secret']}"
        elif backend.get("api_key"):
            litellm_params["api_key"] = backend["api_key"]

        # For cloud backends with a declared models list, register each model
        # by its exact id so agents can route requests to a specific model.
        if backend_type in CLOUD_BACKEND_TYPES:
            declared_models: list[str] = []
            for m in backend.get("models") or []:
                if isinstance(m, dict):
                    mid = m.get("id") or m.get("name") or ""
                else:
                    mid = str(m)
                if mid:
                    declared_models.append(mid)

            for model_id in declared_models:
                per_model_params: dict = {
                    "model": f"{prefix}/{model_id}",
                }
                # kilocode isn't a native LiteLLM provider — must set api_base
                if backend_type == "kilocode":
                    per_model_params["api_base"] = url
                elif url and backend_type not in ("openai", "anthropic"):
                    # For openrouter and other pass-through types, set api_base
                    # when an explicit url is provided
                    per_model_params["api_base"] = url
                # API key resolution
                if backend.get("api_key_secret"):
                    per_model_params["api_key"] = f"os.environ/{backend['api_key_secret']}"
                elif backend.get("api_key"):
                    per_model_params["api_key"] = backend["api_key"]
                model_list.append({
                    "model_name": model_id,
                    "litellm_params": per_model_params,
                    "metadata": {
                        "priority": backend.get("priority", 99),
                        "backend_name": backend.get("name", ""),
                    },
                })

        model_list.append({
            "model_name": default_model,
            "litellm_params": litellm_params,
            "metadata": {
                "priority": backend.get("priority", 99),
                "backend_name": backend.get("name", ""),
            },
        })

        # For local backends (name "local-<service-id>" from
        # auto_register_from_manifest), also register every installed
        # model that targets this backend as its own LiteLLM model_name.
        # Independent of cloud/non-cloud: openai-compatible is in
        # CLOUD_BACKEND_TYPES even when it's actually a local llama-server
        # (e.g. rk-llama.cpp). Without this, an agent picker that says
        # "use gemma-4-e2b-gguf" → LiteLLM call with that model_name →
        # 400 because no such alias is registered.
        backend_name = backend.get("name", "")
        if backend_name.startswith("local-") and url:
            for manifest_id in _local_backend_models_from_registry(backend, registry):
                # Skip if already added (e.g. cloud per-model loop above
                # already registered it under the same name).
                if any(e.get("model_name") == manifest_id for e in model_list):
                    continue
                per_model_params: dict = {
                    "model": f"{prefix}/{manifest_id}",
                    "api_base": url,
                }
                if backend.get("api_key_secret"):
                    per_model_params["api_key"] = f"os.environ/{backend['api_key_secret']}"
                elif backend.get("api_key"):
                    per_model_params["api_key"] = backend["api_key"]
                model_list.append({
                    "model_name": manifest_id,
                    "litellm_params": per_model_params,
                    "metadata": {
                        "priority": backend.get("priority", 99),
                        "backend_name": backend_name,
                        "source": "local-installed",
                    },
                })

        # Auto-discover embedding models on ollama-compatible backends and
        # register each as its own LiteLLM entry. The first embedding model
        # found across all backends also claims the stable
        # ``taos-embedding-default`` alias so containers have one name to
        # inject regardless of which rkllama box holds the model.
        if backend_type in ("ollama", "rkllama"):
            discovered = _discover_ollama_models(url)
            for discovered_name in discovered:
                if not _is_embedding_model(discovered_name):
                    continue
                embed_params = {
                    "model": f"ollama/{discovered_name}",
                    "api_base": url,
                }
                model_list.append({
                    "model_name": discovered_name,
                    "litellm_params": embed_params,
                    "model_info": {"mode": "embedding"},
                    "metadata": {
                        "priority": backend.get("priority", 99),
                        "backend_name": backend.get("name", ""),
                    },
                })
                if not aliased_embedding_claimed:
                    model_list.append({
                        "model_name": EMBEDDING_ALIAS,
                        "litellm_params": dict(embed_params),
                        "model_info": {"mode": "embedding"},
                        "metadata": {
                            "priority": backend.get("priority", 99),
                            "backend_name": backend.get("name", ""),
                            "aliases": discovered_name,
                        },
                    })
                    aliased_embedding_claimed = True

    return {
        "model_list": model_list,
        "router_settings": {
            "routing_strategy": "simple-shuffle",
            "num_retries": 2,
            "timeout": 120,
            "enable_pre_call_checks": False,
        },
        "general_settings": {
            "master_key": TAOS_LITELLM_MASTER_KEY,
            "background_health_checks": False,
            "disable_spend_logs": True,
        },
        # LiteLLM's proxy reads custom logger classes from
        # ``litellm_settings.callbacks``. The loader (get_instance_fn) resolves
        # the dotted path relative to the config file's directory — so the
        # sibling ``taos_callback.py`` shim written by ``write_config`` below
        # imports our installed module and re-exports the instance.
        "litellm_settings": {
            "callbacks": "taos_callback.proxy_handler_instance",
        },
    }
