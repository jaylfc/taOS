"""Model name -> backend, read per request from the ONE routing table.

The table is ``litellm_config.build_model_list``: the exact function
``generate_litellm_config`` wraps for the LiteLLM proxy. So a model name
routes to the same backend through LiteLLM and through this gateway, and a
provider added in the UI is visible here on the next request (no restart,
no second copy of the routing rules).

``taos-default`` is not in the table: it is the account's current default
chat model, which is the taOS agent's model preference (the "pick a model"
step of first-run setup, ``PATCH /api/taos-agent/settings``). It is read on
every request, so changing it takes effect immediately.

The offline model (LFM3 1.7B on-device) is injected into the table when the
user has enabled it in Settings. It is never the default.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tinyagentos import litellm_config
from tinyagentos.llm_gateway.errors import model_not_found
from tinyagentos.routes.offline_model import _build_offline_model_entry

TAOS_DEFAULT = "taos-default"
# desktop_settings preference that holds the default chat model; the same
# ("user", "taos_agent") blob routes/taos_agent.py and routes/setup.py read.
_DEFAULT_PREF = ("user", "taos_agent")


@dataclass(frozen=True)
class Route:
    model_name: str        # the table's model_name (after taos-default resolution)
    provider: str          # LiteLLM provider prefix, e.g. "openai", "anthropic"
    upstream_model: str    # what the backend is asked for
    api_base: str | None
    api_key_ref: str | None  # literal key or "os.environ/<secret>"; never render it
    backend_name: str
    # taOS backend type ("ollama", "rkllama", "hailo-ollama", "deepseek", ...),
    # from the table entry's metadata. The provider prefix alone cannot tell
    # rkllama from hailo-ollama (both ``ollama_chat``), and they stream
    # differently.
    backend_type: str = ""
    metadata: dict = field(default_factory=dict)


def route_from_entry(entry: dict, name: str) -> Route:
    """The Route for one routing-table entry, served under ``name``."""
    params = entry.get("litellm_params") or {}
    provider, sep, upstream = str(params.get("model", "")).partition("/")
    if not sep:
        provider, upstream = "", provider
    metadata = entry.get("metadata") or {}
    return Route(
        model_name=name,
        provider=provider,
        upstream_model=upstream,
        api_base=params.get("api_base") or None,
        api_key_ref=params.get("api_key") or None,
        backend_name=str(metadata.get("backend_name", "")),
        backend_type=str(metadata.get("backend_type") or ""),
        metadata=metadata,
    )


async def routing_table(state) -> list[dict]:
    """The model list LiteLLM would be configured with, built now.

    ``discover=False``: never probe ollama ``/api/tags`` on the request path.
    That only drops probe-derived embedding entries, which are ollama-routed
    and so not servable by G1 anyway.

    The offline model is appended when the user has enabled it.
    """
    config = getattr(state, "config", None)
    backends = getattr(config, "backends", None) or []
    table = litellm_config.build_model_list(
        backends, registry=getattr(state, "registry", None), discover=False,
    )
    # Inject offline model if enabled
    store = getattr(state, "desktop_settings", None)
    if store is not None:
        try:
            prefs = await store.get_preference("user", "offline_model") or {}
            if prefs.get("enabled"):
                entry = _build_offline_model_entry()
                if entry:
                    table.append(entry)
        except Exception:
            pass
    return table


def _is_chat(entry: dict) -> bool:
    return (entry.get("model_info") or {}).get("mode") != "embedding"


def model_names(table: list[dict]) -> list[str]:
    """Distinct chat model names in table (priority) order."""
    seen: dict[str, None] = {}
    for entry in table:
        name = entry.get("model_name")
        if isinstance(name, str) and name and _is_chat(entry):
            seen.setdefault(name, None)
    return list(seen)


def find_route(table: list[dict], name: str) -> Route | None:
    """First (highest-priority) chat entry for name."""
    routes = find_routes(table, name)
    return routes[0] if routes else None


def find_routes(table: list[dict], name: str) -> list[Route]:
    """All chat entries for name, in priority order (highest first)."""
    return [
        route_from_entry(entry, name)
        for entry in table
        if entry.get("model_name") == name and _is_chat(entry)
    ]


async def default_chat_model(state) -> str | None:
    store = getattr(state, "desktop_settings", None)
    if store is None:
        return None
    prefs = await store.get_preference(*_DEFAULT_PREF) or {}
    model = prefs.get("model")
    if isinstance(model, str) and model.strip() and model.strip() != TAOS_DEFAULT:
        return model.strip()
    return None


async def resolve(state, requested: str) -> Route:
    name = requested
    if requested == TAOS_DEFAULT:
        name = await default_chat_model(state)
        if name is None:
            raise model_not_found(
                "taos-default has nothing behind it: no default chat model is set. "
                "Pick one in the taOS agent settings."
            )
    route = find_route(await routing_table(state), name)
    if route is None:
        suffix = " (taos-default points at it)" if name != requested else ""
        raise model_not_found(f"model {name!r} not found{suffix}")
    return route
