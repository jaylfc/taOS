from __future__ import annotations

import shutil
from pathlib import Path

import httpx
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, Response

from tinyagentos.userspace.broker import handle_capability
from tinyagentos.userspace.package import extract_package, PackageError
from tinyagentos.userspace.url_guard import is_safe_public_url

router = APIRouter()

_SDK_PATH = Path(__file__).resolve().parent.parent / "userspace" / "sdk" / "taos-app-sdk.js"

# Bundle CSP. The `sandbox allow-scripts` directive (no allow-same-origin)
# forces the document into an OPAQUE origin even on a direct top-level
# navigation -- so a userspace bundle can never execute on the core origin with
# the session cookie (defends against stored XSS), while still letting the app
# run its own scripts inside our sandboxed iframe. `default-src 'none'` plus
# the explicit self/inline allowances keep it locked down.
_BUNDLE_CSP = (
    "sandbox allow-scripts allow-forms allow-popups; "
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; base-uri 'none'"
)


def _apps_root(request: Request) -> Path:
    return Path(request.app.state.data_dir) / "apps"


@router.get("/api/userspace-apps/sdk.js")
async def serve_sdk(request: Request):
    resp = FileResponse(_SDK_PATH, media_type="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@router.get("/api/userspace-apps")
async def list_apps(request: Request):
    return await request.app.state.userspace_apps.list_installed()


# Cap the package upload / fetch size to bound memory and pre-filter zip bombs.
_MAX_PACKAGE_BYTES = 64 * 1024 * 1024


@router.post("/api/userspace-apps/install")
async def install_app(request: Request, package: UploadFile | None = File(default=None)):
    store = request.app.state.userspace_apps
    if package is not None:
        data = await package.read(_MAX_PACKAGE_BYTES + 1)
    else:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        url = body.get("source_url")
        if not url:
            return JSONResponse({"error": "source_url or package required"}, status_code=400)
        # SSRF guard: only fetch public http(s) hosts, and do not follow
        # redirects (a 3xx could bounce to a blocked internal address).
        if not is_safe_public_url(url):
            return JSONResponse(
                {"error": "source_url is not allowed -- only public http(s) hosts "
                          "(no private, loopback, link-local or reserved addresses)"},
                status_code=400,
            )
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                data = resp.content
        except httpx.HTTPStatusError as exc:
            return JSONResponse(
                {"error": f"upstream returned {exc.response.status_code}"},
                status_code=502,
            )
        except httpx.HTTPError as exc:
            return JSONResponse({"error": f"upstream fetch failed: {exc}"}, status_code=502)
    if len(data) > _MAX_PACKAGE_BYTES:
        return JSONResponse({"error": "package too large"}, status_code=413)
    try:
        manifest = extract_package(data, apps_root=_apps_root(request))
    except PackageError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    # Reject container packages before persisting anything -- no partial state.
    if manifest["app_type"] == "container":
        return JSONResponse(
            {"error": "container packages are not supported in this release (web-only)"},
            status_code=501,
        )
    existing = await store.get(manifest["id"])
    new_perms = [
        p for p in manifest["permissions"]
        if existing and p not in existing["permissions_granted"]
    ]
    await store.install(
        app_id=manifest["id"], name=manifest["name"], version=manifest["version"],
        app_type=manifest["app_type"], entry=manifest["entry"], icon=manifest["icon"],
        permissions_requested=manifest["permissions"],
    )
    return {
        "app_id": manifest["id"],
        "permissions_requested": manifest["permissions"],
        "needs_consent": bool(existing and new_perms),
        "new_permissions": new_perms,
    }


@router.post("/api/userspace-apps/{app_id}/permissions")
async def set_permissions(request: Request, app_id: str):
    body = await request.json()
    await request.app.state.userspace_apps.set_permissions_granted(app_id, body.get("granted", []))
    return {"status": "ok"}


@router.post("/api/userspace-apps/{app_id}/enable")
async def enable_app(request: Request, app_id: str):
    await request.app.state.userspace_apps.set_enabled(app_id, True)
    return {"status": "ok"}


@router.post("/api/userspace-apps/{app_id}/disable")
async def disable_app(request: Request, app_id: str):
    await request.app.state.userspace_apps.set_enabled(app_id, False)
    return {"status": "ok"}


@router.delete("/api/userspace-apps/{app_id}")
async def uninstall_app(request: Request, app_id: str):
    store = request.app.state.userspace_apps
    removed = await store.uninstall(app_id)
    root = _apps_root(request).resolve()
    app_dir = (root / app_id).resolve()
    if app_dir.is_relative_to(root) and app_dir != root and app_dir.exists():
        shutil.rmtree(app_dir, ignore_errors=True)
    return {"status": "ok", "removed": removed}


@router.get("/api/userspace-apps/{app_id}/bundle/{path:path}")
async def serve_bundle(request: Request, app_id: str, path: str):
    root = (_apps_root(request) / app_id).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or target == root or not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    resp = FileResponse(target)
    resp.headers["Content-Security-Policy"] = _BUNDLE_CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@router.get("/api/userspace-apps/{app_id}/icon")
async def serve_icon(request: Request, app_id: str):
    app = await request.app.state.userspace_apps.get(app_id)
    if not app or not app["icon"]:
        return Response(status_code=404)
    root = (_apps_root(request) / app_id).resolve()
    icon = (root / app["icon"]).resolve()
    if not icon.is_relative_to(root) or icon == root or not icon.is_file():
        return Response(status_code=404)
    return FileResponse(icon)


def _broker_services(request: Request, app: dict) -> dict:
    """Core services the broker may expose for gated capabilities. Each optional;
    absence => the gated capability returns a null/empty result."""
    st = request.app.state
    backend_url = None
    if app.get("container_host") and app.get("container_port"):
        backend_url = f"http://{app['container_host']}:{app['container_port']}"
    return {
        "notifications": getattr(st, "notifications", None),
        "memory": getattr(st, "user_memory", None),
        "llm": getattr(st, "llm_proxy", None),
        "agent": None,  # agent-invocation adapter wired in a later increment
        "app_backend_url": backend_url,
    }


@router.post("/api/userspace-apps/{app_id}/broker")
async def broker(request: Request, app_id: str):
    store = request.app.state.userspace_apps
    app = await store.get(app_id)
    if app is None or not app["enabled"]:
        return JSONResponse({"error": "app not found or disabled"}, status_code=404)
    body = await request.json()
    out = await handle_capability(
        app_id, body.get("capability", ""), body.get("args") or {},
        granted=app["permissions_granted"],
        data_store=request.app.state.userspace_data,
        app_dir=_apps_root(request) / app_id,
        services=_broker_services(request, app),
    )
    return out
