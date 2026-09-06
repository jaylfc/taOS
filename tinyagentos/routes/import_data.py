from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".html", ".json", ".csv"}
UPLOAD_DIR = Path(tempfile.gettempdir()) / "tinyagentos_imports"


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

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOAD_DIR / file.filename
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    size = dest.stat().st_size
    return {
        "status": "uploaded",
        "filename": file.filename,
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

    # Verify files exist
    missing = [f for f in filenames if not (UPLOAD_DIR / f).exists()]
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
        fpath = UPLOAD_DIR / fname
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
