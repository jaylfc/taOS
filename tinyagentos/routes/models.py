# tinyagentos/routes/models.py
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.model_sources import (
    estimate_ram_mb,
    get_compatibility,
    get_huggingface_model_files,
    search_huggingface,
    search_ollama,
)

logger = logging.getLogger(__name__)


class DownloadRequest(BaseModel):
    app_id: str
    variant_id: str


class DeleteRequest(BaseModel):
    app_id: str


class PullRequest(BaseModel):
    model_name: str


router = APIRouter()

DEFAULT_MODELS_DIR = Path("/opt/tinyagentos/models")


_MODEL_FILE_SUFFIXES = (".gguf", ".rkllm", ".bin", ".safetensors", ".onnx")


def get_downloaded_models(models_dir: Path) -> list[dict]:
    """Scan model directories and return downloaded model files.

    Walks ``models_dir`` recursively (not just the top level) so the
    shared layout at ``~/models/<backend>/<family>/<id>/<file>`` is
    discovered alongside any legacy flat ``data/models/<file>`` files.
    Each entry carries the relative path so the UI can show backend +
    family grouping without re-deriving them.
    """
    if not models_dir.exists():
        return []
    results = []
    for f in sorted(models_dir.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() not in _MODEL_FILE_SUFFIXES:
            continue
        try:
            rel = str(f.relative_to(models_dir))
        except ValueError:
            rel = f.name
        results.append({
            "filename": f.name,
            "relative_path": rel,
            "size_mb": f.stat().st_size // (1024 * 1024),
            "format": f.suffix.lstrip(".").lower(),
            "path": str(f),
        })
    return results


def _models_dir(request: Request) -> Path:
    return getattr(request.app.state, "models_dir", DEFAULT_MODELS_DIR)


def _scan_roots(request: Request) -> list[Path]:
    """Roots to scan for downloaded model files.

    Legacy ``data/models`` plus the new shared layout root
    (~/models/<backend>/<family>/<id>/...). Both get walked so files
    placed by older installers still surface alongside the new tree.
    """
    primary = _models_dir(request)
    extra = getattr(request.app.state, "models_root", None)
    roots = [primary]
    if extra is not None and Path(extra) != primary:
        roots.append(Path(extra))
    return roots


def _scan_all_roots(roots: list[Path]) -> list[dict]:
    """Run get_downloaded_models against each root and merge, de-duped
    by absolute path so a single file never appears twice even if a
    legacy directory is symlinked into the new layout.
    """
    seen: set[str] = set()
    merged: list[dict] = []
    for r in roots:
        for entry in get_downloaded_models(r):
            if entry["path"] in seen:
                continue
            seen.add(entry["path"])
            merged.append(entry)
    return merged


def _is_service_installed(variant: dict) -> bool:
    """Detect whether a multi-file service-backed variant (e.g. RKNN SD) is
    installed on disk outside the ``data/models`` tree. Looks for an
    ``install_script`` marker and a representative file at the install path.
    """
    if not variant.get("multi_file"):
        return False
    # darkbit1001 / happyme531 layout — models live under
    # ~/.local/share/tinyagentos/rknn-sd/model/{text_encoder,unet,vae_decoder}
    # and the install marker is the unet rknn weights.
    if variant.get("backend") and "rknn-stable-diffusion" in variant.get("backend", []):
        install_root = Path.home() / ".local" / "share" / "tinyagentos" / "rknn-sd"
        unet = install_root / "model" / "unet" / "model.rknn"
        return unet.exists() and unet.stat().st_size > 1_000_000_000
    return False


def _variant_compatibility(variant: dict, hardware_profile) -> str:
    """Return green/yellow/red based on hardware compatibility."""
    ram_mb = hardware_profile.ram_mb
    min_ram = variant.get("min_ram_mb", 0)
    requires_npu = variant.get("requires_npu", [])

    # Check NPU requirement
    if requires_npu:
        soc = hardware_profile.cpu.soc
        if soc in requires_npu:
            return "green"
        else:
            return "red"

    # Check RAM
    if min_ram == 0:
        return "green"
    if ram_mb >= min_ram * 1.5:
        return "green"
    elif ram_mb >= min_ram:
        return "yellow"
    else:
        return "red"


def _matches_live_backend(
    manifest_id: str, variant: dict, live_models: list[dict]
) -> bool:
    """True if any live backend is currently advertising this specific variant.

    Backend-driven: we ask the live catalog ``catalog.all_models()`` for the
    full loaded-model list and match against the combined
    ``manifest_id-variant_id`` token. To avoid matching sibling variants
    under the same manifest, we require the backend model name to mention
    the variant id (either an exact match on the full id, or ``manifest_id``
    plus a token from the variant id separated by ``-`` or ``_``).
    """
    if not live_models:
        return False
    variant_id = variant.get("id", "").lower()
    if not variant_id:
        return False
    manifest_lower = manifest_id.lower()
    full_id = f"{manifest_lower}-{variant_id}"
    # Variant id may be dotted/hyphenated; require at least the first token
    # to appear in the backend model name.
    variant_token = variant_id.split("-")[0].split("_")[0]
    if not variant_token:
        return False

    for m in live_models:
        name = (m.get("name") or m.get("id") or "").lower().replace("_", "-")
        if not name:
            continue
        normalised_variant = variant_id.replace("_", "-")
        normalised_full = full_id.replace("_", "-")
        # Exact or prefix match on the full id is definitive.
        if name == normalised_full or name.startswith(normalised_full + "-"):
            return True
        # Otherwise require both the manifest id and the variant token to
        # appear in the backend name to disambiguate siblings.
        if manifest_lower in name and variant_token in name.split(manifest_lower, 1)[-1]:
            return True
        if name == normalised_variant or name.endswith("-" + normalised_variant):
            return True
    return False


def _model_to_dict(
    manifest,
    hardware_profile,
    downloaded_files: list[dict],
    live_models: list[dict] | None = None,
    *,
    registry_installed: bool = False,
) -> dict:
    """Convert an AppManifest to a model dict with compatibility info.

    ``live_models`` is the current flat list from BackendCatalog.all_models().
    If supplied, variants advertised by any live backend are marked downloaded.
    The on-disk filesystem scan (``downloaded_files``) remains as a fallback
    for models that have been pulled to ``data/models`` but no backend has
    loaded yet — though this is a transitional state; the long-term answer is
    always the live catalog.

    ``registry_installed`` is the install-registry's verdict for this manifest
    id, but only counts when corroborated by at least one of:

    - a file under ``downloaded_files`` whose path contains this manifest id
      (the new shared layout from #430 puts files at
      ``~/models/<backend>/<family>/<manifest_id>/`` — any file there is
      strong evidence the install really happened)
    - a live backend that advertises this manifest id

    Pure registry breadcrumbs without disk or live-backend evidence are
    treated as stale and NOT marked downloaded. johny saw a bunch of
    models showing as installed on a fresh install because the registry
    had stale entries from earlier sessions; that bypass is closed here.
    """
    downloaded_filenames = {d["filename"] for d in downloaded_files}
    live_models = live_models or []

    # Corroboration check for registry_installed — does this manifest have
    # at least one file on disk or one live-backend hit somewhere? Used
    # below in place of an unconditional registry_installed OR.
    manifest_id_lower = (manifest.id or "").lower()
    has_disk_evidence = any(
        manifest_id_lower in (d.get("relative_path", d.get("filename", "")) or "").lower()
        for d in downloaded_files
    )
    has_live_evidence = any(
        manifest_id_lower in ((m.get("name") or m.get("id") or "").lower().replace("_", "-"))
        for m in live_models
    )
    registry_corroborated = bool(
        registry_installed and (has_disk_evidence or has_live_evidence)
    )

    variants = []
    for v in manifest.variants:
        expected_filename = f"{manifest.id}-{v['id']}.{v.get('format', 'bin')}"
        is_downloaded = (
            _matches_live_backend(manifest.id, v, live_models)
            or expected_filename in downloaded_filenames
            or _is_service_installed(v)
            or registry_corroborated
        )
        compat = _variant_compatibility(v, hardware_profile)
        variants.append({
            **v,
            "downloaded": is_downloaded,
            "compatibility": compat,
        })

    # Overall compatibility: best variant compatibility
    compat_order = {"green": 0, "yellow": 1, "red": 2}
    best = min((v["compatibility"] for v in variants), key=lambda c: compat_order.get(c, 2)) if variants else "red"

    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "capabilities": manifest.capabilities,
        "hardware_tiers": manifest.hardware_tiers,
        "variants": variants,
        "compatibility": best,
        "has_downloaded_variant": any(v["downloaded"] for v in variants),
    }


