"""SSRF guard for the BrowserApp proxy.

The proxy server-side-fetches URLs on behalf of the user. Without
guards an attacker who controls a URL the proxy fetches can point us
at internal services (cloud metadata, the Pi's own admin interfaces,
RFC1918 hosts on the user's LAN). This module is the choke point that
parses every target URL, resolves its hostname, and refuses to proceed
if any resolved address is in the blocklist.

Usage:

    from tinyagentos.routes.desktop_browser.ssrf import (
        SsrfBlockedError,
        validate_url_or_raise,
    )

    try:
        validate_url_or_raise(target_url)
    except SsrfBlockedError as e:
        return JSONResponse({"error": str(e)}, status_code=403)

For redirect handling, callers must invoke validate_url_or_raise on
EVERY redirect target (not just the initial URL). The `httpx`
follow_redirects=True default does not give us a callback per redirect,
so the proxy implementation in PR 3 disables auto-follow and walks the
redirect chain manually, calling this guard each step.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class SsrfBlockedError(Exception):
    """Raised when a URL fails SSRF validation."""


# Hostname-suffix blocklist — applied before DNS resolution.
_BLOCKED_TLDS = (
    ".local",     # mDNS / Bonjour
    ".onion",     # Tor hidden services
    ".internal",  # common internal alias
    ".home",      # IETF reserved for residential networks
    ".corp",      # common enterprise internal alias
    ".lan",       # common home-network alias
    ".intranet",  # common enterprise alias
)

# Networks not covered by ipaddress's `is_private` flag but still
# reachable on typical home networks / shared infrastructure.
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),  # RFC 6598 CGNAT — common on consumer ISPs
    ipaddress.ip_network("fec0::/10"),      # RFC 3879 deprecated IPv6 site-local — still on legacy gear
)


def validate_url_or_raise(url: str, *, allow_private: bool = False) -> None:
    """Validate that `url` is safe to fetch.

    Parses the URL, checks scheme + hostname suffix, resolves DNS, and
    verifies every resolved address against the blocklist. Raises
    `SsrfBlockedError` on any failure.

    Pass ``allow_private=True`` to permit RFC1918 addresses and their IPv6
    unique-local equivalent (e.g. self-hosted LAN services) while still
    refusing loopback, link-local, multicast, reserved, and unspecified
    ranges. CGNAT is NOT permitted by ``allow_private`` — see
    `validate_resolved_addr` for the ranges that stay blocked either way.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise SsrfBlockedError(f"rejected scheme: {parsed.scheme!r}")

    if not parsed.hostname:
        raise SsrfBlockedError("URL has no hostname")

    host = parsed.hostname.strip().lower()

    # Hostname-based blocklist (catches .local / .onion / .internal
    # before we even resolve DNS, since these may not resolve at all
    # but still indicate non-public intent)
    for suffix in _BLOCKED_TLDS:
        if host.endswith(suffix):
            raise SsrfBlockedError(f"blocked hostname suffix: {suffix}")

    # Try parsing as a literal IP first. This catches decimal / octal /
    # hex / IPv4-mapped-IPv6 encodings that bypass naive string
    # blocklists. ipaddress accepts all of these forms.
    addrs: list[str] = []
    try:
        # ipaddress.ip_address handles "127.0.0.1", "::1",
        # "::ffff:127.0.0.1", and "0:0:0:0:0:0:0:1". It does NOT handle
        # decimal/octal IPv4 (e.g. "2130706433"). For those we use
        # socket.gethostbyname_ex which interprets them as hostnames
        # AND resolves them as IPs.
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        # Not a recognised literal — try the encoded forms by attempting
        # int conversion (decimal "2130706433") or hex/octal int parsing.
        encoded = _try_parse_encoded_ipv4(host)
        if encoded is not None:
            addrs = [encoded]
        else:
            # Real DNS resolution. We use getaddrinfo (NOT gethostbyname_ex)
            # because the latter is IPv4-only — it silently ignores AAAA
            # records, which would let an attacker DNS-pin a hostname to
            # one public IPv4 + one private IPv6 and bypass the guard.
            try:
                results = socket.getaddrinfo(host, None)
                # results is a list of (family, type, proto, canonname, sockaddr).
                # sockaddr[0] is the address string for both AF_INET and AF_INET6.
                addrs = list({r[4][0] for r in results})
            except socket.gaierror as e:
                raise SsrfBlockedError(f"could not resolve hostname: {e}") from e

    if not addrs:
        raise SsrfBlockedError("hostname resolved to no addresses")

    for addr in addrs:
        validate_resolved_addr(addr, allow_private=allow_private)


def validate_resolved_addr(addr: str, *, allow_private: bool = False) -> None:
    """Validate that a resolved IP address is safe to connect to.

    Rejects loopback, RFC1918, link-local, multicast, broadcast,
    unspecified (0.0.0.0), and the IPv6 equivalents (incl. IPv4-mapped
    IPv6 forms like ::ffff:10.0.0.1).

    Pass ``allow_private=True`` to skip the ``is_private`` check, which
    permits RFC1918 and IPv6 unique-local (``fc00::/7``) addresses for
    self-hosted LAN services. Everything in `_BLOCKED_NETWORKS` — CGNAT
    (RFC 6598 ``100.64.0.0/10``) and deprecated IPv6 site-local
    (``fec0::/10``) — stays blocked regardless of allow_private, because
    our own A2A bus lives in the CGNAT range.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError as e:
        raise SsrfBlockedError(f"could not parse resolved address {addr!r}") from e

    # Normalise IPv4-mapped IPv6 (e.g. ::ffff:10.0.0.1) to its IPv4
    # equivalent so the IPv4 blocklist catches it. ipaddress lets us
    # check this via .ipv4_mapped on IPv6Address.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise SsrfBlockedError(f"resolved address {addr!r} is in the blocklist")

    # allow_private=True skips this check, which is what permits RFC1918
    # and IPv6 unique-local for self-hosted LAN services. The
    # _BLOCKED_NETWORKS pass below still applies either way.
    if not allow_private and ip.is_private:
        raise SsrfBlockedError(f"resolved address {addr!r} is in the blocklist")

    # Always blocked, even when allow_private=True: CGNAT (our own A2A bus
    # lives in that range) and deprecated IPv6 site-local.
    for net in _BLOCKED_NETWORKS:
        if ip in net:
            raise SsrfBlockedError(
                f"resolved address {addr!r} is in blocked network {net}"
            )


def _try_parse_encoded_ipv4(host: str) -> str | None:
    """Attempt to interpret `host` as an integer-encoded IPv4 address.

    Handles decimal ("2130706433"), hex ("0x7f000001"), octal
    ("017700000001"). Returns the dotted-quad form if successful, else
    None.
    """
    # int(host, 0) handles 0x prefix, 0 prefix (octal), and plain
    # decimal in one call, but we need to guard against host strings
    # that happen to be parseable as ints but aren't valid IPv4 (e.g.
    # negative numbers, numbers > 0xFFFFFFFF).
    try:
        as_int = int(host, 0)
    except (ValueError, TypeError):
        return None

    if not (0 <= as_int <= 0xFFFFFFFF):
        return None

    return str(ipaddress.IPv4Address(as_int))
