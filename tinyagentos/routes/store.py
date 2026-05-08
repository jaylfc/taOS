# tinyagentos/routes/store.py
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.catalog.resolver import (
    ResolveErr,
    ResolveOk,
    classify,
    resolve,
)
from tinyagentos.installers.base import get_installer
from tinyagentos.routes.store_install import get_device_capability


class InstallRequest(BaseModel):
    app_id: str
    variant_id: str | None = None  # for models


class UninstallRequest(BaseModel):
    app_id: str

router = APIRouter()


def _build_app_items(registry, profile_id: str, type_filter: str | None = None, query: str | None = None, installation=None):
    """Build app item dicts for templates, with compat info and optional filtering.

    The ``installation`` argument is an InstallationState that joins the
    registry cache with the live BackendCatalog; callers that pass it
    get accurate 'installed' state for services and models. Passing
    ``None`` falls back to the registry cache only (tests and early
    startup paths).

    "Installed" here means actually-usable-right-now or in-cache-with-no-
    probe-surface (agents/plugins). ``stale`` cache entries — services
    whose backends are unreachable, models whose files no longer exist —
    are deliberately excluded from the installed set so the user sees
    them as installable rather than as a permanent "Installed (broken)"
    state with no working uninstall. johny flagged this on #312/#356:
    Ollama / Open WebUI / rk-llama.cpp etc were showing as installed
    when he hadn't actually installed them, blocking him from installing
    them legitimately.
    """
    apps = registry.list_available(type_filter=type_filter or None)
    if installation is not None:
        # Filter out stale entries — only entries the live probe says are
        # running, or no-probe-surface types (agent/plugin) that the cache
        # alone is authoritative for.
        installed_ids = {
            entry["id"] for entry in installation.list_installed()
            if entry.get("state") in ("running", "installed")
        }
    else:
        installed_ids = {a["id"] for a in registry.list_installed()}
    items = []
    for a in apps:
        if query and query.lower() not in a.name.lower() and query.lower() not in a.description.lower():
            continue
        # Determine hardware compatibility
        compat = None
        if a.hardware_tiers:
            tier = a.hardware_tiers.get(profile_id)
            if tier is None:
                compat = "unsupported"
            elif isinstance(tier, str) and tier == "unsupported":
                compat = "unsupported"
            elif isinstance(tier, str) and tier in ("full", "optimal"):
                compat = "compatible"
            elif isinstance(tier, dict):
                compat = "compatible"
            else:
                compat = "degraded"
        items.append({"manifest": a, "installed": a.id in installed_ids, "compat": compat})
    return items


@router.get("/api/store/catalog")
async def list_catalog(request: Request, type: str | None = None):
    """List all available apps in the catalog, optionally filtered by type."""
    registry = request.app.state.registry
    installation = getattr(request.app.state, "installation_state", None)
    apps = registry.list_available(type_filter=type)
    def _slim_variants(manifest_app) -> list[dict]:
        """Return id/name/size/backend for each variant. Models with
        multiple variants need this so the Store can render a chooser
        instead of forcing the resolver's auto-pick. ``backend`` is the
        list of backend ids the variant declares under
        ``requires.backends`` — the Store's BackendPillBar uses it to
        derive which backend filters apply to which model."""
        if manifest_app.type != "model":
            return []
        out: list[dict] = []
        for v in (manifest_app.variants or []):
            if not isinstance(v, dict):
                continue
            vid = v.get("id")
            if not vid:
                continue
            backend_ids: list[str] = []
            for b in (v.get("requires", {}) or {}).get("backends", []) or []:
                if isinstance(b, dict) and b.get("id"):
                    backend_ids.append(b["id"])
                elif isinstance(b, str):
                    backend_ids.append(b)
            out.append({
                "id": vid,
                "name": v.get("name") or vid,
                "size_mb": v.get("size_mb") or 0,
                "backend": backend_ids,
            })
        return out

    def _installed_flag(app_id: str) -> bool:
        # Match _build_app_items semantics: stale entries should NOT
        # count as installed for the user-facing flag, otherwise the
        # Store shows them as installed with a non-functional Uninstall
        # button (johny's #312 complaint).
        if installation is None:
            return registry.is_installed(app_id)
        return installation.state(app_id) in ("running", "installed")

    return [
        {
            "id": a.id, "name": a.name, "type": a.type, "category": a.category,
            "version": a.version,
            "description": a.description, "icon": a.icon,
            "requires": a.requires, "hardware_tiers": a.hardware_tiers,
            "install_method": (a.install.get("method") or a.install.get("backend") or "") if isinstance(a.install, dict) else "",
            "installed": _installed_flag(a.id),
            "state": (installation.state(a.id) if installation else ("installed" if registry.is_installed(a.id) else "not_installed")),
            "variants": _slim_variants(a),
        }
        for a in apps
    ]


