"""Agent tool for discovering installable Store apps and backends.

Lets the agent see what is in the Store (apps, models, services, backends,
plugins) and whether each is installed, so it can offer a concrete next step
("I can install the X backend / app for you") instead of guessing. Reads the
in-process registry (the same source /api/store/catalog serves). Read-only; the
install itself stays a user-driven action in the Store app.
"""
from __future__ import annotations

from fastapi import Request


def _installed(registry, installation, app_id: str) -> bool:
    """Mirror the Store's installed semantics: a live-probe running/installed
    state when an InstallationState is present, else the registry cache. Stale
    cache entries are treated as not-installed (they are installable)."""
    if installation is not None:
        try:
            return installation.state(app_id) in ("running", "installed")
        except Exception:
            pass
    try:
        return bool(registry.is_installed(app_id))
    except Exception:
        return False


LIST_STORE_APPS_TOOL = {
    "name": "list_store_apps",
    "description": (
        "List installable things in the taOS Store (apps, models, services, "
        "backends, plugins) with whether each is installed, so you can offer to "
        "install what the user needs. Optional 'type' filters (e.g. app, model, "
        "service, backend, plugin) and 'query' searches name/description. "
        "Read-only; the user installs from the Store app."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "description": "Optional type filter (app, model, service, backend, plugin)."},
            "query": {"type": "string", "description": "Optional search over name and description."},
        },
    },
}


async def execute_list_store_apps(args: dict, request: Request) -> dict:
    args = args or {}
    type_filter = args.get("type") or None
    query = args.get("query")
    query = query.lower() if isinstance(query, str) and query.strip() else None

    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return {"error": "store registry not available"}
    installation = getattr(request.app.state, "installation_state", None)

    try:
        apps = registry.list_available(type_filter=type_filter)
    except TypeError:
        # Older registries may not accept the keyword; fall back to all.
        apps = registry.list_available()

    items = []
    for a in apps:
        name = getattr(a, "name", "") or ""
        description = getattr(a, "description", "") or ""
        if query and query not in name.lower() and query not in description.lower():
            continue
        app_id = getattr(a, "id", None)
        items.append({
            "id": app_id,
            "name": name,
            "type": getattr(a, "type", None),
            "category": getattr(a, "category", None),
            "description": description,
            "installed": _installed(registry, installation, app_id),
        })
    return {"ok": True, "apps": items, "count": len(items)}
