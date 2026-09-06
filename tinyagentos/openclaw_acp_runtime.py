"""Fork-free OpenClaw runtime: drive an agent turn over ACP.

Replaces the forked ``taos-bridge`` (where OpenClaw connected OUT to taOS over
SSE). Here taOS drives IN: it spawns ``openclaw acp --session <key>`` inside the
agent's container and runs one turn via :class:`ACPAdapter`, mapping the ACP
``session/update`` stream onto the same reply kinds ``bridge_session.record_reply``
already consumes (delta/final/tool_call/tool_result/reasoning/error).

Validated live against OpenClaw 2026.4.18: the bridge MUST be launched bound to
the agent's gateway session (``agent:main:main``) or the turn hangs.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from tinyagentos.adapters.acp_adapter import ACPAdapter, ACPConfig

logger = logging.getLogger(__name__)

# The persistent gateway session the agent runs under. Binding the ACP bridge
# to it is required — an unbound session has no model and the turn hangs.
OPENCLAW_SESSION_KEY = "agent:main:main"


def container_name(slug: str) -> str:
    """The LXC container name for an agent slug (matches deployer.py)."""
    return f"taos-agent-{slug}"


async def drive_turn(
    *,
    slug: str,
    text: str,
    trace_id: str | None,
    record_reply: Callable[[str, dict], Awaitable[None]],
    exec_command: list[str] | None = None,
    adapter_factory: Callable[[ACPConfig, Callable], ACPAdapter] = ACPAdapter,
) -> str:
    """Run one OpenClaw turn for *slug* over ACP, streaming mapped replies to
    ``record_reply(slug, body)``. Returns the ACP stopReason.

    Spawns ``openclaw acp`` inside the agent's container via ``incus exec`` (the
    controller runs as root, so no sudo). ``exec_command`` overrides the launch
    argv (tests / non-incus backends); ``adapter_factory`` is injectable for
    tests.
    """
    command = exec_command or [
        "incus", "exec", container_name(slug), "--", "openclaw", "acp",
    ]

    async def sink(body: dict) -> None:
        # The adapter emits bridge_session-shaped reply dicts already.
        await record_reply(slug, body)

    cfg = ACPConfig(command=command, session_key=OPENCLAW_SESSION_KEY)
    adapter = None
    try:
        adapter = adapter_factory(cfg, sink)
        await adapter.spawn()
        await adapter.initialize()
        session_id = await adapter.new_session()
        stop_reason = await adapter.prompt(session_id, text, trace_id=trace_id)
        logger.info("openclaw acp turn slug=%s stopReason=%s", slug, stop_reason)
        return stop_reason
    except Exception:
        # Never let a transport/adapter failure escape — always degrade to a
        # chat-visible error so the turn ends cleanly. The record_reply itself
        # is guarded too (its failure must not mask the original).
        logger.exception("openclaw acp turn failed for %s", slug)
        try:
            await record_reply(slug, {
                "kind": "error",
                "trace_id": trace_id,
                "error": "agent turn failed (ACP transport)",
            })
        except Exception:
            logger.exception("openclaw acp: error reply also failed for %s", slug)
        return "error"
    finally:
        if adapter is not None:
            try:
                await adapter.close()
            except Exception:
                logger.exception("openclaw acp: adapter close failed for %s", slug)