@router.get("/api/models")
async def list_models(request: Request):
    """List all available models with download status and compatibility.

    Backend-driven: "downloaded" is computed from the live BackendCatalog
    first (what's actually loaded right now on some backend), and only
    falls back to the on-disk filesystem scan for files that are staged
    but not yet loaded.
    """
    registry = request.app.state.registry
    hardware_profile = request.app.state.hardware_profile
    catalog = getattr(request.app.state, "backend_catalog", None)

    models = registry.list_available(type_filter="model")
    downloaded = _scan_all_roots(_scan_roots(request))
    live_models = catalog.all_models() if catalog is not None else []
    # Build {app_id: True} from the install registry so backend-installed
    # models (e.g. rk-llama.cpp GGUFs at ~/rk-llama.cpp/models/) still show
    # up as downloaded in /api/models even though they're not under data/.
    try:
        installed_ids = {row["id"] for row in registry.list_installed() if "id" in row}
    except Exception:  # noqa: BLE001 — registry is best-effort, never break /api/models
        installed_ids = set()

    return {
        "models": [
            _model_to_dict(
                m,
                hardware_profile,
                downloaded,
                live_models,
                registry_installed=(m.id in installed_ids),
            )
            for m in models
        ],
        "downloaded_files": downloaded,
        "hardware_profile_id": hardware_profile.profile_id,
    }


