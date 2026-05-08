"""Shared on-disk layout for installed models.

All backend installers write into one tree:

    ~/models/<backend>/<family>/<manifest_id>/<filename>

Examples:
    ~/models/rk-llama.cpp/gemma/gemma-4-e2b-gguf/gemma-4-E2B-it-Q4_K_M.gguf
    ~/models/llama-cpp/qwen3.5/qwen3.5-9b/qwen3.5-9b-Q4_K_M.gguf

Why a single tree:
- The user can browse one place and see everything they have.
- Workers can rsync a manifest dir directly across the cluster and the
  receiving end mounts the exact same path layout.
- The Models app can group by family and surface size/cleanup actions
  uniformly without each backend reinventing its layout.

Ollama is intentionally not migrated here — it manages its own blob
store (~/.ollama/models) and reading from a foreign tree is a separate
discoverability concern handled in routes/models.py.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse


def models_root() -> Path:
    """Single root for the layout. Override with TAOS_MODELS_ROOT for tests
    or alternate filesystems (network share, dedicated SSD, etc.).
    """
    override = os.environ.get("TAOS_MODELS_ROOT")
    return Path(override) if override else Path.home() / "models"


_FAMILY_FALLBACK = "uncategorised"


def family_from_manifest(manifest_or_id: object) -> str:
    """Pick the family directory name for a manifest.

    Resolution order:

    1. Explicit ``family`` field on the manifest (preferred — gives
       maintainers full control for cases like ``paligemma-2`` that
       should arguably nest under ``gemma``).
    2. First dash-separated token of the manifest id, lowercased. We
       deliberately keep version numbers as part of the family token
       (``qwen3.5-9b`` -> ``qwen3.5``, ``llama-3.2-1b`` -> ``llama``)
       because the literal first token is deterministic and easy to
       reason about — set ``family:`` explicitly when you want to merge
       e.g. qwen / qwen2 / qwen3 under one bucket.
    3. ``uncategorised`` if everything else falls through (defensive —
       shouldn't happen with well-formed manifest ids).
    """
    explicit = None
    manifest_id = ""
    if isinstance(manifest_or_id, str):
        manifest_id = manifest_or_id
    else:
        explicit = getattr(manifest_or_id, "family", None) or (
            manifest_or_id.get("family") if isinstance(manifest_or_id, dict) else None
        )
        manifest_id = (
            getattr(manifest_or_id, "id", None)
            or (manifest_or_id.get("id") if isinstance(manifest_or_id, dict) else "")
            or ""
        )
    if explicit:
        return str(explicit).lower()
    first_token = (manifest_id or "").split("-", 1)[0].strip().lower()
    return first_token or _FAMILY_FALLBACK


def backend_model_dir(backend_id: str, manifest_or_id: object) -> Path:
    """Resolved directory for a specific (backend, manifest) install.

    Caller is responsible for mkdir-ing this — the helper is a pure path
    builder so tests don't need a real filesystem.
    """
    family = family_from_manifest(manifest_or_id)
    manifest_id = (
        manifest_or_id
        if isinstance(manifest_or_id, str)
        else (
            getattr(manifest_or_id, "id", None)
            or (manifest_or_id.get("id") if isinstance(manifest_or_id, dict) else "")
            or ""
        )
    )
    if not manifest_id:
        raise ValueError("backend_model_dir: manifest_or_id has no id")
    return models_root() / backend_id / family / manifest_id


_FILENAME_BAD = re.compile(r"[^A-Za-z0-9._+\-]")


def filename_from_url(url: str, fallback: str) -> str:
    """Pull the basename from a download URL, falling back when the URL
    is opaque (query strings, redirects to opaque CDN paths, etc.).
    """
    if url:
        try:
            path = urlparse(url).path
            base = path.rsplit("/", 1)[-1]
            base = _FILENAME_BAD.sub("_", base)
            if base and "." in base:
                return base
        except Exception:  # noqa: BLE001 — pure best-effort parse
            pass
    return fallback
