"""SSRF guard for userspace app installs.

POST /api/userspace-apps/install can fetch a .taosapp from a caller-supplied
source_url. Without validation that lets an authenticated user make the
controller fetch internal addresses (cloud metadata at 169.254.169.254,
localhost services, private LAN ranges) -- a classic SSRF. This module rejects
non-public hosts before any request is made; the caller should also use
follow_redirects=False so a 3xx cannot bounce to a blocked host.

Validating is not enough on its own: a client that re-resolves the hostname
when it connects reopens a DNS-rebinding / TOCTOU window where the connected
IP differs from the validated one. So every successful validation is cached
here (hostname -> validated IPs, short TTL), and `userspace_fetch_client`
hands out an httpx client whose transport pins each connection to the
cached validated IP -- the intermediate hardening jaylfc endorsed on #971,
pending the full pinned fetch in the P2 download broker.
"""
from __future__ import annotations

import socket
import time
from ipaddress import ip_address
from urllib.parse import urlparse

import httpx

_ALLOWED_SCHEMES = {"http", "https"}

# How long a validation stays authoritative for connection pinning. The
# resolver does not expose the record's real DNS TTL through getaddrinfo,
# so use the conservative 30s default suggested on #971: long enough that
# a validate-then-fetch pair always lands inside it, short enough that a
# nameserver whose answer turns hostile is re-checked almost immediately.
_VALIDATED_IP_TTL_SECS = 30.0

# hostname -> (chosen IP, all validated IPs, monotonic expiry). Populated
# only by successful resolve_safe_public_ip calls; a failed validation
# caches nothing. Plain dict: entries are small, and a lost update under
# the GIL costs at most one re-validation.
_validated_ip_cache: dict[str, tuple[str, frozenset[str], float]] = {}

# Upper bound on cached hostnames. An install validates a handful of
# source hosts, so this is generous; it exists so a caller validating
# many distinct hostnames cannot grow the dict without limit. Inserts
# also sweep expired entries (see _cache_validated_ips), so a hostname
# that is never looked up again does not linger past its TTL.
_VALIDATED_IP_CACHE_MAX_ENTRIES = 512


def _cache_validated_ips(hostname: str, chosen: str, validated: list[str]) -> None:
    """Record a successful validation in the TTL cache.

    Expired entries are swept first: expiry is otherwise only enforced
    when the same hostname is looked up again, and a hostname that is
    validated once and never fetched by name (the install route fetches
    an IP literal) would otherwise stay in memory past its TTL. The
    cache is then bounded to _VALIDATED_IP_CACHE_MAX_ENTRIES, evicting
    the oldest entries first (insertion order == validation order, and
    every entry shares the same TTL, so oldest == soonest to expire).
    """
    now = time.monotonic()
    expired = [key for key, entry in _validated_ip_cache.items() if now >= entry[2]]
    for key in expired:
        del _validated_ip_cache[key]
    key = hostname.lower()
    # Re-insert so a re-validated hostname counts as newest for eviction.
    _validated_ip_cache.pop(key, None)
    _validated_ip_cache[key] = (
        chosen,
        frozenset(validated),
        now + _VALIDATED_IP_TTL_SECS,
    )
    while len(_validated_ip_cache) > _VALIDATED_IP_CACHE_MAX_ENTRIES:
        del _validated_ip_cache[next(iter(_validated_ip_cache))]


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

    A successful validation is also recorded in the TTL cache
    (`cached_validated_ips`), which is what `userspace_fetch_client` pins
    connections to.
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
    validated: list[str] = []
    for info in infos:
        try:
            ip = ip_address(info[4][0])
        except ValueError:
            return None
        if _is_blocked_ip(ip):
            return None
        validated.append(str(ip))
    chosen = validated[0]
    _cache_validated_ips(parsed.hostname, chosen, validated)
    return chosen


