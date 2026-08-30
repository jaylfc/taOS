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


MAX_PAYLOAD = 237  # bytes budget per emitted part (including the [part N/M] prefix)


def _degrade(response: OutgoingMessage) -> tuple[list[str], list[str]]:
    """Degrade rich elements and chunk long replies for the text-only link.

    - Reads response.buttons, response.images, response.cards; drops them
      and returns a one-time notice per element kind. The notices are meant to
      be surfaced to the transport, never silently discarded.
    - Chunks on encoded bytes, not characters, never splitting a multibyte
      character and always accounting for the '[part N/M] ' prefix bytes.
    - Derives total from the same chunking the parts use, so the label
      denominator always equals the number of emitted parts even as the
      prefix width grows (e.g. [part 9/9] -> [part 10/10]).
    """
    notices: list[str] = []

    # Drop buttons -- emit one-time notice per conversation
    if response.buttons:
        notices.append("[button dropped: Meshtastic is text-only]")
        response.buttons = []

    # Drop images -- emit one-time notice per conversation
    if response.images:
        notices.append("[image dropped: Meshtastic is text-only]")
        response.images = []

    # Drop cards -- emit one-time notice per conversation
    if response.cards:
        notices.append("[card dropped: Meshtastic is text-only]")
        response.cards = []

    # The text is already clean (parse_inline_hints stripped markup),
    # but we work with whatever content remains.
    encoded = response.content.encode("utf-8")
    if not encoded:
        return [], notices

    # Provisional total ignores the prefix length; refine until the label
    # denominator equals the actual number of emitted parts. The prefix grows
    # with both idx and total, so the per-part content budget is smaller than
    # chunk_size and a naive len/237 overcounts capacity and mislabels the last
    # part (e.g. [part 3/2]).
    total = max(1, (len(encoded) + MAX_PAYLOAD - 1) // MAX_PAYLOAD)
    while True:
        parts: list[str] = []
        idx = 1
        start = 0
        while start < len(encoded):
            prefix = f"[part {idx}/{total}] ".encode("utf-8")
            content_bytes = MAX_PAYLOAD - len(prefix)
            end = min(start + content_bytes, len(encoded))
            while end < len(encoded) and (encoded[end] & 0xC0) == 0x80:
                end -= 1
            chunk_text = encoded[start:end].decode("utf-8")
            parts.append(f"[part {idx}/{total}] {chunk_text}")
            start = end
            idx += 1
        if len(parts) == total:
            return parts, notices
        total = len(parts)
