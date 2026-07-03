from __future__ import annotations

import logging
import shutil
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, Response

from tinyagentos.userspace.broker import handle_capability
from tinyagentos.userspace.capabilities import capability_ceiling, default_provenance_for_trust
from tinyagentos.userspace.package import extract_package, PackageError
from tinyagentos.userspace.url_guard import resolve_safe_public_ip

# Provenance values a caller may set through the PUBLIC install endpoint. Never
# includes "first-party" -- that tier is only reachable via the internal
# boot-seeding path (seed.py) or a verified signature (P2), matching the
# existing trust="community"-only invariant this endpoint already enforces.
_PUBLIC_PROVENANCES = {"ai-generated", "user-uploaded"}

logger = logging.getLogger(__name__)

router = APIRouter()

_SDK_PATH = Path(__file__).resolve().parent.parent / "userspace" / "sdk" / "taos-app-sdk.js"

# Bundle CSP for sandboxed userspace packages. The `sandbox allow-scripts`
# directive (no allow-same-origin) forces the document into an OPAQUE origin
# even on a direct top-level navigation -- so a userspace bundle can never
# execute on the core origin with the session cookie (defends against stored
# XSS), while still letting the app run its own scripts inside our sandboxed
# iframe. `default-src 'none'` plus the explicit self/inline allowances keep it
# locked down. connect-src defaults to 'self' (the broker only); an app the
# user has granted `network:<origin>` permissions gets exactly those origins
# added to connect-src and nothing else (each origin is strictly validated at
# manifest-parse time, so it cannot inject other CSP directives).
#
# `provenance` only ever TIGHTENS this baseline further -- it never grants back
# a directive every tier already gets. Today that means the "unknown" tier (an
# app that could not be classified at all) also loses the permissive
# `img-src https:` catch-all, so it cannot even passively load images from an
# arbitrary origin. Every other tier keeps the existing img-src.
def _bundle_csp(net_origins: list[str], provenance: str = "user-uploaded") -> str:
    connect = "connect-src 'self'" + "".join(" " + o for o in net_origins)
    img_src = "img-src 'self' data: blob:" if provenance == "unknown" else "img-src 'self' https: data: blob:"
    return (
        "sandbox allow-scripts allow-forms allow-popups; "
        "default-src 'none'; "
        "script-src 'self' 'unsafe-inline' blob:; "
        "style-src 'self' 'unsafe-inline'; "
        f"{img_src}; "
        "font-src 'self' data:; "
        f"{connect}; "
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
async def install_app(
    request: Request,
    package: UploadFile | None = File(default=None),
    provenance: str | None = Form(default=None),
):
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
        provenance = body.get("provenance") or provenance
        # SSRF guard: resolve + validate the host ONCE, then pin the connection
        # to that validated IP. Re-resolving at fetch time would reopen a
        # DNS-rebinding TOCTOU window. follow_redirects stays off so a 3xx
        # cannot bounce to a blocked host.
        pinned_ip = resolve_safe_public_ip(url)
        if pinned_ip is None:
            return JSONResponse(
                {"error": "source_url is not allowed -- only public http(s) hosts "
                          "(no private, loopback, link-local or reserved addresses)"},
                status_code=400,
            )
        _u = urlparse(url)
        _ip_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        _netloc = _ip_host if not _u.port else f"{_ip_host}:{_u.port}"
        _pinned_url = _u._replace(netloc=_netloc).geturl()
        _host_header = _u.hostname if not _u.port else f"{_u.hostname}:{_u.port}"
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as c:
                # Connect to the pinned IP; keep the original Host header + TLS
                # SNI so vhost routing and certificate validation still work.
                resp = await c.get(
                    _pinned_url,
                    headers={"Host": _host_header},
                    extensions={"sni_hostname": _u.hostname},
                )
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
    # A public install must never replace an app installed as first-party: that
    # would let an attacker overwrite a trusted studio's bundle (and, before the
    # UPSERT fix, inherit its first-party privileges).
    if existing is not None and existing.get("trust") == "first-party":
        return JSONResponse(
            {"error": "an app with this id is installed as first-party "
                      "and cannot be replaced by a public install"},
            status_code=409,
        )
    new_perms = [
        p for p in manifest["permissions"]
        if existing and p not in existing["permissions_granted"]
    ]
    # trust is always 'community' through this public endpoint -- no manifest
    # field can elevate it. first-party trust is set only through the internal
    # boot-seeding path (P4) or after package signature verification (P2).
    #
    # provenance defaults to "user-uploaded" (a public install IS a side-load,
    # by definition) but a caller can tag it "ai-generated" instead, e.g. once
    # App Studio's publish flow posts here -- "first-party" is never accepted,
    # same invariant as trust above.
    resolved_provenance = provenance if provenance in _PUBLIC_PROVENANCES else "user-uploaded"
    await store.install(
        app_id=manifest["id"], name=manifest["name"], version=manifest["version"],
        app_type=manifest["app_type"], entry=manifest["entry"], icon=manifest["icon"],
        permissions_requested=manifest["permissions"],
        trust="community",
        provenance=resolved_provenance,
    )
    return {
        "app_id": manifest["id"],
        "permissions_requested": manifest["permissions"],
        "needs_consent": bool(existing and new_perms),
        "new_permissions": new_perms,
    }


@router.post("/api/userspace-apps/{app_id}/permissions")
async def set_permissions(request: Request, app_id: str):
    store = request.app.state.userspace_apps
    app = await store.get(app_id)
    if app is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    # Only grant permissions the package actually requested -- a caller cannot
    # escalate an app to capabilities its manifest never declared.
    requested = set(app.get("permissions_requested") or [])
    safe = [p for p in body.get("granted", []) if p in requested]
    await store.set_permissions_granted(app_id, safe)
    return {"status": "ok", "granted": safe}


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
    app = await request.app.state.userspace_apps.get(app_id)
    granted = (app or {}).get("permissions_granted") or []
    net_origins = [p[len("network:"):] for p in granted
                   if isinstance(p, str) and p.startswith("network:")]
    provenance = (app or {}).get("provenance") or default_provenance_for_trust((app or {}).get("trust"))
    resp = FileResponse(target)
    resp.headers["Content-Security-Policy"] = _bundle_csp(net_origins, provenance)
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
    # granted starts at the app's provenance ceiling (#PROV): first-party's
    # ceiling is every known capability, so it behaves exactly like the old
    # trust="first-party" bypass; every other tier's ceiling excludes the
    # gated namespaces (network/agent/llm/memory), so those still need an
    # explicit grant exactly as "community" apps always required. On top of
    # the ceiling, the app's own declared+granted permissions and the
    # app_grants consent ledger are additive, same as before.
    provenance = app.get("provenance") or default_provenance_for_trust(app.get("trust"))
    granted = set(capability_ceiling(provenance))
    granted |= set(app["permissions_granted"])
    uid = getattr(request.state, "user_id", None)
    grants_store = getattr(request.app.state, "app_grants", None)
    if uid and grants_store is not None:
        try:
            granted |= await grants_store.granted_capabilities(uid, app_id)
        except Exception:
            # Genuinely best-effort: an uninitialised store or a query error
            # must not turn a previously-working broker call into a 500. Fall
            # back to the ceiling + per-app granted set.
            logger.warning(
                "app_grants lookup failed for app %s; using per-app grants only",
                app_id,
                exc_info=True,
            )
    out = await handle_capability(
        app_id, body.get("capability", ""), body.get("args") or {},
        granted=granted,
        data_store=request.app.state.userspace_data,
        app_dir=_apps_root(request) / app_id,
        services=_broker_services(request, app),
    )
    return out
