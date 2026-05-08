from __future__ import annotations

import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from tinyagentos.backend_adapters import get_adapter
from tinyagentos.config import save_config_locked, VALID_BACKEND_TYPES
from tinyagentos.lifecycle_manager import LifecycleManager
from tinyagentos.llm_proxy import TAOS_LITELLM_MASTER_KEY

logger = logging.getLogger(__name__)

router = APIRouter()

# Provider categories used by the UI to group entries. The backend
# doesn't care about category for routing — it's purely display metadata.
CLOUD_TYPES = {"openai", "anthropic", "openrouter", "kilocode", "openai-compatible"}

# Defaults applied per-type when the Add Provider form doesn't supply
# them. Covers the case where the UI collects just api_key + name and
# relies on the server to know the canonical base URL.
PROVIDER_URL_DEFAULTS: dict[str, str] = {
    "kilocode": "https://api.kilo.ai/api/gateway",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

# Seed model list for cloud providers that don't expose an openly-listable
# /v1/models endpoint — used as a last-resort fallback when /models probing
# also fails. kilocode ships a documented "auto" alias that always routes
# so we keep that as a safety net.
PROVIDER_DEFAULT_MODELS: dict[str, list[dict]] = {
    "kilocode": [{"id": "kilo-auto/free"}],
}


async def _resolve_backend_secrets(
    app_state, backends: list[dict]
) -> dict[str, str]:
    """Build a name→value map of every ``api_key_secret`` referenced
    from ``backends``. Used to refresh the LiteLLM subprocess env on
    reload so newly-added/rotated provider keys take effect without
    a full app restart."""
    secrets_store = getattr(app_state, "secrets", None)
    if secrets_store is None:
        return {}
    out: dict[str, str] = {}
    for backend in backends:
        name = backend.get("api_key_secret")
        if not name or name in out:
            continue
        try:
            rec = await secrets_store.get(name)
        except Exception as exc:
            logger.warning("provider reload: secret lookup %s failed: %s", name, exc)
            continue
        if rec and rec.get("value"):
            out[name] = rec["value"]
    return out


async def _discover_provider_models(
    base_url: str, api_key: str | None, timeout: float = 5.0,
) -> list[dict]:
    """Probe ``{base_url}/models`` for an OpenAI-shaped model list.

    Returns a list of ``{"id": ...}`` dicts on success, empty list on any
    failure. Works for openai, anthropic, openrouter, kilocode — they all
    expose an OpenAI-compatible models endpoint that returns
    ``{"data": [{"id": "..."}]}``. Provider-agnostic: no per-type branching
    so a new cloud provider with the same shape just works.
    """
    url = f"{base_url.rstrip('/')}/models"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.warning(
                "provider model discovery at %s returned HTTP %d",
                url, resp.status_code,
            )
            return []
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return []
        ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
        return [{"id": mid} for mid in ids]
    except Exception as exc:
        logger.warning("provider model discovery at %s failed: %s", url, exc)
        return []


# Short TTL for the /api/providers/models cache. Opens of the agent-
# creation dialog in quick succession (e.g. user closes + reopens) share
# the probe cost. A refresh=true query string always bypasses it.
MODELS_CACHE_TTL_SECONDS = 60.0

# Per-provider probe timeout when fanning out in _refresh_all_cloud_backends
# — short enough that one dead cloud endpoint doesn't hold up the whole
# dialog, long enough that a slow first request (TLS, DNS) still lands.
_REFRESH_PROBE_TIMEOUT = 3.0


async def _resolve_api_key_for_backend(app_state, backend: dict) -> str | None:
    """Resolve ``backend.api_key_secret`` against the secrets store, falling
    back to the inline ``api_key`` field. Returns ``None`` when neither is
    configured or lookup fails."""
    secret_name = backend.get("api_key_secret")
    if secret_name:
        secrets_store = getattr(app_state, "secrets", None)
        if secrets_store is not None:
            try:
                rec = await secrets_store.get(secret_name)
                if rec and rec.get("value"):
                    return rec["value"]
            except Exception as exc:
                logger.warning(
                    "secret lookup for %s failed during backend refresh: %s",
                    secret_name, exc,
                )
    return backend.get("api_key")


async def _refresh_backend(
    app_state, backend: dict, timeout: float = _REFRESH_PROBE_TIMEOUT,
) -> dict:
    """Re-probe a single cloud backend's ``/models`` endpoint and update
    its declared models list in place. No-op for non-cloud types or for
    entries missing a URL. Returns the (possibly updated) backend dict.
    """
    if backend.get("type") not in CLOUD_TYPES:
        return backend
    url = backend.get("url")
    if not url:
        return backend
    api_key = await _resolve_api_key_for_backend(app_state, backend)
    discovered = await _discover_provider_models(url, api_key, timeout=timeout)
    if discovered:
        backend["models"] = discovered
    return backend


async def _refresh_all_cloud_backends(app_state, config, proxy) -> int:
    """Re-probe every cloud backend in ``config.backends`` in parallel,
    update their ``models`` lists, persist the config, and SIGHUP LiteLLM
    so the new ``model_list`` takes effect. Returns the number of cloud
    backends that were probed.
    """
    cloud = [b for b in config.backends if b.get("type") in CLOUD_TYPES]
    if not cloud:
        return 0
    await asyncio.gather(
        *(_refresh_backend(app_state, b) for b in cloud),
        return_exceptions=True,
    )
    await save_config_locked(config, config.config_path)
    if proxy and proxy.is_running():
        resolved = await _resolve_backend_secrets(app_state, config.backends)
        await proxy.reload_config(config.backends, secrets=resolved)
    return len(cloud)


async def _fetch_litellm_models(proxy) -> list[dict]:
    """Fetch ``/v1/models`` from the running LiteLLM proxy using the master
    key. Returns the raw ``data`` list (list of dicts) or ``[]`` on any
    failure. LiteLLM's response shape is OpenAI-compatible:
    ``{"data": [{"id": "...", ...}], "object": "list"}``.
    """
    if not proxy or not proxy.is_running():
        return []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{proxy.url}/v1/models",
                headers={"Authorization": f"Bearer {TAOS_LITELLM_MASTER_KEY}"},
            )
        if resp.status_code != 200:
            logger.warning(
                "LiteLLM /v1/models returned HTTP %d: %.200s",
                resp.status_code, resp.text,
            )
            return []
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("LiteLLM /v1/models fetch failed: %s", exc)
        return []