@router.post("/api/models/download")
async def download_model(request: Request, body: DownloadRequest):
    """Start a background download for a specific model variant."""
    registry = request.app.state.registry
    manifest = registry.get(body.app_id)
    if not manifest or manifest.type != "model":
        return JSONResponse({"error": f"Model '{body.app_id}' not found"}, status_code=404)

    variant = next((v for v in manifest.variants if v["id"] == body.variant_id), None)
    if not variant:
        return JSONResponse({"error": f"Variant '{body.variant_id}' not found"}, status_code=404)

    url = variant.get("download_url", "")
    if not url:
        return JSONResponse({"error": "No download URL for variant"}, status_code=400)

    models_dir = _models_dir(request)
    fmt = variant.get("format", "bin")
    filename = f"{body.app_id}-{body.variant_id}.{fmt}"
    dest = models_dir / filename

    download_id = f"{body.app_id}-{body.variant_id}"
    dm = request.app.state.download_manager
    # Hybrid download: DownloadManager tries the torrent swarm first if
    # the variant declares a magnet URI and the catalog publisher has
    # marked its licence as allowing redistribution. Failures fall back
    # transparently to HTTP — the user never has to know which
    # transport ran.
    dm.start_download(
        download_id=download_id,
        url=url,
        dest=dest,
        expected_sha256=variant.get("sha256"),
        magnet=variant.get("magnet"),
        license_allows_redistribution=bool(
            variant.get("license_allows_redistribution", False)
        ),
    )

    return {
        "status": "started",
        "download_id": download_id,
        "app_id": body.app_id,
        "variant_id": body.variant_id,
    }


@router.get("/api/models/downloads")
async def list_downloads(request: Request):
    """List all downloads with progress information."""
    dm = request.app.state.download_manager
    tasks = dm.list_all()
    return {
        "downloads": [_task_to_dict(t) for t in tasks],
    }


@router.get("/api/models/downloads/{download_id}")
async def get_download_progress(request: Request, download_id: str):
    """Get progress for a specific download."""
    dm = request.app.state.download_manager
    task = dm.get_progress(download_id)
    if not task:
        return JSONResponse({"error": f"Download '{download_id}' not found"}, status_code=404)
    return _task_to_dict(task)


