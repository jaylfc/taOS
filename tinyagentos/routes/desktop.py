from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

router = APIRouter()

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
SPA_DIR = PROJECT_DIR / "static" / "desktop"


@router.get("/api/desktop/settings")
async def get_settings(request: Request):
    store = request.app.state.desktop_settings
    settings = await store.get_settings("user")
    return JSONResponse(settings)


@router.put("/api/desktop/settings")
async def update_settings(request: Request):
    store = request.app.state.desktop_settings
    body = await request.json()
    await store.update_settings("user", body)
    return JSONResponse({"ok": True})


@router.get("/api/desktop/dock")
async def get_dock(request: Request):
    store = request.app.state.desktop_settings
    dock = await store.get_dock("user")
    return JSONResponse(dock)


@router.put("/api/desktop/dock")
async def update_dock(request: Request):
    store = request.app.state.desktop_settings
    body = await request.json()
    await store.update_dock("user", body)
    return JSONResponse({"ok": True})


@router.get("/api/desktop/windows")
async def get_windows(request: Request):
    store = request.app.state.desktop_settings
    windows = await store.get_windows("user")
    return JSONResponse(windows)


@router.put("/api/desktop/windows")
async def save_windows(request: Request):
    store = request.app.state.desktop_settings
    body = await request.json()
    await store.save_windows("user", body.get("positions", []))
    return JSONResponse({"ok": True})


@router.get("/api/desktop/widgets")
async def get_widgets(request: Request):
    store = request.app.state.desktop_settings
    widgets = await store.get_widgets("user")
    return JSONResponse(widgets)


@router.put("/api/desktop/widgets")
async def save_widgets(request: Request):
    store = request.app.state.desktop_settings
    body = await request.json()
    widgets = body.get("widgets", [])
    await store.save_widgets("user", widgets)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Generic preferences — namespaced JSON blobs that follow the user across
# devices. Use this for any cross-session setting (weather home location,
# temperature units, quick notes content, etc.) so the experience resumes
# on any device the user signs into.
# ---------------------------------------------------------------------------


@router.get("/api/preferences/{namespace}")
async def get_preference(request: Request, namespace: str):
    store = request.app.state.desktop_settings
    data = await store.get_preference("user", namespace)
    return JSONResponse(data)


@router.put("/api/preferences/{namespace}")
async def save_preference(request: Request, namespace: str):
    store = request.app.state.desktop_settings
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse(
            {"error": "preference body must be a JSON object"},
            status_code=400,
        )
    await store.save_preference("user", namespace, body)
    return JSONResponse({"ok": True})



@router.get("/chat-pwa")
async def serve_chat_pwa():
    """Serve the standalone chat PWA."""
    chat_html = SPA_DIR / "chat.html"
    if chat_html.exists():
        return FileResponse(chat_html, media_type="text/html")
    return JSONResponse({"error": "Chat PWA not built"}, status_code=404)


@router.get("/chat-pwa/{rest:path}")
async def serve_chat_pwa_assets(rest: str = ""):
    """Serve assets for the chat PWA (uses same /desktop/assets base)."""
    # Assets are at /desktop/assets/... due to base path — this route just serves index
    chat_html = SPA_DIR / "chat.html"
    if chat_html.exists():
        return FileResponse(chat_html, media_type="text/html")
    return JSONResponse({"error": "Chat PWA not built"}, status_code=404)


@router.post("/api/desktop/browser/agent-command")
async def browser_agent_command(request: Request):
    """Execute a natural language command on the current page using browser-use."""
    body = await request.json()
    url = body.get("url", "")
    command = body.get("command", "")
    agent_name = body.get("agent_name")

    if not url or not command:
        return JSONResponse({"error": "url and command required"}, status_code=400)

    # Check if browser-use is installed
    try:
        import importlib.util
        if importlib.util.find_spec("browser_use") is None:
            return JSONResponse({
                "error": "browser-use not installed",
                "install": "pip install browser-use[cli]",
            }, status_code=503)
    except Exception as e:
        return JSONResponse({"error": f"Failed to check browser-use: {e}"}, status_code=500)

    # For now, return a placeholder response indicating the feature is wired but needs an agent
    return JSONResponse({
        "status": "queued",
        "url": url,
        "command": command,
        "agent_name": agent_name,
        "message": "Browser task queued. Requires an agent with browser-use capability.",
        "note": "Full integration requires browser-use plugin installation and agent configuration.",
    })


# index.html points at hashed bundle filenames that change every build.
# Caching it would lock browsers (especially installed PWAs on iOS) onto
# stale script tags pointing at bundles that no longer exist on disk.
# Hashed assets themselves are safe to cache aggressively because the
# filename is the cache key.
_HTML_NO_CACHE = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


_SW_HEADERS = {
    "Content-Type": "application/javascript",
    "Service-Worker-Allowed": "/",
    "Cache-Control": "no-cache, no-store, must-revalidate",
}


@router.get("/sw.js")
async def serve_service_worker():
    """Serve the SPA's service worker at root scope.

    The file lives at /static/desktop/sw.js but the SW must claim scope
    `/` to cover both /desktop (desktop SPA) and /chat-pwa (chat PWA).
    Browsers only honor a non-default scope if the response carries the
    Service-Worker-Allowed header — hence the explicit headers below.

    The SW itself must never be cached aggressively or browsers will pin
    a stale worker after a deploy."""
    sw_file = SPA_DIR / "sw.js"
    if sw_file.exists():
        return FileResponse(sw_file, headers=_SW_HEADERS)
    return JSONResponse({"error": "Service worker not built"}, status_code=404)


@router.get("/desktop")
async def serve_spa_root():
    """Serve the SPA index.html at /desktop."""
    index = SPA_DIR / "index.html"
    if index.exists():
        return FileResponse(index, media_type="text/html", headers=_HTML_NO_CACHE)
    return JSONResponse({"error": "Desktop shell not built. Run: cd desktop && npm run build"}, status_code=404)


@router.get("/desktop/{rest:path}")
async def serve_spa(rest: str = ""):
    """Serve static assets from the SPA build, fall back to index.html for client-side routes."""
    # Try to serve the exact file first (CSS, JS, images). Hashed asset
    # filenames are safe to cache for a long time — the filename changes
    # every build, so a cache hit on /assets/main-XXX.js is by definition
    # the right content.
    file_path = SPA_DIR / rest
    if file_path.is_file() and SPA_DIR in file_path.resolve().parents:
        if rest.startswith("assets/"):
            return FileResponse(
                file_path,
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
        # Anything else under /desktop/ (manifest, sw registration shim,
        # etc.) shouldn't be aggressively cached.
        return FileResponse(file_path, headers=_HTML_NO_CACHE)
    # Fall back to index.html for client-side routing
    index = SPA_DIR / "index.html"
    if index.exists():
        return FileResponse(index, media_type="text/html", headers=_HTML_NO_CACHE)
    return JSONResponse({"error": "Desktop shell not built. Run: cd desktop && npm run build"}, status_code=404)
