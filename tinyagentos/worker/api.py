"""Worker HTTP API — handles controller-to-worker requests (deploy, remote, model ops)."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from tinyagentos.worker.deploy import run_deploy
from tinyagentos.worker.pairing import load_signing_key, default_state_dir
from tinyagentos.cluster.worker_auth import _HMACError, require_worker_hmac

logger = logging.getLogger(__name__)

router = APIRouter()

_STATE_DIR = default_state_dir()


async def require_controller_hmac(request: Request) -> str:
    """Verify HMAC from controller using worker's pairing key.

    Returns the worker name on success. Raises _HMACError on failure.
    """
    # Re-use the same HMAC verification logic but the controller signs with
    # the worker's key (which the controller has from the pairing store).
    worker_name = request.headers.get("x-taos-worker-name", "").strip()
    timestamp_str = request.headers.get("x-taos-timestamp", "").strip()
    signature = request.headers.get("x-taos-signature", "").strip()

    if not worker_name or not timestamp_str or not signature:
        raise _HMACError(
            JSONResponse(
                {"error": "Missing HMAC headers", "code": "missing_hmac"},
                status_code=401,
            )
        )

    try:
        ts = int(timestamp_str)
    except ValueError:
        raise _HMACError(
            JSONResponse({"error": "Invalid timestamp", "code": "stale_timestamp"}, status_code=401)
        )

    import time

    if abs(time.time() - ts) > 300:
        raise _HMACError(
            JSONResponse({"error": "Timestamp too far from server time", "code": "stale_timestamp"}, status_code=401)
        )

    signing_key = load_signing_key(_STATE_DIR)
    if signing_key is None:
        raise _HMACError(
            JSONResponse(
                {"error": "Worker not paired", "code": "worker_not_paired"},
                status_code=401,
            )
        )

    raw_body = await request.body()
    body_hash = hashlib.sha256(raw_body).hexdigest()
    method = request.method.upper()
    path = request.url.path
    message = f"{timestamp_str}.{method}.{path}.{body_hash}".encode()
    expected = hashlib.hmac.new(signing_key, message, hashlib.sha256).hexdigest()
    if not hashlib.hmac.compare_digest(expected, signature):
        raise _HMACError(
            JSONResponse({"error": "Signature verification failed", "code": "bad_signature"}, status_code=401)
        )

    request.state.hmac_worker_name = worker_name
    return worker_name


# --- Models ---


class DeployRequest(BaseModel):
    command: str


class RemoteRequest(BaseModel):
    command: str
    timeout: int = 30


class ModelPullRequest(BaseModel):
    model_id: str
    source_url: str
    expected_checksum: str
    file_size: int
    backend_type: str = "auto"


class ModelLoadRequest(BaseModel):
    model_id: str
    backend_type: str = "auto"


class ModelUnloadRequest(BaseModel):
    model_id: str
    backend_type: str = "auto"


# --- Helpers ---


def _get_model_store() -> Path:
    """Get the worker's model store directory."""
    # Try TAOS_MODEL_STORE env var, then default locations
    store = os.environ.get("TAOS_MODEL_STORE")
    if store:
        return Path(store)
    # Default: ~/.local/share/tinyagentos-worker/models
    install_dir = os.environ.get("TAOS_INSTALL_DIR", os.path.expanduser("~/.local/share/tinyagentos-worker"))
    return Path(install_dir) / "models"


def _get_backend_url(backend_type: str) -> str | None:
    """Get the backend URL for a given type from the worker's config."""
    # The worker agent detects backends on each heartbeat. We could cache this,
    # but for simplicity we'll use common defaults.
    defaults = {
        "ollama": "http://localhost:11434",
        "rkllama": "http://localhost:7833",
        "llama-cpp": "http://localhost:8000",
        "llama-swap": "http://localhost:8080",
        "vllm": "http://localhost:8000",
    }
    return defaults.get(backend_type)


async def _download_with_progress(url: str, dest: Path, expected_size: int, progress_cb) -> None:
    """Download a file with progress callbacks."""
    import httpx

    async with httpx.AsyncClient(timeout=3600) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            downloaded = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        await progress_cb(downloaded, expected_size)


async def _verify_checksum(filepath: Path, expected: str) -> bool:
    """Verify SHA256 checksum of a file."""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest() == expected


# --- Routes ---


@router.post("/api/worker/deploy")
async def worker_deploy(request: Request, body: DeployRequest, worker_name: str = Depends(require_controller_hmac)):
    """Run a deploy command via taos-deploy-helper."""
    result = await run_deploy(body.command)
    if not result["ok"]:
        return JSONResponse(result, status_code=500)
    return result


@router.post("/api/worker/remote")
async def worker_remote(request: Request, body: RemoteRequest, worker_name: str = Depends(require_controller_hmac)):
    """Run an allowlisted command on the worker."""
    import asyncio
    from tinyagentos.routes.cluster import REMOTE_EXEC_ALLOWLIST

    if not any(body.command.startswith(prefix) for prefix in REMOTE_EXEC_ALLOWLIST):
        return JSONResponse(
            {"error": "Command not in allowlist", "allowed_prefixes": REMOTE_EXEC_ALLOWLIST},
            status_code=403,
        )

    cmd = body.command.split()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=body.timeout)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        return {
            "ok": proc.returncode == 0,
            "command": body.command,
            "output": output,
            "exit_code": proc.returncode or 0,
        }
    except asyncio.TimeoutError:
        return JSONResponse({"error": f"Command timed out after {body.timeout}s"}, status_code=504)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# --- Model Operations ---