def _categorise(provider: dict) -> str:
    """Return the UI category for a provider entry.

    - ``cloud`` for managed API providers (OpenAI, Anthropic, etc.)
    - ``network`` for backends reported by a remote cluster worker
    - ``local`` for controller-local configured backends
    """
    if provider.get("source", "").startswith("worker:"):
        return "network"
    if provider.get("type", "") in CLOUD_TYPES:
        return "cloud"
    return "local"


class ProviderCreate(BaseModel):
    name: str
    type: str
    url: str | None = None
    priority: int = 99
    api_key_secret: str | None = None
    models: list[dict] | list[str] | None = None

class ProviderTest(BaseModel):
    type: str
    url: str

class ProviderPatch(BaseModel):
    enabled: bool | None = None
    auto_manage: bool | None = None
    keep_alive_minutes: int | None = None
    # Routing-affecting fields — when any are set, PATCH re-probes the
    # provider's /models endpoint and reloads LiteLLM so the new URL / key
    # is live without needing a full app restart.
    url: str | None = None
    api_key_secret: str | None = None
    api_key: str | None = None

class ProviderStop(BaseModel):
    force: bool = False

@router.get("/api/providers")
async def list_providers(request: Request):
    """List every provider the controller knows about.

    Combines three sources into one unified list with a ``source`` and
    ``category`` tag on each entry:

    - **Controller-local** backends from ``config.backends`` — user adds
      these via the Add Provider form. Includes cloud providers (OpenAI,
      Anthropic) and on-host local backends (rkllama on the Pi, etc.).
    - **Worker-reported** backends from ``cluster.aggregate_catalog()``
      — any online worker's live backends (ollama on the Fedora worker,
      llama-cpp on a gaming PC, etc.). These aren't in the config; they
      appear automatically when the worker registers and heartbeats.

    The UI groups by ``category`` (local / network / cloud) and can show
    a worker host badge when ``category == "network"``.
    """
    config = request.app.state.config
    http_client = request.app.state.http_client
    providers = []

    # 1) Controller-local providers (live health probe)
    # Only expose backends with a recognised AI type — entries with an empty
    # or unrecognised type are auxiliary services (Home Assistant, Gitea, etc.)
    # and belong in a future Services app, not here.
    catalog = getattr(request.app.state, "backend_catalog", None)
    for backend in [b for b in config.backends if b.get("type") in VALID_BACKEND_TYPES]:
        status = "unknown"
        response_ms = 0
        models = []
        try:
            adapter = get_adapter(backend["type"])
            result = await adapter.health(http_client, backend["url"])
            status = result.get("status", "error")
            response_ms = result.get("response_ms", 0)
            models = result.get("models", [])
        except Exception:
            status = "error"
        # Cloud / openai-compatible adapters call /models without auth.
        # That returns 401 for any auth-required endpoint (LiteLLM proxy,
        # private OpenAI gateway, etc.) and we end up with an empty
        # models list even though the user gave us a valid key. Re-probe
        # with the resolved api_key when the auth-less path returned
        # nothing — issue #356 (johny: 'LiteLLM provider connects but
        # picker shows 0 models').
        if (
            status == "ok"
            and not models
            and backend.get("type") in ("openai", "anthropic", "openrouter", "kilocode", "openai-compatible")
        ):
            api_key = await _resolve_api_key_for_backend(request.app.state, backend)
            if api_key:
                discovered = await _discover_provider_models(backend["url"], api_key)
                if discovered:
                    models = [{"name": m.get("id", ""), "size_mb": 0} for m in discovered]
        lifecycle_state = catalog.get_lifecycle_state(backend["name"]) if catalog else "running"
        entry = {
            **backend,
            "status": status,
            "response_ms": response_ms,
            "models": models,
            "source": "local",
            "lifecycle_state": lifecycle_state,
            "enabled": backend.get("enabled", True),
        }
        entry["category"] = _categorise(entry)
        # Cloud providers don't participate in lifecycle management
        if entry["category"] != "cloud":
            entry["auto_manage"] = backend.get("auto_manage", False)
            entry["keep_alive_minutes"] = backend.get("keep_alive_minutes", 10)
        providers.append(entry)

    # 2) Worker-reported remote backends (from heartbeats — no extra
    #    probe, the worker already vouches for their status).
    cluster = getattr(request.app.state, "cluster_manager", None)
    if cluster is not None:
        try:
            agg = cluster.aggregate_catalog()
            for b in agg.get("backends", []):
                worker_name = b.get("worker", "")
                entry = {
                    # Prefix the name with worker for uniqueness across cluster
                    "name": f"{worker_name}/{b.get('name', 'backend')}",
                    "type": b.get("type", ""),
                    "url": b.get("url", ""),
                    "priority": b.get("priority", 99),
                    "status": b.get("status", "online"),
                    "response_ms": b.get("response_ms", 0),
                    "models": b.get("models", []),
                    "source": f"worker:{worker_name}",
                    "worker_name": worker_name,
                    "worker_url": b.get("worker_url", ""),
                    "worker_platform": b.get("worker_platform", ""),
                }
                entry["category"] = _categorise(entry)
                providers.append(entry)
        except Exception:
            # Cluster manager not ready or misbehaving — don't fail the
            # whole endpoint, just skip remote backends.
            pass

    return providers

