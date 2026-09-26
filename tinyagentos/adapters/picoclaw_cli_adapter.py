"""PicoClaw CLI adapter: one ``picoclaw agent`` turn mapped onto taOS's reply kinds.

Drives the host PicoClaw that runs the system taOS Agent on a taOSmobile
handset (see :mod:`tinyagentos.picoclaw_runtime`). Not to be confused with
``picoclaw_adapter``, the channel-hub bridge for PicoClaw inside an agent
container.

Same interface as :class:`~tinyagentos.adapters.opencode_adapter.OpenCodeAdapter`
so the taOS Agent chat route drives either one unchanged::

    adapter = PicoClawCliAdapter(PicoClawCliConfig(harness=h), sink)
    await adapter.ensure_session()
    await adapter.prompt("Hello", trace_id="t1", attachments=[])
    await adapter.close()

PicoClaw's one-shot CLI returns the whole answer when the turn ends, so a turn
is one ``delta`` (the answer) then ``final``, or one ``error``. ``prompt()``
never raises for a failed turn, mirroring the opencode adapter.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from tinyagentos.picoclaw_runtime import PicoClawHarness

logger = logging.getLogger(__name__)

DEFAULT_SESSION = "taos:taos-agent"


@dataclass
class PicoClawCliConfig:
    harness: PicoClawHarness
    session: str = DEFAULT_SESSION
    """PicoClaw session key; PicoClaw keeps the history under its workspace."""
    system: str | None = None
    """Written to the workspace AGENTS.md before the turn (PicoClaw's system prompt)."""


class PicoClawCliAdapter:
    def __init__(self, config: PicoClawCliConfig, sink) -> None:
        self._cfg = config
        self._sink = sink
        self.session_id: str | None = None

    async def _emit(self, reply: dict) -> None:
        try:
            res = self._sink(reply)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            logger.exception("picoclaw_cli_adapter: sink raised for reply kind=%s", reply.get("kind"))

    async def ensure_session(self) -> str:
        if self.session_id is None:
            self.session_id = self._cfg.session
        return self.session_id

    async def prompt(
        self,
        text: str,
        trace_id: str | None = None,
        attachments: list[dict] | None = None,
    ) -> None:
        harness = self._cfg.harness
        if attachments:
            # PicoClaw's one-shot CLI has no way to pass an image or a file.
            await self._emit({
                "kind": "error", "trace_id": trace_id,
                "error": "attachments are not supported by the PicoClaw taOS Agent yet; "
                         "send the message without them",
            })
            return
        try:
            await self.ensure_session()
            if self._cfg.system is not None:
                harness.write_system_prompt(self._cfg.system)
            result = await harness.run_turn(text, self.session_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade to an error reply
            logger.error("picoclaw_cli_adapter: turn failed: %s", type(exc).__name__)
            await self._emit({"kind": "error", "trace_id": trace_id,
                              "error": harness.redact(f"the taOS agent (picoclaw) failed: {exc}")})
            return
        if result.timed_out:
            await self._emit({"kind": "error", "trace_id": trace_id, "error": result.detail})
            return
        if result.returncode != 0 or not result.reply:
            why = result.detail or "no answer"
            logger.warning("picoclaw_cli_adapter: turn ended with exit %d: %s", result.returncode, why)
            await self._emit({
                "kind": "error", "trace_id": trace_id,
                "error": f"the taOS agent (picoclaw) returned no answer (exit {result.returncode}): {why}",
            })
            return
        await self._emit({"kind": "delta", "trace_id": trace_id, "content": result.reply})
        await self._emit({"kind": "final", "trace_id": trace_id, "content": result.reply})

    async def close(self) -> None:
        return None
