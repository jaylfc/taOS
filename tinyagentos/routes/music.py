# tinyagentos/routes/music.py
from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

# stable-audio-open is cataloged but has no runnable compose path yet.
MUSIC_SERVICE_IDS = ("musicgpt", "musicgen")
DEFAULT_MUSICGPT_PORT = 30264


class ComposeRequest(BaseModel):
    prompt: str
    duration: int = Field(default=10, ge=1, le=30)


def _music_dir(request: Request) -> Path:
    """Return workspace/music/generated, creating it if needed."""
    config_path = getattr(request.app.state, "config_path", None)
    if config_path is not None:
        data_dir = Path(config_path).parent
    else:
        data_dir = Path(__file__).parent.parent.parent / "data"
    d = data_dir / "workspace" / "music" / "generated"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _music_url_path(filename: str) -> str:
    return f"/data/workspace/music/generated/{filename}"


def _apps_dir(request: Request) -> Path | None:
    apps_dir = getattr(request.app.state, "apps_dir", None)
    if apps_dir is not None:
        return Path(apps_dir)
    data_dir = getattr(request.app.state, "data_dir", None)
    if data_dir is not None:
        return Path(data_dir) / "apps"
    return None


def _service_python(request: Request, app_id: str) -> Path | None:
    root = _apps_dir(request)
    if root is None:
        return None
    py = root / app_id / "venv" / "bin" / "python"
    return py if py.exists() else None


async def _store_ready(store: object | None) -> bool:
    return store is not None and getattr(store, "_db", None) is not None


async def _is_service_installed(request: Request, app_id: str) -> bool:
    store = getattr(request.app.state, "installed_apps", None)
    if await _store_ready(store) and await store.is_installed(app_id):
        return True
    registry = getattr(request.app.state, "registry", None)
    if registry is not None and registry.is_installed(app_id):
        return True
    installation = getattr(request.app.state, "installation_state", None)
    if installation is not None and installation.is_installed(app_id):
        return True
    if app_id == "musicgpt" and shutil.which("musicgpt"):
        return True
    if app_id == "musicgen" and _service_python(request, app_id):
        return True
    return False


async def _http_backend_reachable(backend_url: str) -> bool:
    """Return True when an HTTP music backend responds on a lightweight probe."""
    base = backend_url.rstrip("/")
    async with httpx.AsyncClient(timeout=3) as client:
        for path in ("/health", "/v1/models", "/"):
            try:
                resp = await client.get(f"{base}{path}")
                if resp.status_code < 500:
                    return True
            except httpx.TransportError:
                continue
    return False


def _normalized_origin(url: str) -> tuple[str, str, int] | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return (parsed.scheme.lower(), parsed.hostname.lower(), port)


def _same_backend_host(download_url: str, backend_url: str) -> bool:
    """Only follow backend-provided URLs that match the trusted backend origin."""
    dl_origin = _normalized_origin(download_url)
    be_origin = _normalized_origin(backend_url)
    if dl_origin is None or be_origin is None:
        return False
    return dl_origin == be_origin


async def _resolve_music_backend(
    request: Request,
) -> tuple[str | None, str | None, str]:
    """Return (backend_id, backend_url, mode).

    mode is one of: http, musicgpt-cli, musicgen-cli
    """
    config = request.app.state.config
    override = config.server.get("music_backend_url")
    if override:
        url = str(override)
        if await _http_backend_reachable(url):
            return "music-backend", url, "http"

    store = getattr(request.app.state, "installed_apps", None)
    if await _store_ready(store):
        for app_id in MUSIC_SERVICE_IDS:
            if not await store.is_installed(app_id):
                continue
            loc = await store.get_runtime_location(app_id)
            if loc and loc.get("runtime_host") and loc.get("runtime_port"):
                host = loc["runtime_host"]
                port = loc["runtime_port"]
                url = f"http://{host}:{port}"
                if await _http_backend_reachable(url):
                    return app_id, url, "http"
            if app_id == "musicgpt":
                url = f"http://127.0.0.1:{DEFAULT_MUSICGPT_PORT}"
                if await _http_backend_reachable(url):
                    return app_id, url, "http"

    for app_id in MUSIC_SERVICE_IDS:
        if not await _is_service_installed(request, app_id):
            continue
        if app_id == "musicgpt" and shutil.which("musicgpt"):
            return app_id, None, "musicgpt-cli"
        if app_id == "musicgen" and _service_python(request, "musicgen"):
            return app_id, None, "musicgen-cli"

    return None, None, ""