@router.post("/api/providers/test")
async def test_provider(request: Request, body: ProviderTest):
    """Test connectivity to a provider. Auto-starts if stopped and auto_manage is on."""
    if not body.url:
        return JSONResponse({"error": "URL required"}, status_code=400)
    if body.type not in VALID_BACKEND_TYPES:
        return JSONResponse({"error": f"Invalid type. Must be one of: {sorted(VALID_BACKEND_TYPES)}"}, status_code=400)

    # Auto-start if the provider is stopped and auto_manage is enabled
    config = request.app.state.config
    backend = next(
        (b for b in config.backends if b.get("url") == body.url and b.get("type") == body.type),
        None,
    )
    if backend and backend.get("auto_manage") and backend.get("enabled", True):
        _catalog = getattr(request.app.state, "backend_catalog", None)
        _lifecycle = getattr(request.app.state, "lifecycle_manager", None)
        if _catalog and _lifecycle:
            if _catalog.get_lifecycle_state(backend["name"]) == "stopped":
                try:
                    await _lifecycle.start(backend["name"])
                except Exception as e:
                    return JSONResponse({"reachable": False, "error": f"Auto-start failed: {e}"})

    try:
        adapter = get_adapter(body.type)
        http_client = request.app.state.http_client
        result = await adapter.health(http_client, body.url)
        return {
            "reachable": result["status"] == "ok",
            "response_ms": result.get("response_ms", 0),
            "models": result.get("models", []),
        }
    except Exception as e:
        return {"reachable": False, "error": str(e)}

