"""Claude Code CLI adapter: one ``claude -p`` stream-json turn mapped onto taOS's reply kinds.

Drives the Claude Code harness of the system taOS Agent (see
:mod:`tinyagentos.claude_code_runtime`). Same interface as
:class:`~tinyagentos.adapters.opencode_adapter.OpenCodeAdapter` so the taOS
Agent chat route drives either one unchanged::

    adapter = ClaudeCodeCliAdapter(ClaudeCodeCliConfig(harness=h), sink)
    await adapter.ensure_session()
    await adapter.prompt("Hello", trace_id="t1", attachments=[])
    await adapter.close()

:func:`map_claude_event` is the pure codec. ``prompt()`` never raises for a
failed turn, mirroring the opencode adapter.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from tinyagentos.claude_code_runtime import ClaudeCodeHarness

logger = logging.getLogger(__name__)


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return "" if content is None else str(content)


def map_claude_event(evt: dict, state: dict) -> list[tuple[str, dict]]:
    """Map one Claude Code stream-json event to ``(kind, payload)`` pairs.

    Pure: no I/O. ``state["text"]`` collects the assistant text so a result
    without a ``result`` string still has a final answer.
    """
    etype = evt.get("type")
    if etype == "assistant":
        out: list[tuple[str, dict]] = []
        for block in (evt.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text", "")
                state["text"] = state.get("text", "") + text
                out.append(("delta", {"content": text}))
            elif block.get("type") == "tool_use":
                out.append(("tool_call", {"name": block.get("name"), "input": block.get("input"),
                                          "id": block.get("id")}))
        return out
    if etype == "user":
        return [
            ("tool_result", {"tool_use_id": block.get("tool_use_id"),
                             "content": _block_text(block.get("content"))})
            for block in (evt.get("message") or {}).get("content") or []
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
    if etype == "result":
        if evt.get("is_error"):
            return [("error", {"error": evt.get("result") or "claude code turn failed"})]
        return [("final", {"content": evt.get("result") or state.get("text", "")})]
    return []


@dataclass
class ClaudeCodeCliConfig:
    harness: ClaudeCodeHarness
    system: str | None = None
    """Carried for symmetry with the other adapters; the harness holds the
    system prompt it passes to ``--append-system-prompt``."""


class ClaudeCodeCliAdapter:
    def __init__(self, config: ClaudeCodeCliConfig, sink) -> None:
        self._cfg = config
        self._sink = sink
        self.session_id: str | None = None

    async def _emit(self, reply: dict) -> None:
        try:
            res = self._sink(reply)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            logger.exception("claude_code_cli_adapter: sink raised for reply kind=%s", reply.get("kind"))

    async def ensure_session(self) -> str | None:
        """The session is created by Claude Code on the first turn; until then
        ``session_id`` is whatever the caller restored (or None)."""
        return self.session_id

    async def prompt(
        self,
        text: str,
        trace_id: str | None = None,
        attachments: list[dict] | None = None,
    ) -> None:
        harness = self._cfg.harness
        if attachments:
            await self._emit({
                "kind": "error", "trace_id": trace_id,
                "error": "attachments are not supported by the Claude Code taOS Agent yet; "
                         "send the message without them",
            })
            return
        state = {"text": ""}
        emitted: list[str] = []
        pending: list[dict] = []
        errors: list[str] = []

        def on_event(evt: dict) -> None:
            for kind, payload in map_claude_event(evt, state):
                if kind == "error":
                    errors.append(payload["error"])  # reported once, below
                    continue
                emitted.append(kind)
                pending.append({"kind": kind, "trace_id": trace_id, **payload})
            # Drain on the loop thread: on_event runs inside run_turn's reader.
            while pending:
                item = pending.pop(0)
                try:
                    res = self._sink(item)
                    if asyncio.iscoroutine(res):
                        asyncio.ensure_future(res)
                except Exception:
                    logger.exception("claude_code_cli_adapter: sink raised for reply kind=%s", item.get("kind"))

        try:
            result = await harness.run_turn(text, self.session_id, on_event=on_event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade to an error reply
            logger.error("claude_code_cli_adapter: turn failed: %s", type(exc).__name__)
            await self._emit({"kind": "error", "trace_id": trace_id,
                              "error": harness.redact(f"the taOS agent (claude code) failed: {exc}")})
            return
        if result.session_id:
            self.session_id = result.session_id
        if result.timed_out:
            await self._emit({"kind": "error", "trace_id": trace_id, "error": result.detail})
            return
        if result.returncode != 0 or "final" not in emitted:
            why = (errors[-1] if errors else result.detail) or "no answer"
            logger.warning("claude_code_cli_adapter: turn ended with exit %d", result.returncode)
            await self._emit({
                "kind": "error", "trace_id": trace_id,
                "error": harness.redact(
                    f"the taOS agent (claude code) returned no answer (exit {result.returncode}): {why}"),
            })

    async def close(self) -> None:
        return None
