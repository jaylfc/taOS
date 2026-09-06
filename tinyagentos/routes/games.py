from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
import httpx

from tinyagentos.userspace.package import PackageError, build_package

router = APIRouter()

# --------------------------------------------------------------------------
# Game persistence (Game Studio) — mirrors routes/video.py's storage pattern:
# a per-item directory under the data dir, filename traversal guards, a
# metadata sidecar (here game.json). A game is data/games/{game_id}/ holding
# game.json + a flat set of web files (index.html, game.js, three.module.js,
# ...). No subdirectories in v1 -- every file lives directly in the game's
# folder, so a single "no '/', '\\' or '..'" filename check is a complete
# traversal guard (same posture as video.py's filename check).
# --------------------------------------------------------------------------

GAMES_DIR_NAME = "games"
_SLUG_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_FILES = 40

# Generated binary assets (game_assets.py textures now; audio/3D meshes later)
# that the text editor does not manage; a full-replace save must never sweep
# them, even if their bytes happen to decode as UTF-8.
_BINARY_ASSET_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".glb", ".gltf", ".wav", ".mp3", ".ogg",
}


class GameSaveRequest(BaseModel):
    name: str | None = None
    prompt: str | None = None
    template: str | None = None
    files: dict[str, str]


def _games_dir(request: Request) -> Path:
    """Return the games directory, creating it if needed."""
    data_dir = getattr(request.app.state, "data_dir", None)
    if data_dir:
        d = Path(data_dir) / GAMES_DIR_NAME
    else:
        d = Path(__file__).parent.parent.parent / "data" / GAMES_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_safe_filename(name: str) -> bool:
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name and ".." not in name


def _load_meta(game_dir: Path) -> dict | None:
    meta_path = game_dir / "game.json"
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _list_game_files(game_dir: Path) -> dict[str, str]:
    """Read every non-metadata file in a game's directory into a flat {name: text} map."""
    files: dict[str, str] = {}
    for p in sorted(game_dir.iterdir()):
        if not p.is_file() or p.name == "game.json":
            continue
        try:
            files[p.name] = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return files


@router.get("/api/games")
async def list_games(request: Request):
    """List all saved games' metadata, most recently updated first."""
    games_dir = _games_dir(request)
    results = []
    for d in sorted(games_dir.iterdir()):
        if not d.is_dir():
            continue
        meta = _load_meta(d)
        if meta is not None:
            results.append(meta)
    results.sort(key=lambda g: g.get("updated", 0), reverse=True)
    return {"games": results}


@router.get("/api/games/{game_id}")
async def get_game(request: Request, game_id: str):
    """Return a game's metadata plus its full file map."""
    if not _SLUG_RE.match(game_id):
        return JSONResponse({"error": "invalid game id"}, status_code=400)
    game_dir = _games_dir(request) / game_id
    meta = _load_meta(game_dir)
    if meta is None:
        return JSONResponse({"error": f"game '{game_id}' not found"}, status_code=404)
    return {**meta, "files": _list_game_files(game_dir)}


