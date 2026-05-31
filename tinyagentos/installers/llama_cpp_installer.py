"""llama.cpp installer — downloads GGUF with magic-byte validation.

Unlike the generic DownloadInstaller, this installer validates that the
downloaded file is a real GGUF by checking the magic bytes (ASCII "GGUF"
at offset 0).  Corrupt downloads, truncated files, and wrong-format
responses from CDN caches are caught immediately rather than surfacing
as an opaque runtime crash when llama-server tries to load the file.

Files land at ``~/models/llama-cpp/<family>/<manifest_id>/<filename>``
inside the shared model layout so the Models app can discover them from
one place.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tinyagentos.installers.base import AppInstaller
from tinyagentos.installers.download_installer import download_file
from tinyagentos.installers.model_paths import (
    backend_model_dir,
    filename_from_url,
)

logger = logging.getLogger(__name__)

BACKEND_ID = "llama-cpp"

GGUF_MAGIC = b"GGUF"


class LlamaCppInstaller(AppInstaller):
    """Download GGUF models for serving via llama.cpp / llama-server."""

    def __init__(self, timeout: int = 1800):
        self.timeout = timeout

    async def install(
        self,
        app_id: str,
        install_config: dict,
        variant: dict | None = None,
        **_: Any,
    ) -> dict:
        if not variant:
            return {
                "success": False,
                "error": "llama-cpp install requires a variant (with download_url)",
            }
        url = variant.get("download_url")
        if not url:
            return {
                "success": False,
                "error": f"variant {variant.get('id')!r} missing download_url",
            }

        target_dir = backend_model_dir(BACKEND_ID, app_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        fallback = f"{app_id}-{variant.get('id', 'model')}.gguf"
        filename = filename_from_url(url, fallback)
        dest = target_dir / filename

        if dest.exists():
            logger.info("llama-cpp install: %s already present, reusing", dest)
            try:
                self._verify_gguf(dest)
            except ValueError as exc:
                logger.warning(
                    "llama-cpp install: cached file %s failed GGUF check: %s — "
                    "re-downloading",
                    dest, exc,
                )
                dest.unlink()
            else:
                return {"success": True, "path": str(dest), "cached": True}

        on_progress = _.get("on_progress")

        try:
            await download_file(
                url, dest,
                expected_sha256=variant.get("sha256"),
                on_progress=on_progress,
            )
        except Exception as exc:  # noqa: BLE001
            if dest.exists():
                dest.unlink()
            return {"success": False, "error": f"download failed: {exc}"}

        # GGUF validation — catch corrupt/misformatted downloads before
        # the user tries to serve the file.
        try:
            self._verify_gguf(dest)
        except ValueError as exc:
            dest.unlink()
            return {
                "success": False,
                "error": f"GGUF validation failed: {exc}",
            }

        return {"success": True, "path": str(dest), "app_id": app_id}

    async def uninstall(self, app_id: str) -> dict:
        target_dir = backend_model_dir(BACKEND_ID, app_id)
        deleted: list[str] = []
        if target_dir.exists():
            for f in sorted(target_dir.glob("*")):
                if f.is_file():
                    f.unlink()
                    deleted.append(f.name)
            try:
                target_dir.rmdir()
            except OSError:
                pass
        return {"success": True, "deleted": deleted, "backend": BACKEND_ID}

    @staticmethod
    def _verify_gguf(path: Path) -> None:
        """Raise ValueError if the file isn't a valid GGUF.

        GGUF files start with the four literal ASCII bytes ``GGUF``
        followed by a 32-bit version field (currently 2 or 3).
        """
        try:
            with open(path, "rb") as fh:
                magic = fh.read(4)
        except OSError as exc:
            raise ValueError(f"cannot read file: {exc}") from exc
        if magic != GGUF_MAGIC:
            raise ValueError(
                f"expected GGUF magic bytes, got {magic!r} ({magic.hex()})"
            )
