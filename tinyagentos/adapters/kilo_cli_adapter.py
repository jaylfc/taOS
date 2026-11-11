"""Kilo CLI adapter: one ``kilo run --format json`` turn mapped onto taOS's reply kinds.

Drives the Kilo harness of the system taOS Agent (see
:mod:`tinyagentos.kilo_runtime`). Same interface as
:class:`~tinyagentos.adapters.opencode_adapter.OpenCodeAdapter` so the taOS
Agent chat route drives either one unchanged::

    adapter = KiloCliAdapter(KiloCliConfig(harness=h), sink)
    await adapter.ensure_session()
    await adapter.prompt("Hello", trace_id="t1", attachments=[])
    await adapter.close()

``prompt()`` never raises for a failed turn, mirroring the opencode adapter.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from tinyagentos.kilo_runtime import KiloHarness

logger = logging.getLogger(__name__)


def map_kilo_event(evt: dict, state: dict) -> list[tuple[str, dict]]:
    """Map one Kilo stream-json event to ``(kind, payload)`` pairs.

    Pure: no I/O. ``state["text"]`` collects the assistant text so a turn
    without a reply still has a final answer to stream.
    """
    etype = evt.get("type")
    if etype == "text":
        part = evt.get("part")
        text = ""
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            text = part["text"]
        state["text"] = state.get("text", "") + text
        return [("delta", {"content": text})] if text else []
    return []


@dataclass
class KiloCliConfig:
    harness: KiloHarness
    system: str | None = None
    """Carried for symmetry with the other adapters; the harness holds the
    system prompt it passes to ``--model`` (Kilo has no append-system-prompt
    flag, so this is kept for interface parity only)."""


class KiloCliAdapter:
    def __init__(self, config: KiloCliConfig, sink) -> None:
        self._cfg = config
        self._sink = sink
        self.session_id: str | None = None

    async def _emit(self, reply: dict) -> None:
        try:
            res = self._sink(reply)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            logger.exception("kilo_cli_adapter: sink raised for reply kind=%s", reply.get("kind"))

    async def ensure_session(self) -> str | None:
        """The session is created by Kilo on the first turn; until then
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
                "error": "attachments are not supported by the Kilo taOS Agent yet; "
                         "send the message without them",
            })
            return
        state = {"text": ""}
        pending: list[dict] = []
        errors: list[str] = []

        def on_event(evt: dict) -> None:
            for kind, payload in map_kilo_event(evt, state):
                if kind == "error":
                    errors.append(payload["error"])  # reported once, below
                    continue
                pending.append({"kind": kind, "trace_id": trace_id, **payload})
            # Drain on the loop thread: on_event runs inside run_turn's reader.
            while pending:
                item = pending.pop(0)
                try:
                    res = self._sink(item)
                    if asyncio.iscoroutine(res):
                        asyncio.ensure_future(res)
                except Exception:
                    logger.exception("kilo_cli_adapter: sink raised for reply kind=%s", item.get("kind"))

        try:
            result = await harness.run_turn(text, self.session_id, on_event=on_event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade to an error reply
            logger.error("kilo_cli_adapter: turn failed: %s", type(exc).__name__)
            await self._emit({"kind": "error", "trace_id": trace_id,
                              "error": f"the taOS agent (kilo) failed: {exc}"})
            return
        if result.session_id:
            self.session_id = result.session_id
        if result.timed_out:
            await self._emit({"kind": "error", "trace_id": trace_id, "error": result.detail})
            return
        # Kilo has no ``result`` event (unlike Claude Code), so ``emitted``
        # only ever holds "delta"; a successful turn (exit 0) streams the
        # collected reply as a final frame regardless.
        if result.returncode != 0:
            why = (errors[-1] if errors else result.detail) or "no answer"
            logger.warning("kilo_cli_adapter: turn ended with exit %d", result.returncode)
            await self._emit({
                "kind": "error", "trace_id": trace_id,
                "error": f"the taOS agent (kilo) returned no answer (exit {result.returncode}): {why}",
            })
            return
        await self._emit({"kind": "final", "trace_id": trace_id, "content": result.reply or state.get("text", "")})

    async def close(self) -> None:
        return None