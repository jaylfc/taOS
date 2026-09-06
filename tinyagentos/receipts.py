"""Receipt capture at the OS boundary (#155 slice 2).

Turns a tool call into an append-only action receipt. Everything here is
fail-soft and meant to be fire-and-forget: a receipt write must NEVER affect the
tool's own result or add latency to it. The route extracts the plain values it
needs and schedules ``emit_tool_receipt`` on the event loop, so the tool response
returns before the receipt is even written.

Redaction (Jay's call): the user owns the redaction policy, but the OS keeps a
non-optional BASELINE that masks obviously-secret values before they can land in
the ledger, since receipts are meant to be exported/shared between agents. Slice
2 ships the pattern-based baseline (token-like strings); user-defined rules and
secrets-store value masking layer on in a later slice.
"""

from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger(__name__)


def hash_text(s) -> str:
    """Content fingerprint for a receipt. Safe to store even when the content was
    secret: the hash does not reveal it, but lets another person verify a replay
    produced the same bytes. Hashes bytes as-is; everything else as UTF-8."""
    data = s if isinstance(s, (bytes, bytearray)) else str(s).encode("utf-8", "replace")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def derive_io(tool_name: str, args: dict, result) -> tuple[list[dict], list[dict]]:
    """Return (input_refs, files_changed) with content hashes for the tool call,
    so the receipt records exactly what went in and what changed. Tool-specific;
    unknown tools get empty lists (the receipt still carries tool_name + args)."""
    args = args or {}
    is_error = isinstance(result, dict) and bool(result.get("error"))
    input_refs: list[dict] = []
    files_changed: list[dict] = []
    if tool_name == "file_write":
        content = args.get("content", "")
        h = hash_text(content)
        input_refs.append({"name": "content", "hash": h})
        # Only record a file change once the tool CONFIRMS the write (status ==
        # "written"). A non-error-but-malformed result is not proof a write
        # landed, so recording one would put a false change in the ledger.
        if isinstance(result, dict) and result.get("status") == "written":
            # The tool reports the bytes it actually wrote; trust that over a
            # recomputed count. Fall back to the UTF-8 byte length of content.
            byte_count = result.get("bytes")
            if byte_count is None:
                # Mirror hash_text: count bytes as-is, everything else as UTF-8.
                byte_count = len(
                    content if isinstance(content, (bytes, bytearray))
                    else str(content).encode("utf-8", "replace")
                )
            files_changed.append(
                {"path": args.get("path", ""), "hash_after": h, "bytes": byte_count}
            )
    elif tool_name == "code_exec":
        input_refs.append({"name": "code", "hash": hash_text(args.get("code", ""))})
    elif tool_name == "file_read":
        input_refs.append({"name": "path", "value": args.get("path", "")})
        if not is_error and isinstance(result, dict) and "content" in result:
            input_refs.append({"name": "content_read", "hash": hash_text(result.get("content", ""))})
    return input_refs, files_changed

# Baseline secret patterns. Conservative on purpose: better to mask a few benign
# long tokens than to leak a real key into a portable receipt. The user policy
# layer (later slice) refines this; it is never allowed to disable the baseline.
_SECRET_KEY_HINT = re.compile(r"(secret|token|api[_-]?key|password|passwd|bearer|auth)", re.I)
# Only well-known token SHAPES, not a generic "long string" catch-all: the broad
# rule masked benign data (file paths, hashes, base64 payloads) and corrupted the
# ledger. Secrets passed under non-obvious keys are caught by the user policy +
# secrets-store value masking in a later slice; the catch-all did more harm here.
_TOKEN_VALUE = re.compile(
    r"("
    r"sk-[A-Za-z0-9]{16,}"           # OpenAI-style
    r"|gh[pousr]_[A-Za-z0-9]{20,}"   # GitHub tokens
    r"|xox[baprs]-[A-Za-z0-9-]{10,}" # Slack
    r"|AKIA[0-9A-Z]{16}"             # AWS access key id
    r")"
)
_REDACTED = "[REDACTED]"


def redact_args(args: dict) -> tuple[dict, list[dict]]:
    """Return (redacted_copy, redactions). Masks ANY value whose KEY looks secret
    (regardless of type, so a non-string secret cannot slip through) and masks
    token-shaped substrings in string values. Non-destructive: the original dict
    is untouched. Records what was masked (field + reason) for the receipt."""
    redactions: list[dict] = []
    out: dict = {}
    for k, v in (args or {}).items():
        if _SECRET_KEY_HINT.search(str(k)):
            out[k] = _REDACTED
            redactions.append({"field": str(k), "reason": "secret-key-name"})
        elif isinstance(v, str) and _TOKEN_VALUE.search(v):
            out[k] = _TOKEN_VALUE.sub(_REDACTED, v)
            redactions.append({"field": str(k), "reason": "token-pattern"})
        else:
            out[k] = v
    return out, redactions


def summarize_result(result) -> tuple[str, str, str]:
    """Derive (output_ref, result_summary, stop_reason) from a skill result.
    Kept small: full payloads live in the trace store, the receipt needs only a
    pointer + a human summary. files_changed is owned by derive_io (which has the
    args + content hashes), so it is intentionally not produced here."""
    if not isinstance(result, dict):
        return "", str(result)[:200], "completed"
    if result.get("error"):
        return "", str(result.get("error"))[:200], "error"
    # Short, lossy summary; the trace store holds the full result.
    summary = ", ".join(f"{k}={str(v)[:60]}" for k, v in result.items() if k != "content")[:200]
    return "", summary, "completed"


async def emit_tool_receipt(
    store,
    *,
    agent: str,
    tool_name: str,
    args: dict,
    result,
    project_id: str = "",
) -> None:
    """Write one receipt for a tool call. Fail-soft: any error is logged and
    swallowed so a receipt write can never disrupt the agent. Intended to be
    scheduled fire-and-forget by the route. input_refs + files_changed (with
    content hashes) are derived from the tool name, args, and result."""
    if store is None or not agent:
        return
    try:
        red_args, redactions = redact_args(args)
        output_ref, summary, stop_reason = summarize_result(result)
        input_refs, files_changed = derive_io(tool_name, args, result)
        await store.record(
            agent,
            tool_name=tool_name,
            tool_args=red_args,
            result_summary=summary,
            output_ref=output_ref,
            input_refs=input_refs,
            files_changed=files_changed,
            stop_reason=stop_reason,
            redactions=redactions,
            project_id=project_id,
        )
    except Exception:  # noqa: BLE001 - a receipt must never break the tool path
        logger.warning("receipt capture failed for tool %s", tool_name, exc_info=True)