@router.post("/api/providers")
async def add_provider(request: Request, body: ProviderCreate):
    """Add a new provider to the configuration.

    Only controller-local providers can be added this way (cloud APIs
    and custom on-host / network endpoints). Worker-reported backends
    auto-populate from heartbeats and don't need to be added manually.
    """
    config = request.app.state.config
    if any(b["name"] == body.name for b in config.backends):
        return JSONResponse({"error": f"Provider '{body.name}' already exists"}, status_code=409)
    entry = body.model_dump(exclude_none=True)
    # Auto-fill canonical URL so a minimal Add Provider form (name +
    # api_key) still produces a routable entry. Without this, a cloud
    # provider saved without `url` never lands in LiteLLM's model_list.
    if not entry.get("url") and entry.get("type") in PROVIDER_URL_DEFAULTS:
        entry["url"] = PROVIDER_URL_DEFAULTS[entry["type"]]
    if not entry.get("url"):
        return JSONResponse({"error": "URL required for this provider type"}, status_code=400)
    # Reject (type, url) duplicates too — johny on #312 ended up with two
    # rkllama provider entries (different names, same type+url) because
    # the existing check only looked at name. Two providers pointing at
    # the same URL is never useful and confuses the picker / lifecycle
    # logic.
    if any(
        b.get("type") == entry.get("type") and b.get("url") == entry.get("url")
        for b in config.backends
    ):
        return JSONResponse(
            {"error": (
                f"A provider for {entry.get('type')!r} at {entry.get('url')!r} "
                "already exists. Edit it from the Providers list rather than adding "
                "another."
            )},
            status_code=409,
        )
    # Auto-discover models for cloud providers when the caller didn't
    # supply any. Keeps the path generic across openai/anthropic/
    # openrouter/kilocode — each exposes an OpenAI-shaped {url}/models.
    # On probe failure we fall back to the per-type seed list (if any)
    # so the entry still registers at least one routable model. The
    # entry is saved either way so the user can refine in Settings.
    if not entry.get("models") and entry.get("type") in CLOUD_TYPES:
        api_key = None
        secret_name = entry.get("api_key_secret")
        if secret_name:
            secrets = getattr(request.app.state, "secrets", None)
            if secrets is not None:
                try:
                    rec = await secrets.get(secret_name)
                    if rec:
                        api_key = rec.get("value")
                except Exception as exc:
                    logger.warning(
                        "secret lookup for %s failed during provider add: %s",
                        secret_name, exc,
                    )
        discovered = await _discover_provider_models(entry["url"], api_key)
        if discovered:
            entry["models"] = discovered
        elif entry.get("type") in PROVIDER_DEFAULT_MODELS:
            entry["models"] = list(PROVIDER_DEFAULT_MODELS[entry["type"]])
    config.backends.append(entry)
    await save_config_locked(config, config.config_path)
    # Reconfigure LLM proxy if running
    proxy = getattr(request.app.state, "llm_proxy", None)
    if proxy and proxy.is_running():
        resolved = await _resolve_backend_secrets(request.app.state, config.backends)
        await proxy.reload_config(config.backends, secrets=resolved)
    return {"status": "added", "name": body.name}

@router.patch("/api/providers/{name}")
async def patch_provider(request: Request, name: str, body: ProviderPatch):
    """Update a provider's config. Re-probes ``/models`` and reloads
    LiteLLM when routing-affecting fields (url/api_key_secret/api_key)
    change, so the new config is live without an app restart.

    Also invalidates the models cache so the next ``/api/providers/models``
    call re-reads LiteLLM's fresh model_list.
    """
    config = request.app.state.config
    backend = next((b for b in config.backends if b.get("name") == name), None)
    if backend is None:
        return JSONResponse({"error": f"Provider '{name}' not found"}, status_code=404)
    if body.enabled is not None:
        backend["enabled"] = body.enabled
    if body.auto_manage is not None:
        backend["auto_manage"] = body.auto_manage
    if body.keep_alive_minutes is not None:
        backend["keep_alive_minutes"] = body.keep_alive_minutes
    routing_changed = False
    if body.url is not None:
        backend["url"] = body.url
        routing_changed = True
    if body.api_key_secret is not None:
        backend["api_key_secret"] = body.api_key_secret
        routing_changed = True
    if body.api_key is not None:
        backend["api_key"] = body.api_key
        routing_changed = True
    # Re-probe cloud providers on any PATCH — a no-op change (e.g.
    # `enabled=true` that was already true) still validates the current
    # model list is fresh and costs one cheap /models call.
    if backend.get("type") in CLOUD_TYPES:
        await _refresh_backend(request.app.state, backend)
    await save_config_locked(config, config.config_path)
    proxy = getattr(request.app.state, "llm_proxy", None)
    if proxy and proxy.is_running():
        resolved = await _resolve_backend_secrets(request.app.state, config.backends)
        await proxy.reload_config(config.backends, secrets=resolved)
    # Invalidate the models cache so the next dialog open re-reads LiteLLM
    # with the updated routing. Best-effort — cache is optional.
    try:
        request.app.state.litellm_models_cache_at = 0.0
    except Exception:
        pass
    _ = routing_changed  # retained for future use; currently all PATCHes re-probe
    return {"status": "updated", "name": name}


