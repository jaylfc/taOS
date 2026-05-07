"""Ollama installer — pulls models via `ollama pull` over HTTP.

Ollama runs its own daemon on port 11434 with an OllamaCompatible API,
including ``POST /api/pull`` (the same shape as rkllama, conveniently).
This installer just calls that endpoint — Ollama owns the model files
on disk; we don't push anything from the controller.

Variants in catalog manifests can declare an explicit ``ollama_name``
field with the Ollama library identifier (e.g. ``qwen2.5:3b``,
``llama3.2:1b-q8_0``). When absent, we fall back to the manifest's ID
verbatim — works for any model whose catalog ID matches its Ollama
library entry, fails clearly otherwise so the user can fix the manifest.

Configuration via env var (matching scripts/install-ollama.sh):

- ``OLLAMA_HOST`` — daemon URL (default: ``http://localhost:11434``)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from tinyagentos.installers.base import AppInstaller

logger = logging.getLogger(__name__)


def _default_host() -> str:
    """Resolve daemon URL from OLLAMA_HOST or fallback to localhost:11434."""
    raw = os.environ.get("OLLAMA_HOST", "").strip()
    if not raw:
        return "http://localhost:11434"
    # OLLAMA_HOST sometimes carries just `host:port` without scheme — normalize.
    if "://" not in raw:
        raw = f"http://{raw}"
    return raw.rstrip("/")


class OllamaInstaller(AppInstaller):
    """Install models for serving via the Ollama daemon."""

    def __init__(self, host: str | None = None, timeout: int = 3600):
        self.host = host.rstrip("/") if host else _default_host()
        # Pulls can take a long time (multi-GB layers, slow mirrors).
        # 60 minutes is a generous default; callers can override.
        self.timeout = timeout

    async def install(
        self,
        app_id: str,
        install_config: dict,
        variant: dict | None = None,
        **_: Any,
    ) -> dict:
        # Resolve the ollama model name. Variant's explicit field wins; fall
        # back to manifest_id (passed as app_id by the dispatcher).
        model_name: str = ""
        if variant and isinstance(variant, dict):
            model_name = str(variant.get("ollama_name", "") or "").strip()
        if not model_name:
            model_name = app_id

        if not model_name:
            return {
                "success": False,
                "error": "no ollama model name to pull (variant.ollama_name or app_id required)",
            }

        # Verify the daemon is reachable before kicking off a long pull.
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.host}/api/tags")
                resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            return {
                "success": False,
                "error": (
                    f"ollama daemon not reachable at {self.host}: {exc}. "
                    "Run scripts/install-ollama.sh first."
                ),
            }

        # Pull via the streaming /api/pull endpoint. Status events flow as
        # NDJSON; a final {"status": "success"} indicates completion.
        last_status = ""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.host}/api/pull",
                    json={"name": model_name, "stream": True},
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if data.get("error"):
                            return {
                                "success": False,
                                "error": f"ollama pull failed: {data['error']}",
                                "ollama_model": model_name,
                            }
                        last_status = data.get("status", last_status)
        except httpx.HTTPError as exc:
            return {
                "success": False,
                "error": f"ollama pull HTTP error: {exc}",
                "ollama_model": model_name,
            }

        if last_status != "success":
            return {
                "success": False,
                "error": (
                    f"ollama pull ended with status {last_status!r} (expected 'success'). "
                    "Check the daemon logs for details."
                ),
                "ollama_model": model_name,
            }

        return {
            "success": True,
            "app_id": app_id,
            "ollama_model": model_name,
            "endpoint": self.host,
            "runtime_location": {
                "host": self.host.replace("http://", "").replace("https://", "").split(":")[0],
                "port": int(self.host.rsplit(":", 1)[-1]) if ":" in self.host.replace("://", "") else 11434,
                "backend": "ollama",
            },
        }

    async def uninstall(self, app_id: str) -> dict:
        """Best-effort uninstall via DELETE /api/delete.

        The ollama model name needs to come from caller-supplied metadata
        (the dispatcher's registry knows what was pulled). Without it we
        fall back to the app_id itself.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(
                    "DELETE",
                    f"{self.host}/api/delete",
                    json={"name": app_id},
                )
                if resp.status_code == 200:
                    return {"success": True, "status": "uninstalled", "ollama_model": app_id}
                # 404 means already gone — that's fine for uninstall.
                if resp.status_code == 404:
                    return {"success": True, "status": "not-installed", "ollama_model": app_id}
                return {
                    "success": False,
                    "error": f"ollama delete returned {resp.status_code}: {resp.text[:200]}",
                }
        except httpx.HTTPError as exc:
            return {"success": False, "error": f"ollama delete HTTP error: {exc}"}
