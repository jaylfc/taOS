from __future__ import annotations

import shutil
from pathlib import Path

import httpx
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, Response

from tinyagentos.userspace.broker import handle_capability
from tinyagentos.userspace.container_deploy import deploy_app_container, destroy_app_container
from tinyagentos.userspace.package import extract_package, PackageError
from tinyagentos.userspace.url_guard import is_safe_public_url

router = APIRouter()

_SDK_PATH = Path(__file__).resolve().parent.parent / "userspace" / "sdk" / "taos-app-sdk.js"

# Bundle CSP. The `sandbox allow-scripts` directive (no allow-same-origin)
# forces the document into an OPAQUE origin even on a direct top-level
# navigation — so a userspace bundle can never execute on the core origin with
# the session cookie (defends against stored XSS), while still letting the app
# run its own scripts inside our sandboxed iframe. `default-src 'none'` plus the
# explicit self/inline allowances keep it locked down.
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


@router.post("/api/userspace-apps/install")
async def install_app(request: Request, package: UploadFile | None = File(default=None)):
    store = request.app.state.userspace_apps
    if package is not None:
        data = await package.read()
    else:
        body = await request.json()
        url = body.get("source_url")
        if not url:
            return JSONResponse({"error": "source_url or package required"}, status_code=400)
        # SSRF guard: only fetch public http(s) hosts, and don't follow
        # redirects (a 3xx could bounce to a blocked internal address).
        if not is_safe_public_url(url):
            return JSONResponse(
                {"error": "source_url is not allowed — only public http(s) hosts "
                          "(no private, loopback, link-local or reserved addresses)"},
                status_code=400,
            )
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            data = resp.content
    try:
        manifest = extract_package(data, apps_root=_apps_root(request))
    except PackageError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
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
    deploy_info: dict = {}
    if manifest["app_type"] == "container":
        dep = await deploy_app_container(manifest["id"], manifest.get("container", {}))
        if dep.get("success"):
            await store.set_runtime_location(manifest["id"], dep["host"], dep["port"])
            deploy_info = {"container_deployed": True}
        else:
            # App stays registered; its backend just isn't running. Surface
            # the reason so the UI can show it / offer a retry.
            deploy_info = {"container_deployed": False,
                           "deploy_error": dep.get("error", "deploy failed")}
    return {
        "app_id": manifest["id"],
        "permissions_requested": manifest["permissions"],
        "needs_consent": bool(existing and new_perms),
        "new_permissions": new_perms,
        **deploy_info,
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
    app = await store.get(app_id)
    removed = await store.uninstall(app_id)
    if app and app.get("app_type") == "container":
        await destroy_app_container(app_id)
    root = _apps_root(request).resolve()
    app_dir = (root / app_id).resolve()
    if str(app_dir).startswith(str(root) + "/") and app_dir.exists():
        shutil.rmtree(app_dir, ignore_errors=True)
    return {"status": "ok", "removed": removed}


@router.get("/api/userspace-apps/{app_id}/bundle/{path:path}")
async def serve_bundle(request: Request, app_id: str, path: str):
    root = (_apps_root(request) / app_id).resolve()
    target = (root / path).resolve()
    if not str(target).startswith(str(root) + "/") or not target.is_file():
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
    if not str(icon).startswith(str(root) + "/") or not icon.is_file():
        return Response(status_code=404)
    return FileResponse(icon)


def _broker_services(request: Request) -> dict:
    """Core services the broker may expose for gated capabilities. Each optional;
    absence => the gated capability returns a null/empty result."""
    st = request.app.state
    return {
        "notifications": getattr(st, "notifications", None),
        "memory": getattr(st, "user_memory", None),
        "llm": getattr(st, "llm_proxy", None),
        "agent": None,  # agent-invocation adapter wired in a later increment
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
        services=_broker_services(request),
    )
    return out
