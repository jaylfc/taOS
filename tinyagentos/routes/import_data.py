from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".html", ".json", ".csv"}

UPLOAD_DIR = Path(tempfile.gettempdir()) / "tinyagentos_imports"

_SAFE_AGENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _upload_path(filename: str) -> Path | None:
    """Map a client-supplied filename to a path INSIDE ``UPLOAD_DIR``.

    ``Path("a") / "/etc/x"`` silently discards the left operand, so an
    absolute or ``..`` filename would let any authenticated user write
    (upload) or read (embed) any file the server process can reach
    (GHSA-rwrp-hfc4-qg2w). Browsers only ever send a bare basename, so
    anything with a separator, a NUL, or a leading dot is rejected outright.
    """
    if not filename or "/" in filename or "\\" in filename or "\x00" in filename:
        return None
    if filename in {".", ".."} or filename.startswith("."):
        return None
    dest = UPLOAD_DIR / filename
    if dest.resolve().parent != UPLOAD_DIR.resolve():
        return None
    return dest


@router.post("/api/import/upload")
async def upload_file(request: Request, file: UploadFile):
    if not file.filename:
        return JSONResponse({"error": "No file provided"}, status_code=400)

    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return JSONResponse(
            {"error": f"Unsupported format '{ext}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"},
            status_code=400,
        )

    dest = _upload_path(file.filename)
    if dest is None:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    size = dest.stat().st_size
    return {
        "status": "uploaded",
        "filename": dest.name,
        "size": size,
        "path": str(dest),
    }


@router.post("/api/import/embed")
async def embed_files(request: Request):
    body = await request.json()
    agent_name = body.get("agent_name")
    filenames = body.get("files", [])

    if not agent_name:
        return JSONResponse({"error": "agent_name is required"}, status_code=400)
    if not filenames:
        return JSONResponse({"error": "No files specified"}, status_code=400)
    if not isinstance(agent_name, str) or not _SAFE_AGENT_NAME.match(agent_name):
        return JSONResponse({"error": "Invalid agent_name"}, status_code=400)

    # Resolve every name INSIDE UPLOAD_DIR before touching the filesystem;
    # a traversal name is a 400, not a read of whatever it points at.
    resolved: dict[str, Path] = {}
    for f in filenames:
        p = _upload_path(f) if isinstance(f, str) else None
        if p is None:
            return JSONResponse({"error": f"Invalid filename: {f!r}"}, status_code=400)
        resolved[f] = p

    # Verify files exist
    missing = [f for f, p in resolved.items() if not p.exists()]
    if missing:
        return JSONResponse({"error": f"Files not found: {', '.join(missing)}"}, status_code=404)

    # Persist each file into the agent's per-agent index via the shared
    # qmd serve POST /ingest endpoint. The dbPath we pass is the same
    # one the deployer bind-mounts into the agent container at /memory,
    # so the agent and its container see identical state.
    # See docs/design/framework-agnostic-runtime.md.
    http_client = request.app.state.http_client
    qmd_base = request.app.state.qmd_client.base_url
    agent_db = (
        Path(request.app.state.agent_memory_dir) / agent_name / "index.sqlite"
    )
    agent_db.parent.mkdir(parents=True, exist_ok=True)

    embedded_files = []
    all_embedded = True

    for fname in filenames:
        fpath = resolved[fname]
        text = fpath.read_text(errors="replace")
        file_embedded = False

        try:
            resp = await http_client.post(
                f"{qmd_base}/ingest",
                json={
                    "body": text,
                    "path": fname,
                    "title": fname,
                    "collection": "imports",
                    "dbPath": str(agent_db),
                },
                timeout=120,
            )
            resp.raise_for_status()
            file_embedded = True
        except Exception as exc:
            logger.warning(
                "QMD ingest failed for agent %s file %s: %s",
                agent_name, fname, exc,
            )
            all_embedded = False

        embedded_files.append({
            "filename": fname,
            "size": fpath.stat().st_size,
            "embedded": file_embedded,
        })

    return {
        "status": "embedded",
        "agent_name": agent_name,
        "files": embedded_files,
        "count": len(embedded_files),
        "embedded": all_embedded,
    }
