"""Desktop service icon API.

GET /api/apps/installed — list installed services that have a recorded
runtime location (host + port). These are the apps that get desktop icons
and can be opened in a taOS web-app window via the service proxy.

Only includes apps with a runtime_host/runtime_port entry, i.e. those
successfully installed via the LXC installer path. Docker-only apps
without proxy routing are excluded until their install path also records
a runtime location.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_GENERIC_ICON = "/static/app-icons/generic-service.svg"

# Optional, frontend-only desktop apps that ship in the build but are NOT
# installed by default. They live in the Store under "taOS Apps" and the
# desktop launcher hides them until the user installs one. Install state is a
# single row in installed_apps tagged kind="frontend-app" (no runtime/service is
# spawned), so these never appear in /api/apps/installed (which requires a
# runtime location). The frontend owns name/icon/cover; the backend only tracks
# which ids are installed, gated to this allowlist so the endpoint can't be used
# to write arbitrary install rows.
OPTIONAL_FRONTEND_APPS = {"reddit", "youtube-library", "github-browser", "x-monitor"}
_FRONTEND_APP_KIND = "frontend-app"


def _resolve_icon(manifest_icon: str, manifest_dir) -> str:
    """Resolve the manifest's icon field to a URL string.

    Accepts:
    - Absolute URL paths like /static/app-icons/gitea.svg  → returned as-is.
    - http/https URLs                                        → returned as-is.
    - Relative paths (e.g. icons/gitea.svg) relative to
      the manifest dir — not currently served, so fall back
      to the generic icon.
    Returns the generic icon if the field is empty.
    """
    if not manifest_icon:
        return _GENERIC_ICON
    if manifest_icon.startswith("/") or manifest_icon.startswith("http"):
        return manifest_icon
    # Relative path — would need extra static-mount work; use generic for now.
    return _GENERIC_ICON


@router.get("/api/apps/installed")
async def list_installed_apps(request: Request):
    """Return installed services that have a live proxy location.

    Shape per item::

        {
            "app_id": "gitea-lxc",
            "display_name": "Gitea",
            "icon": "/static/app-icons/gitea.svg",
            "url": "/apps/gitea-lxc/",
            "category": "dev-tool",
            "backend": "lxc",
            "status": "running" | "unknown"
        }

    ``status`` is "running" when runtime_host + runtime_port are recorded;
    no live health check is performed here (that would add latency to every
    desktop load).
    """
    installed_apps: object = getattr(request.app.state, "installed_apps", None)
    registry: object = getattr(request.app.state, "registry", None)

    if installed_apps is None:
        return []

    rows = await installed_apps.list_installed()
    result = []

    for row in rows:
        app_id: str = row["app_id"]
        loc = await installed_apps.get_runtime_location(app_id)
        if loc is None:
            # No runtime location → not accessible via proxy → skip.
            continue
        if not loc.get("runtime_host") or not loc.get("runtime_port"):
            # Incomplete runtime record — not proxy-routable yet.
            continue

        # Best-effort manifest lookup for display metadata.
        manifest = registry.get(app_id) if registry is not None else None
        if manifest is not None:
            install_block = getattr(manifest, "install", None) or {}
            if not isinstance(install_block, dict):
                install_block = {}
            display_name: str = (
                install_block.get("display_name")
                or manifest.name
                or app_id
            )
            icon: str = _resolve_icon(
                install_block.get("icon") or manifest.icon or "",
                manifest.manifest_dir,
            )
            category: str = manifest.category or ""
        else:
            display_name = app_id
            icon = _GENERIC_ICON
            category = ""

        backend: str = loc.get("backend") or ""
        ui_path: str = str(loc.get("ui_path") or "/")
        if not ui_path.startswith("/"):
            ui_path = f"/{ui_path}"
        url = f"/apps/{app_id}{ui_path}"

        result.append({
            "app_id": app_id,
            "display_name": display_name,
            "icon": icon,
            "url": url,
            "category": category,
            "backend": backend,
            "status": "running",
        })

    return result


# --------------------------------------------------------------------------- #
# Optional frontend apps (Reddit / YouTube / GitHub / X) — Store install state.
# --------------------------------------------------------------------------- #


@router.get("/api/apps/optional/installed")
async def list_installed_optional_apps(request: Request):
    """Return the ids of optional frontend apps the user has installed.

    Shape: {"installed": ["reddit", "x-monitor"]}. The desktop launcher unions
    this with the always-on apps to decide what to show; the Store uses it to
    render Install vs Remove. Unknown/legacy rows are ignored via the allowlist.
    """
    store = getattr(request.app.state, "installed_apps", None)
    if store is None:
        return {"installed": []}
    rows = await store.list_installed()
    installed = [
        r["app_id"]
        for r in rows
        if r["app_id"] in OPTIONAL_FRONTEND_APPS
        and (r.get("metadata") or {}).get("kind") == _FRONTEND_APP_KIND
    ]
    return {"installed": installed}


@router.post("/api/apps/optional/{app_id}/install")
async def install_optional_app(app_id: str, request: Request):
    """Mark an optional frontend app installed. Instant — no service is spawned.

    Rejected unless app_id is in the OPTIONAL_FRONTEND_APPS allowlist so this
    endpoint can't seed arbitrary install rows.
    """
    if app_id not in OPTIONAL_FRONTEND_APPS:
        return JSONResponse({"error": f"not an optional app: {app_id}"}, status_code=404)
    store = getattr(request.app.state, "installed_apps", None)
    if store is None:
        return JSONResponse({"error": "install store unavailable"}, status_code=503)
    await store.install(app_id, metadata={"kind": _FRONTEND_APP_KIND})
    return {"status": "installed", "app_id": app_id}


@router.post("/api/apps/optional/{app_id}/uninstall")
async def uninstall_optional_app(app_id: str, request: Request):
    """Remove an optional frontend app so it leaves the launcher again."""
    if app_id not in OPTIONAL_FRONTEND_APPS:
        return JSONResponse({"error": f"not an optional app: {app_id}"}, status_code=404)
    store = getattr(request.app.state, "installed_apps", None)
    if store is None:
        return JSONResponse({"error": "install store unavailable"}, status_code=503)
    await store.uninstall(app_id)
    return {"status": "uninstalled", "app_id": app_id}