def _list_tracks(music_dir: Path) -> list[dict]:
    results = []
    for ext in ("*.wav", "*.mp3"):
        for audio in music_dir.glob(ext):
            meta_path = audio.with_suffix(".json")
            metadata: dict = {}
            if meta_path.exists():
                try:
                    metadata = json.loads(meta_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
            results.append({
                "filename": audio.name,
                "path": _music_url_path(audio.name),
                "size_bytes": audio.stat().st_size,
                "prompt": metadata.get("prompt", ""),
                "duration": metadata.get("duration", 0),
                "backend": metadata.get("backend", ""),
            })
    results.sort(key=lambda x: x["filename"], reverse=True)
    return results


async def _compose_via_http(
    backend_url: str,
    *,
    prompt: str,
    duration: int,
) -> bytes:
    payload = {
        "prompt": prompt,
        "duration": duration,
        "response_format": "b64_json",
    }
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{backend_url.rstrip('/')}/v1/audio/generations",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    try:
        entry = data["data"][0]
    except (KeyError, IndexError) as exc:
        raise RuntimeError("Unexpected response format from music backend") from exc

    b64 = entry.get("b64_json") or entry.get("b64_wav")
    if b64:
        return base64.b64decode(b64)

    url = entry.get("url")
    if not url:
        raise RuntimeError("Music backend returned neither b64 data nor url")
    if not _same_backend_host(url, backend_url):
        raise RuntimeError("Music backend returned an untrusted download URL")

    async with httpx.AsyncClient(timeout=300) as client:
        dl = await client.get(url)
        dl.raise_for_status()
        return dl.content


async def _compose_via_musicgpt_cli(
    *,
    prompt: str,
    duration: int,
    output_path: Path,
) -> None:
    binary = shutil.which("musicgpt")
    if not binary:
        raise RuntimeError("musicgpt binary not found on PATH")

    proc = await asyncio.create_subprocess_exec(
        binary,
        prompt,
        "--secs",
        str(duration),
        "--output",
        str(output_path),
        "--no-playback",
        "--no-interactive",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"musicgpt failed: {detail}")
    if not output_path.exists():
        raise RuntimeError("musicgpt did not produce an output file")


_MUSICGEN_SCRIPT = """
import sys
from pathlib import Path

prompt = sys.argv[1]
duration = float(sys.argv[2])
output = Path(sys.argv[3])

from audiocraft.models import MusicGen

model = MusicGen.get_pretrained("facebook/musicgen-small")
model.set_generation_params(duration=duration)
wav = model.generate([prompt])
import scipy.io.wavfile
scipy.io.wavfile.write(str(output), model.sample_rate, wav[0].cpu().numpy().T)
"""


async def _compose_via_musicgen_cli(
    request: Request,
    *,
    prompt: str,
    duration: int,
    output_path: Path,
) -> None:
    python = _service_python(request, "musicgen")
    if python is None:
        raise RuntimeError("musicgen venv not found")

    proc = await asyncio.create_subprocess_exec(
        str(python),
        "-c",
        _MUSICGEN_SCRIPT,
        prompt,
        str(duration),
        str(output_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"musicgen failed: {detail}")
    if not output_path.exists():
        raise RuntimeError("musicgen did not produce an output file")


@router.get("/api/music/status")
async def music_status(request: Request):
    """Report whether a music generation backend is available."""
    backend_id, backend_url, mode = await _resolve_music_backend(request)
    installed = []
    for app_id in MUSIC_SERVICE_IDS:
        if await _is_service_installed(request, app_id):
            installed.append(app_id)
    return {
        "available": backend_id is not None,
        "backend": backend_id,
        "mode": mode or None,
        "backend_url": backend_url,
        "installed": installed,
    }


@router.post("/api/music/compose")
async def compose_music(request: Request, body: ComposeRequest):
    """Generate audio from a text prompt using an installed music backend."""
    prompt = body.prompt.strip()
    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)

    backend_id, backend_url, mode = await _resolve_music_backend(request)
    if not backend_id:
        installed = [
            app_id
            for app_id in MUSIC_SERVICE_IDS
            if await _is_service_installed(request, app_id)
        ]
        if installed:
            names = ", ".join(installed)
            return JSONResponse(
                {
                    "error": (
                        f"Music backend(s) installed ({names}) but not runnable yet. "
                        "Start the service runtime or install musicgpt/musicgen."
                    ),
                },
                status_code=503,
            )
        return JSONResponse(
            {
                "error": (
                    "No music generation backend installed. "
                    "Install musicgpt or musicgen from the Store."
                ),
            },
            status_code=503,
        )

    music_dir = _music_dir(request)
    timestamp = int(time.time())
    track_id = uuid.uuid4().hex[:8]
    filename = f"{timestamp}_{track_id}.wav"
    output_path = music_dir / filename

    try:
        if mode == "http":
            assert backend_url is not None
            audio_bytes = await _compose_via_http(
                backend_url,
                prompt=prompt,
                duration=body.duration,
            )
            output_path.write_bytes(audio_bytes)
        elif mode == "musicgpt-cli":
            await _compose_via_musicgpt_cli(
                prompt=prompt,
                duration=body.duration,
                output_path=output_path,
            )
        elif mode == "musicgen-cli":
            await _compose_via_musicgen_cli(
                request,
                prompt=prompt,
                duration=body.duration,
                output_path=output_path,
            )
        else:
            return JSONResponse(
                {"error": f"Backend {backend_id!r} is installed but not runnable yet."},
                status_code=503,
            )

        metadata = {
            "prompt": prompt,
            "duration": body.duration,
            "backend": backend_id,
            "filename": filename,
        }
        (music_dir / f"{timestamp}_{track_id}.json").write_text(
            json.dumps(metadata, indent=2),
        )

        return {
            "status": "generated",
            "filename": filename,
            "path": _music_url_path(filename),
            "size_bytes": output_path.stat().st_size,
            **metadata,
        }
    except httpx.ConnectError:
        return JSONResponse(
            {"error": "Cannot connect to music backend. Is it running?"},
            status_code=503,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": "Music generation timed out. The backend may be busy."},
            status_code=504,
        )
    except httpx.HTTPStatusError as exc:
        return JSONResponse(
            {"error": f"Music backend returned error: {exc.response.status_code}"},
            status_code=502,
        )
    except Exception:
        logger.exception("music compose failed")
        return JSONResponse(
            {"error": "Music generation failed. Check server logs for details."},
            status_code=500,
        )


@router.get("/api/music")
async def list_music(request: Request):
    """List generated music tracks, newest first."""
    return {"tracks": _list_tracks(_music_dir(request))}