@router.get("/api/store/installed")
async def list_installed(request: Request):
    """List currently installed apps.

    Backend-driven: returns the union of the registry cache and the live
    BackendCatalog. Each row carries a ``state`` field — 'running' for
    live-probed entries, 'stale' for cache entries whose backend is
    currently unreachable, 'installed' for types without a live probe
    (agent frameworks, plugins)."""
    installation = getattr(request.app.state, "installation_state", None)
    if installation is not None:
        return installation.list_installed()
    return request.app.state.registry.list_installed()


@router.get("/api/store/app/{app_id}")
async def get_app(request: Request, app_id: str):
    """Get detailed information about a specific app."""
    registry = request.app.state.registry
    installation = getattr(request.app.state, "installation_state", None)
    app = registry.get(app_id)
    if not app:
        return JSONResponse({"error": f"App '{app_id}' not found"}, status_code=404)
    return {
        "id": app.id, "name": app.name, "type": app.type, "version": app.version,
        "description": app.description, "homepage": app.homepage, "license": app.license,
        "requires": app.requires, "install": app.install,
        "hardware_tiers": app.hardware_tiers, "config_schema": app.config_schema,
        "variants": app.variants, "capabilities": app.capabilities,
        "installed": (installation.is_installed(app.id) if installation else registry.is_installed(app.id)),
        "state": (installation.state(app.id) if installation else ("installed" if registry.is_installed(app.id) else "not_installed")),
    }


@router.get("/api/hardware")
async def hardware_profile(request: Request):
    """Get detected hardware profile for this device."""
    profile = request.app.state.hardware_profile
    data = asdict(profile)
    data["profile_id"] = profile.profile_id
    return data


@router.post("/api/hardware/detect")
async def redetect_hardware(request: Request):
    """Re-detect hardware profile and save updated results."""
    from tinyagentos.hardware import detect_hardware
    profile = detect_hardware()
    profile.save(request.app.state.config_path.parent / "hardware.json")
    request.app.state.hardware_profile = profile
    data = asdict(profile)
    data["profile_id"] = profile.profile_id
    return data


@router.post("/api/store/sync")
async def sync_store(request: Request):
    """Sync the app catalog from the git repository."""
    from tinyagentos.catalog_sync import sync_catalog
    registry = request.app.state.registry
    result = await sync_catalog(registry.catalog_dir)
    if result["success"]:
        registry.reload()
    return result


