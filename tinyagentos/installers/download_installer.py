from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Callable

import httpx

from tinyagentos.installers.base import AppInstaller

logger = logging.getLogger(__name__)

# Signature: callback(downloaded_bytes, total_bytes_or_zero_if_unknown)
ProgressCallback = Callable[[int, int], None]


async def download_file(
    url: str,
    dest: Path,
    expected_sha256: str | None = None,
    *,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Download a file with optional SHA256 verification.

    ``on_progress`` is called periodically with (downloaded_bytes,
    total_bytes). total_bytes is 0 when the server didn't send a
    Content-Length. Callbacks are throttled to roughly once a second
    so the install-progress store doesn't take a write lock per chunk.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    import time as _time
    last_cb = 0.0
    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            sha = hashlib.sha256()
            downloaded = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    sha.update(chunk)
                    downloaded += len(chunk)
                    if on_progress is not None:
                        now = _time.monotonic()
                        if now - last_cb >= 1.0:
                            try:
                                on_progress(downloaded, total)
                            except Exception as exc:  # noqa: BLE001
                                # Never let a bad callback kill the
                                # download, but log so a regression in
                                # the progress store is debuggable.
                                logger.warning(
                                    "download_file: progress callback raised %s — continuing download",
                                    exc,
                                )
                            last_cb = now
            # Always emit a final update at 100% so the UI can flip to
            # "verifying" promptly instead of waiting for the next tick.
            if on_progress is not None:
                try:
                    on_progress(downloaded, total or downloaded)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "download_file: final progress callback raised %s",
                        exc,
                    )
    if expected_sha256 and sha.hexdigest() != expected_sha256:
        dest.unlink()
        raise ValueError(f"SHA256 mismatch: expected {expected_sha256}, got {sha.hexdigest()}")
    return dest


class DownloadInstaller(AppInstaller):
    """Generic file-download installer for backends that just need bytes
    on disk (llama-cpp, mlx, vllm, transformers, diffusers,
    sentence-transformers, whisper-cpp, piper, onnxruntime, nemo, sd-webui).

    Files land in the shared layout at
    ``~/models/<backend>/<family>/<manifest_id>/<filename>`` so the user
    has one place to inspect everything and workers can rsync a manifest
    dir across the cluster.

    The ``models_dir`` constructor arg is kept for tests that want a
    sandboxed root, but production code should leave it ``None`` and let
    ``model_paths.models_root()`` decide (driven by
    ``TAOS_MODELS_ROOT`` when set).
    """

    def __init__(self, models_dir: Path | None = None):
        # If a caller passes models_dir, treat it as an alternate root —
        # the per-(backend, family, id) sub-tree is appended below.
        # Production callers leave this None.
        self._root_override = Path(models_dir) if models_dir else None

    def _backend_id(self, install_config: dict) -> str:
        # The store-install dispatcher injects "backend" into install_config
        # before calling us — same field that drives _BACKEND_TO_METHOD.
        backend = install_config.get("backend") if install_config else None
        return str(backend) if backend else "download"

    def _target_dir(self, backend_id: str, app_id: str) -> Path:
        from tinyagentos.installers.model_paths import (
            backend_model_dir,
            family_from_manifest,
        )
        if self._root_override is not None:
            return self._root_override / backend_id / family_from_manifest(app_id) / app_id
        return backend_model_dir(backend_id, app_id)

    async def install(self, app_id: str, install_config: dict, variant: dict | None = None, **kwargs) -> dict:
        if not variant:
            return {"success": False, "error": "variant required for model download"}

        from tinyagentos.installers.model_paths import filename_from_url

        backend_id = self._backend_id(install_config or {})
        target_dir = self._target_dir(backend_id, app_id)
        target_dir.mkdir(parents=True, exist_ok=True)

        url = variant.get("download_url", "")
        fallback = f"{app_id}-{variant['id']}.{variant.get('format', 'bin')}"
        filename = filename_from_url(url, fallback)
        dest = target_dir / filename

        if dest.exists():
            return {"success": True, "path": str(dest), "cached": True}

        # Optional progress hook from the dispatcher. The Store route
        # passes one wired to the install-progress store so the
        # frontend can render a download bar.
        on_progress: ProgressCallback | None = kwargs.get("on_progress")

        try:
            path = await download_file(
                url=url,
                dest=dest,
                expected_sha256=variant.get("sha256"),
                on_progress=on_progress,
            )
            return {"success": True, "path": str(path)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def uninstall(self, app_id: str, variant_id: str | None = None, **kwargs) -> dict:
        # Locate the manifest dir under whichever backend it's recorded
        # against. We don't know the backend here for sure (uninstall is
        # called with just the app_id), so scan every backend root for a
        # matching manifest dir.
        from tinyagentos.installers.model_paths import (
            family_from_manifest,
            models_root,
        )

        deleted: list[str] = []
        family = family_from_manifest(app_id)
        # Walk one level deep under models_root to find every backend.
        root = self._root_override if self._root_override is not None else models_root()
        if not root.exists():
            return {"success": True, "deleted": deleted}

        for backend_dir in sorted(root.iterdir()):
            if not backend_dir.is_dir():
                continue
            manifest_dir = backend_dir / family / app_id
            if not manifest_dir.exists():
                continue
            for f in sorted(manifest_dir.glob("*")):
                if not f.is_file():
                    continue
                if variant_id and variant_id not in f.name:
                    continue
                f.unlink()
                deleted.append(f"{backend_dir.name}/{family}/{app_id}/{f.name}")
            # Best-effort cleanup — only rmdir an empty dir.
            try:
                manifest_dir.rmdir()
            except OSError:
                pass

        return {"success": True, "deleted": deleted}
