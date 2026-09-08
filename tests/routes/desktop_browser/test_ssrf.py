"""Tests for SSRF guard — proves the host blocklist + redirect re-resolution."""
from __future__ import annotations

from unittest.mock import patch

import pytest


class TestUrlScheme:
    def test_rejects_non_http_scheme(self):
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_url_or_raise,
        )

        for bad in ("file:///etc/passwd", "gopher://x.test/", "javascript:alert(1)", "data:text/html,xx"):
            with pytest.raises(SsrfBlockedError, match="scheme"):
                validate_url_or_raise(bad)

    def test_accepts_http_and_https(self):
        from tinyagentos.routes.desktop_browser.ssrf import validate_url_or_raise

        # Public IPs resolve fine and pass — using example.com which is RFC 2606
        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("93.184.216.34", 0)),  # AF_INET
            ],
        ):
            validate_url_or_raise("http://example.com/")
            validate_url_or_raise("https://example.com/")


class TestPrivateAddressRejection:
    @pytest.mark.parametrize("addr", [
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",  # AWS metadata service
        "0.0.0.0",
        "224.0.0.1",        # multicast
        "255.255.255.255",  # broadcast
        "100.64.0.1",       # RFC 6598 CGNAT (start of range)
        "100.127.255.254",  # RFC 6598 CGNAT (near end of range)
    ])
    def test_rejects_ipv4_blocklisted(self, addr):
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_resolved_addr,
        )

        with pytest.raises(SsrfBlockedError):
            validate_resolved_addr(addr)

    @pytest.mark.parametrize("addr", [
        "::1",
        "fc00::1",        # ULA
        "fe80::1",        # link-local
        "ff02::1",        # multicast
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",   # IPv4-mapped RFC1918
        "fec0::1",        # RFC 3879 deprecated site-local (still on legacy gear)
        "feff::1",        # end of fec0::/10 range
    ])
    def test_rejects_ipv6_blocklisted(self, addr):
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_resolved_addr,
        )

        with pytest.raises(SsrfBlockedError):
            validate_resolved_addr(addr)

    @pytest.mark.parametrize("addr", [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",   # example.com
        "2001:4860:4860::8888",  # public IPv6
    ])
    def test_accepts_public(self, addr):
        from tinyagentos.routes.desktop_browser.ssrf import validate_resolved_addr

        validate_resolved_addr(addr)  # must not raise


class TestHostnameRejection:
    @pytest.mark.parametrize("host", [
        "anything.local",
        "host.onion",
        "deeper.subdomain.local",
        "router.home",
        "wiki.corp",
        "nas.lan",
        "mail.intranet",
    ])
    def test_rejects_internal_network_tlds(self, host):
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_url_or_raise,
        )

        with pytest.raises(SsrfBlockedError, match="hostname"):
            validate_url_or_raise(f"http://{host}/")


class TestEncodedIpAddresses:
    """Decimal/octal/hex encoded IP literals must be parsed and rejected."""

    @pytest.mark.parametrize("encoded", [
        "2130706433",        # decimal for 127.0.0.1
        "0x7f000001",        # hex for 127.0.0.1
        "017700000001",      # octal for 127.0.0.1
    ])
    def test_rejects_encoded_loopback(self, encoded):
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_url_or_raise,
        )

        with pytest.raises(SsrfBlockedError):
            validate_url_or_raise(f"http://{encoded}/")

    @pytest.mark.parametrize("encoded", [
        "1681915905",        # decimal for 100.64.0.1 (CGNAT)
        "0x64400001",        # hex for 100.64.0.1
    ])
    def test_rejects_encoded_cgnat(self, encoded):
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_url_or_raise,
        )

        with pytest.raises(SsrfBlockedError):
            validate_url_or_raise(f"http://{encoded}/")


