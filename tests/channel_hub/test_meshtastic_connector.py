"""Tests for the Meshtastic (LoRa) connector -- the text-only send path that
degrades rich OutgoingMessages via _degrade and transmits the parts.

These tests use an injectable transport (an async callable) so no radio
hardware is required. They pin the three #2623 regressions: _degrade is called
from a real send path, the [part N/M] denominator equals the emitted part
count, and the dropped-element notices reach the transport.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tinyagentos.channel_hub.meshtastic_connector import MeshtasticConnector
from tinyagentos.channel_hub.message import MAX_PAYLOAD, OutgoingMessage, _degrade, parse_inline_hints


def _make_connector(sink: list[str] | None = None):
    async def transport(frame: str) -> None:
        if sink is not None:
            sink.append(frame)

    router = AsyncMock()
    connector = MeshtasticConnector(
        agent_name="lora-agent", router=router, transport=transport
    )
    return connector, router


class TestMeshtasticSendPath:
    @pytest.mark.asyncio
    async def test_degrade_is_called_from_the_send_path(self):
        # The LoRa send path must not be inert: _degrade runs on every send.
        sent: list[str] = []
        connector, _ = _make_connector(sent)
        response = OutgoingMessage(
            content="x" * 474,
            buttons=[{"label": "Yes", "action": "yes"}],
            images=["img.png"],
            cards=[{"title": "c"}],
        )
        await connector._send_response(response)

        # Notices dropped by _degrade must reach the transport.
        joined = "\n".join(sent)
        assert "[button dropped: Meshtastic is text-only]" in joined
        assert "[image dropped: Meshtastic is text-only]" in joined
        assert "[card dropped: Meshtastic is text-only]" in joined

        # Every part label denominator equals the emitted part count.
        parts = [f for f in sent if f.startswith("[part ")]
        assert len(parts) >= 1
        for part in parts:
            denom = part.split("/")[1].split("]")[0]
            assert denom == str(len(parts)), f"{denom} != {len(parts)}"
            assert len(part.encode("utf-8")) <= MAX_PAYLOAD

    @pytest.mark.asyncio
    async def test_notice_text_reaches_transport(self):
        # Acceptance: the dropped-element notice text reaches the transport.
        sent: list[str] = []
        connector, _ = _make_connector(sent)
        await connector._send_response(
            OutgoingMessage(content="hi", buttons=[{"label": "OK", "action": "ok"}])
        )
        assert "[button dropped: Meshtastic is text-only]" in sent
        assert any(f.startswith("[part ") for f in sent)

    @pytest.mark.asyncio
    async def test_denominator_matches_emitted_parts(self):
        # Acceptance: label denominator equals the emitted part count, even when
        # the prefix growth shifts the true part count above ceil(len/237).
        sent: list[str] = []
        connector, _ = _make_connector(sent)
        await connector._send_response(OutgoingMessage(content="A" * 1000))
        parts = [f for f in sent if f.startswith("[part ")]
        assert len(parts) > 1
        for part in parts:
            assert len(part.encode("utf-8")) <= MAX_PAYLOAD
            denom = part.split("/")[1].split("]")[0]
            assert denom == str(len(parts)), f"{denom} != {len(parts)}"

    @pytest.mark.asyncio
    async def test_no_transport_does_not_raise(self):
        # A connector with no configured radio must not crash on send.
        connector = MeshtasticConnector(agent_name="lora-agent", router=AsyncMock())
        await connector._send_response(
            OutgoingMessage(content="hi", buttons=[{"label": "OK", "action": "ok"}])
        )

    @pytest.mark.asyncio
    async def test_short_reply_single_part(self):
        sent: list[str] = []
        connector, _ = _make_connector(sent)
        await connector._send_response(OutgoingMessage(content="hello"))
        parts = [f for f in sent if f.startswith("[part ")]
        assert len(parts) == 1
        assert parts[0] == "[part 1/1] hello"

    @pytest.mark.asyncio
    async def test_handle_incoming_routes_and_sends(self):
        sent: list[str] = []
        connector, router = _make_connector(sent)
        router.route_message = AsyncMock(
            return_value=OutgoingMessage(content="A" * 474)
        )
        result = await connector.handle_incoming(
            {"id": "pkt1", "from_id": "node-1", "text": "ping"}
        )
        router.route_message.assert_called_once()
        incoming = router.route_message.call_args[0][1]
        assert incoming.platform == "meshtastic"
        assert incoming.from_id == "node-1"
        assert incoming.text == "ping"
        assert result is not None
        # The reply was degraded and transmitted through the transport.
        parts = [f for f in sent if f.startswith("[part ")]
        assert len(parts) == 3
        for part in parts:
            denom = part.split("/")[1].split("]")[0]
            assert denom == str(len(parts))

    @pytest.mark.asyncio
    async def test_send_response_never_transmits_over_budget(self):
        # Multibyte content that, under the byte-slice bug, produced a 239-byte
        # frame. The guard must never transmit an over-budget frame.
        for text in ("日" * 76, "🌍" * 57):
            sent: list[str] = []
            connector, _ = _make_connector(sent)
            await connector._send_response(OutgoingMessage(content=text))
            assert sent, f"no frames transmitted for {text[:6]!r}..."
            for frame in sent:
                byte_len = len(frame.encode("utf-8"))
                assert byte_len <= MAX_PAYLOAD, (
                    f"frame over {MAX_PAYLOAD} bytes: {frame!r} ({byte_len})"
                )
            parts = [f for f in sent if f.startswith("[part ")]
            reencoded = "".join(p.split("] ", 1)[1] for p in parts).encode("utf-8")
            assert reencoded == text.encode("utf-8"), (
                f"reassembly not byte-identical for {text[:6]!r}..."
            )

    @pytest.mark.asyncio
    async def test_guard_raises_when_part_exceeds_budget(self):
        # If _degrade ever emits an over-budget part, the send path must
        # raise rather than narrate-truncate-and-ship an oversized frame.
        sent: list[str] = []
        connector, _ = _make_connector(sent)
        with patch(
            "tinyagentos.channel_hub.meshtastic_connector._degrade",
            return_value=(["x" * 300], []),
        ):
            with pytest.raises(ValueError, match="exceeds"):
                await connector._send_response(OutgoingMessage(content="stub"))
            # _transmit must never be called with the oversize frame.
            assert sent == []


class TestDegradeWithParsedHints:
    """Regression tests: _degrade must operate on the structured fields that
    parse_inline_hints populates, not on markup left in .content.

    These tests use a real parse_inline_hints-built OutgoingMessage so the
    fixture reflects the actual connector input and would catch both the
    string-replace bug (buttons/images survive, no notice emitted) and the
    character-slice bug (over-budget chunk on non-ASCII input).
    """

    def test_degrade_drops_rich_elements_from_parsed_message(self):
        response = parse_inline_hints(
            "[button:Yes:yes] Reboot the node? [image:/tmp/node.png]"
        )
        parts, notices = _degrade(response)

        # Structured fields must be cleared.
        assert response.buttons == [], f"buttons not dropped: {response.buttons}"
        assert response.images == [], f"images not dropped: {response.images}"

        # One-time notices must be emitted, not discarded.
        assert "[button dropped: Meshtastic is text-only]" in notices
        assert "[image dropped: Meshtastic is text-only]" in notices

        # No markup may survive into emitted parts.
        assert len(parts) >= 1
        for part in parts:
            assert "[button:" not in part
            assert "[image:" not in part
            assert len(part.encode("utf-8")) <= MAX_PAYLOAD

    def test_degrade_chunks_non_ascii_within_byte_budget(self):
        # 79 '日' characters = 237 raw bytes. Under character-based slicing
        # the prefix "[part 1/1] " (12 bytes) pushes a single part to 249
        # bytes, breaching the 237-byte wire budget. The correct byte-aware
        # chunker splits this into two parts, each <= 237 bytes.
        text = "日" * 79
        response = parse_inline_hints(text + " [button:OK:ok]")
        parts, notices = _degrade(response)

        for part in parts:
            byte_len = len(part.encode("utf-8"))
            assert byte_len <= MAX_PAYLOAD, (
                f"part over {MAX_PAYLOAD} bytes: {part!r} ({byte_len})"
            )

        # Reassembly must be byte-identical to the original text.
        reencoded = "".join(p.split("] ", 1)[1] for p in parts).encode("utf-8")
        assert reencoded == text.encode("utf-8")

        # Button must be dropped and noticed even when the content is long.
        assert "[button dropped: Meshtastic is text-only]" in notices
        assert response.buttons == []

    def test_degrade_parsed_hints_emits_no_notices_when_no_rich_elements(self):
        response = parse_inline_hints("Just plain text")
        parts, notices = _degrade(response)
        assert notices == []
        assert len(parts) == 1
        assert parts[0] == "[part 1/1] Just plain text"
