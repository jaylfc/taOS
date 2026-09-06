"""LoRA Studio routes -- ingest, archive, and browse Civitai LoRA models.

POST   /api/loras/ingest            -- accept a Civitai model-page URL
GET    /api/loras                   -- list archived LoRAs
GET    /api/loras/{id}              -- one row
GET    /api/loras/{id}/preview/{n}  -- serve a stored preview image
DELETE /api/loras/{id}              -- remove row + files
POST   /api/loras/{id}/retry        -- re-run a failed ingest

Egress: the geo-block (Civitai returns HTTP 451 to UK addresses) is worked
around with an explicit, opt-in proxy (config key ``lora_ingest_proxy_url``,
default direct). It is passed per-request to the httpx clients used here
ONLY -- nothing else in taOS changes its egress behaviour.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, JSONResponse

from tinyagentos.installers.download_installer import download_file
from tinyagentos.installers.model_paths import models_root
from tinyagentos.lora_store import LoraStore
from tinyagentos.task_utils import _create_supervised_task

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_CIVITAI_HOSTS = frozenset({"civitai.com", "civitai.red"})
LORA_MODEL_TYPES = frozenset({"LORA", "LoCon", "DoRA"})
MAX_PREVIEW_IMAGES = 4

# Module-level background task tracking so unreferenced tasks are not
# garbage-collected when request.app.state._background_tasks is absent
# (mirrors tinyagentos/routes/library.py's idiom).
_background_tasks: set[asyncio.Task] = set()


def _track_background_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)

    def _on_done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)

    task.add_done_callback(_on_done)
    _background_tasks.add(task)
    return task


def _schedule(request: Request, coro) -> None:
    task_set = getattr(request.app.state, "_background_tasks", None)
    if task_set is None:
        _track_background_task(coro)
    else:
        _create_supervised_task(coro, task_set)


# ---------------------------------------------------------------------------
# URL parsing / slugging
# ---------------------------------------------------------------------------


class CivitaiUrlError(ValueError):
    """A Civitai URL failed the host allowlist or the /models/<id> shape."""


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower().strip()).strip("-")


def lora_slug(url_slug: str | None, model_id: int) -> str:
    """Return the directory/id slug for a Civitai model.

    Prefers the human-readable slug already present in the model URL
    (``/models/<id>/<slug>``); always appends the numeric model id so the
    slug is guaranteed unique even without one.
    """
    base = _slugify(url_slug) if url_slug else ""
    return f"{base}-{model_id}" if base else str(model_id)


def slug_from_lora_id(lora_id: str) -> str:
    return lora_id[len("lora-"):] if lora_id.startswith("lora-") else lora_id


def parse_civitai_url(url: str) -> tuple[int, str | None, int | None]:
    """Parse a Civitai model URL into (model_id, url_slug, version_id).

    Accepts ``/models/<id>`` and ``/models/<id>/<slug>``, with an optional
    ``?modelVersionId=`` query param. Raises CivitaiUrlError for any other
    host or shape.
    """
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise CivitaiUrlError(f"Could not parse URL {url!r}: {e}") from e

    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in ALLOWED_CIVITAI_HOSTS:
        raise CivitaiUrlError(
            f"Unsupported host {host!r}; only {sorted(ALLOWED_CIVITAI_HOSTS)} are allowed"
        )

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0] != "models":
        raise CivitaiUrlError(f"Could not find a model id in {url!r} (expected /models/<id>)")
    try:
        model_id = int(parts[1])
    except ValueError:
        raise CivitaiUrlError(f"Model id {parts[1]!r} in {url!r} is not numeric") from None

    url_slug = parts[2] if len(parts) > 2 else None

    version_id: int | None = None
    qs = parse_qs(parsed.query)
    if "modelVersionId" in qs:
        try:
            version_id = int(qs["modelVersionId"][0])
        except (ValueError, IndexError):
            version_id = None

    return model_id, url_slug, version_id


def is_civitai_url(url: str) -> bool:
    """True if *url*'s host is on the Civitai allowlist (used by detect_kind)."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if host.startswith("www."):
        host = host[4:]
    return host in ALLOWED_CIVITAI_HOSTS


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def loras_root() -> Path:
    """LoRA Studio owns models_root()/loras/ -- routes/models.py excludes it."""
    return models_root() / "loras"