def _task_to_dict(task) -> dict:
    pct = 0
    if task.total_bytes > 0:
        pct = round(task.downloaded_bytes / task.total_bytes * 100, 1)
    return {
        "id": task.id,
        "url": task.url,
        "dest": str(task.dest),
        "total_bytes": task.total_bytes,
        "downloaded_bytes": task.downloaded_bytes,
        "percent": pct,
        "status": task.status,
        "error": task.error,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
    }


def _add_compatibility_to_results(results: list[dict], hardware_profile) -> list[dict]:
    """Add hardware compatibility info to search results."""
    ram_mb = hardware_profile.ram_mb
    for r in results:
        if r.get("source") == "huggingface":
            r["ram_available_mb"] = ram_mb
        elif r.get("source") == "ollama":
            r["ram_available_mb"] = ram_mb
    return results


def _search_catalog(registry, query: str, hardware_profile) -> list[dict]:
    """Search the local catalog for models matching the query."""
    query_lower = query.lower()
    models = registry.list_available(type_filter="model")
    results = []
    for m in models:
        if query_lower in m.name.lower() or query_lower in m.id.lower() or query_lower in m.description.lower():
            results.append({
                "id": m.id,
                "name": m.name,
                "author": "catalog",
                "description": m.description,
                "tags": m.capabilities,
                "source": "catalog",
                "url": f"/api/models/{m.id}",
            })
    return results


def _render_search_results(request: Request, results: list[dict], source: str):
    """Return search results as JSON."""
    return {"results": results, "source": source, "count": len(results)}


@router.get("/api/models/search/huggingface")
async def search_hf(request: Request, q: str = Query("", min_length=0)):
    """Search HuggingFace for GGUF models."""
    if not q.strip():
        return {"results": [], "source": "huggingface"}
    hardware_profile = request.app.state.hardware_profile
    results = await search_huggingface(q.strip())
    results = _add_compatibility_to_results(results, hardware_profile)
    return {"results": results, "source": "huggingface"}


@router.get("/api/models/search/ollama")
async def search_ol(request: Request, q: str = Query("", min_length=0)):
    """Search Ollama model library."""
    if not q.strip():
        return {"results": [], "source": "ollama"}
    hardware_profile = request.app.state.hardware_profile
    results = await search_ollama(q.strip())
    results = _add_compatibility_to_results(results, hardware_profile)
    return {"results": results, "source": "ollama"}


@router.get("/api/models/search")
async def search_models(
    request: Request,
    q: str = Query("", min_length=0),
    source: str = Query("all"),
):
    """Search models across sources. source=all|huggingface|ollama|catalog."""
    query = q.strip()
    if not query:
        return _render_search_results(request, [], source)

    hardware_profile = request.app.state.hardware_profile
    results: list[dict] = []

    if source in ("all", "huggingface"):
        hf_task = search_huggingface(query)
    else:
        hf_task = None

    if source in ("all", "ollama"):
        ol_task = search_ollama(query)
    else:
        ol_task = None

    if source in ("all", "catalog"):
        registry = request.app.state.registry
        catalog_results = _search_catalog(registry, query, hardware_profile)
    else:
        catalog_results = []

    # Await external searches concurrently
    tasks = []
    task_labels = []
    if hf_task:
        tasks.append(hf_task)
        task_labels.append("huggingface")
    if ol_task:
        tasks.append(ol_task)
        task_labels.append("ollama")

    if tasks:
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        for label, result in zip(task_labels, gathered):
            if isinstance(result, Exception):
                logger.warning(f"Search {label} failed: {result}")
            elif isinstance(result, list):
                results.extend(result)

    results.extend(catalog_results)
    results = _add_compatibility_to_results(results, hardware_profile)

    return _render_search_results(request, results, source)