def is_safe_public_url(url: str) -> bool:
    """True only if url is http(s) and every resolved IP is a public address.

    Thin wrapper over resolve_safe_public_ip; prefer that function at the call
    site so the connection can be pinned to the validated IP.
    """
    return resolve_safe_public_ip(url) is not None


def cached_validated_ips(hostname: str) -> tuple[str, frozenset[str]] | None:
    """(chosen IP, all validated IPs) for hostname from the TTL cache.

    Returns None when the hostname was never validated through
    resolve_safe_public_ip, or when its entry has outlived
    _VALIDATED_IP_TTL_SECS (expired entries are dropped). This is a
    point-in-time record for connection pinning, not a fresh validation:
    on None, validate again with resolve_safe_public_ip.
    """
    key = hostname.lower()
    entry = _validated_ip_cache.get(key)
    if entry is None:
        return None
    chosen, ips, expiry = entry
    if time.monotonic() >= expiry:
        del _validated_ip_cache[key]
        return None
    return chosen, ips


def clear_validated_ip_cache() -> None:
    """Drop every cached validation (tests, network reconfiguration)."""
    _validated_ip_cache.clear()


class _ValidatedPinTransport(httpx.AsyncBaseTransport):
    """Transport wrapper that pins every connection to a validated IP.

    Wraps another transport (the stock httpx one by default). For a
    hostname target, the request handed down has its host replaced by the
    IP this module validated for that name -- taken from the TTL cache
    populated by resolve_safe_public_ip, or from a fresh validation on a
    cache miss/expiry -- with the original Host header and TLS SNI
    preserved, so vhost routing and certificate validation still work
    while the HTTP stack never resolves the name itself. If the hostname
    does not validate (including because its answer turned internal after
    the cached entry expired), the fetch is refused before any connection
    is attempted. IP-literal targets pass through only when the literal
    itself is a public address.

    This is the "lightweight middleware in the httpx client factory" from
    #971: the check lives here, not in install_app, so route code keeps
    fetching by hostname and cannot re-resolve past the guard.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner if inner is not None else httpx.AsyncHTTPTransport()

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        url = request.url
        host = url.host
        try:
            literal = ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if _is_blocked_ip(literal):
                raise httpx.ConnectError(
                    f"refusing to connect to non-public address {host}",
                    request=request,
                )
            return await self._inner.handle_async_request(request)

        entry = cached_validated_ips(host)
        if entry is not None:
            chosen_ip = entry[0]
        else:
            # Cache miss or expired entry: validate (and re-cache) now.
            chosen_ip = resolve_safe_public_ip(str(url))
            if chosen_ip is None:
                raise httpx.ConnectError(
                    f"refusing to fetch {host!r}: not a validated public host",
                    request=request,
                )
        request.url = url.copy_with(host=chosen_ip)
        if "host" not in request.headers:
            request.headers["Host"] = (
                host if url.port is None else f"{host}:{url.port}"
            )
        if url.scheme == "https":
            request.extensions.setdefault("sni_hostname", host)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def userspace_fetch_client(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    **kwargs,
) -> httpx.AsyncClient:
    """An httpx.AsyncClient for fetching caller-supplied userspace URLs.

    Use this -- not a bare httpx.AsyncClient -- for userspace fetches of
    URLs a caller supplied (e.g. an install source_url). Every connection
    is pinned to an IP this module validated (see _ValidatedPinTransport),
    closing the DNS-rebinding window between validation time and connect
    time. `transport` optionally supplies the inner transport the pinning
    wrapper delegates to (tests, specialised stacks). Remaining kwargs go
    to httpx.AsyncClient; follow_redirects defaults to False so a 3xx
    cannot bounce to an unvalidated host. Note that supplying a transport
    means httpx no longer picks up HTTP_PROXY/HTTPS_PROXY from the
    environment -- a proxy would do its own resolving and defeat the pin
    anyway.
    """
    kwargs.setdefault("follow_redirects", False)
    return httpx.AsyncClient(
        transport=_ValidatedPinTransport(transport), **kwargs
    )
