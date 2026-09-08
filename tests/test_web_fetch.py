from __future__ import annotations

import pytest
import httpx
from tinyagentos.web_fetch import stream_text_response


class _StreamingMockResponse:
    """Response that tracks how many bytes are read from the stream."""

    def __init__(self, chunks, content_type="text/html", encoding="utf-8"):
        self._chunks = list(chunks)
        self.status_code = 200
        self.headers = {"content-type": content_type}
        self.is_redirect = False
        self.bytes_streamed = 0
        self._encoding = encoding

    def raise_for_status(self):
        pass

    async def aiter_bytes(self, chunk_size=8192):
        for chunk in self._chunks:
            self.bytes_streamed += len(chunk)
            yield chunk

    @property
    def text(self):
        return b"".join(self._chunks).decode(self._encoding, errors="replace")

    @property
    def encoding(self):
        return self._encoding


@pytest.mark.asyncio
async def test_stream_text_response_accepts_text_html():
    """text/html under the cap should be returned in full."""
    chunks = [b"<html><body>Hello</body></html>"]
    resp = _StreamingMockResponse(chunks, content_type="text/html")
    content_type, encoding, body = await stream_text_response(resp, max_bytes=1024)
    assert content_type == "text/html"
    assert body == b"<html><body>Hello</body></html>"
    assert resp.bytes_streamed == len(b"<html><body>Hello</body></html>")


@pytest.mark.asyncio
async def test_stream_text_response_accepts_text_plain():
    """text/plain under the cap should be accepted."""
    chunks = [b"plain text content"]
    resp = _StreamingMockResponse(chunks, content_type="text/plain")
    content_type, encoding, body = await stream_text_response(resp, max_bytes=1024)
    assert content_type == "text/plain"
    assert body == b"plain text content"


@pytest.mark.asyncio
async def test_stream_text_response_rejects_application_octet_stream():
    """application/octet-stream must be rejected without buffering."""
    resp = _StreamingMockResponse([b"binary data"], content_type="application/octet-stream")
    with pytest.raises(ValueError, match="Non-text"):
        await stream_text_response(resp, max_bytes=1024)
    assert resp.bytes_streamed == 0, "Non-text response must not be buffered"


@pytest.mark.asyncio
async def test_stream_text_response_rejects_oversized_body_and_limits_bytes():
    """Streaming stops at the cap; full body is never buffered."""
    chunk_size = 8192
    num_chunks = 200  # ~1.5 MB total
    chunks = [b"x" * chunk_size] * num_chunks
    resp = _StreamingMockResponse(chunks, content_type="text/html")

    cap = 1024 * 1024  # 1 MB
    with pytest.raises(ValueError, match="exceeds"):
        await stream_text_response(resp, max_bytes=cap)

    assert resp.bytes_streamed <= cap + chunk_size, (
        f"Bytes streamed ({resp.bytes_streamed}) must not exceed cap ({cap}) "
        f"by more than one chunk"
    )


@pytest.mark.asyncio
async def test_stream_text_response_content_type_with_charset():
    """Content-type with charset should still be accepted as text/*."""
    chunks = [b"<html>charset test</html>"]
    resp = _StreamingMockResponse(
        chunks, content_type="text/html; charset=utf-8"
    )
    content_type, _, body = await stream_text_response(resp, max_bytes=1024)
    assert content_type == "text/html; charset=utf-8"
    assert body == b"<html>charset test</html>"