@router.get("/api/models/files/{model_id:path}")
async def get_model_files(request: Request, model_id: str):
    """Get GGUF files for a HuggingFace model with compatibility info."""
    hardware_profile = request.app.state.hardware_profile
    ram_mb = hardware_profile.ram_mb
    files = await get_huggingface_model_files(model_id)
    for f in files:
        ram_needed = estimate_ram_mb(f["size_mb"])
        f["ram_estimate_mb"] = ram_needed
        f["compatibility"] = get_compatibility(ram_needed, ram_mb)
    return {"model_id": model_id, "files": files, "ram_available_mb": ram_mb}


@router.post("/api/models/pull")
async def pull_model(request: Request, body: PullRequest):
    """Pull an Ollama model by calling the configured backend."""
    model_name = body.model_name.strip()
    if not model_name:
        return JSONResponse({"error": "model_name is required"}, status_code=400)
    try:
        http_client = request.app.state.http_client
        config = request.app.state.config
        ollama_url = None
        for b in config.backends:
            if b.type == "ollama":
                ollama_url = b.url
                break
        if not ollama_url:
            ollama_url = "http://localhost:11434"
        resp = await http_client.post(
            f"{ollama_url}/api/pull",
            json={"name": model_name, "stream": False},
            timeout=300,
        )
        if resp.status_code == 200:
            return {"status": "pulled", "model": model_name}
        return JSONResponse(
            {"error": f"Ollama pull failed: {resp.text}"},
            status_code=resp.status_code,
        )
    except Exception as e:
        logger.warning(f"Ollama pull failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@router.get("/api/models/recommended")
async def recommended_models(request: Request):
    """Return models recommended for the current hardware profile."""
    registry = request.app.state.registry
    hw = request.app.state.hardware_profile
    profile_id = hw.profile_id

    all_models = registry.list_available(type_filter="model")
    recommended = []
    compatible = []

    for model in all_models:
        tiers = getattr(model, "hardware_tiers", {}) or {}
        tier_status = tiers.get(profile_id, "")

        entry = {
            "id": model.id,
            "name": model.name,
            "description": getattr(model, "description", ""),
            "compatibility": tier_status or "unknown",
        }

        if tier_status == "full":
            recommended.append(entry)
        elif tier_status == "limited":
            compatible.append(entry)

    return {
        "profile_id": profile_id,
        "recommended": recommended,
        "compatible": compatible,
    }


def _infer_purpose(model_name: str) -> str:
    """Guess the model's purpose from its name."""
    name = (model_name or "").lower()
    if any(k in name for k in ["embed", "e5", "bge", "nomic"]):
        return "embeddings"
    if any(k in name for k in ["sd", "stable-diffusion", "sdxl", "flux", "dreamshaper", "lcm", "pixart"]):
        return "image-generation"
    if any(k in name for k in ["whisper", "stt"]):
        return "speech-to-text"
    if any(k in name for k in ["tts", "kokoro", "piper", "bark"]):
        return "text-to-speech"
    if any(k in name for k in ["vision", "llava", "moondream", "blip"]):
        return "vision"
    if any(k in name for k in ["code", "deepseek", "qwen-coder", "codellama"]):
        return "code"
    return "chat"


@router.get("/api/models/loaded")
async def loaded_models(request: Request):
    """Return models currently loaded in memory across all backends.

    Queries each configured backend for its running/loaded models and
    aggregates them with purpose inference and resource usage info.
    Offline backends are skipped silently.
    """
    config = request.app.state.config
    backends = getattr(config, "backends", []) or []
    http_client = getattr(request.app.state, "http_client", None)

    loaded: list[dict] = []

    async def _query(client: httpx.AsyncClient) -> None:
        for backend in backends:
            backend_type = backend.get("type", "") if isinstance(backend, dict) else getattr(backend, "type", "")
            backend_url = backend.get("url", "") if isinstance(backend, dict) else getattr(backend, "url", "")
            backend_name = backend.get("name", "") if isinstance(backend, dict) else getattr(backend, "name", "")
            if not backend_url:
                continue
            base = backend_url.rstrip("/")

            try:
                if backend_type in ("ollama", "rkllama"):
                    resp = await client.get(f"{base}/api/ps", timeout=5)
                    if resp.status_code == 200:
                        data = resp.json()
                        for m in data.get("models", []) or []:
                            size = m.get("size", 0) or 0
                            size_vram = m.get("size_vram", 0) or 0
                            loaded.append({
                                "name": m.get("name", m.get("model", "unknown")),
                                "backend": backend_name,
                                "backend_type": backend_type,
                                "backend_url": backend_url,
                                "purpose": _infer_purpose(m.get("name", "") or m.get("model", "")),
                                "size_mb": size // (1024 * 1024),
                                "vram_mb": size_vram // (1024 * 1024),
                                "ram_mb": max(0, size - size_vram) // (1024 * 1024),
                                "expires_at": m.get("expires_at"),
                                "details": m.get("details", {}) or {},
                            })
                elif backend_type in ("llama-cpp", "vllm", "openai", "exo", "mlx", "anthropic"):
                    resp = await client.get(f"{base}/v1/models", timeout=5)
                    if resp.status_code == 200:
                        data = resp.json()
                        for m in data.get("data", []) or []:
                            model_id = m.get("id", "unknown")
                            loaded.append({
                                "name": model_id,
                                "backend": backend_name,
                                "backend_type": backend_type,
                                "backend_url": backend_url,
                                "purpose": _infer_purpose(model_id),
                                "size_mb": None,
                                "vram_mb": None,
                                "ram_mb": None,
                                "expires_at": None,
                                "details": {},
                            })
                elif backend_type == "rknn-sd":
                    # Use /health to determine actual pipeline load state.
                    # /v1/models lists what the server *can* serve (always
                    # populated) and does not reflect whether weights are
                    # actually in NPU memory.
                    resp = await client.get(f"{base}/health", timeout=5)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("pipeline_loaded") is True:
                            loaded.append({
                                "name": data.get("model", "rknn-sd"),
                                "backend": backend_name,
                                "backend_type": backend_type,
                                "backend_url": backend_url,
                                "purpose": "image-generation",
                                "size_mb": None,
                                "vram_mb": None,
                                "ram_mb": None,
                                "expires_at": None,
                                "details": {},
                            })
                elif backend_type == "sd-cpp":
                    # sd-cpp's /sdapi/v1/options reports the configured
                    # checkpoint but does not expose whether weights are
                    # currently resident in memory. Reporting it as "loaded"
                    # would be misleading. sd-cpp users see their installed
                    # model in the Models app instead.
                    pass
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
                continue
            except Exception as e:
                logger.debug(f"loaded_models: backend {backend_name} failed: {e}")
                continue

    if http_client is not None:
        await _query(http_client)
    else:
        async with httpx.AsyncClient(timeout=5) as client:
            await _query(client)

    return JSONResponse({"loaded": loaded})


@router.get("/api/models/{model_id}")
async def get_model(request: Request, model_id: str):
    """Get detailed information about a specific model."""
    registry = request.app.state.registry
    hardware_profile = request.app.state.hardware_profile
    models_dir = _models_dir(request)

    manifest = registry.get(model_id)
    if not manifest or manifest.type != "model":
        return JSONResponse({"error": f"Model '{model_id}' not found"}, status_code=404)

    downloaded = get_downloaded_models(models_dir)
    return _model_to_dict(manifest, hardware_profile, downloaded)


@router.delete("/api/models/{model_id}")
async def delete_model(request: Request, model_id: str):
    """Delete all downloaded files for a model."""
    registry = request.app.state.registry
    models_dir = _models_dir(request)

    manifest = registry.get(model_id)
    if not manifest or manifest.type != "model":
        return JSONResponse({"error": f"Model '{model_id}' not found"}, status_code=404)

    deleted = []
    for f in models_dir.glob(f"{model_id}*"):
        if f.is_file() and f.suffix in (".gguf", ".rkllm", ".bin"):
            f.unlink()
            deleted.append(f.name)

    if deleted:
        registry.mark_uninstalled(model_id)

    return {"status": "deleted", "model_id": model_id, "deleted_files": deleted}
