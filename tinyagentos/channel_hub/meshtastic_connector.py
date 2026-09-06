from __future__ import annotations

import asyncio
import logging

from tinyagentos.channel_hub.message import (
    MAX_PAYLOAD,
    IncomingMessage,
    OutgoingMessage,
    _degrade,
)

logger = logging.getLogger(__name__)

# The Meshtastic / LoRa link is a text-only ~237-byte radio frame, so rich
# OutgoingMessages are degraded (buttons/images/cards dropped with one-time
# notices, long replies chunked) before reaching the transport.


class MeshtasticConnector:
    """LoRa / Meshtastic text-only channel connector.

    The transport is an injectable async callable ``send_text(frame)`` so the
    send path is testable without radio hardware; when none is supplied frames
    are logged at warning level rather than dropped silently.
    """

    def __init__(self, agent_name: str, router, *, transport=None):
        self.agent_name = agent_name
        self.router = router
        self._transport = transport
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """No blocking radio I/O on start; the link is driven per-frame.

        A real deployment opens the Meshtastic serial or BLE link here, but that
        is deferred to the hardware phase (see tsk-ha5iau). start() must not
        block so the channel-hub connect path can stand up the connector in
        tests without hardware.
        """
        self._running = True
        logger.info("Meshtastic connector started for agent '%s'", self.agent_name)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _transmit(self, text: str) -> None:
        """Push a single frame (<= MAX_PAYLOAD bytes) through the transport."""
        if self._transport is None:
            logger.warning(
                "Meshtastic transport not configured; dropping frame: %r",
                text[:40],
            )
            return
        await self._transport(text)

    async def _send_response(self, response: OutgoingMessage) -> None:
        """Degrade a rich reply for the text-only link and transmit it.

        This is the LoRa send path: it is the only production caller of
        _degrade. Notices (dropped buttons/images/cards) are surfaced to the
        transport alongside the chunked parts, and every emitted frame carries
        a [part N/M] label whose denominator equals the number of parts.
        """
        parts, notices = _degrade(response)
        for notice in notices:
            await self._transmit(notice)
        for part in parts:
            if len(part.encode("utf-8")) > MAX_PAYLOAD:
                raise ValueError(
                    f"Meshtastic part exceeds {MAX_PAYLOAD} bytes after degradation: "
                    f"{len(part.encode('utf-8'))} bytes"
                )
            await self._transmit(part)

    async def handle_incoming(self, packet: dict) -> OutgoingMessage | None:
        """Route an inbound LoRa packet and send the (degraded) reply back."""
        incoming = IncomingMessage(
            id=packet.get("id", ""),
            from_id=packet.get("from_id", packet.get("from", "node")),
            from_name=packet.get("from_name", "Node"),
            platform="meshtastic",
            channel_id=str(packet.get("channel_id", "mesh")),
            channel_name=packet.get("channel_name", "Mesh"),
            text=packet.get("text", ""),
            raw=packet,
        )
        response = await self.router.route_message(self.agent_name, incoming)
        if response is not None:
            await self._send_response(response)
        return response