@router.post("/api/store/install")
async def install_app(request: Request, body: InstallRequest):
    """Install an app from the catalog."""
    registry = request.app.state.registry
    manifest = registry.get(body.app_id)
    if not manifest:
        return JSONResponse({"error": f"App '{body.app_id}' not found"}, status_code=404)
    if registry.is_installed(body.app_id):
        return JSONResponse({"error": f"App '{body.app_id}' already installed"}, status_code=409)

    method = manifest.install.get("method", "")
    try:
        installer = get_installer(method)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    kwargs = {}
    if manifest.type == "model" and body.variant_id:
        variant = next((v for v in manifest.variants if v["id"] == body.variant_id), None)
        if not variant:
            return JSONResponse({"error": f"Variant '{body.variant_id}' not found"}, status_code=404)
        kwargs["variant"] = variant

    result = await installer.install(body.app_id, manifest.install, **kwargs)
    if result["success"]:
        registry.mark_installed(body.app_id, manifest.version)
        if manifest.type == "plugin":
            mcp_store = getattr(request.app.state, "mcp_store", None)
            if mcp_store is not None:
                transport = manifest.install.get("transport", "stdio")
                await mcp_store.register_server(
                    body.app_id, manifest.version, transport
                )
        return {"status": "installed", "app_id": body.app_id}
    return JSONResponse({"error": result.get("error", "Install failed")}, status_code=500)


@router.post("/api/store/resolve")
async def resolve_model(request: Request):
    """Wrapper around the resolver for the Store frontend.

    Returns a JSON envelope with ``result`` ("ok" | "err"), the resolver's
    structured payload, and the green/amber/red compatibility classification
    (so the Store can colour-code the card from a single round-trip).
    """
    body = await request.json()
    manifest_id = body.get("manifest_id") or body.get("app_id")
    variant_id = body.get("variant_id", "auto")
    target_remote = body.get("target_remote") or None
    force = bool(body.get("force", False))

    registry = request.app.state.registry
    manifest = registry.get(manifest_id) if registry else None
    if manifest is None:
        return JSONResponse({"error": f"manifest {manifest_id!r} not found"}, status_code=404)

    device = await get_device_capability(request, target_remote)
    manifest_dict = {
        "id": manifest.id,
        "type": manifest.type,
        "variants": manifest.variants,
        "context_window": getattr(manifest, "context_window", 0),
    }
    res = resolve(manifest_dict, variant_id, device, force=force)
    compat = classify(manifest_dict, device)

    if isinstance(res, ResolveOk):
        return {
            "result": "ok",
            "backend_id": res.backend_id,
            "variant_id": res.variant_id,
            "action": res.action,
            "compat": compat,
        }
    return {
        "result": "err",
        "reason": res.reason,
        "near_miss": res.near_miss,
        "suggestions": res.suggestions,
        "compat": compat,
    }


@router.post("/api/store/uninstall")
async def uninstall_app(request: Request, body: UninstallRequest):
    """Uninstall an installed app.

    Accepts either an actually-installed app (live or cached) OR a stale
    cache entry. johny saw the catalog show items as installed (via the
    joined live+cache view) and then the uninstall route reject them as
    "not installed" (cache-only check). Use the same joined view here so
    the two endpoints agree, AND fall through to mark_uninstalled even
    when the live probe says the service is gone — cleaning the
    breadcrumb is exactly what the user wants in that case.
    """
    registry = request.app.state.registry
    installation = getattr(request.app.state, "installation_state", None)
    is_known = (
        installation.is_installed(body.app_id)
        if installation is not None
        else registry.is_installed(body.app_id)
    )
    if not is_known:
        return JSONResponse({"error": f"App '{body.app_id}' not installed"}, status_code=404)

    manifest = registry.get(body.app_id)
    method = manifest.install.get("method", "") if manifest else "pip"
    try:
        installer = get_installer(method)
    except ValueError:
        pass  # best effort uninstall
    else:
        await installer.uninstall(body.app_id)

    if manifest and manifest.type == "plugin":
        mcp_supervisor = getattr(request.app.state, "mcp_supervisor", None)
        if mcp_supervisor is not None:
            mcp_store = getattr(request.app.state, "mcp_store", None)
            if mcp_store is not None:
                existing = await mcp_store.get_server(body.app_id)
                if existing is not None:
                    await mcp_supervisor.uninstall(body.app_id)
    registry.mark_uninstalled(body.app_id)
    return {"status": "uninstalled", "app_id": body.app_id}
