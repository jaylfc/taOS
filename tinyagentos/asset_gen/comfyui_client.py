"""Async ComfyUI API client for offline asset generation.

ComfyUI (https://github.com/comfyanonymous/ComfyUI) exposes a small HTTP API:

  POST {base}/prompt            submit a workflow graph -> {"prompt_id": "..."}
  GET  {base}/history/{id}      poll for completion -> {id: {outputs: {...}}}
  GET  {base}/view?filename=... fetch an output image (raw bytes)

This client submits a (already-parameterised) workflow, polls history until the
job produces an image, and fetches that image. It is deliberately *fail-soft*:
ComfyUI is an optional, store-installed backend that is not present in CI or on
GPU-less hosts, so every failure path (unreachable server, timeout, malformed
response, non-image body) is caught and turned into ``None`` rather than raised
at the caller. The route layer maps ``None`` to a clean HTTP response.

The base URL comes from the ``base_url`` argument, else the ``TAOS_COMFYUI_URL``
environment variable, else ``http://127.0.0.1:8188``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188"


def default_base_url() -> str:
    """Resolve the ComfyUI base URL from the environment, with a local default."""
    return os.environ.get("TAOS_COMFYUI_URL", DEFAULT_COMFYUI_URL)


@dataclass
class ComfyUIResult:
    """A completed ComfyUI generation: the output image bytes + the prompt id."""

    image_bytes: bytes
    prompt_id: str


def _looks_like_image(data: bytes) -> bool:
    """True if *data* starts with a known image magic signature.

    ComfyUI can answer 200 with a JSON/text error body on /view; those bytes
    would otherwise be saved as a ``.png`` and reported as success. Reject any
    body that is not clearly an image so it routes into the fail-soft path.
    """
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")  # PNG
        or data.startswith(b"\xff\xd8\xff")  # JPEG
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")  # WEBP
    )


class ComfyUIClient:
    """Thin async client for a single ComfyUI server.

    ``timeout`` bounds each individual HTTP call; ``poll_timeout`` bounds the
    total time spent waiting for a submitted workflow to finish (generation can
    take tens of seconds on modest GPUs); ``poll_interval`` is the gap between
    history polls.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout: float = 30.0,
        poll_timeout: float = 180.0,
        poll_interval: float = 1.0,
    ):
        self.base = (base_url or default_base_url()).rstrip("/")
        self._timeout = timeout
        self._poll_timeout = poll_timeout
        self._poll_interval = poll_interval

    async def generate(self, workflow: dict) -> Optional[ComfyUIResult]:
        """Submit *workflow*, wait for it to finish, and return its first output
        image. Returns ``None`` on any failure (never raises)."""
        client_id = uuid4().hex
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                submit = await client.post(
                    f"{self.base}/prompt",
                    json={"prompt": workflow, "client_id": client_id},
                )
                submit.raise_for_status()
                prompt_id = (submit.json() or {}).get("prompt_id")
                if not prompt_id:
                    logger.warning("ComfyUI /prompt returned no prompt_id")
                    return None

                image_ref = await self._poll_history(client, prompt_id)
                if image_ref is None:
                    return None

                view = await client.get(f"{self.base}/view", params=image_ref)
                view.raise_for_status()
                data = view.content
                if not _looks_like_image(data):
                    logger.warning(
                        "ComfyUI /view returned a non-image body: %s",
                        view.text[:200],
                    )
                    return None
                return ComfyUIResult(image_bytes=data, prompt_id=str(prompt_id))
        except Exception:  # noqa: BLE001 — fail-soft: any error -> None
            logger.warning("ComfyUI generation failed", exc_info=True)
            return None

    async def _poll_history(
        self, client: httpx.AsyncClient, prompt_id: str
    ) -> Optional[dict]:
        """Poll /history/{id} until the job produces an image, then return the
        {filename, subfolder, type} ref for /view. Returns ``None`` on timeout
        or when the job finished without an image."""
        deadline = time.monotonic() + self._poll_timeout
        while time.monotonic() < deadline:
            resp = await client.get(f"{self.base}/history/{prompt_id}")
            resp.raise_for_status()
            entry = (resp.json() or {}).get(prompt_id)
            if entry:
                ref = _first_image_ref(entry.get("outputs") or {})
                if ref is not None:
                    return ref
                # The job is in history but yielded no image: it is done and
                # failed, so stop polling rather than spin to the deadline.
                logger.warning("ComfyUI job %s finished with no image", prompt_id)
                return None
            await asyncio.sleep(self._poll_interval)
        logger.warning("ComfyUI history poll timed out for %s", prompt_id)
        return None


def _first_image_ref(outputs: dict) -> Optional[dict]:
    """Return the first output image's {filename, subfolder, type} ref, or None.

    ``outputs`` is ``{node_id: {"images": [{filename, subfolder, type}, ...]}}``.
    """
    for node in outputs.values():
        for image in node.get("images") or []:
            filename = image.get("filename")
            if filename:
                return {
                    "filename": filename,
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                }
    return None
