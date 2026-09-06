"""Chat message exporter: transforms ChatMessageStore rows into A2A bus
import-batch envelopes.

DROPPED FIELDS: the following ``chat_messages`` columns (and the
``chat_attachments`` table) are intentionally NOT carried into the export
envelope — they live outside the agreed bus envelope by design:
content_type, embeds, components, attachments (+ chat_attachments table),
reactions, metadata, edited_at, pinned, ephemeral, expires_at, author_type.
Only ``from``, ``thread``, ``body``, ``blocks``, ``ts``, ``source``,
``source_id``, and (when applicable) ``reply_to`` are exported.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

_MAX_MESSAGE_BYTES = 64 * 1024
_DEFAULT_SOURCE = "taos-chat"


class ChatExportError(Exception):
    """Raised when a batch cannot be exported."""


def flatten_body(blocks: list[dict] | None) -> str:
    """Flatten content blocks to plain text.

    Blocks carrying a ``text`` key flatten as text (empty text contributes
    nothing, same as before). Blocks without a ``text`` key (images, files,
    and other non-text blocks) flatten to a descriptive placeholder built
    from their type plus the most useful identifying field present, so a
    non-text block never silently disappears or bricks the export.
    """
    if not blocks:
        return ""
    parts = []
    for block in blocks:
        if isinstance(block, dict):
            if "text" in block:
                text = block.get("text") or ""
                if text:
                    parts.append(str(text))
                continue
            block_type = block.get("type") or "block"
            identifier = (
                block.get("url") or block.get("name") or block.get("filename")
            )
            if identifier:
                parts.append(f"[{block_type}: {identifier}]")
            else:
                parts.append(f"[{block_type} block]")
        elif isinstance(block, str):
            if block:
                parts.append(block)
    return "\n".join(parts)


def _serialized_size(envelope: dict) -> int:
    return len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))


def _causal_tiebreak(group: list[dict]) -> list[dict]:
    """Stable topological sort of a same-timestamp group of messages: a
    message replying (via ``thread_id``) to another message in the same
    group is ordered after that parent. Messages with no such relationship
    keep their original (already created_at/id sorted) relative order."""
    ids = {m.get("id") for m in group}
    indegree = {m.get("id"): 0 for m in group}
    children: dict[Any, list[dict]] = {m.get("id"): [] for m in group}
    for m in group:
        parent_id = m.get("thread_id")
        if parent_id in ids:
            children[parent_id].append(m)
            indegree[m.get("id")] += 1

    ready = [m for m in group if indegree[m.get("id")] == 0]
    ordered: list[dict] = []
    while ready:
        m = ready.pop(0)
        ordered.append(m)
        for child in children[m.get("id")]:
            indegree[child.get("id")] -= 1
            if indegree[child.get("id")] == 0:
                ready.append(child)
    if len(ordered) < len(group):
        # thread_id cycle (e.g. a crafted self-reference - the store does not
        # validate). No topological order exists for the cycle members, but a
        # migration tool must never silently drop a row: append them in their
        # original (created_at, id) order.
        seen = {id(m) for m in ordered}
        ordered.extend(m for m in group if id(m) not in seen)
    return ordered


def _sort_with_causality(messages: list[dict]) -> list[dict]:
    """Sort by (created_at, id) then run a same-timestamp causality pass so
    a reply never precedes its parent within an equal-timestamp group."""
    messages = sorted(
        messages, key=lambda m: (m.get("created_at") or 0.0, m.get("id") or "")
    )
    result: list[dict] = []
    i, n = 0, len(messages)
    while i < n:
        j = i
        ts = messages[i].get("created_at") or 0.0
        while j < n and (messages[j].get("created_at") or 0.0) == ts:
            j += 1
        result.extend(_causal_tiebreak(messages[i:j]))
        i = j
    return result


class ChatExporter:
    """Export chat messages to A2A bus import batches.

    Reads messages from a ChatMessageStore and transforms them into the
    envelope format expected by the taOSmd bus import endpoint.

    Identity mapping is explicit: every author_id in the batch must have a
    corresponding handle in identity_map, or the whole batch fails.
    """

    def __init__(
        self,
        message_store: Any,
        identity_map: dict[str, str],
        source: str = _DEFAULT_SOURCE,
        max_message_bytes: int = _MAX_MESSAGE_BYTES,
        file_writer: Callable[[str, bytes], Awaitable[str]] | None = None,
    ) -> None:
        self._msg_store = message_store
        self._identity_map = dict(identity_map)
        self._source = source
        self._max_message_bytes = max_message_bytes
        self._file_writer = file_writer

    async def export_channel(self, channel_id: str) -> list[dict]:
        """Export all non-deleted, complete messages from a channel.

        Returns envelopes ordered by (created_at ASC, id ASC), with a
        same-timestamp causality pass so a reply never precedes its parent,
        for deterministic, reproducible output.  Raises ChatExportError if
        any author_id is unmapped.
        """
        messages = await self._msg_store.get_all_messages_for_channel(channel_id)
        messages = [
            m
            for m in messages
            if m.get("deleted_at") is None
            and m.get("state") in (None, "complete")
        ]
        messages = _sort_with_causality(messages)

        batch: list[dict] = []
        exported_ids: set[str] = set()

        for msg in messages:
            envelope = await self._transform_message(msg)
            batch.append(envelope)
            exported_ids.add(envelope["source_id"])

        for envelope in batch:
            reply_to = envelope.get("reply_to")
            if reply_to is not None and reply_to not in exported_ids:
                del envelope["reply_to"]

        return batch

    async def _transform_message(self, msg: dict) -> dict:
        """Transform a single chat message to a bus envelope.

        Raises ChatExportError if author_id is not in identity_map.
        """
        author_id = msg.get("author_id", "")
        handle = self._identity_map.get(author_id)
        if handle is None:
            raise ChatExportError(
                f"unmapped author_id {author_id!r} for message {msg.get('id')!r}"
            )

        content_blocks = msg.get("content_blocks") or []
        body = flatten_body(content_blocks)

        if not body and msg.get("content"):
            body = str(msg["content"])

        if content_blocks and not body.strip():
            raise ChatExportError(
                f"message {msg.get('id')!r} has content_blocks but empty body"
            )

        source_id = msg.get("id", "")

        envelope: dict[str, Any] = {
            "from": handle,
            "thread": msg.get("channel_id", ""),
            "body": body,
            "blocks": content_blocks,
            "ts": msg.get("created_at", 0.0),
            "source": self._source,
            "source_id": source_id,
        }

        thread_id = msg.get("thread_id")
        if thread_id:
            envelope["reply_to"] = thread_id

        if _serialized_size(envelope) > self._max_message_bytes:
            if self._file_writer is None:
                raise ChatExportError(
                    f"message {source_id!r} exceeds {self._max_message_bytes} bytes "
                    f"and no file_writer configured"
                )
            # Preserve the full original envelope (blocks intact) out-of-band
            # rather than destroying it — oversize is usually caused by the
            # blocks that would otherwise be dropped.
            full_bytes = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            ref = await self._file_writer(source_id, full_bytes)

            # Budget the body against the SERIALIZED envelope size: the ref
            # note and the envelope's other fields (from/thread/ts/source/...)
            # all count toward the bus's per-message limit, so truncating the
            # body to the raw limit alone can still emit an oversized envelope
            # the bus rejects.
            ref_note = f"\n[oversized content exported to: {ref}]"
            envelope["blocks"] = []
            envelope["body"] = ""
            overhead = len(
                json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            ) + len(json.dumps(ref_note, ensure_ascii=False).encode("utf-8"))
            truncate_note = "... [truncated]"
            body_bytes = body.encode("utf-8")
            emitted_body = body
            if overhead + len(body_bytes) > self._max_message_bytes:
                budget = max(
                    self._max_message_bytes
                    - overhead
                    - len(truncate_note.encode("utf-8")),
                    0,
                )
                emitted_body = (
                    body_bytes[:budget].decode("utf-8", errors="ignore")
                    + truncate_note
                )
            envelope["body"] = f"{emitted_body}{ref_note}"

        return envelope

    async def export_all_channels(self, channel_ids: list[str]) -> list[dict]:
        """Export multiple channels and concatenate the batches."""
        batch: list[dict] = []
        for ch_id in channel_ids:
            batch.extend(await self.export_channel(ch_id))
        return batch
