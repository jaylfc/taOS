"""vLLM installer — downloads models to the HuggingFace cache.

vLLM loads models directly from the HF cache (~/.cache/huggingface/hub/)
via ``vllm serve <model>``, which triggers ``snapshot_download`` under
the hood.  Pre-downloading into the cache via this installer means the
first serve call starts immediately instead of blocking on a multi-GB
download.

When ``huggingface_hub`` is not installed, we install it first via pip
so the snapshot download can proceed.  A missing ``huggingface_hub`` is
the most common reason a fresh controller can't talk to HF.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from tinyagentos.installers.base import AppInstaller

logger = logging.getLogger(__name__)

BACKEND_ID = "vllm"


def _try_import_snapshot_download() -> Any | None:
    """Return ``snapshot_download`` callable, or None if not available."""
    try:
        from huggingface_hub import snapshot_download
        return snapshot_download
    except ImportError:
        return None


async def _ensure_huggingface_hub() -> str:
    """Install huggingface_hub via pip if not already present.

    Returns the pip output on success; raises RuntimeError on failure.
    """
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        "pip", "install", "huggingface_hub[hf_transfer]",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(
            f"pip install huggingface_hub failed: {(stdout or b'').decode()[:500]}"
        )
    return (stdout or b"").decode()


class VllmInstaller(AppInstaller):
    """Download HF repos to the local HF cache for vLLM serving."""

    def __init__(self, cache_dir: str | None = None):
        # cache_dir defaults to ~/.cache/huggingface/hub/ (the HF default).
        # Callers can override for tests or custom cache locations.
        self.cache_dir = cache_dir

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
                "error": "vllm install requires a variant (with hf_repo)",
            }

        repo = variant.get("hf_repo") or variant.get("download_url")
        if not repo:
            return {
                "success": False,
                "error": (
                    f"variant {variant.get('id')!r} missing hf_repo "
                    "(vLLM models are HF repos)"
                ),
            }
        # Normalise repo — strip https://huggingface.co/ prefix if present.
        if repo.startswith("https://huggingface.co/"):
            repo = repo[len("https://huggingface.co/"):]
        elif repo.startswith("http://"):
            repo = repo[len("http://"):]

        revision = variant.get("hf_revision") or "main"

        sd = _try_import_snapshot_download()
        if sd is None:
            try:
                await _ensure_huggingface_hub()
            except RuntimeError as exc:
                return {"success": False, "error": str(exc)}
            sd = _try_import_snapshot_download()
            if sd is None:
                return {
                    "success": False,
                    "error": (
                        "huggingface_hub still not importable after pip install — "
                        "check the controller's Python environment"
                    ),
                }

        # snapshot_download is synchronous (I/O bound), so run it in a
        # thread to avoid blocking the event loop.
        import asyncio

        loop = asyncio.get_running_loop()
        kwargs: dict = {
            "repo_id": repo,
            "revision": revision,
        }
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir

        try:
            local_path = await loop.run_in_executor(None, lambda: sd(**kwargs))
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "error": f"snapshot_download failed for {repo!r}: {exc}",
            }

        return {
            "success": True,
            "app_id": app_id,
            "repo": repo,
            "revision": revision,
            "local_path": str(local_path),
            "backend": BACKEND_ID,
        }

    async def uninstall(self, app_id: str) -> dict:
        """Best-effort removal from the HF cache.

        The HF cache is a shared content-addressed store — removing one
        model doesn't break others that share blobs. We scan the cache's
        refs for the model name and remove the symlink directory.
        """
        import shutil

        cache = Path(self.cache_dir) if self.cache_dir else Path.home() / ".cache" / "huggingface" / "hub"
        if not cache.exists():
            return {"success": True, "deleted": [], "backend": BACKEND_ID}

        deleted: list[str] = []
        # HF hub stores models under models--<org>--<name>
        safe_name = app_id.replace("/", "--")
        for models_dir in [
            cache / "models" / safe_name,
            cache / f"models--{safe_name}",
        ]:
            if models_dir.exists():
                try:
                    shutil.rmtree(str(models_dir))
                    deleted.append(str(models_dir))
                except OSError as exc:
                    logger.warning(
                        "vllm uninstall: could not remove %s: %s", models_dir, exc
                    )

        return {
            "success": True,
            "deleted": deleted,
            "backend": BACKEND_ID,
        }
