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

import logging
import re

logger = logging.getLogger(__name__)

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


def summarize_result(result) -> tuple[str, str, list[dict], str]:
    """Derive (output_ref, result_summary, files_changed, stop_reason) from a
    skill result dict. Kept small: full payloads live in the trace store, the
    receipt only needs a pointer + a human summary."""
    if not isinstance(result, dict):
        return "", str(result)[:200], [], "completed"
    if result.get("error"):
        return "", str(result.get("error"))[:200], [], "error"
    files_changed: list[dict] = []
    # file_write returns {"status": "written", "bytes": N}; the path is in args,
    # threaded in by the caller, so files_changed is enriched there. Here we only
    # surface the byte count if present.
    if result.get("status") == "written" and "bytes" in result:
        files_changed = [{"bytes": result.get("bytes")}]
    # Short, lossy summary; the trace store holds the full result.
    summary = ", ".join(f"{k}={str(v)[:60]}" for k, v in result.items() if k != "content")[:200]
    return "", summary, files_changed, "completed"


async def emit_tool_receipt(
    store,
    *,
    agent: str,
    tool_name: str,
    args: dict,
    result,
    project_id: str = "",
    files_changed: list | None = None,
) -> None:
    """Write one receipt for a tool call. Fail-soft: any error is logged and
    swallowed so a receipt write can never disrupt the agent. Intended to be
    scheduled fire-and-forget by the route."""
    if store is None or not agent:
        return
    try:
        red_args, redactions = redact_args(args)
        output_ref, summary, fc_from_result, stop_reason = summarize_result(result)
        await store.record(
            agent,
            tool_name=tool_name,
            tool_args=red_args,
            result_summary=summary,
            output_ref=output_ref,
            files_changed=files_changed if files_changed is not None else fc_from_result,
            stop_reason=stop_reason,
            redactions=redactions,
            project_id=project_id,
        )
    except Exception:  # noqa: BLE001 - a receipt must never break the tool path
        logger.warning("receipt capture failed for tool %s", tool_name, exc_info=True)
