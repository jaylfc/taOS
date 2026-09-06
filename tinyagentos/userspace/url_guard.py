"""SSRF guard for userspace app installs.

POST /api/userspace-apps/install can fetch a .taosapp from a caller-supplied
source_url. Without validation that lets an authenticated user make the
controller fetch internal addresses (cloud metadata at 169.254.169.254,
localhost services, private LAN ranges) -- a classic SSRF. This module rejects
non-public hosts before any request is made; the caller should also use
follow_redirects=False so a 3xx cannot bounce to a blocked host.
"""
from __future__ import annotations

import socket
from ipaddress import ip_address
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def _is_blocked_ip(ip) -> bool:
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_unspecified or ip.is_multicast
    )


def resolve_safe_public_ip(url: str) -> str | None:
    """Resolve url's host ONCE and return a validated public IP to connect to,
    or None if the url is not http(s) or any resolved address is non-public.

    Rejects private, loopback, link-local, reserved, unspecified and multicast
    addresses (covers 127/8, ::1, 10/8, 172.16/12, 192.168/16, 169.254/16,
    0.0.0.0, etc.). The caller MUST connect to this pinned IP (keeping the
    original Host header and TLS SNI) rather than letting the HTTP client
    re-resolve the hostname -- re-resolution reopens a DNS-rebinding TOCTOU
    window where the resolved-and-validated address differs from the one
    actually connected to.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        return None
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or None)
    except (socket.gaierror, UnicodeError, OSError):
        return None
    if not infos:
        return None
    chosen: str | None = None
    for info in infos:
        try:
            ip = ip_address(info[4][0])
        except ValueError:
            return None
        if _is_blocked_ip(ip):
            return None
        if chosen is None:
            chosen = str(ip)
    return chosen


def is_safe_public_url(url: str) -> bool:
    """True only if url is http(s) and every resolved IP is a public address.

    Thin wrapper over resolve_safe_public_ip; prefer that function at the call
    site so the connection can be pinned to the validated IP.
    """
    return resolve_safe_public_ip(url) is not None