class TestDnsResolutionFailure:
    def test_unresolvable_host_raises(self):
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_url_or_raise,
        )

        # Use a hostname that will fail DNS — patching to simulate
        import socket

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            side_effect=socket.gaierror("name resolution failed"),
        ):
            with pytest.raises(SsrfBlockedError, match="resolve"):
                validate_url_or_raise("http://does-not-exist-anywhere.test/")

    def test_multi_record_host_must_pass_all(self):
        """A host that resolves to multiple IPs must be rejected if ANY is blocked.

        Defends against DNS pinning attacks where a hostname returns one
        public IP and one private IP — a naive implementation might fetch
        the public one but a re-resolve at TCP-connect time could hit the
        private one.
        """
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_url_or_raise,
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("8.8.8.8", 0)),
                (2, 1, 6, "", ("127.0.0.1", 0)),
            ],
        ):
            with pytest.raises(SsrfBlockedError):
                validate_url_or_raise("http://evil.test/")

    def test_rejects_when_only_ipv6_resolves_to_private(self):
        """Defends against DNS records that mix public IPv4 with private IPv6."""
        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            validate_url_or_raise,
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("8.8.8.8", 0)),         # public IPv4
                (10, 1, 6, "", ("::1", 0, 0, 0)),       # private IPv6 (loopback)
            ],
        ):
            with pytest.raises(SsrfBlockedError):
                validate_url_or_raise("http://dual-stack.test/")


class TestPinnedTransport:
    """The guarded client must connect to the address the guard checked."""

    def test_validate_returns_the_addresses_it_approved(self):
        from tinyagentos.routes.desktop_browser.ssrf import validate_url_or_raise

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("93.184.216.34", 0)),
                (2, 1, 6, "", ("93.184.216.35", 0)),
            ],
        ):
            addrs = validate_url_or_raise("http://example.com/")

        # Resolver order is preserved: the first answer is the one a pinned
        # connection uses, so it must not come back through a set.
        assert addrs == ["93.184.216.34", "93.184.216.35"]

    def test_client_pins_the_connection_to_the_checked_address(self):
        """The pin sits on the pool's network backend, below the request URL."""
        from tinyagentos.routes.desktop_browser.ssrf import (
            _PinnedResolutionBackend,
            guarded_async_client,
        )

        client = guarded_async_client()
        backend = client._transport._pool._network_backend
        assert isinstance(backend, _PinnedResolutionBackend)

    def test_pinned_backend_hands_the_socket_a_checked_literal(self):
        """connect_tcp resolves once, validates, and connects to that answer."""
        import asyncio
        from unittest.mock import AsyncMock

        from tinyagentos.routes.desktop_browser.ssrf import _PinnedResolutionBackend

        inner = AsyncMock()
        backend = _PinnedResolutionBackend(inner)

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            asyncio.run(backend.connect_tcp("example.com", 443, timeout=5.0))

        args, kwargs = inner.connect_tcp.await_args
        assert args[0] == "93.184.216.34"
        assert args[1] == 443
        assert kwargs["timeout"] == 5.0

    def test_pinned_backend_refuses_a_blocked_answer(self):
        import asyncio

        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            _PinnedResolutionBackend,
        )
        from unittest.mock import AsyncMock

        inner = AsyncMock()
        backend = _PinnedResolutionBackend(inner)

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 0))],
        ):
            with pytest.raises(SsrfBlockedError):
                asyncio.run(backend.connect_tcp("rebind.test", 80))

        inner.connect_tcp.assert_not_awaited()

    def test_allow_private_reaches_the_pin(self):
        """A LAN-facing caller pins too — it just permits RFC1918."""
        import asyncio
        from unittest.mock import AsyncMock

        from tinyagentos.routes.desktop_browser.ssrf import _PinnedResolutionBackend

        inner = AsyncMock()
        backend = _PinnedResolutionBackend(inner, allow_private=True)

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("192.168.1.50", 0))],
        ):
            asyncio.run(backend.connect_tcp("nas.example.com", 80))

        assert inner.connect_tcp.await_args[0][0] == "192.168.1.50"

    def test_tls_verification_stays_on_and_the_url_keeps_the_hostname(self):
        """Pinning below the URL is what keeps certificate checks meaningful."""
        import ssl

        from tinyagentos.routes.desktop_browser.ssrf import guarded_async_client

        client = guarded_async_client()
        request = client.build_request("GET", "https://example.com/page")
        # The URL is untouched, so httpcore derives SNI and the cert hostname
        # from the real name rather than from a pinned literal.
        assert request.url.host == "example.com"
        assert request.headers["host"] == "example.com"

        ssl_context = client._transport._pool._ssl_context
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert ssl_context.check_hostname is True

    def test_unix_sockets_are_refused(self):
        import asyncio
        from unittest.mock import AsyncMock

        from tinyagentos.routes.desktop_browser.ssrf import (
            SsrfBlockedError,
            _PinnedResolutionBackend,
        )

        backend = _PinnedResolutionBackend(AsyncMock())
        with pytest.raises(SsrfBlockedError):
            asyncio.run(backend.connect_unix_socket("/run/taos.sock"))