@router.put("/api/games/{game_id}")
async def save_game(request: Request, game_id: str, body: GameSaveRequest):
    """Create or update a game. Full-replace semantics: any existing file not
    present in the new file map is deleted, so the request body is always the
    authoritative file set (matches the editor's "current file set" model)."""
    if not _SLUG_RE.match(game_id):
        return JSONResponse({"error": "invalid game id: must match [a-z0-9-]"}, status_code=400)
    if not body.files:
        return JSONResponse({"error": "files must be a non-empty map of filename to text content"}, status_code=400)
    if len(body.files) > _MAX_FILES:
        return JSONResponse({"error": f"too many files (max {_MAX_FILES})"}, status_code=413)
    for name, content in body.files.items():
        if not _is_safe_filename(name):
            return JSONResponse({"error": f"invalid filename: {name!r}"}, status_code=400)
        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            return JSONResponse(
                {"error": f"file '{name}' exceeds the {_MAX_FILE_BYTES}-byte limit"}, status_code=413
            )

    games_dir = _games_dir(request)
    game_dir = games_dir / game_id
    existing = _load_meta(game_dir)
    now = time.time()
    game_dir.mkdir(parents=True, exist_ok=True)

    for p in game_dir.iterdir():
        if not (p.is_file() and p.name != "game.json" and p.name not in body.files):
            continue
        # The editor's file set is text-only (_list_game_files skips undecodable
        # files), so generated binary assets (textures/sprites written by
        # routes/game_assets.py) never appear in `body.files`. Preserve them
        # instead of sweeping them: full-replace semantics apply only to the
        # text files the editor actually manages. Match a known binary-asset
        # extension first (a binary payload that happens to decode as UTF-8 —
        # e.g. a GLB whose bytes are coincidentally valid — must not be swept),
        # then fall back to an undecodable-bytes check for anything unlisted.
        if p.suffix.lower() in _BINARY_ASSET_EXTS:
            continue
        try:
            p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        p.unlink()
    for name, content in body.files.items():
        (game_dir / name).write_text(content, encoding="utf-8")

    meta = {
        "id": game_id,
        "name": body.name or (existing or {}).get("name") or game_id,
        "prompt": body.prompt if body.prompt is not None else (existing or {}).get("prompt", ""),
        "template": body.template or (existing or {}).get("template", ""),
        "created": (existing or {}).get("created", now),
        "updated": now,
    }
    (game_dir / "game.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {**meta, "files": body.files}


@router.delete("/api/games/{game_id}")
async def delete_game(request: Request, game_id: str):
    if not _SLUG_RE.match(game_id):
        return JSONResponse({"error": "invalid game id"}, status_code=400)
    games_dir = _games_dir(request).resolve()
    game_dir = (games_dir / game_id).resolve()
    if not game_dir.is_relative_to(games_dir) or game_dir == games_dir or not game_dir.is_dir():
        return JSONResponse({"error": f"game '{game_id}' not found"}, status_code=404)
    shutil.rmtree(game_dir)
    return {"status": "deleted", "id": game_id}


# Sandbox CSP for the live preview iframe: `sandbox allow-scripts` with NO
# allow-same-origin forces an opaque origin even on direct navigation (same
# posture as the userspace bundle CSP in routes/userspace_apps.py), so a
# previewed game can never run with any credential or reach any origin beyond
# itself. Games get no network permissions in v1 (connect-src is 'self' only,
# unconditionally -- there is no per-game grant system to widen it).
_GAME_PREVIEW_CSP = (
    "sandbox allow-scripts; "
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; base-uri 'none'"
)


@router.get("/api/games/{game_id}/preview/{path:path}")
async def preview_game_file(request: Request, game_id: str, path: str):
    """Serve one of a game's files for the live-preview iframe, under a strict CSP."""
    if not _SLUG_RE.match(game_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    games_dir = _games_dir(request).resolve()
    game_dir = (games_dir / game_id).resolve()
    if not game_dir.is_relative_to(games_dir) or not game_dir.is_dir():
        return JSONResponse({"error": "not found"}, status_code=404)
    target = (game_dir / path).resolve()
    if (
        not target.is_relative_to(game_dir)
        or target == game_dir
        or not target.is_file()
        or target.name == "game.json"
    ):
        return JSONResponse({"error": "not found"}, status_code=404)
    resp = FileResponse(target)
    resp.headers["Content-Security-Policy"] = _GAME_PREVIEW_CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@router.get("/api/games/{game_id}/package")
async def package_game(request: Request, game_id: str):
    """Build a .taosapp package (manifest.yaml + the game's files) and return it
    as a downloadable zip. Reused both for "Export .taosapp" (download as-is)
    and "Install on this taOS" (the frontend fetches this, then POSTs the
    bytes to the existing /api/userspace-apps/install endpoint)."""
    if not _SLUG_RE.match(game_id):
        return JSONResponse({"error": "invalid game id"}, status_code=400)
    game_dir = _games_dir(request) / game_id
    meta = _load_meta(game_dir)
    if meta is None:
        return JSONResponse({"error": f"game '{game_id}' not found"}, status_code=404)
    files = _list_game_files(game_dir)
    manifest = {
        "id": game_id,
        "name": meta.get("name") or game_id,
        "version": "1.0.0",
        "app_type": "web",
        "entry": "index.html",
        "icon": "",
        "permissions": [],
    }
    try:
        data = build_package(manifest, files)
    except PackageError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{game_id}.taosapp"'},
    )


@router.post("/api/games/chess/move")
async def chess_move(request: Request):
    """Ask an agent to pick a chess move.

    Request: {agent_name, fen, legal_moves: [...], history: [...]}
    Response: {move, commentary}
    """
    body = await request.json()
    agent_name = body.get("agent_name")
    fen = body.get("fen", "")
    legal_moves = body.get("legal_moves", [])
    history = body.get("history", [])

    if not agent_name or not legal_moves:
        return JSONResponse({"error": "Missing agent_name or legal_moves"}, status_code=400)

    # Build prompt for the agent
    prompt = f"""You are playing chess. Current position (FEN): {fen}

Move history: {' '.join(history) if history else 'Game start'}

Your legal moves: {', '.join(legal_moves)}

Pick your next move. Respond with ONLY the move in UCI format (e.g. e2e4) from the legal moves list. No explanation."""

    # Resolve the agent's adapter URL via the channel hub router
    url = None
    try:
        hub_router = getattr(request.app.state, "channel_hub_router", None)
        if hub_router is not None:
            port = None
            if hasattr(hub_router, "get_adapter_port"):
                port = hub_router.get_adapter_port(agent_name)
            if port:
                url = f"http://localhost:{port}"
        # Fallback: some deployments expose an adapter manager with get_adapter_url
        if url is None:
            adapter_mgr = getattr(request.app.state, "channel_hub", None)
            if adapter_mgr and hasattr(adapter_mgr, "get_adapter_url"):
                url = adapter_mgr.get_adapter_url(agent_name)
    except Exception:
        url = None

    if not url:
        # Fallback: random legal move
        import random
        return JSONResponse({
            "move": random.choice(legal_moves),
            "commentary": f"(Agent '{agent_name}' not reachable — random move)"
        })

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{url}/message", json={"text": prompt, "from_name": "Chess"})
            data = resp.json()
            response_text = (data.get("content") or data.get("text") or "").strip()

            # Find a legal move in the response
            for move in legal_moves:
                if move in response_text:
                    return JSONResponse({"move": move, "commentary": response_text})

            # No valid move found, return random
            import random
            return JSONResponse({
                "move": random.choice(legal_moves),
                "commentary": f"Agent returned: {response_text[:100]}"
            })
    except Exception as e:
        import random
        return JSONResponse({
            "move": random.choice(legal_moves),
            "commentary": f"Error: {str(e)[:100]}"
        })
