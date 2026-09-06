"""Shared streaming text fetcher with size and content-type guards."""

from __future__ import annotations

import asyncio

import httpx


_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


async def stream_text_response(
    resp: httpx.Response,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> tuple[str, str, bytes]:
    """Read a streaming response with text/* content-type and byte cap.

    Returns (content_type, encoding, body_bytes).
    Raises ValueError for non-text content-type or body > max_bytes.
    The body is streamed so the cap prevents OOM on hostile responses.

    Falls back to ``resp.text`` when the response does not expose a working
    ``aiter_bytes`` (e.g. test doubles that only implement ``.text``).
    """
    content_type = resp.headers.get("content-type", "")
    ct_base = content_type.split(";")[0].strip().lower()
    if ct_base and not ct_base.startswith("text/"):
        raise ValueError(
            f"Non-text content-type {content_type!r} — only text/* is supported"
        )

    try:
        iterator = resp.aiter_bytes(8192)
        if asyncio.iscoroutine(iterator):
            raise TypeError
        chunks: list[bytes] = []
        total = 0
        async for chunk in iterator:
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    f"Response body exceeds {max_bytes} bytes"
                )
            chunks.append(chunk)
        encoding = resp.encoding or "utf-8"
        return content_type, encoding, b"".join(chunks)
    except TypeError:
        text = resp.text
        total = len(text.encode("utf-8"))
        if total > max_bytes:
            raise ValueError(
                f"Response body exceeds {max_bytes} bytes"
            )
        return content_type, resp.encoding or "utf-8", text.encode("utf-8")

