from __future__ import annotations
import re
from pathlib import Path
from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse

from tinyagentos.themes.package import extract_theme_package, ThemePackageError

router = APIRouter()

# theme_id is interpolated into filesystem paths, so it must be a plain slug —
# never a value that could contain "/" or ".." and escape the themes root.
_THEME_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


def _valid_theme_id(theme_id: str) -> bool:
    return bool(_THEME_ID_RE.fullmatch(theme_id))


def _themes_root(request: Request) -> Path:
    return Path(request.app.state.data_dir) / "themes"

@router.get("/api/themes")
async def list_themes(request: Request):
    return await request.app.state.themes.list_installed()

@router.post("/api/themes/install")
async def install_theme(request: Request, package: UploadFile = File(...)):
    data = await package.read()
    try:
        manifest = extract_theme_package(data, themes_root=_themes_root(request))
    except ThemePackageError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    await request.app.state.themes.install(
        theme_id=manifest["id"], name=manifest["name"],
        version=manifest["version"], config={
            "tokens": manifest.get("tokens", {}), "structure": manifest.get("structure", {}),
            "effects": manifest.get("effects", []), "requires": manifest.get("requires", []),
            "wallpaper": manifest.get("wallpaper"),
        })
    return {"theme_id": manifest["id"]}

@router.delete("/api/themes/{theme_id}")
async def remove_theme(request: Request, theme_id: str):
    import shutil
    if not _valid_theme_id(theme_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    removed = await request.app.state.themes.remove(theme_id)
    root = _themes_root(request).resolve()
    d = (root / theme_id).resolve()
    if d.is_relative_to(root) and d != root and d.exists():
        shutil.rmtree(d, ignore_errors=True)
    return {"status": "ok", "removed": removed}

@router.get("/api/themes/{theme_id}/assets/{path:path}")
async def theme_asset(request: Request, theme_id: str, path: str):
    if not _valid_theme_id(theme_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    asset_root = (_themes_root(request) / theme_id / "assets").resolve()
    target = (asset_root / path).resolve()
    # asset_root can't escape the themes root (theme_id is a validated slug);
    # is_relative_to then blocks any "../" traversal via the {path:path} segment.
    if not target.is_relative_to(asset_root) or not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(target)
