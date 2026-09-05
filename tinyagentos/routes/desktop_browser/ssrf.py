"""SSRF guard for the BrowserApp proxy.

The proxy server-side-fetches URLs on behalf of the user. Without
guards an attacker who controls a URL the proxy fetches can point us
at internal services (cloud metadata, the Pi's own admin interfaces,
RFC1918 hosts on the user's LAN). This module is the choke point that
parses every target URL, resolves its hostname, and refuses to proceed
if any resolved address is in the blocklist.

Validating is not enough on its own: a check that resolves the hostname
and then lets the HTTP client resolve it a second time can be defeated by
an attacker who runs the authoritative nameserver for that hostname and
answers public to the check and 127.0.0.1 to the connection. So the
address that was checked has to be the address that is connected to. That
is what `guarded_async_client` is for — it hands out an `httpx` client
whose connections resolve the hostname exactly once, validate that answer,
and open the socket to it. TLS is untouched: the request URL still carries
the hostname, so SNI and certificate verification still run against the
original name.

Usage:

    from tinyagentos.routes.desktop_browser.ssrf import (
        SsrfBlockedError,
        guarded_async_client,
        validate_url_or_raise,
    )

    try:
        validate_url_or_raise(target_url)  # fail fast, with a reason
    except SsrfBlockedError as e:
        return JSONResponse({"error": str(e)}, status_code=403)

    async with guarded_async_client(timeout=30) as http:  # enforced here
        resp = await http.get(target_url)

Any client that fetches a user-supplied URL must come from
`guarded_async_client`; a bare `httpx.AsyncClient` re-resolves the name and
reopens the hole. Clients that only talk to trusted local services (the LLM
backend, qmd) do not need it.

For redirect handling, callers must invoke validate_url_or_raise on
EVERY redirect target (not just the initial URL). The `httpx`
follow_redirects=True default does not give us a callback per redirect,
so the proxy implementation in PR 3 disables auto-follow and walks the
redirect chain manually, calling this guard each step.
"""
from __future__ import annotations

import ipaddress
import socket
import typing
from urllib.parse import urlparse

import httpcore
import httpx


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


def validate_url_or_raise(url: str, *, allow_private: bool = False) -> list[str]:
    """Validate that `url` is safe to fetch.

    Parses the URL, checks scheme + hostname suffix, resolves DNS, and
    verifies every resolved address against the blocklist. Raises
    `SsrfBlockedError` on any failure.

    Returns the resolved, checked addresses in resolver order. Permission
    on its own is not enough: whoever fetches the URL has to connect to the
    address that was checked, or an attacker-run nameserver can answer this
    lookup public and the connection's lookup 127.0.0.1. Fetch with a
    `guarded_async_client`, which resolves and validates inside the
    connection itself.

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

    return resolve_and_validate(parsed.hostname, allow_private=allow_private)


def resolve_and_validate(hostname: str, *, allow_private: bool = False) -> list[str]:
    """Resolve `hostname` once and validate every address it answers with.

    Returns the checked addresses in resolver order — the first is the one
    a connection should be opened to. Raises `SsrfBlockedError` if the
    hostname carries a blocked suffix, does not resolve, or resolves to any
    blocked address. See `validate_resolved_addr` for ``allow_private``.
    """
    host = hostname.strip().lower()

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
                # Dedupe but keep resolver order: the first answer is the one a
                # pinned connection uses, and getaddrinfo already sorts by the
                # RFC 6724 destination preference.
                addrs = list(dict.fromkeys(r[4][0] for r in results))
            except socket.gaierror as e:
                raise SsrfBlockedError(f"could not resolve hostname: {e}") from e

    if not addrs:
        raise SsrfBlockedError("hostname resolved to no addresses")

    for addr in addrs:
        validate_resolved_addr(addr, allow_private=allow_private)

    return addrs


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


class _PinnedResolutionBackend(httpcore.AsyncNetworkBackend):
    """Network backend that connects to the address it just validated.

    `httpcore` calls `connect_tcp` with the hostname from the request URL,
    which is where the second, unchecked DNS lookup used to happen. This
    backend does that lookup itself, runs the blocklist over the answer,
    and hands the socket layer the literal address instead of the name —
    so there is only ever one lookup, and it is the checked one.

    The request URL is left alone, so `httpcore` still derives SNI and the
    certificate-verification hostname from the original name.
    """

    def __init__(
        self, inner: httpcore.AsyncNetworkBackend, *, allow_private: bool = False,
    ) -> None:
        self._inner = inner
        self._allow_private = allow_private

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addrs = resolve_and_validate(host, allow_private=self._allow_private)
        return await self._inner.connect_tcp(
            addrs[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        # Nothing routes a user-supplied URL to a unix socket, and one would
        # bypass the address blocklist entirely, so refuse rather than pass through.
        raise SsrfBlockedError("unix-socket connections are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class SsrfGuardedAsyncTransport(httpx.AsyncHTTPTransport):
    """`httpx` transport whose connections are pinned to a checked address.

    Everything above the socket is stock `httpx`: request URLs, redirects,
    cookies, and TLS verification behave exactly as they do on the default
    transport. Only the pool's network backend is swapped, for one that
    resolves and validates the hostname as part of opening the connection.
    """

    def __init__(self, *, allow_private: bool = False, **kwargs: typing.Any) -> None:
        super().__init__(**kwargs)
        pool = getattr(self, "_pool", None)
        if not hasattr(pool, "_network_backend"):
            # An httpx/httpcore upgrade moved the seam. Fail loudly: handing
            # back a transport that silently does not pin is the bug itself.
            raise RuntimeError(
                "SSRF pinning could not be installed — httpx's connection pool "
                "no longer exposes _network_backend. Refusing to hand out an "
                "unpinned client."
            )
        pool._network_backend = _PinnedResolutionBackend(
            pool._network_backend, allow_private=allow_private,
        )


def guarded_async_client(
    *,
    allow_private: bool = False,
    verify: typing.Any = True,
    http2: bool = False,
    **kwargs: typing.Any,
) -> httpx.AsyncClient:
    """An `httpx.AsyncClient` that only ever connects to checked addresses.

    Use this — not a bare `httpx.AsyncClient` — for every fetch of a URL
    the user or a remote peer supplied. Remaining keyword arguments go to
    `httpx.AsyncClient` (timeout, follow_redirects, cookies, headers, ...).

    TLS verification stays on (`verify` defaults to True) and still checks
    the certificate against the original hostname; pinning happens below
    the URL, so there is no reason to weaken it. Note that supplying a
    transport means `httpx` no longer picks up HTTP_PROXY/HTTPS_PROXY from
    the environment — proxying an untrusted fetch would defeat the pin
    anyway, since the proxy would do the resolving.
    """
    return httpx.AsyncClient(
        transport=SsrfGuardedAsyncTransport(
            allow_private=allow_private, verify=verify, http2=http2,
        ),
        **kwargs,
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
