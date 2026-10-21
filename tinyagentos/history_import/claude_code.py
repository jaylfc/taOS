"""Pure converter: Claude Code session .jsonl -> Envelope v1 events + Tier 1 index lines.

Reads a Claude Code session transcript (the .jsonl written by the Claude Code
CLI) and emits:

* Envelope v1 dicts -- shapes documented in
  ``tinyagentos/trace_store.py`` ``ENVELOPE_V1_SCHEMA``.  Kinds produced:
  ``message_in``, ``message_out``, ``tool_call``, ``tool_result``.
* Tier 1 index lines for fast search -- one per ``message_in``,
  ``message_out`` and ``tool_call``.  ``tool_result`` bodies NEVER appear
  in index lines.

No I/O beyond reading *path*.  All redaction is delegated to the injected
``redact`` callable; secret-file detection is delegated to the injected
``is_secret_file_read`` predicate (card T3).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime
from typing import Any

SCHEMA_VERSION = 1

_IMAGE_PLACEHOLDER = {"text": "[image omitted]"}

_ARG_KEYS = ("command", "file_path", "pattern", "url")


def _epoch(ts: str | None) -> float:
    """Convert an ISO 8601 timestamp string to Unix epoch float."""
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _redact_text(redact, text: str) -> str:
    """Apply the redact callable to *text*, or return text unchanged."""
    if redact is None:
        return text
    return redact(text)


def _redact_args(redact, value: Any) -> Any:
    """Return a copy of *value* with every str leaf passed through _redact_text.

    Recurses into dict values and list items; other types are returned unchanged.
    """
    if isinstance(value, str):
        return _redact_text(redact, value)
    if isinstance(value, dict):
        return {k: _redact_args(redact, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_args(redact, item) for item in value]
    return value


def _block_text(content: Any) -> str:
    """Flatten a Claude Code content field (str or list of blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return "" if content is None else str(content)


def _envelope(
    agent_name: str,
    kind: str,
    created_at: float,
    trace_id: str | None,
    id: str | None,
    parent_id: str | None,
    payload: dict,
) -> dict:
    """Build a minimal Envelope v1 dict matching ENVELOPE_V1_SCHEMA."""
    return {
        "v": SCHEMA_VERSION,
        "id": id,
        "trace_id": trace_id,
        "parent_id": parent_id,
        "created_at": created_at,
        "agent_name": agent_name,
        "kind": kind,
        "channel_id": None,
        "thread_id": None,
        "backend_name": None,
        "model": None,
        "duration_ms": None,
        "tokens_in": None,
        "tokens_out": None,
        "cost_usd": None,
        "error": None,
        "payload": payload,
    }


def _short_arg(args: dict) -> str:
    """Pick the first of command/file_path/pattern/url from tool *args*."""
    for key in _ARG_KEYS:
        if key in args and args[key] is not None:
            return str(args[key])[:200]
    return ""


def _count_skipped_lines(path) -> int:
    """Count non-blank lines in *path* that fail JSON decoding or are not objects."""
    count = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                count += 1
                continue
            if not isinstance(value, dict):
                count += 1
    return count


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def iter_records(path) -> Iterator[dict]:
    """Yield parsed JSON records from a Claude Code .jsonl session file.

    Lines that fail JSON decoding or are not JSON objects are silently skipped.
    """
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            yield value


def to_envelopes(
    records,
    *,
    agent_name: str,
    redact,
    is_secret_file_read=None,
) -> list[dict]:
    """Convert Claude Code session records into Envelope v1 dicts.

    *user* text -> ``message_in`` {from: "user", text}
    *assistant* text -> ``message_out`` {content}
    *assistant* tool_use -> ``tool_call`` {tool, args, caller}
    *user* tool_result -> ``tool_result`` {tool, result, success}

    - ``trace_id`` = record ``sessionId``
    - ``id`` = record ``uuid`` (tool_use id for tool_call)
    - ``parent_id`` of a tool_result = its tool_use id
    - ``created_at`` = record ``timestamp`` as epoch float

    A typed user prompt whose ``message.content`` is a plain string is emitted
    as a single ``message_in``.  Records carrying ``"isMeta": true`` are
    harness injections (skill bodies, command caveats), not user prompts, and
    are skipped in the user branch.  Thinking blocks are dropped.  Image bytes
    are replaced with ``{"text": "[image omitted]"}``.  Every text field
    passes through the injected ``redact`` callable.  A tool_result whose
    matching tool_call read a path where ``is_secret_file_read(path)`` is
    True gets ``result`` replaced with ``"[secret file read omitted]"``.
    """
    envelopes: list[dict] = []
    tool_calls: dict[str, tuple[str, dict]] = {}

    for record in records:
        rtype = record.get("type")
        if not rtype:
            continue
        if rtype == "user" and record.get("isMeta"):
            continue
        trace_id = record.get("sessionId") or record.get("session_id")
        rid = record.get("uuid")
        created_at = _epoch(record.get("timestamp"))
        message = record.get("message") or {}
        blocks = message.get("content") or []

        if rtype == "user":
            if isinstance(blocks, str):
                text = _redact_text(redact, blocks)
                if text:
                    envelopes.append(
                        _envelope(
                            agent_name,
                            "message_in",
                            created_at,
                            trace_id,
                            rid,
                            None,
                            {"from": "user", "text": text},
                        )
                    )
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = _redact_text(redact, _block_text(block.get("text", "")))
                    if text:
                        envelopes.append(
                            _envelope(
                                agent_name,
                                "message_in",
                                created_at,
                                trace_id,
                                rid,
                                None,
                                {"from": "user", "text": text},
                            )
                        )
                elif btype == "tool_result":
                    env = _build_tool_result(
                        block,
                        rid,
                        trace_id,
                        created_at,
                        agent_name,
                        tool_calls,
                        redact,
                        is_secret_file_read,
                    )
                    if env is not None:
                        envelopes.append(env)

        elif rtype == "assistant":
            text_parts: list[str] = []
            content_blocks: list[dict] = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(_block_text(block.get("text", "")))
                elif btype == "thinking":
                    continue
                elif btype == "image":
                    content_blocks.append(_IMAGE_PLACEHOLDER)
                elif btype == "tool_use":
                    tu_id = block.get("id")
                    tool_name = block.get("name", "")
                    tool_input = block.get("input") or {}
                    if isinstance(tool_input, dict):
                        tool_calls[tu_id] = (tool_name, tool_input)
                    redacted_name = _redact_text(redact, tool_name)
                    envelopes.append(
                        _envelope(
                            agent_name,
                            "tool_call",
                            created_at,
                            trace_id,
                            tu_id,
                            None,
                            {
                                "tool": redacted_name,
                                "args": _redact_args(redact, tool_input),
                                "caller": agent_name,
                            },
                        )
                    )

            content = _redact_text(redact, "".join(text_parts))
            payload: dict = {"content": content}
            if content_blocks:
                payload["content_blocks"] = content_blocks
            if content or content_blocks:
                envelopes.append(
                    _envelope(
                        agent_name,
                        "message_out",
                        created_at,
                        trace_id,
                        rid,
                        None,
                        payload,
                    )
                )

    return envelopes


def _build_tool_result(
    block: dict,
    rid: str | None,
    trace_id: str | None,
    created_at: float,
    agent_name: str,
    tool_calls: dict,
    redact,
    is_secret_file_read,
) -> dict | None:
    """Build a tool_result envelope from a user-side tool_result block."""
    tu_id = block.get("tool_use_id")
    if tu_id is None:
        return None
    tool_name, tool_input = tool_calls.get(tu_id, ("", {}))

    result_text = _block_text(block.get("content"))
    is_error = block.get("is_error", False)
    success = not is_error

    omit_result = False
    if is_secret_file_read is not None and isinstance(tool_input, dict):
        file_path = tool_input.get("file_path")
        if file_path and is_secret_file_read(file_path):
            omit_result = True
            result_text = "[secret file read omitted]"

    if not omit_result:
        result_text = _redact_text(redact, result_text)

    return _envelope(
        agent_name,
        "tool_result",
        created_at,
        trace_id,
        rid,
        tu_id,
        {
            "tool": _redact_text(redact, tool_name),
            "result": result_text,
            "success": success,
        },
    )


def to_index_lines(envelopes) -> list[dict]:
    """Build Tier 1 index lines from envelopes.

    One line per ``message_in``, ``message_out`` and ``tool_call``.
    ``tool_result`` bodies NEVER appear in index lines.
    """
    lines: list[dict] = []
    for env in envelopes:
        kind = env.get("kind")
        payload = env.get("payload", {})

        if kind == "message_in":
            lines.append(
                {
                    "trace_id": env.get("trace_id"),
                    "created_at": env.get("created_at"),
                    "role": "user",
                    "text": payload.get("text", ""),
                }
            )
        elif kind == "message_out":
            lines.append(
                {
                    "trace_id": env.get("trace_id"),
                    "created_at": env.get("created_at"),
                    "role": "assistant",
                    "text": payload.get("content", ""),
                }
            )
        elif kind == "tool_call":
            tool = payload.get("tool", "")
            short = _short_arg(payload.get("args", {}))
            text = f"{tool} {short}" if short else tool
            lines.append(
                {
                    "trace_id": env.get("trace_id"),
                    "created_at": env.get("created_at"),
                    "role": "tool",
                    "text": text[:200],
                    "ref": env.get("id"),
                }
            )
    return lines


def manifest(path, envelopes, index_lines) -> dict:
    """Summarise the conversion: byte counts and record counts."""
    bytes_in = os.path.getsize(path)
    skipped = _count_skipped_lines(path)
    index_bytes = len(json.dumps(index_lines, default=str).encode("utf-8"))
    return {
        "bytes_in": bytes_in,
        "envelopes": len(envelopes),
        "index_lines": len(index_lines),
        "index_bytes": index_bytes,
        "skipped_lines": skipped,
    }