# ---------------------------------------------------------------------------
# Ingest job
# ---------------------------------------------------------------------------


class CivitaiIngestError(Exception):
    """A specific, human-readable ingest failure reason."""


def _civitai_api_base(source_url: str) -> str:
    parsed = urlparse(source_url)
    return f"{parsed.scheme}://{parsed.hostname}"


async def _fetch_model(api_base: str, model_id: int, proxy_url: str) -> dict:
    url = f"{api_base}/api/v1/models/{model_id}"
    async with httpx.AsyncClient(
        proxy=proxy_url or None, trust_env=False, timeout=30,
    ) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return resp.json()


def _pick_version(model_json: dict, explicit_version_id: int | None) -> dict:
    versions = model_json.get("modelVersions") or []
    if not versions:
        raise CivitaiIngestError("Civitai model has no versions")
    if explicit_version_id is not None:
        for v in versions:
            if v.get("id") == explicit_version_id:
                return v
        raise CivitaiIngestError(f"Model version {explicit_version_id} not found")
    return versions[0]


def _pick_file(version: dict) -> dict:
    """Pick the .safetensors file to archive, preferring the primary one.

    The extension is checked on BOTH passes: Civitai can mark a non-safetensors
    file (a .bin pickle, a training config) as primary, and archiving that would
    contradict both this function's error message and what LoRA Studio stores.
    """
    files = version.get("files") or []
    for f in files:
        if f.get("primary") and _is_safetensors(f):
            return f
    for f in files:
        if _is_safetensors(f):
            return f
    raise CivitaiIngestError("No .safetensors file found on this model version")


def _is_safetensors(file_entry: dict) -> bool:
    return (file_entry.get("name") or "").lower().endswith(".safetensors")


def _safe_filename(raw: str, fallback: str) -> str:
    """Reduce a Civitai-supplied file name to a single safe path component.

    The name comes from a remote API response, so it is untrusted: an absolute
    path or one containing ``..`` would otherwise escape ``loras_root()`` when
    joined to the LoRA directory (``Path("/a") / "/tmp/x"`` is ``/tmp/x``), and
    an escaped file is invisible to both the failure cleanup and the delete
    route, which are anchored on that root.
    """
    name = PurePosixPath(str(raw or "").replace("\\", "/")).name.strip()
    if not name or name in {".", ".."} or set(name) == {"."}:
        return fallback
    return name


async def _download_previews(images: list[dict], lora_dir: Path, proxy_url: str) -> list[str]:
    """Best-effort preview download -- a failed preview never fails the ingest."""
    paths: list[str] = []
    preview_dir = lora_dir / "previews"
    for i, img in enumerate((images or [])[:MAX_PREVIEW_IMAGES]):
        url = img.get("url", "")
        if not url:
            continue
        preview_dir.mkdir(parents=True, exist_ok=True)
        dest = preview_dir / f"{i:02d}.jpg"
        try:
            await download_file(url, dest, proxy=proxy_url or None, trust_env=False)
            paths.append(str(dest))
        except Exception:
            logger.warning("LoRA ingest: preview image %s failed to download", url, exc_info=True)
    return paths


def _describe_ingest_error(exc: Exception) -> str:
    """Map an exception raised anywhere in the ingest job to a specific,
    human-readable, loud-failure message."""
    if isinstance(exc, CivitaiIngestError):
        return str(exc)
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 451:
        return (
            "Civitai geo-blocked this request (HTTP 451). Set lora_ingest_proxy_url "
            "to a proxy exiting outside the blocked region and retry."
        )
    if isinstance(exc, httpx.TransportError):
        return (
            f"Could not reach Civitai ({type(exc).__name__}: {exc}). If "
            "lora_ingest_proxy_url is unset or misconfigured, Civitai may be "
            "geo-blocking this server; set lora_ingest_proxy_url and retry."
        )
    return f"LoRA ingest failed: {exc}"


