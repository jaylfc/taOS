"""Bundled mDNS reflector with loop prevention and a liveness self-check.

Screencast slice C2. taOS often runs where multicast does not cross the
network boundary on its own (an LXC or Docker bridge, a second NIC). The
reflector bridges ``224.0.0.251:5353`` between the IPv4 interfaces it can
see, so cast discovery works without the user configuring host networking.
It only helps where it can see BOTH segments (the host side of a container
bridge, or a host with two NICs); with a single interface there is nothing
to bridge and the status says so instead of pretending.

Two requirements shape the design:

* **Loop prevention.** A reflector that re-announces what it reflected
  amplifies traffic and can flood every device on the LAN. The core dedups
  by ``(source, payload digest)`` inside a short TTL and never re-emits a
  payload whose digest it forwarded recently, from any source on any
  interface (it will hear its own forwards, or another reflector's echo of
  them). Packets sourced from one of our own addresses are never reflected.

* **Liveness.** A bridge that silently stops working yields an empty device
  list that reads exactly like "no TVs on this network". :meth:`status`
  reports ``ok`` / ``unavailable`` (with a reason) / ``disabled``, and
  :func:`discovery_view` is the contract the device list (C1) uses to show
  "discovery is not working" rather than an empty list.

Why the reflector does not ride the existing ``AsyncZeroconf`` instance
(:mod:`tinyagentos.services.mdns_publisher`): zeroconf parses packets into
records and has no public API to receive the raw datagram with its ingress
interface or to send raw bytes out of one chosen interface, which is all a
reflector does. Hooking its private listener would break on upgrades. So
the reflector opens its own sockets, built to coexist rather than compete:
``SO_REUSEADDR`` + ``SO_REUSEPORT`` (the same flags zeroconf sets, so
neither bind fails whichever starts first), bound to the GROUP address
rather than the wildcard (unicast replies to ``<host>:5353`` keep going to
zeroconf, never to the reflector), ``IP_MULTICAST_ALL=0`` so each socket
only hears the interface it joined on, and multicast loopback off. It never
constructs a second Zeroconf instance. C1's ServiceBrowser should still
reuse the publisher's AsyncZeroconf, as the spec requires.

The core is pure (no sockets, injectable clock) so the loop and liveness
behaviour is unit-tested by packet counts; :class:`MdnsReflector` is the
thin asyncio glue. Off unless ``mdns_reflector.enabled`` is true in
config.yaml, so existing installs see no change.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
import time
from collections import OrderedDict
from typing import Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353

_DNS_HEADER_LEN = 12
# Linux value; not exported by the socket module on every Python build.
_IP_MULTICAST_ALL = getattr(socket, "IP_MULTICAST_ALL", 49)

DEFAULT_DEDUP_TTL = 1.0
DEFAULT_SILENCE_WINDOW = 600.0
DEFAULT_MAX_ENTRIES = 4096


def _digest(payload: bytes) -> bytes:
    return hashlib.blake2b(payload, digest_size=16).digest()


class ReflectorCore:
    """Pure reflect decision plus liveness bookkeeping.

    ``ingest`` returns the emits ``[(egress_iface, payload), ...]``; the
    caller does the sending and reports the outcome with ``note_sent`` /
    ``note_send_failure``.
    """

    def __init__(
        self,
        interfaces: Iterable[str],
        *,
        local_addresses: Iterable[str] = (),
        clock: Callable[[], float] = time.monotonic,
        dedup_ttl: float = DEFAULT_DEDUP_TTL,
        silence_window: float = DEFAULT_SILENCE_WINDOW,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.interfaces = list(dict.fromkeys(interfaces))
        self._local = set(local_addresses)
        self._clock = clock
        self._ttl = dedup_ttl
        self._silence = silence_window
        self._max = max(1, max_entries)
        # key -> expiry. Keys are ("seen", src_ip, digest) or ("fwd", digest).
        # TTL is constant, so insertion order is expiry order.
        self._cache: OrderedDict[tuple, float] = OrderedDict()
        self._running = False
        self._started_at: float | None = None
        self._last_ingest_at: float | None = None
        self._last_forward_at: float | None = None
        # iface -> (ok, message) for the latest send attempt on that iface.
        self._send_outcome: dict[str, tuple[bool, str | None]] = {}
        self._stats = {
            "packets_in": 0,
            "packets_out": 0,
            "dropped_duplicate": 0,
            "dropped_own_forward": 0,
            "dropped_local": 0,
            "dropped_runt": 0,
            "send_errors": 0,
        }

    # -- reflect ----------------------------------------------------------

    def _prune(self, now: float) -> None:
        while self._cache:
            key, expiry = next(iter(self._cache.items()))
            if expiry > now:
                break
            self._cache.popitem(last=False)

    def _remember(self, key: tuple, now: float) -> None:
        self._cache.pop(key, None)
        self._cache[key] = now + self._ttl
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)

    def ingest(self, iface: str, source: tuple | str, payload: bytes) -> list[tuple[str, bytes]]:
        now = self._clock()
        src_ip = source[0] if isinstance(source, tuple) else source
        self._stats["packets_in"] += 1
        self._last_ingest_at = now
        if len(payload) < _DNS_HEADER_LEN:
            self._stats["dropped_runt"] += 1
            return []
        if src_ip in self._local:
            self._stats["dropped_local"] += 1
            return []
        self._prune(now)
        digest = _digest(payload)
        if ("fwd", digest) in self._cache:
            self._stats["dropped_own_forward"] += 1
            return []
        seen_key = ("seen", src_ip, digest)
        if seen_key in self._cache:
            self._stats["dropped_duplicate"] += 1
            return []
        self._remember(seen_key, now)
        emits = [(out, payload) for out in self.interfaces if out != iface]
        if emits:
            self._remember(("fwd", digest), now)
            self._stats["packets_out"] += len(emits)
            self._last_forward_at = now
        return emits

    def cache_size(self) -> int:
        return len(self._cache)

    def stats(self) -> dict:
        return dict(self._stats)

    # -- liveness ---------------------------------------------------------

    def mark_running(self) -> None:
        self._running = True
        self._started_at = self._clock()

    def mark_stopped(self) -> None:
        self._running = False

    def note_sent(self, iface: str) -> None:
        self._send_outcome[iface] = (True, None)

    def note_send_failure(self, iface: str, exc: BaseException) -> None:
        self._stats["send_errors"] += 1
        self._send_outcome[iface] = (False, str(exc))
        logger.debug("mdns reflector: send on %s failed: %s", iface, exc)

    def status(self) -> dict:
        now = self._clock()
        failing = {
            name: msg for name, (ok, msg) in self._send_outcome.items() if not ok
        }
        healthy = [i for i in self.interfaces if i not in failing]

        def _age(t: float | None) -> float | None:
            return None if t is None else round(now - t, 3)

        out = {
            "state": "ok",
            "reason": None,
            "interfaces": list(self.interfaces),
            "failing_interfaces": failing,
            "seconds_since_last_packet": _age(self._last_ingest_at),
            "seconds_since_last_forward": _age(self._last_forward_at),
            **self.stats(),
        }
        if not self._running:
            reason = "not_running"
        elif len(self.interfaces) < 2:
            reason = "single_interface"
        elif len(healthy) < 2:
            reason = "send_failed"
        elif now - (self._last_ingest_at or self._started_at or now) > self._silence:
            # mDNS is chatty; total silence on every leg for this long means
            # we are not receiving, not that the LAN is empty.
            reason = "no_traffic"
        else:
            reason = None
        if reason is not None:
            out["state"] = "unavailable"
            out["reason"] = reason
        return out


def disabled_status() -> dict:
    """Status when the reflector is not enabled: it makes no claim."""
    return {"state": "disabled", "reason": "reflector_not_enabled", "interfaces": []}


def start_failed_status(error: str) -> dict:
    """Status when the reflector is enabled but failed to start: not bridging."""
    return {
        "state": "unavailable",
        "reason": "start_failed",
        "error": error,
        "interfaces": [],
    }


def discovery_view(devices: list, status: dict) -> dict:
    """Contract for the cast device list (C1).

    ``discovery_unavailable`` whenever the reflector reports it is not
    bridging, even if some devices are listed (they may be stale);
    otherwise ``devices`` or ``empty``. A disabled reflector makes no claim
    (host-networking installs discover natively), so it falls through to
    the plain list.
    """
    devices = list(devices)
    if status.get("state") == "unavailable":
        state = "discovery_unavailable"
    elif devices:
        state = "devices"
    else:
        state = "empty"
    return {"state": state, "devices": devices, "discovery": status}


# -- asyncio glue ---------------------------------------------------------


def enumerate_ipv4_interfaces(allow: Iterable[str] | None = None) -> dict[str, str]:
    """Return ``{iface_name: ipv4}`` for non-loopback, non-link-local IPv4
    interfaces (first address per interface), optionally limited to
    ``allow``."""
    try:
        import ifaddr
    except ImportError:
        return {}
    wanted = set(allow) if allow else None
    out: dict[str, str] = {}
    for adapter in ifaddr.get_adapters():
        name = adapter.name
        if wanted is not None and name not in wanted:
            continue
        for ip_entry in adapter.ips:
            ip = ip_entry.ip
            if not isinstance(ip, str) or ip.startswith("127.") or ip.startswith("169.254."):
                continue
            out.setdefault(name, ip)
    return out


def _open_mdns_socket(ip: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        try:
            sock.setsockopt(socket.IPPROTO_IP, _IP_MULTICAST_ALL, 0)
        except OSError:
            pass
        # Bound to the group, not the wildcard: unicast to <host>:5353 stays
        # with zeroconf.
        sock.bind((MDNS_GROUP, MDNS_PORT))
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_ADD_MEMBERSHIP,
            socket.inet_aton(MDNS_GROUP) + socket.inet_aton(ip),
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
        sock.setblocking(False)
    except BaseException:
        sock.close()
        raise
    return sock


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, reflector: "MdnsReflector", iface: str) -> None:
        self._r = reflector
        self._iface = iface

    def datagram_received(self, data: bytes, addr) -> None:
        self._r.on_datagram(self._iface, data, addr)

    def error_received(self, exc: Exception) -> None:
        if self._r.core is not None:
            self._r.core.note_send_failure(self._iface, exc)


Opener = Callable[[str, str], Awaitable[object]]


class MdnsReflector:
    """Owns one multicast socket per interface and feeds a ReflectorCore."""

    def __init__(
        self,
        interfaces: dict[str, str],
        *,
        opener: Opener | None = None,
        clock: Callable[[], float] = time.monotonic,
        **core_kwargs,
    ) -> None:
        self._ifaces = dict(interfaces)
        self._opener = opener or self._default_opener
        self._clock = clock
        self._core_kwargs = core_kwargs
        self._transports: dict[str, object] = {}
        self.bind_errors: dict[str, str] = {}
        self.core: ReflectorCore | None = None

    @classmethod
    def from_config(cls, cfg: dict) -> "MdnsReflector":
        kwargs = {}
        if cfg.get("silence_window_s") is not None:
            kwargs["silence_window"] = float(cfg["silence_window_s"])
        return cls(enumerate_ipv4_interfaces(cfg.get("interfaces") or None), **kwargs)

    async def _default_opener(self, name: str, ip: str):
        sock = _open_mdns_socket(ip)
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self, name), sock=sock
        )
        return transport

    async def start(self) -> None:
        """Bind every interface it can. Never raises: failures land in
        :meth:`status`."""
        for name, ip in self._ifaces.items():
            try:
                self._transports[name] = await self._opener(name, ip)
            except Exception as exc:  # noqa: BLE001 - reported via status
                self.bind_errors[name] = str(exc)
                logger.warning("mdns reflector: cannot bind on %s (%s): %s", name, ip, exc)
        self.core = ReflectorCore(
            list(self._transports),
            local_addresses=self._ifaces.values(),
            clock=self._clock,
            **self._core_kwargs,
        )
        self.core.mark_running()
        logger.info(
            "mdns reflector: bridging %s", ", ".join(self._transports) or "nothing"
        )

    async def stop(self) -> None:
        for t in self._transports.values():
            try:
                t.close()
            except Exception:
                logger.exception("mdns reflector: close failed")
        self._transports.clear()
        if self.core is not None:
            self.core.mark_stopped()

    def on_datagram(self, iface: str, data: bytes, addr) -> None:
        if self.core is None:
            return
        for egress, payload in self.core.ingest(iface, addr, data):
            transport = self._transports.get(egress)
            if transport is None:
                continue
            try:
                transport.sendto(payload, (MDNS_GROUP, MDNS_PORT))
            except OSError as exc:
                self.core.note_send_failure(egress, exc)
            else:
                self.core.note_sent(egress)

    def status(self) -> dict:
        if self.core is None:
            out = ReflectorCore([], clock=self._clock).status()
        else:
            out = self.core.status()
        out["bind_errors"] = dict(self.bind_errors)
        if out["reason"] == "single_interface" and self.bind_errors:
            out["reason"] = "bind_failed"
        return out
