"""Regression tests for DNS-rebinding / TOCTOU SSRF hardening in download_file.

Covers the installer source_url / download_url fetch path -- the download_file
function in download_installer.py now validates every URL hop via
resolve_safe_public_ip and pins the connection to the validated IP so a
second DNS resolution cannot return a different (malicious) address.
"""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tinyagentos.installers.download_installer import (
    _pin_url,
    _ssrf_guard,
    download_file,
)


# ── helpers ────────────────────────────────────────────────────────

def _gai(ip: str):
    """Return a mocked socket.getaddrinfo result for a single IPv4."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _gai6(ip: str):
    """Return a mocked socket.getaddrinfo result for a single IPv6."""
    return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0))]


class _FakeResponse:
    """A minimal stand-in for ``httpx.Response`` that the download_file loop
    can consume."""

    def __init__(self, status: int = 200, body: bytes = b"ok",
                 headers: dict | None = None) -> None:
        self.status_code = status
        self.headers = httpx.Headers(headers or {})
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=MagicMock(),
                response=self,  # type: ignore[arg-type]
            )

    async def aiter_bytes(self, chunk_size: int = 65536):
        yield self._body

    async def aread(self) -> None:
        pass  # redirect body drain


def _mock_async_client(*fake_responses: _FakeResponse) -> tuple[MagicMock, AsyncMock]:
    """Return ``(mock_client, mock_send)`` for a fake httpx.AsyncClient.

    The mock client's ``.send()`` (an AsyncMock) returns each
    *fake_responses* in order (or the last one if called more times).
    ``build_request`` delegates to the real ``httpx.AsyncClient`` so
    the returned request objects have the right shape.
    ``__aenter__`` / ``__aexit__`` are wired so ``async with`` returns
    the configured mock client, not a default AsyncMock.
    """
    mock_client = MagicMock()
    mock_client.build_request = httpx.AsyncClient().build_request
    mock_send: AsyncMock
    if len(fake_responses) == 1:
        mock_send = AsyncMock(return_value=fake_responses[0])
    else:
        mock_send = AsyncMock(side_effect=list(fake_responses))
    mock_client.send = mock_send
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client, mock_send


# ── _ssrf_guard ────────────────────────────────────────────────────

class TestSsrfGuard:
    def test_allows_public_ip(self):
        assert _ssrf_guard("https://8.8.8.8/file.bin") == "8.8.8.8"

    def test_allows_public_hostname(self):
        with patch("socket.getaddrinfo", return_value=_gai("93.184.216.34")):
            assert _ssrf_guard("https://example.com/file.bin") == "93.184.216.34"

    def test_rejects_loopback(self):
        with pytest.raises(ValueError, match="SSRF blocked"):
            _ssrf_guard("http://127.0.0.1/secret")

    def test_rejects_private_rfc1918(self):
        for url in ("http://10.0.0.1/x", "https://192.168.1.1/x", "http://172.16.0.1/x"):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _ssrf_guard(url)

    def test_rejects_link_local_metadata(self):
        with pytest.raises(ValueError, match="SSRF blocked"):
            _ssrf_guard("http://169.254.169.254/latest/meta-data")

    def test_rejects_hostname_resolving_to_private(self):
        with patch("socket.getaddrinfo", return_value=_gai("10.1.2.3")):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _ssrf_guard("https://evil.example/model.bin")

    def test_rejects_mixed_public_and_private(self):
        # If ANY resolved address is non-public, reject the whole host.
        mixed = _gai("93.184.216.34") + _gai("192.168.0.1")
        with patch("socket.getaddrinfo", return_value=mixed):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _ssrf_guard("https://example.com/model.bin")

    def test_rejects_non_http_schemes(self):
        with pytest.raises(ValueError, match="SSRF blocked"):
            _ssrf_guard("ftp://8.8.8.8/file.bin")
        with pytest.raises(ValueError, match="SSRF blocked"):
            _ssrf_guard("file:///etc/passwd")

    def test_rejects_ipv6_loopback_and_ula(self):
        for url in ("http://[::1]/", "http://[fc00::1]/"):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _ssrf_guard(url)

    def test_allows_public_ipv6_literal(self):
        assert _ssrf_guard("http://[2606:4700:4700::1111]/") == "2606:4700:4700::1111"


# ── _pin_url ───────────────────────────────────────────────────────

class TestPinUrl:
    def test_pins_ipv4(self):
        pinned, host_hdr = _pin_url("https://example.com/path?q=1", "93.184.216.34")
        assert pinned == "https://93.184.216.34/path?q=1"
        assert host_hdr == "example.com"

    def test_pins_ipv4_with_port(self):
        pinned, host_hdr = _pin_url("http://example.com:8080/api", "1.2.3.4")
        assert pinned == "http://1.2.3.4:8080/api"
        assert host_hdr == "example.com:8080"

    def test_pins_ipv6(self):
        pinned, host_hdr = _pin_url("https://example.com/x", "2606:4700:4700::1111")
        assert pinned == "https://[2606:4700:4700::1111]/x"
        assert host_hdr == "example.com"

    def test_pins_ipv6_with_port(self):
        pinned, host_hdr = _pin_url("https://example.com:443/x", "::1")
        assert pinned == "https://[::1]:443/x"
        assert host_hdr == "example.com:443"


# ── download_file integration (mocked httpx + DNS) ─────────────────

class TestDownloadFileSsrf:
    @pytest.mark.asyncio
    async def test_blocks_private_ip(self, tmp_path):
        """download_file must reject a URL that resolves to a private IP."""
        dest = tmp_path / "out.bin"
        with pytest.raises(ValueError, match="SSRF blocked"):
            await download_file("http://127.0.0.1/model.bin", dest)

    @pytest.mark.asyncio
    async def test_blocks_hostname_to_private(self, tmp_path):
        """A hostname resolving to RFC1918 must be rejected before any fetch."""
        dest = tmp_path / "out.bin"
        with patch("socket.getaddrinfo", return_value=_gai("10.0.0.5")):
            with pytest.raises(ValueError, match="SSRF blocked"):
                await download_file("https://evil.example/model.bin", dest)

    @pytest.mark.asyncio
    async def test_allows_public_with_mocked_httpx(self, tmp_path):
        """Public URL: SSRF guard passes; download proceeds with pinned IP."""
        dest = tmp_path / "out.bin"

        fake = _FakeResponse(200, body=b"hello world",
                              headers={"content-length": "11"})

        mock_client, mock_send = _mock_async_client(fake)

        with patch("socket.getaddrinfo", return_value=_gai("93.184.216.34")):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await download_file(
                    "https://example.com/model.bin",
                    dest,
                    expected_sha256=(
                        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
                    ),
                )

        # Verify the request was built with the pinned IP.
        call_args = mock_send.call_args[0]
        request = call_args[0]
        assert "93.184.216.34" in str(request.url)
        assert request.headers.get("host") == "example.com"
        assert result == dest
        assert dest.read_bytes() == b"hello world"

    @pytest.mark.asyncio
    async def test_dns_rebind_toctou_prevented(self, tmp_path):
        """The connection must be pinned to the IP from the ONE resolve call.

        Even if a second resolve would return a private IP, the connection
        goes to the originally-validated public IP (which httpx never
        resolves itself because we pin it in the URL).
        """
        dest = tmp_path / "out.bin"
        fake = _FakeResponse(200, body=b"safe",
                              headers={"content-length": "4"})

        mock_client, mock_send = _mock_async_client(fake)

        # First call returns public; second (if made) would return private.
        # Because we pin the IP in the URL, httpx never re-resolves.
        call_count = [0]

        def _alternating(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _gai("93.184.216.34")  # first (validation) -- public
            return _gai("10.99.99.99")        # second would be private

        with patch("socket.getaddrinfo", side_effect=_alternating):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await download_file(
                    "https://evil.example/model.bin", dest,
                )

        # The request MUST use the pinned public IP, not the private one.
        request = mock_send.call_args[0][0]
        assert "93.184.216.34" in str(request.url)
        assert "10.99.99.99" not in str(request.url)
        assert result == dest

    @pytest.mark.asyncio
    async def test_rejects_redirect_to_private_ip(self, tmp_path):
        """A redirect chain where the target is private must be rejected."""
        dest = tmp_path / "out.bin"

        # Redirect to a hostname that resolves to a private IP.
        redirect = _FakeResponse(302, body=b"",
                                 headers={"location": "https://evil.internal/leak"})

        mock_client, mock_send = _mock_async_client(redirect)

        def _dns(host, *args, **kwargs):
            if "evil.internal" in str(host):
                return _gai("10.0.0.5")
            return _gai("93.184.216.34")

        with patch("socket.getaddrinfo", side_effect=_dns):
            with patch("httpx.AsyncClient", return_value=mock_client):
                with pytest.raises(ValueError, match="SSRF blocked"):
                    await download_file("https://safe.example/model.bin", dest)

    @pytest.mark.asyncio
    async def test_follows_public_redirect(self, tmp_path):
        """A redirect to another public host should be followed and pin the
        new IP."""
        dest = tmp_path / "out.bin"

        # First: redirect.  Second: the actual response.
        redirect_resp = _FakeResponse(302, body=b"",
                                       headers={"location": "https://cdn.example/file.bin"})
        ok_resp = _FakeResponse(200, body=b"downloaded",
                                 headers={"content-length": "10"})

        mock_client, mock_send = _mock_async_client(redirect_resp, ok_resp)

        def _ip_for_host(*args, **kwargs):
            # Return different public IPs so we can verify each hop is
            # validated independently and pinned to its own IP.
            host = args[0] if args else ""
            if "safe.example" in host:
                return _gai("93.184.216.34")
            return _gai("1.2.3.4")  # CDN

        with patch("socket.getaddrinfo", side_effect=_ip_for_host):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await download_file(
                    "https://safe.example/model.bin", dest,
                )

        # Two send() calls: one for the redirect (pinned to 93.184.216.34),
        # one for the CDN (pinned to 1.2.3.4).
        assert mock_send.call_count == 2
        r0 = mock_send.call_args_list[0][0][0]
        r1 = mock_send.call_args_list[1][0][0]
        assert "93.184.216.34" in str(r0.url)
        assert "1.2.3.4" in str(r1.url)
        assert result == dest
        assert dest.read_bytes() == b"downloaded"

    @pytest.mark.asyncio
    async def test_too_many_redirects(self, tmp_path):
        """Exceeding max redirects must raise ValueError."""
        dest = tmp_path / "out.bin"
        redirect = _FakeResponse(302, body=b"",
                                  headers={"location": "https://safe.example/loop"})

        mock_client, mock_send = _mock_async_client(redirect)

        with patch("socket.getaddrinfo", return_value=_gai("93.184.216.34")):
            with patch("httpx.AsyncClient", return_value=mock_client):
                with pytest.raises(ValueError, match="Too many redirects"):
                    await download_file("https://safe.example/model.bin", dest)
