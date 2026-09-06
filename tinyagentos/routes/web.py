from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from tinyagentos.userspace.package import PackageError, build_package
from tinyagentos.web_sites import WebSiteStore

router = APIRouter()

# Sites can embed image data URIs in `content`; cap the row so a single
# oversized upload can't bloat the SQLite database unboundedly. The same cap
# applies to `index_html` (the rendered export of `content`, always smaller
# or comparable in size).
MAX_CONTENT_BYTES = 5 * 1024 * 1024  # 5 MB


def _get_store(request: Request) -> WebSiteStore:
    return request.app.state.web_sites


def _validate_title(title: Any) -> str | None:
    if not isinstance(title, str) or not title.strip():
        return None
    return title.strip()


async def _parse_json(request: Request) -> dict | JSONResponse:
    # request.json() can raise JSONDecodeError on malformed input, but an empty
    # body or a wrong Content-Type can surface as a ValueError/UnicodeDecodeError
    # too. Treat all of them as a 400. A non-object JSON body (list, string,
    # number) is also rejected so downstream .get() access is always safe.
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "JSON body must be an object"}, status_code=400)
    return body


def _validate_text_field(value: Any, field_name: str) -> JSONResponse | None:
    """Returns an error response if `value` is not an acceptably-sized string, else None."""
    if not isinstance(value, str):
        return JSONResponse({"error": f"{field_name} must be a string"}, status_code=400)
    if len(value.encode("utf-8")) > MAX_CONTENT_BYTES:
        return JSONResponse(
            {"error": f"{field_name} exceeds the {MAX_CONTENT_BYTES} byte limit"},
            status_code=413,
        )
    return None


def _validate_content(content: Any) -> JSONResponse | None:
    return _validate_text_field(content, "content")


def _field_or_default(body: dict, key: str, default: str) -> str:
    """Resolve an optional text field, treating a MISSING key or an explicit
    JSON ``null`` the same way: fall back to ``default``. Without this, a body
    like ``{"index_html": null}`` would yield None from ``dict.get`` and then
    fail the string type-check as a spurious 400 (Kilo finding)."""
    value = body.get(key)
    return default if value is None else value


@router.post("/api/web/sites")
async def create_site(request: Request):
    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    title = _validate_title(body.get("title"))
    if title is None:
        return JSONResponse({"error": "title is required"}, status_code=400)
    content = _field_or_default(body, "content", "")
    content_error = _validate_content(content)
    if content_error is not None:
        return content_error
    index_html = _field_or_default(body, "index_html", "")
    index_html_error = _validate_text_field(index_html, "index_html")
    if index_html_error is not None:
        return index_html_error

    store = _get_store(request)
    site = await store.create(title=title, content=content, index_html=index_html)
    return site


@router.get("/api/web/sites")
async def list_sites(request: Request):
    store = _get_store(request)
    return await store.list()


@router.get("/api/web/sites/{site_id}")
async def get_site(request: Request, site_id: str):
    store = _get_store(request)
    site = await store.get(site_id)
    if site is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return site


@router.put("/api/web/sites/{site_id}")
async def update_site(request: Request, site_id: str):
    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    store = _get_store(request)

    existing = await store.get(site_id)
    if existing is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    title_raw = body.get("title", existing["title"])
    title = _validate_title(title_raw)
    if title is None:
        return JSONResponse({"error": "title is required"}, status_code=400)

    content = _field_or_default(body, "content", existing["content"])
    content_error = _validate_content(content)
    if content_error is not None:
        return content_error

    index_html = _field_or_default(body, "index_html", existing.get("index_html", ""))
    index_html_error = _validate_text_field(index_html, "index_html")
    if index_html_error is not None:
        return index_html_error

    site = await store.update(site_id=site_id, title=title, content=content, index_html=index_html)
    return site


@router.delete("/api/web/sites/{site_id}")
async def delete_site(request: Request, site_id: str):
    store = _get_store(request)
    deleted = await store.delete(site_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": site_id}


# Sandbox CSP for the live preview response: `sandbox allow-scripts` with NO
# allow-same-origin forces an opaque origin (same posture as
# routes/games.py's _GAME_PREVIEW_CSP). A site export has no <script> tags
# and only ever embeds images as data: URIs, so script-src/connect-src are
# left at 'none' rather than widened for content that doesn't need them.
#
# NB: `sandbox` here is the CSP *directive* (Content-Security-Policy: sandbox
# ...), not the iframe sandbox attribute -- it is a legitimate first directive
# in a single Content-Security-Policy header value, semicolon-separated from
# the rest, and is what enforces the opaque origin. Not a malformed header.
_WEB_PREVIEW_CSP = (
    "sandbox allow-scripts; "
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "img-src data: blob:; "
    "font-src data:; "
    "connect-src 'none'; "
    "frame-ancestors 'self'; base-uri 'none'"
)


@router.get("/api/web/sites/{site_id}/preview")
async def preview_site(request: Request, site_id: str):
    """Serve a site's stored, rendered index.html for the live-preview iframe,
    under a strict CSP. 404s until the site has been saved at least once --
    an unsaved in-editor site is previewed client-side via srcDoc instead."""
    store = _get_store(request)
    site = await store.get(site_id)
    if site is None or not site.get("index_html"):
        return JSONResponse({"error": "not found"}, status_code=404)
    resp = Response(content=site["index_html"], media_type="text/html")
    resp.headers["Content-Security-Policy"] = _WEB_PREVIEW_CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@router.get("/api/web/sites/{site_id}/package")
async def package_site(request: Request, site_id: str):
    """Build a .taosapp package (manifest.yaml + the rendered index.html) and
    return it as a downloadable zip -- reused both for "Export .taosapp"
    (download as-is) and "Install on this taOS" (the frontend fetches this,
    then POSTs the bytes to the existing /api/userspace-apps/install
    endpoint), mirroring routes/games.py's package route.

    Phase 2 (not implemented here): a site whose content needs a real
    backend would instead emit an app_type: "container" manifest and reuse
    userspace/container_deploy.py -- v1 is static-HTML only.
    """
    store = _get_store(request)
    site = await store.get(site_id)
    if site is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    index_html = site.get("index_html") or ""
    if not index_html:
        return JSONResponse(
            {"error": "this site has no rendered content yet; save it first"}, status_code=400
        )
    manifest = {
        "id": site_id,
        "name": site["title"],
        "version": "1.0.0",
        "app_type": "web",
        "entry": "index.html",
        "icon": "",
        "permissions": [],
    }
    try:
        data = build_package(manifest, {"index.html": index_html})
    except PackageError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{site_id}.taosapp"'},
    )
