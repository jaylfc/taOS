"""mDNS / Bonjour publisher for the taOS controller.

Advertises the running HTTP server on the local network so users on the
same LAN can browse to ``http://taos.local:<port>/`` (or discover taOS
via any Bonjour service browser) without having to know the host's IP.

Uses ``zeroconf.asyncio.AsyncZeroconf`` so registration runs on the event
loop and does not block startup. The ``server`` field on
:class:`zeroconf.ServiceInfo` is what makes ``taos.local`` resolve to the
detected primary LAN IPv4.

This is a *nice-to-have* — if multicast is disabled, a port is already
bound by another responder, or anything else goes wrong, we log and move
on. The controller's HTTP server is still reachable via its IP address.

Coexists fine with a system avahi-daemon: multiple responders on the
same multicast group is normal mDNS behaviour, not a conflict.

If two taOS instances run on the same LAN both claiming ``taos.local``,
zeroconf resolves the collision by suffixing the second one
(``taos-2.local`` etc.). No special-case code needed here — the library
handles it via ``allow_name_change``.

"""
from tinyagentos.atomic_io import atomic_write_text

import logging
import os
import re
import secrets
import socket
from pathlib import Path

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf
from zeroconf import AddressResolver

logger = logging.getLogger(__name__)

_SERVICE_TYPE = "_http._tcp.local."

_HOSTNAME_PATTERN = re.compile(r"^taos(-[a-z0-9]{4})?\.local\.$")


def _detect_primary_ipv4() -> list[str]:
    """Return all non-loopback, non-link-local IPv4 addresses.

    Tries the kernel's default-route probe first (a UDP connect to 8.8.8.8
    with no packets sent) and includes the result first when it matches an
    ifaddr address.  Falls back to ``ifaddr.get_adapters()`` which
    enumerates every interface without requiring network I/O, so the
    service still publishes on hosts with no default route.
    """
    primary: str | None = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            primary = sock.getsockname()[0]
    except OSError as exc:
        # No default route (offline LAN, gateway-less switch): the ifaddr
        # enumeration below still finds the interfaces.
        logger.debug("mDNS: default-route probe failed, using ifaddr: %s", exc)

    try:
        import ifaddr
    except ImportError:
        return [primary] if primary else []

    addrs: list[str] = []
    for adapter in ifaddr.get_adapters():
        for ip_entry in adapter.ips:
            ip = ip_entry.ip
            if not isinstance(ip, str):
                continue
            parts = ip.split(".")
            if len(parts) != 4:
                continue
            if parts[0] == "127":
                continue
            if parts[0] == "169" and parts[1] == "254":
                continue
            addrs.append(ip)

    if primary and primary in addrs:
        addrs.remove(primary)
        addrs.insert(0, primary)
    elif primary:
        addrs.insert(0, primary)
    return addrs


async def choose_hostname(zc: AsyncZeroconf, data_dir: Path, own_ips: list[str]) -> str:
    """Choose a stable mDNS hostname, probing for collisions.

    1. If <data_dir>/mdns_hostname exists and holds a valid name
       (regex ^taos(-[a-z0-9]{4})?\\.local\\.$), use it unchanged.
    2. Else probe for "taos.local.": if another host owns it, pick
       "taos-<4 random [a-z0-9]>.local." and probe that too, retry up
       to 5 times on collision.
    3. Write the chosen name to <data_dir>/mdns_hostname (atomic write).
    4. Probe failures (exceptions, multicast off) do NOT block startup:
       log and fall back to the persisted name or "taos.local.".
    """
    hostname_file = data_dir / "mdns_hostname"
    own_ips_set = set(own_ips)

    # 1. Check for persisted valid hostname
    if hostname_file.exists():
        try:
            persisted = hostname_file.read_text(encoding="utf-8").strip()
            if _HOSTNAME_PATTERN.match(persisted):
                logger.info("mDNS: using persisted hostname %s", persisted)
                return persisted
            else:
                logger.warning("mDNS: persisted hostname %r invalid, will re-probe", persisted)
        except OSError as exc:
            logger.warning("mDNS: failed to read persisted hostname: %s", exc)

    # 2. Probe for taos.local. first
    candidate = "taos.local."
    for attempt in range(5):
        try:
            resolver = AddressResolver(candidate)
            found = await resolver.async_request(zc.zeroconf, 1500)
            if found:
                resolved_ips = set(resolver.parsed_addresses())
                foreign_ips = resolved_ips - own_ips_set
                if foreign_ips:
                    logger.info(
                        "mDNS: %s owned by foreign IP(s) %s, trying suffix",
                        candidate,
                        ", ".join(sorted(foreign_ips)),
                    )
                    # Generate a new candidate with random suffix
                    suffix = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(4))
                    candidate = f"taos-{suffix}.local."
                    continue
                else:
                    # Only our own IPs - this hostname is ours
                    logger.info("mDNS: %s resolves to our own IP(s), claiming it", candidate)
                    break
            else:
                # No one answered - hostname is available
                logger.info("mDNS: %s available, claiming it", candidate)
                break
        except Exception as exc:
            # Probe failed (multicast disabled, etc.) - log and fall back
            logger.warning("mDNS: probe for %s failed: %s, falling back", candidate, exc)
            candidate = "taos.local."
            break

    # 3. Write chosen name atomically
    try:
        atomic_write_text(hostname_file, candidate + "\n")
        logger.info("mDNS: wrote hostname %s to %s", candidate, hostname_file)
    except OSError as exc:
        logger.warning("mDNS: failed to write hostname file: %s", exc)

    return candidate