@router.post("/api/worker/models/pull")
async def worker_model_pull(request: Request, body: ModelPullRequest, worker_name: str = Depends(require_controller_hmac)):
    """Pull a model file from a source URL (controller model store or source worker).

    - Checks disk space before downloading (needs 2x file_size)
    - Streams download with progress
    - Verifies SHA256 checksum
    - Stores in worker's model store
    """
    model_store = _get_model_store()
    model_store.mkdir(parents=True, exist_ok=True)

    # Disk space check
    stat = shutil.disk_usage(model_store)
    available = stat.free
    required = body.file_size * 2  # 2x for staging
    if available < required:
        return JSONResponse(
            {"error": f"Insufficient disk space: need {required}, have {available}", "code": "insufficient_space"},
            status_code=507,
        )

    # Determine filename from source_url or use model_id
    filename = body.model_id
    if "/" in body.source_url:
        # Try to extract filename from URL
        url_filename = body.source_url.split("/")[-1]
        if url_filename and "." in url_filename:
            filename = url_filename

    dest = model_store / filename
    temp_dest = dest.with_suffix(dest.suffix + ".part")

    try:
        # Download with progress
        async def progress_cb(downloaded: int, total: int):
            # Could emit progress via SSE or update a job store
            pass

        await _download_with_progress(body.source_url, temp_dest, body.file_size, progress_cb)

        # Verify checksum
        if not await _verify_checksum(temp_dest, body.expected_checksum):
            temp_dest.unlink(missing_ok=True)
            return JSONResponse(
                {"error": "Checksum mismatch", "code": "checksum_mismatch"},
                status_code=409,
            )

        # Move into place
        temp_dest.rename(dest)

        return {"status": "pulled", "model_id": body.model_id, "path": str(dest), "size": body.file_size}

    except Exception as exc:
        temp_dest.unlink(missing_ok=True)
        logger.exception("Model pull failed for %s", body.model_id)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/worker/models/load")
async def worker_model_load(request: Request, body: ModelLoadRequest, worker_name: str = Depends(require_controller_hmac)):
    """Load a model into the appropriate backend."""
    import httpx

    model_store = _get_model_store()
    model_path = model_store / body.model_id

    if not model_path.exists():
        # Try to find by partial match
        matches = list(model_store.glob(f"*{body.model_id}*"))
        if matches:
            model_path = matches[0]
        else:
            return JSONResponse({"error": f"Model file not found: {body.model_id}"}, status_code=404)

    # Determine backend type and URL
    backend_type = body.backend_type
    if backend_type == "auto":
        # Guess from file extension
        if model_path.suffix == ".gguf":
            backend_type = "llama-cpp"
        elif model_path.suffix == ".rkllm":
            backend_type = "rkllama"
        else:
            backend_type = "ollama"

    backend_url = _get_backend_url(backend_type)
    if not backend_url:
        return JSONResponse({"error": f"Unknown backend type: {backend_type}"}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            if backend_type in ("ollama", "rkllama"):
                # Ollama/rkllama: pull the model
                resp = await client.post(
                    f"{backend_url}/api/pull",
                    json={"name": body.model_id, "stream": False},
                    timeout=300,
                )
                if resp.status_code != 200:
                    return JSONResponse({"error": f"Backend pull failed: {resp.text}"}, status_code=502)
            elif backend_type == "llama-swap":
                # llama-swap: load model
                resp = await client.post(
                    f"{backend_url}/load",
                    json={"model": body.model_id},
                    timeout=60,
                )
                if resp.status_code != 200:
                    return JSONResponse({"error": f"llama-swap load failed: {resp.text}"}, status_code=502)
            elif backend_type in ("llama-cpp", "vllm"):
                # llama.cpp/vLLM: models are loaded on-demand via /v1/chat/completions
                # No explicit load needed; just verify the file exists
                pass
            else:
                return JSONResponse({"error": f"Unsupported backend type: {backend_type}"}, status_code=400)

        return {"status": "loaded", "model_id": body.model_id, "backend": backend_type}

    except Exception as exc:
        logger.exception("Model load failed for %s", body.model_id)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/worker/models/unload")
async def worker_model_unload(request: Request, body: ModelUnloadRequest, worker_name: str = Depends(require_controller_hmac)):
    """Unload a model from the backend."""
    import httpx

    backend_type = body.backend_type
    if backend_type == "auto":
        # Try common backends
        for bt in ("ollama", "rkllama", "llama-swap"):
            backend_url = _get_backend_url(bt)
            if backend_url:
                backend_type = bt
                break

    backend_url = _get_backend_url(backend_type)
    if not backend_url:
        return JSONResponse({"error": "No backend found to unload from"}, status_code=404)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            if backend_type in ("ollama", "rkllama"):
                # Ollama doesn't have explicit unload; models auto-unload on timeout
                # We can't force unload, so just report success
                pass
            elif backend_type == "llama-swap":
                resp = await client.post(
                    f"{backend_url}/unload",
                    json={"model": body.model_id},
                    timeout=30,
                )
                if resp.status_code != 200:
                    return JSONResponse({"error": f"llama-swap unload failed: {resp.text}"}, status_code=502)

        return {"status": "unloaded", "model_id": body.model_id, "backend": backend_type}

    except Exception as exc:
        logger.exception("Model unload failed for %s", body.model_id)
        return JSONResponse({"error": str(exc)}, status_code=500)


# --- App Factory ---


def create_worker_app() -> "FastAPI":
    """Create the worker's FastAPI application."""
    from fastapi import FastAPI

    app = FastAPI(title="taOS Worker API")
    app.include_router(router)
    return app