async def run_civitai_ingest(store: LoraStore, lora_id: str, proxy_url: str) -> None:
    """Run the 7-step Civitai ingest job for an existing pending/failed row.

    On any failure: status=failed + a specific human-readable error, and any
    partially-downloaded files are removed. Always re-raises so callers that
    need to propagate the failure (the library pipeline) can do so; the
    background-task wrapper here swallows it after logging.
    """
    row = await store.get(lora_id)
    if not row:
        return

    slug = slug_from_lora_id(lora_id)
    lora_dir = loras_root() / slug

    try:
        await store.update(lora_id, status="downloading", error="")

        # 1. Fetch model metadata through the proxy.
        api_base = _civitai_api_base(row["source_url"])
        model_json = await _fetch_model(api_base, row["civitai_model_id"], proxy_url)

        # 2. Assert the model is a LoRA.
        model_type = model_json.get("type", "")
        if model_type not in LORA_MODEL_TYPES:
            raise CivitaiIngestError(
                f"Model type {model_type!r} is not a LoRA (expected one of "
                f"{sorted(LORA_MODEL_TYPES)}); LoRA Studio v1 archives LoRAs only."
            )

        # 3. Pick version + primary file.
        version = _pick_version(model_json, row["civitai_version_id"])
        file_info = _pick_file(version)

        sha256 = ((file_info.get("hashes") or {}).get("SHA256") or "").lower() or None
        download_url = file_info.get("downloadUrl", "")
        if not download_url:
            raise CivitaiIngestError("Civitai file entry has no downloadUrl")
        filename = _safe_filename(file_info.get("name"), f"{slug}.safetensors")

        # 4. Download the safetensors file (SHA256-verified) through the proxy.
        lora_dir.mkdir(parents=True, exist_ok=True)
        dest = lora_dir / filename
        await download_file(
            download_url, dest, expected_sha256=sha256,
            proxy=proxy_url or None, trust_env=False,
        )
        file_bytes = dest.stat().st_size

        # 5. Download up to 4 preview images (best-effort).
        preview_paths = await _download_previews(
            version.get("images") or [], lora_dir, proxy_url,
        )

        # 6. Fill row from the API response.
        trigger_words = version.get("trainedWords") or []
        creator = (model_json.get("creator") or {}).get("username", "") or ""

        await store.update(
            lora_id,
            name=model_json.get("name", "") or "",
            description=model_json.get("description", "") or "",
            creator=creator,
            base_model=version.get("baseModel", "") or "",
            trigger_words=trigger_words,
            tags=model_json.get("tags") or [],
            nsfw=1 if model_json.get("nsfw") else 0,
            file_path=str(dest),
            file_name=filename,
            sha256=sha256 or "",
            bytes=file_bytes,
            preview_paths=preview_paths,
            meta_json=model_json,
            status="ready",
            error="",
        )
    except Exception as exc:
        # Loud failure: never leave a partial/error-page file on disk, and
        # never report success on a fetch that could not be verified.
        if lora_dir.exists():
            shutil.rmtree(lora_dir, ignore_errors=True)
        message = _describe_ingest_error(exc)
        await store.update(lora_id, status="failed", error=message)
        raise CivitaiIngestError(message) from exc


async def _run_ingest_background(store: LoraStore, lora_id: str, proxy_url: str) -> None:
    """Fire-and-forget wrapper for the route-triggered background task.

    run_civitai_ingest already recorded status=failed + error before
    raising; this just prevents the exception from becoming an unhandled
    task-exception warning.
    """
    try:
        await run_civitai_ingest(store, lora_id, proxy_url)
    except Exception:
        logger.info("LoRA ingest for %s ended in failure (recorded on the row)", lora_id)


def _proxy_url(request: Request) -> str:
    config = getattr(request.app.state, "config", None)
    return getattr(config, "lora_ingest_proxy_url", "") or "" if config else ""


# ---------------------------------------------------------------------------
# Store access
# ---------------------------------------------------------------------------


def _get_store(request: Request) -> LoraStore:
    return request.app.state.lora_store


