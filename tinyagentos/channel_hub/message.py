from __future__ import annotations
from dataclasses import dataclass, field
import re
import time


@dataclass
class IncomingMessage:
    id: str
    from_id: str
    from_name: str
    platform: str  # telegram | discord | slack | email | web | webhook
    channel_id: str
    channel_name: str
    text: str
    attachments: list[dict] = field(default_factory=list)
    reply_to: str | None = None
    timestamp: float = field(default_factory=time.time)
    raw: dict = field(default_factory=dict)  # original platform payload


@dataclass
class OutgoingMessage:
    content: str = ""
    buttons: list[dict] = field(default_factory=list)  # [{label, action}]
    images: list[str] = field(default_factory=list)  # file paths or URLs
    cards: list[dict] = field(default_factory=list)
    reply_to: str | None = None
    passthrough: bool = False
    passthrough_platform: str = ""
    passthrough_payload: dict = field(default_factory=dict)


def parse_inline_hints(text: str) -> OutgoingMessage:
    """Parse inline hints like [button:Label:action] from plain text responses."""
    buttons = []
    images = []
    clean_text = text

    # Parse [button:Label:action]
    for match in re.finditer(r'\[button:([^:]+):([^\]]+)\]', text):
        buttons.append({"label": match.group(1), "action": match.group(2)})
        clean_text = clean_text.replace(match.group(0), "")

    # Parse [image:path]
    for match in re.finditer(r'\[image:([^\]]+)\]', text):
        images.append(match.group(1))
        clean_text = clean_text.replace(match.group(0), "")

    return OutgoingMessage(
        content=clean_text.strip(),
        buttons=buttons,
        images=images,
    )


def _degrade(response: OutgoingMessage) -> list[str]:
    """Degrade rich elements and chunk long replies for text-only link.

    - Reads response.buttons, response.images, response.cards; drops them
      and emits a one-time notice per element kind.
    - Chunks on encoded bytes, not characters, never splitting a multibyte
      character and always accounting for the '[part N/M] ' prefix bytes.
    - Derives total from byte-accurate chunking, not len(text).
    """
    notices: list[str] = []

    # Drop buttons — emit one-time notice per conversation
    if response.buttons:
        if "[button dropped: Meshtastic is text-only]" not in notices:
            notices.append("[button dropped: Meshtastic is text-only]")
        response.buttons = []

    # Drop images — emit one-time notice per conversation
    if response.images:
        if "[image dropped: Meshtastic is text-only]" not in notices:
            notices.append("[image dropped: Meshtastic is text-only]")
        response.images = []

    # Drop cards — emit one-time notice per conversation
    if response.cards:
        if "[card dropped: Meshtastic is text-only]" not in notices:
            notices.append("[card dropped: Meshtastic is text-only]")
        response.cards = []

    # The text is already clean (parse_inline_hints stripped markup),
    # but we work with whatever content remains.
    text = response.content

    # Chunk on encoded bytes, not characters.
    encoded = text.encode("utf-8")
    chunk_size = 237  # bytes budget per emitted part (including prefix)
    total = (len(encoded) + chunk_size - 1) // chunk_size if encoded else 0

    parts: list[str] = []
    idx = 1
    start = 0
    while start < len(encoded):
        prefix = f"[part {idx}/{total}] ".encode("utf-8")
        content_bytes = chunk_size - len(prefix)
        end = start + content_bytes
        if end > len(encoded):
            end = len(encoded)
        byte_chunk = encoded[start:end]
        chunk_text = byte_chunk.decode("utf-8", errors="replace")
        parts.append(f"[part {idx}/{total}] {chunk_text}")
        start = end
        idx += 1

    return parts
