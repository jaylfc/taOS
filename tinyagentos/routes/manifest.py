"""Dynamic PWA manifest endpoint.

GET /manifest?app=<id> returns a Web App Manifest JSON for apps that are
flagged pwa:true on the frontend. This mirrors the frontend pwa:true flag in
app-registry.ts; a fuller DRY source shared between frontend and backend is a
follow-up.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()

# Maps app id -> manifest metadata for each pwa:true app in app-registry.ts.
# Mirrors the frontend pwa:true flag; a shared source-of-truth is a follow-up.
_PWA_APPS: dict[str, dict] = {
    "messages": {
        "name": "taOS talk",
        "short_name": "taOS talk",
        "theme_color": "#141415",
        "background_color": "#141415",
    },
}


@router.get("/manifest")
async def get_manifest(app: str) -> JSONResponse:
    """Return a Web App Manifest for a PWA-enabled app.

    Returns 404 for unknown or non-PWA app ids.
    """
    meta = _PWA_APPS.get(app)
    if not meta:
        raise HTTPException(status_code=404, detail="App not found or not PWA-enabled")

    manifest = {
        "name": meta["name"],
        "short_name": meta["short_name"],
        "id": f"/app.html?app={app}",
        "start_url": f"/app.html?app={app}",
        "scope": "/",
        "display": "standalone",
        "theme_color": meta["theme_color"],
        "background_color": meta["background_color"],
        "icons": [
            {
                "src": "/static/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "/static/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
    }
    return JSONResponse(
        content=manifest,
        headers={"Content-Type": "application/manifest+json"},
    )
