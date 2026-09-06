from __future__ import annotations

import time

import httpx

from tinyagentos.clients.retry import with_retry


class QmdClient:
    """HTTP client for qmd serve API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def init(self) -> None:
        self._client = httpx.AsyncClient(timeout=60)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    async def embed(self, text: str, *, timeout=httpx.USE_CLIENT_DEFAULT) -> list[float]:
        """Get embedding vector for text via qmd serve /embed.

        Parameters
        ----------
        text:
            The text to embed.
        timeout:
            Per-call timeout in seconds, or httpx.USE_CLIENT_DEFAULT to keep
            the client's default (60 s).  Pass a float to override for this
            single request — useful when one slow model load should not block
            the whole chain.
        """
        url = f"{self.base_url}/embed"
        client = self._client

        async def _call():
            resp = await client.post(url, json={"text": text}, timeout=timeout)
            resp.raise_for_status()
            return resp

        resp = await with_retry(_call)
        return resp.json()["embedding"]

    async def search(
        self,
        query: str,
        collection: str | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
        *,
        timeout=httpx.USE_CLIENT_DEFAULT,
    ) -> list[dict]:
        """Search documents via qmd serve /search.

        Parameters
        ----------
        timeout:
            Per-call timeout in seconds, or httpx.USE_CLIENT_DEFAULT to keep
            the client's default (60 s).  Pass a float to override for this
            single request.
        """
        url = f"{self.base_url}/search"
        client = self._client
        payload: dict = {"query": query, "limit": limit}
        if collection is not None:
            payload["collection"] = collection
        if tags is not None:
            payload["tags"] = tags

        async def _call():
            resp = await client.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp

        resp = await with_retry(_call)
        return resp.json().get("items", [])

    async def health(self) -> dict:
        """Check qmd serve health."""
        start = time.monotonic()
        try:
            resp = await self._client.get(f"{self.base_url}/health", timeout=10)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            resp.raise_for_status()
            data = resp.json()
            return {**data, "response_ms": elapsed_ms}
        except (httpx.HTTPError, Exception):
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"status": "error", "response_ms": elapsed_ms}