def _public_row(row: dict) -> dict:
    """Deserialise the JSON-text columns into real JSON for API consumers."""
    out = dict(row)
    for key in ("trigger_words", "tags", "preview_paths"):
        try:
            out[key] = json.loads(out.get(key) or "[]")
        except (TypeError, ValueError):
            out[key] = []
    try:
        out["meta_json"] = json.loads(out.get("meta_json") or "{}")
    except (TypeError, ValueError):
        out["meta_json"] = {}
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/api/loras/ingest")
async def ingest_lora(request: Request, url: str = Form(...)):
    """Ingest a Civitai model-page URL. Returns the pending row immediately;
    the download runs in a background task."""
    try:
        model_id, url_slug, version_id = parse_civitai_url(url)
    except CivitaiUrlError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    store = _get_store(request)
    lora_id = f"lora-{lora_slug(url_slug, model_id)}"
    row = await store.create_pending(
        lora_id,
        source_url=url,
        civitai_model_id=model_id,
        civitai_version_id=version_id,
    )

    _schedule(request, _run_ingest_background(store, lora_id, _proxy_url(request)))

    return JSONResponse(_public_row(row), status_code=202)


@router.get("/api/loras")
async def list_loras(request: Request, status: str | None = None):
    store = _get_store(request)
    rows = await store.list(status=status)
    return {"loras": [_public_row(r) for r in rows], "count": len(rows)}


@router.get("/api/loras/{lora_id}")
async def get_lora(request: Request, lora_id: str):
    store = _get_store(request)
    row = await store.get(lora_id)
    if not row:
        return JSONResponse({"error": f"LoRA {lora_id!r} not found"}, status_code=404)
    return _public_row(row)


@router.get("/api/loras/{lora_id}/preview/{n}")
async def get_lora_preview(request: Request, lora_id: str, n: int):
    store = _get_store(request)
    row = await store.get(lora_id)
    if not row:
        return JSONResponse({"error": f"LoRA {lora_id!r} not found"}, status_code=404)

    try:
        previews = json.loads(row.get("preview_paths") or "[]")
    except (TypeError, ValueError):
        previews = []
    if n < 0 or n >= len(previews):
        return JSONResponse({"error": "Preview not found"}, status_code=404)

    path = Path(previews[n])
    root = loras_root().resolve()
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return JSONResponse({"error": "Invalid preview path"}, status_code=400)
    if not resolved.exists():
        return JSONResponse({"error": "Preview not found"}, status_code=404)
    return FileResponse(resolved)


@router.delete("/api/loras/{lora_id}")
async def delete_lora(request: Request, lora_id: str):
    store = _get_store(request)
    row = await store.get(lora_id)
    if not row:
        return JSONResponse({"error": f"LoRA {lora_id!r} not found"}, status_code=404)

    root = loras_root().resolve()
    lora_dir = loras_root() / slug_from_lora_id(lora_id)

    paths_to_check: list[Path] = [lora_dir]
    file_path = row.get("file_path") or ""
    if file_path:
        paths_to_check.append(Path(file_path))

    for p in paths_to_check:
        try:
            resolved = p.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            return JSONResponse(
                {"error": f"Refusing to delete: {p} resolves outside the loras root"},
                status_code=400,
            )

    if file_path and Path(file_path).exists() and Path(file_path).is_file():
        try:
            Path(file_path).unlink()
        except OSError:
            logger.warning("Failed to remove LoRA file %s for %s", file_path, lora_id)
    if lora_dir.exists():
        shutil.rmtree(lora_dir, ignore_errors=True)

    await store.delete(lora_id)
    return {"status": "deleted", "id": lora_id}


@router.post("/api/loras/{lora_id}/retry")
async def retry_lora(request: Request, lora_id: str):
    store = _get_store(request)
    row = await store.get(lora_id)
    if not row:
        return JSONResponse({"error": f"LoRA {lora_id!r} not found"}, status_code=404)
    # Atomic failed -> pending: the loser of a concurrent retry gets the 409
    # instead of scheduling a second job into the same LoRA directory.
    if not await store.claim_retry(lora_id):
        return JSONResponse({"error": "LoRA is not in a failed state"}, status_code=409)

    _schedule(request, _run_ingest_background(store, lora_id, _proxy_url(request)))

    return JSONResponse({"id": lora_id, "status": "pending"}, status_code=202)
