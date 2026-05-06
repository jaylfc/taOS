"""rkllama installer — pulls .rkllm models via rkllama's own /api/pull endpoint.

rkllama is a local NPU model server (already running on Orange Pi via
``install-rknpu.sh``). It exposes an Ollama-compatible HTTP API including
``POST /api/pull`` which downloads a model from HuggingFace, places the
``.rkllm`` weight in its models directory, and writes a ``Modelfile``
alongside it. This installer just calls that endpoint — no file pushing
from the controller, no remote SSH, no Modelfile generation on our side.

The endpoint expects the model identifier in three slash-separated parts:
``<hf_user>/<hf_repo>/<filename>.rkllm``. We derive that from the catalog
manifest's variant ``download_url`` (a full HuggingFace ``/resolve/main/``
URL), so we don't need any extra fields on the manifest.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from tinyagentos.installers.base import AppInstaller

logger = logging.getLogger(__name__)


# Match a full HF resolve URL and capture (user, repo, filename).
# Example: https://huggingface.co/c01zaut/Qwen2.5-3B-Instruct-rk3588-1.1.4/resolve/main/foo.rkllm
_HF_RESOLVE_RE = re.compile(
    r"^https?://huggingface\.co/(?P<user>[^/]+)/(?P<repo>[^/]+)/resolve/[^/]+/(?P<filename>[^/?#]+)$"
)


def parse_hf_resolve_url(url: str) -> tuple[str, str, str]:
    """Return (user, repo, filename) for an HF resolve URL.

    Raises ValueError if the URL doesn't look like a HuggingFace resolve URL.
    """
    m = _HF_RESOLVE_RE.match(url)
    if not m:
        raise ValueError(
            f"download_url is not a HuggingFace resolve URL: {url!r}. "
            "Expected shape: https://huggingface.co/<user>/<repo>/resolve/<branch>/<file>"
        )
    return m.group("user"), m.group("repo"), m.group("filename")


class RkllamaInstaller(AppInstaller):
    """Install ``.rkllm`` models by calling the rkllama ``/api/pull`` endpoint.

    By default the installer talks to ``http://localhost:8080`` — the
    controller-local rkllama. When a remote worker hosts rkllama, callers
    pass ``rkllama_url`` (e.g. ``http://192.168.6.123:8080``).
    """

    def __init__(self, rkllama_url: str | None = None, timeout: int = 1800):
        # rkllama install can take many minutes for multi-GB weights — give it
        # half an hour by default. Caller may override.
        self.rkllama_url = (rkllama_url or "http://localhost:8080").rstrip("/")
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
                "error": "rkllama install requires a variant (with download_url)",
            }
        url = variant.get("download_url")
        if not url:
            return {
                "success": False,
                "error": f"variant {variant.get('id')!r} missing download_url",
            }

        try:
            user, repo, filename = parse_hf_resolve_url(url)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        # rkllama's pull handler splits on '/' and expects exactly three parts.
        model_spec = f"{user}/{repo}/{filename}"
        # Stable model name in rkllama: the catalog app_id, not the auto-derived
        # filename. Lets agents reference "qwen2.5-3b-rkllm" instead of the
        # quant-suffixed filename.
        body = {"model": model_spec, "model_name": app_id}

        endpoint = f"{self.rkllama_url}/api/pull"
        logger.info("rkllama install: POST %s body=%r", endpoint, body)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # /api/pull streams ndjson progress; we drain the stream until
                # close. Any non-2xx status is an install failure.
                async with client.stream("POST", endpoint, json=body) as resp:
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode("utf-8", errors="replace")
                        return {
                            "success": False,
                            "error": f"rkllama /api/pull returned {resp.status_code}: {text[:500]}",
                        }
                    last_line = ""
                    async for line in resp.aiter_lines():
                        if line:
                            last_line = line
                    logger.info(
                        "rkllama install: pull complete for %s (last line: %s)",
                        app_id, last_line[:200],
                    )
        except httpx.HTTPError as exc:
            return {
                "success": False,
                "error": f"rkllama /api/pull failed: {exc}",
            }

        # Verify the model now appears in /api/tags so we know rkllama
        # successfully registered it.
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                tags = await client.get(f"{self.rkllama_url}/api/tags")
                tags.raise_for_status()
                names = {m.get("name") for m in tags.json().get("models", [])}
                if app_id not in names:
                    return {
                        "success": False,
                        "error": (
                            f"rkllama pull returned 200 but {app_id!r} is not in "
                            f"/api/tags. Known models: {sorted(names)[:5]}"
                        ),
                    }
        except httpx.HTTPError as exc:
            logger.warning(
                "rkllama install: /api/tags verification failed: %s", exc
            )
            # Non-fatal — pull succeeded; verification problem is likely transient.

        return {"success": True, "app_id": app_id, "model_name": app_id}

    async def uninstall(self, app_id: str) -> dict:
        endpoint = f"{self.rkllama_url}/api/delete"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(
                    "DELETE", endpoint, json={"name": app_id}
                )
                if resp.status_code == 404:
                    return {"success": True, "status": "not_installed"}
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            return {"success": False, "error": f"rkllama /api/delete failed: {exc}"}
        return {"success": True, "status": "uninstalled"}


def resolve_rkllama_url(target_remote: str | None) -> str:
    """Resolve which rkllama instance to talk to.

    - ``None`` / empty / "local" → controller's own rkllama on loopback.
    - Anything else → the remote worker's hostname on port 8080.

    rkllama always serves on 8080; that's hard-coded by ``install-rknpu.sh``
    and the systemd unit. If we ever make the port configurable per worker
    we'll need a registry lookup here.
    """
    if not target_remote or target_remote == "local":
        return "http://localhost:8080"
    return f"http://{target_remote}:8080"
