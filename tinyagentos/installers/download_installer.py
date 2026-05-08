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
    def __init__(self, models_dir: Path | None = None):
        self.models_dir = models_dir or Path("/opt/tinyagentos/models")

    async def install(self, app_id: str, install_config: dict, variant: dict | None = None, **kwargs) -> dict:
        if not variant:
            return {"success": False, "error": "variant required for model download"}

        filename = f"{app_id}-{variant['id']}.{variant.get('format', 'bin')}"
        dest = self.models_dir / filename

        if dest.exists():
            return {"success": True, "path": str(dest), "cached": True}

        # Optional progress hook from the dispatcher. The Store route
        # passes one wired to the install-progress store so the
        # frontend can render a download bar.
        on_progress: ProgressCallback | None = kwargs.get("on_progress")

        try:
            path = await download_file(
                url=variant["download_url"],
                dest=dest,
                expected_sha256=variant.get("sha256"),
                on_progress=on_progress,
            )
            return {"success": True, "path": str(path)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def uninstall(self, app_id: str, variant_id: str | None = None, **kwargs) -> dict:
        # Delete matching model files
        deleted = []
        for f in self.models_dir.glob(f"{app_id}*"):
            if variant_id and variant_id not in f.name:
                continue
            f.unlink()
            deleted.append(f.name)
        return {"success": True, "deleted": deleted}