class MdnsPublisher:
    """Publishes the controller as ``_http._tcp`` on the local network."""

    def __init__(
        self,
        *,
        data_dir: Path,
        service_name: str = "taOS",
        port: int,
    ) -> None:
        self._data_dir = data_dir
        self._service_name = service_name
        self._port = port
        self._zc: AsyncZeroconf | None = None
        self._info: ServiceInfo | None = None
        self._active = False
        self._chosen_hostname: str = "taos.local."
        self._service_instance_name: str | None = None

    @property
    def hostname(self) -> str:
        """The chosen hostname without trailing dot (e.g. 'taos.local')."""
        return self._chosen_hostname.rstrip(".")

    @property
    def service_instance_name(self) -> str | None:
        """The service instance name as finally registered by zeroconf."""
        return self._service_instance_name

    async def start(self) -> None:
        """Register the service. Never raises into the caller."""
        try:
            addresses = _detect_primary_ipv4()
            if not addresses:
                logger.warning(
                    "mDNS: skipping publish — no LAN IPv4 detected"
                )
                return

            # Choose hostname before building ServiceInfo
            zc = AsyncZeroconf()
            try:
                self._chosen_hostname = await choose_hostname(zc, self._data_dir, addresses)
            except Exception:
                # choose_hostname should never raise, but belt-and-braces
                logger.exception("mDNS: choose_hostname failed, using taos.local.")
                self._chosen_hostname = "taos.local."
            finally:
                await zc.async_close()

            # Build ServiceInfo with chosen hostname
            info = ServiceInfo(
                _SERVICE_TYPE,
                f"{self._service_name}.{_SERVICE_TYPE}",
                addresses=[socket.inet_aton(ip) for ip in addresses],
                port=self._port,
                properties={"path": "/"},
                server=self._chosen_hostname,
            )
            zc = AsyncZeroconf()
            try:
                await zc.async_register_service(info, allow_name_change=True)
            except Exception:
                # async_register_service can raise after the AsyncZeroconf
                # is constructed (port conflict, multicast disabled mid-init,
                # etc.). Close the half-built instance so its sockets and
                # multicast subscriptions don't leak; re-raise into the
                # outer except for logging.
                try:
                    await zc.async_close()
                except Exception:
                    logger.exception(
                        "mDNS: cleanup after failed register also failed"
                    )
                raise
            self._zc = zc
            self._info = info
            self._service_instance_name = info.name
            self._active = True
            logger.info(
                "mDNS: published %s at http://%s:%d/ (ips=%s)",
                self._service_name,
                self._chosen_hostname.rstrip("."),
                self._port,
                ", ".join(addresses),
            )
        except Exception:
            logger.exception("mDNS: publish failed — continuing without")
            self._active = False

    async def stop(self) -> None:
        """Unregister and close. No-op if start() failed."""
        if not self._active:
            return
        try:
            if self._zc is not None and self._info is not None:
                await self._zc.async_unregister_service(self._info)
                await self._zc.async_close()
        except Exception:
            logger.exception("mDNS: stop failed")
        finally:
            self._active = False
            self._zc = None
            self._info = None
            self._service_instance_name = None