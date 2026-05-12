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
from __future__ import annotations

import logging
import socket

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

logger = logging.getLogger(__name__)

_SERVICE_TYPE = "_http._tcp.local."


def _detect_primary_ipv4() -> str | None:
    """Pick the LAN IPv4 the kernel would use for default-route traffic.

    Opens a UDP socket "to" 8.8.8.8 and reads ``getsockname()`` — no
    packets are actually sent, so this works fine on air-gapped
    networks (the kernel still resolves the source interface).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError as exc:
        logger.warning("mDNS: could not detect primary LAN IPv4: %s", exc)
        return None
    finally:
        sock.close()


class MdnsPublisher:
    """Publishes the controller as ``_http._tcp`` on the local network."""

    def __init__(
        self,
        *,
        hostname: str = "taos.local.",
        service_name: str = "taOS",
        port: int,
    ) -> None:
        self._hostname = hostname
        self._service_name = service_name
        self._port = port
        self._zc: AsyncZeroconf | None = None
        self._info: ServiceInfo | None = None
        self._active = False

    async def start(self) -> None:
        """Register the service. Never raises into the caller."""
        try:
            ip = _detect_primary_ipv4()
            if ip is None:
                logger.warning(
                    "mDNS: skipping publish — no LAN IPv4 detected"
                )
                return
            info = ServiceInfo(
                _SERVICE_TYPE,
                f"{self._service_name}.{_SERVICE_TYPE}",
                addresses=[socket.inet_aton(ip)],
                port=self._port,
                properties={"path": "/"},
                server=self._hostname,
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
            self._active = True
            logger.info(
                "mDNS: published %s at http://%s:%d/ (ip=%s)",
                self._service_name,
                self._hostname.rstrip("."),
                self._port,
                ip,
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
