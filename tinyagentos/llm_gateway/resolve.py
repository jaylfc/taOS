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
"""
from __future__ import annotations

from dataclasses import dataclass

from tinyagentos import litellm_config
from tinyagentos.llm_gateway.errors import model_not_found

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


def routing_table(state) -> list[dict]:
    """The model list LiteLLM would be configured with, built now.

    ``discover=False``: never probe ollama ``/api/tags`` on the request path.
    That only drops probe-derived embedding entries, which are ollama-routed
    and so not servable by G1 anyway.
    """
    config = getattr(state, "config", None)
    backends = getattr(config, "backends", None) or []
    return litellm_config.build_model_list(
        backends, registry=getattr(state, "registry", None), discover=False,
    )


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
    routes: list[Route] = []
    for entry in table:
        if entry.get("model_name") != name or not _is_chat(entry):
            continue
        params = entry.get("litellm_params") or {}
        provider, sep, upstream = str(params.get("model", "")).partition("/")
        if not sep:
            provider, upstream = "", provider
        routes.append(Route(
            model_name=name,
            provider=provider,
            upstream_model=upstream,
            api_base=params.get("api_base") or None,
            api_key_ref=params.get("api_key") or None,
            backend_name=str((entry.get("metadata") or {}).get("backend_name", "")),
        ))
    return routes


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
    route = find_route(routing_table(state), name)
    if route is None:
        suffix = f" (taos-default points at it)" if name != requested else ""
        raise model_not_found(f"model {name!r} not found{suffix}")
    return route
