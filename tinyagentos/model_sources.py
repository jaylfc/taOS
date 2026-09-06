from __future__ import annotations
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Simple in-memory cache: key -> (timestamp, data)
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, data = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return data


def _cache_set(key: str, data: Any) -> None:
    _cache[key] = (time.monotonic(), data)


async def search_huggingface(query: str, limit: int = 20) -> list[dict]:
    """Search HuggingFace for GGUF models."""
    cache_key = f"hf:{query}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://huggingface.co/api/models",
                params={
                    "search": query,
                    "filter": "gguf",
                    "sort": "downloads",
                    "direction": "-1",
                    "limit": limit,
                },
            )
            resp.raise_for_status()
            models = resp.json()
            result = [
                {
                    "id": m.get("modelId", ""),
                    "name": m.get("modelId", "").split("/")[-1],
                    "author": m.get("modelId", "").split("/")[0] if "/" in m.get("modelId", "") else "",
                    "downloads": m.get("downloads", 0),
                    "likes": m.get("likes", 0),
                    "tags": m.get("tags", []),
                    "source": "huggingface",
                    "url": f"https://huggingface.co/{m.get('modelId', '')}",
                }
                for m in models
            ]
            _cache_set(cache_key, result)
            return result
    except Exception as e:
        logger.warning(f"HuggingFace search failed: {e}")
        return []


async def search_ollama(query: str, limit: int = 20) -> list[dict]:
    """Search the Ollama model library."""
    cache_key = f"ollama:{query}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://ollama.com/api/search",
                params={"q": query},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            models = data.get("models", data) if isinstance(data, dict) else data
            result = [
                {
                    "id": m.get("name", ""),
                    "name": m.get("name", ""),
                    "author": "ollama",
                    "description": m.get("description", ""),
                    "tags": m.get("tags", []),
                    "source": "ollama",
                    "url": f"https://ollama.com/library/{m.get('name', '')}",
                }
                for m in (models[:limit] if isinstance(models, list) else [])
            ]
            _cache_set(cache_key, result)
            return result
    except Exception as e:
        logger.warning(f"Ollama search failed: {e}")
        return []


async def get_huggingface_model_files(model_id: str) -> list[dict]:
    """Get GGUF file details for a specific HuggingFace model."""
    cache_key = f"hf_files:{model_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://huggingface.co/api/models/{model_id}",
                params={"blobs": True},
            )
            resp.raise_for_status()
            data = resp.json()
            siblings = data.get("siblings", [])
            gguf_files = []
            for f in siblings:
                fname = f.get("rfilename", "")
                if fname.endswith(".gguf"):
                    size_bytes = f.get("size", 0)
                    gguf_files.append({
                        "filename": fname,
                        "size_mb": size_bytes // (1024 * 1024) if size_bytes else 0,
                        "download_url": f"https://huggingface.co/{model_id}/resolve/main/{fname}",
                        "quantization": _parse_quantization(fname),
                    })
            result = sorted(gguf_files, key=lambda x: x["size_mb"])
            _cache_set(cache_key, result)
            return result
    except Exception as e:
        logger.warning(f"HuggingFace model files failed: {e}")
        return []


def _parse_quantization(filename: str) -> str:
    """Extract quantization type from GGUF filename."""
    fname = filename.lower()
    for q in [
        "q2_k", "q3_k_s", "q3_k_m", "q3_k_l",
        "q4_0", "q4_k_s", "q4_k_m", "q4_k_l",
        "q5_0", "q5_k_s", "q5_k_m", "q5_k_l",
        "q6_k", "q8_0", "f16", "f32",
    ]:
        if q in fname:
            return q.upper()
    return "unknown"


def estimate_ram_mb(size_mb: int) -> int:
    """Estimate RAM needed to run a model (model size + ~20% overhead)."""
    return int(size_mb * 1.2)


def get_compatibility(ram_required_mb: int, available_ram_mb: int) -> str:
    """Return compatibility level: compatible, tight, incompatible."""
    if available_ram_mb <= 0:
        return "incompatible"
    if ram_required_mb <= available_ram_mb * 0.6:
        return "compatible"
    elif ram_required_mb <= available_ram_mb * 0.85:
        return "tight"
    else:
        return "incompatible"
