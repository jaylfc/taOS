"""SSRF guard for userspace app installs.

`POST /api/userspace-apps/install` can fetch a `.taosapp` from a caller-supplied
`source_url`. Without validation that lets an authenticated user make the
controller fetch internal addresses (cloud metadata at 169.254.169.254,
localhost services, private LAN ranges) — a classic SSRF. This module rejects
non-public hosts before any request is made; the caller should also use
``follow_redirects=False`` so a 3xx can't bounce to a blocked host.
"""
from __future__ import annotations

import socket
from ipaddress import ip_address
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def is_safe_public_url(url: str) -> bool:
    """True only if *url* is http(s) and every resolved IP is a public address.

    Rejects private, loopback, link-local, reserved, unspecified and multicast
    addresses (covers 127/8, ::1, 10/8, 172.16/12, 192.168/16, 169.254/16,
    0.0.0.0, etc.).
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    for info in infos:
        sockaddr = info[4]
        try:
            ip = ip_address(sockaddr[0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_unspecified or ip.is_multicast):
            return False
    return True