@router.get("/api/providers/models")
async def get_litellm_models(request: Request, refresh: bool = False):
    """Return LiteLLM's ``/v1/models`` passthrough — the single source of
    truth for "what models can an agent use".

    - ``refresh=true``: re-probe every cloud provider in parallel, reload
      LiteLLM, then fetch ``/v1/models``. Use this when the agent-creation
      dialog opens so the list reflects the current state of every
      provider's catalogue.
    - Default: serve the cached payload if fresh (``MODELS_CACHE_TTL_SECONDS``),
      otherwise behave like ``refresh=true``.

    Response shape::

        {
          "data": [{"id": "...", ...}, ...],
          "object": "list",
          "cached_at": 1234567890.0,
          "refreshed": true,
        }
    """
    app_state = request.app.state
    now = time.monotonic()
    cached_at = getattr(app_state, "litellm_models_cache_at", 0.0)
    cached_payload = getattr(app_state, "litellm_models_cache", None)
    stale = (now - cached_at) >= MODELS_CACHE_TTL_SECONDS
    do_refresh = bool(refresh) or stale or cached_payload is None

    proxy = getattr(app_state, "llm_proxy", None)
    config = app_state.config
    if do_refresh:
        await _refresh_all_cloud_backends(app_state, config, proxy)
        data = await _fetch_litellm_models(proxy)
        payload = {
            "data": data,
            "object": "list",
        }
        # Cache absolute wall-clock for the client; monotonic for the TTL check.
        import time as _time
        app_state.litellm_models_cache = payload
        app_state.litellm_models_cache_at = now
        app_state.litellm_models_cache_wallclock = _time.time()
        return {
            **payload,
            "cached_at": getattr(app_state, "litellm_models_cache_wallclock", 0.0),
            "refreshed": True,
        }

    return {
        **cached_payload,
        "cached_at": getattr(app_state, "litellm_models_cache_wallclock", 0.0),
        "refreshed": False,
    }


@router.post("/api/providers/{name}/start")
async def start_provider(request: Request, name: str):
    """Manually start a stopped provider."""
    config = request.app.state.config
    if not any(b.get("name") == name for b in config.backends):
        return JSONResponse({"error": f"Provider '{name}' not found"}, status_code=404)
    lifecycle: LifecycleManager = getattr(request.app.state, "lifecycle_manager", None)
    if lifecycle is None:
        return JSONResponse({"error": "Lifecycle manager not available"}, status_code=503)
    try:
        await lifecycle.start(name)
        return {"status": "started", "name": name}
    except TimeoutError as e:
        return JSONResponse({"error": str(e)}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/providers/{name}/stop")
async def stop_provider(request: Request, name: str, body: ProviderStop):
    """Gracefully stop (or force-kill) a running provider."""
    config = request.app.state.config
    if not any(b.get("name") == name for b in config.backends):
        return JSONResponse({"error": f"Provider '{name}' not found"}, status_code=404)
    lifecycle: LifecycleManager = getattr(request.app.state, "lifecycle_manager", None)
    if lifecycle is None:
        return JSONResponse({"error": "Lifecycle manager not available"}, status_code=503)
    try:
        await lifecycle.drain_and_stop(name, force=body.force)
        return {"status": "stopped", "name": name}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/providers/{name}")
async def delete_provider(request: Request, name: str):
    """Remove a provider. Only local (config-based) providers can be
    deleted — worker-reported backends disappear when the worker goes
    offline."""
    config = request.app.state.config
    # Prevent accidental deletion of worker-prefixed names
    if "/" in name:
        return JSONResponse(
            {"error": "Cluster worker backends are auto-discovered and cannot be deleted here. Deregister the worker instead."},
            status_code=400,
        )
    config.backends = [b for b in config.backends if b.get("name") != name]
    await save_config_locked(config, config.config_path)
    proxy = getattr(request.app.state, "llm_proxy", None)
    if proxy and proxy.is_running():
        resolved = await _resolve_backend_secrets(request.app.state, config.backends)
        await proxy.reload_config(config.backends, secrets=resolved)
    return {"status": "deleted", "name": name}
