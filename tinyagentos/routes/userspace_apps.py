from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from tinyagentos.code_analyzer import analyze_app_source, has_critical
from tinyagentos.userspace.broker import handle_capability, GATED_CAPS
from tinyagentos.userspace.capabilities import capability_ceiling, default_provenance_for_trust
from tinyagentos.userspace.container_deploy import deploy_app_container, destroy_app_container
from tinyagentos.userspace.package import extract_package, parse_manifest, PackageError
from tinyagentos.userspace.url_guard import resolve_safe_public_ip

# Provenance values a caller may set through the PUBLIC install endpoint. Never
# includes "first-party" -- that tier is only reachable via the internal
# boot-seeding path (seed.py) or a verified signature (P2), matching the
# existing trust="community"-only invariant this endpoint already enforces.
_PUBLIC_PROVENANCES = {"ai-generated", "user-uploaded"}

logger = logging.getLogger(__name__)

router = APIRouter()

_SDK_PATH = Path(__file__).resolve().parent.parent / "userspace" / "sdk" / "taos-app-sdk.js"

# Extensions the static security analyzer treats as readable app source. Binary
# assets (images, fonts, wasm) are skipped -- they aren't executable script/
# markup and decoding them as text would either error or waste a scan.
_ANALYZABLE_EXTENSIONS = {".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".css"}


def _collect_source_files(app_dir: Path) -> dict[str, str]:
    """Read every analyzable text file under app_dir into a {relpath: content} dict.

    Used to feed the just-extracted package into analyze_app_source(). Paths
    are POSIX-style relative to app_dir so findings read the same way
    regardless of host OS.
    """
    files: dict[str, str] = {}
    for path in sorted(app_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _ANALYZABLE_EXTENSIONS:
            continue
        try:
            # errors="replace" (not "ignore"): dropping invalid bytes could
            # splice two otherwise-separate substrings into a token a
            # detector regex matches on (e.g. the literal name of the JS
            # function that executes a string as code), silently hiding it
            # from the scan below. This file only ever reads app-supplied
            # text into a string for regex scanning -- it never executes it.
            # Replacing invalid bytes with U+FFFD instead breaks such a token
            # apart visibly without ever deleting characters a detector looks
            # for.
            files[path.relative_to(app_dir).as_posix()] = path.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
    return files

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
    apps_root = _apps_root(request).resolve()
    app_dir = (apps_root / manifest["id"]).resolve()
    # Static security gate: this runs server-side on every install regardless
    # of how the package was submitted (upload or source_url), so a
    # modified/bypassed client can never skip it. Container apps ship an
    # opaque Docker image (not analyzable source) and are skipped here --
    # their isolation comes from the container boundary, not this scanner.
    # "tui" packages ship no _ANALYZABLE_EXTENSIONS files (just a
    # manifest.yaml + command spec), so _collect_source_files() naturally
    # finds nothing to scan for them. A critical finding blocks the install
    # outright -- the extracted files are removed so nothing
    # scanned-and-rejected is left reachable via serve_bundle. This is
    # defense in depth in FRONT of the sandbox iframe, not a replacement
    # for it.
    if manifest["app_type"] != "container":
        findings = analyze_app_source(_collect_source_files(app_dir))
        if has_critical(findings):
            if app_dir.is_relative_to(apps_root) and app_dir != apps_root:
                shutil.rmtree(app_dir, ignore_errors=True)
            return JSONResponse(
                {
                    "error": "blocked_by_security_analysis",
                    "findings": [f.to_dict() for f in findings],
                },
                status_code=422,
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


# DoS guards for this endpoint: it has no auth gate of its own (a preview
# convenience, see docstring below), so an unbounded body, file count, or
# per-file size would let a caller burn memory/CPU across every regex
# detector with a single request. Conservative caps, mirroring the
# _MAX_PACKAGE_BYTES pattern above.
_MAX_ANALYZE_BODY_BYTES = 5 * 1024 * 1024   # 5 MB total request body
_MAX_ANALYZE_FILES = 500                     # files per request
_MAX_ANALYZE_FILE_BYTES = 1024 * 1024        # 1 MB per file's source text


@router.post("/api/userspace-apps/analyze")
async def analyze_app(request: Request):
    """Run the static security analyzer over raw App Studio source, ahead of install.

    Body: {"files": {"index.html": "...", "app.js": "...", ...}}. Lets App
    Studio's Build/Publish views show findings while the app is still just
    generated text with no package built yet. This is a convenience preview
    -- the authoritative, unbypassable gate is the analysis that runs inside
    POST /api/userspace-apps/install itself.
    """
    # Reject oversize via Content-Length when present (cheap, before read).
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > _MAX_ANALYZE_BODY_BYTES:
        return JSONResponse({"error": "request body too large"}, status_code=413)
    raw_body = await request.body()
    if len(raw_body) > _MAX_ANALYZE_BODY_BYTES:
        return JSONResponse({"error": "request body too large"}, status_code=413)
    try:
        body = json.loads(raw_body)
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    files = body.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in files.items()
    ):
        return JSONResponse({"error": "files must be a map of filename to source text"}, status_code=400)
    if len(files) > _MAX_ANALYZE_FILES:
        return JSONResponse(
            {"error": f"too many files (max {_MAX_ANALYZE_FILES})"}, status_code=413
        )
    if any(len(v.encode("utf-8")) > _MAX_ANALYZE_FILE_BYTES for v in files.values()):
        return JSONResponse(
            {"error": f"a file exceeds the {_MAX_ANALYZE_FILE_BYTES}-byte per-file limit"},
            status_code=413,
        )
    findings = analyze_app_source(files)
    return {
        "findings": [f.to_dict() for f in findings],
        "blocked": has_critical(findings),
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


def _load_container_spec(app_dir: Path) -> dict | None:
    """Re-parse the installed package's manifest.yaml to recover its
    container block (image + ports). Not persisted in the store row --
    extract_package already wrote manifest.yaml alongside the app's other
    files, so it's re-read here rather than adding new store columns."""
    manifest_path = app_dir / "manifest.yaml"
    if not manifest_path.is_file():
        return None
    try:
        manifest = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    except (PackageError, OSError):
        return None
    return manifest.get("container")


@router.post("/api/userspace-apps/{app_id}/enable")
async def enable_app(request: Request, app_id: str):
    store = request.app.state.userspace_apps
    app = await store.get(app_id)
    if app is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    await store.set_enabled(app_id, True)
    if app["app_type"] == "container":
        container_spec = _load_container_spec(_apps_root(request) / app_id)
        if container_spec is None:
            await store.set_enabled(app_id, False)
            return JSONResponse(
                {"error": "container app manifest missing or invalid"}, status_code=500
            )
        result = await deploy_app_container(app_id, container_spec)
        if not result.get("success"):
            # Do not leave a half-enabled app claiming to run.
            await store.set_enabled(app_id, False)
            return JSONResponse(
                {"error": result.get("error", "container deploy failed")}, status_code=502
            )
        await store.set_runtime_location(app_id, result["host"], result["port"])
    return {"status": "ok"}


@router.post("/api/userspace-apps/{app_id}/disable")
async def disable_app(request: Request, app_id: str):
    store = request.app.state.userspace_apps
    app = await store.get(app_id)
    if app is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if app["app_type"] == "container":
        await destroy_app_container(app_id)
        await store.set_runtime_location(app_id, None, None)
    await store.set_enabled(app_id, False)
    return {"status": "ok"}


@router.delete("/api/userspace-apps/{app_id}")
async def uninstall_app(request: Request, app_id: str):
    store = request.app.state.userspace_apps
    app = await store.get(app_id)
    if app is not None and app["app_type"] == "container":
        # Best-effort: tear down the backend container before removing the
        # store record and files, so nothing is left running unreferenced.
        await destroy_app_container(app_id)
    removed = await store.uninstall(app_id)
    root = _apps_root(request).resolve()
    app_dir = (root / app_id).resolve()
    if app_dir.is_relative_to(root) and app_dir != root and app_dir.exists():
        shutil.rmtree(app_dir, ignore_errors=True)
    return {"status": "ok", "removed": removed}


@router.get("/api/userspace-apps/{app_id}/bundle/{path:path}")
async def serve_bundle(request: Request, app_id: str, path: str):
    # Store lookup FIRST, before any filesystem access: install_app extracts
    # a package to disk and only afterwards runs the security analysis (and,
    # on a pass, the store.install() that actually registers the app). If the
    # file check ran first, a bundle that has been extracted but not yet
    # vetted -- or was vetted and rejected -- would still be servable here
    # for as long as it happens to exist on disk. Gating on the store record
    # closes that window: nothing is served until the app is actually
    # installed.
    app = await request.app.state.userspace_apps.get(app_id)
    if app is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    root = (_apps_root(request) / app_id).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or target == root or not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    granted = (app or {}).get("permissions_granted") or []
    net_origins = [p[len("network:"):] for p in granted
                   if isinstance(p, str) and p.startswith("network:")]
    provenance = (app or {}).get("provenance") or default_provenance_for_trust((app or {}).get("trust"))
    resp = FileResponse(target)
    resp.headers["Content-Security-Policy"] = _bundle_csp(net_origins, provenance)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# Hop-by-hop headers must not be forwarded between the proxy and the
# upstream/client (RFC 2616 §13.5.1). Authorization is stripped too, and the
# taos_session cookie is scrubbed out of Cookie -- an untrusted container-app
# backend must never see the controller session credential.
_PROXY_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authorization", "te",
    "trailer", "transfer-encoding", "upgrade", "host",
})
_PROXY_SENSITIVE_HEADERS = frozenset({"authorization"})
_PROXY_STRIPPED_COOKIES = frozenset({"taos_session"})

# Module-level HTTP client for the container-app proxy -- avoids per-request
# connection churn, mirrors service_proxy.py's pattern.
_container_proxy_client = httpx.AsyncClient(timeout=60.0)


def _strip_taos_session_cookie(cookie_header: str) -> str:
    # Textual scrub that does not depend on SimpleCookie's grammar: a
    # malformed Cookie header must still have taos_session removed, never
    # forwarded whole (SimpleCookie.load could raise and leak the credential).
    if not cookie_header:
        return ""
    kept = []
    for part in cookie_header.split(";"):
        stripped = part.strip()
        if not stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if name in _PROXY_STRIPPED_COOKIES:
            continue
        kept.append(stripped)
    return "; ".join(kept)


def _filter_proxy_headers(headers: dict) -> dict:
    filtered: dict[str, str] = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in _PROXY_HOP_BY_HOP or kl in _PROXY_SENSITIVE_HEADERS:
            continue
        if kl == "cookie":
            stripped = _strip_taos_session_cookie(v)
            if not stripped:
                continue
            filtered[k] = stripped
            continue
        filtered[k] = v
    return filtered


@router.get("/api/userspace-apps/{app_id}/proxy", include_in_schema=False)
async def proxy_no_slash_redirect(app_id: str):
    """Redirect /proxy -> /proxy/ so the container app's relative links work."""
    return RedirectResponse(url=f"/api/userspace-apps/{app_id}/proxy/", status_code=307)


@router.api_route(
    "/api/userspace-apps/{app_id}/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def proxy_container_app(request: Request, app_id: str, path: str):
    # Store lookup FIRST (same reasoning as serve_bundle): nothing is
    # forwarded until the app is installed, enabled, a container app, and has
    # a recorded runtime location.
    app = await request.app.state.userspace_apps.get(app_id)
    if (
        app is None
        or app["app_type"] != "container"
        or not app["enabled"]
        or not app.get("container_host")
        or not app.get("container_port")
    ):
        return JSONResponse({"error": "not found"}, status_code=404)

    # SECURITY: the proxy target is built ONLY from the recorded runtime
    # location (always 127.0.0.1:<port>, set by deploy_app_container) -- never
    # from client-controlled input. This is what rules out SSRF here.
    host = app["container_host"]
    port = app["container_port"]
    upstream = f"http://{host}:{port}/{path}"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"

    fwd_headers = _filter_proxy_headers(dict(request.headers))

    async def _stream_body():
        async for chunk in request.stream():
            yield chunk

    try:
        req = _container_proxy_client.build_request(
            method=request.method, url=upstream, headers=fwd_headers, content=_stream_body(),
        )
        upstream_resp = await _container_proxy_client.send(req, stream=True, follow_redirects=False)
    except httpx.ConnectError:
        return JSONResponse(
            {"error": f"cannot reach app '{app_id}' -- it may be stopped or still starting"},
            status_code=502,
        )
    except httpx.TimeoutException:
        return JSONResponse({"error": f"app '{app_id}' timed out"}, status_code=504)

    resp_headers = _filter_proxy_headers(dict(upstream_resp.headers))
    granted = app.get("permissions_granted") or []
    net_origins = [p[len("network:"):] for p in granted
                   if isinstance(p, str) and p.startswith("network:")]
    provenance = app.get("provenance") or default_provenance_for_trust(app.get("trust"))
    resp_headers["Content-Security-Policy"] = _bundle_csp(net_origins, provenance)
    resp_headers["X-Content-Type-Options"] = "nosniff"

    return StreamingResponse(
        upstream_resp.aiter_bytes(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        background=BackgroundTask(upstream_resp.aclose),
    )


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
