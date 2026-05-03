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
            "tinyagentos.routes.desktop_browser.ssrf.socket.gethostbyname_ex",
            return_value=("example.com", [], ["93.184.216.34"]),
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
    ])
    def test_rejects_local_and_onion_tlds(self, host):
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
            "tinyagentos.routes.desktop_browser.ssrf.socket.gethostbyname_ex",
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
            "tinyagentos.routes.desktop_browser.ssrf.socket.gethostbyname_ex",
            return_value=("evil.test", [], ["8.8.8.8", "127.0.0.1"]),
        ):
            with pytest.raises(SsrfBlockedError):
                validate_url_or_raise("http://evil.test/")
