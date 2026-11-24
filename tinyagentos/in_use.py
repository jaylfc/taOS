"""In-process "is this part busy" counters.

A single instance lives on ``app.state.in_use`` and is shared by the LLM
gateway (open streaming responses) and the bridge session registry (agent
turns in flight). Each counter is incremented on entry and decremented in a
``finally`` block, so a disconnect or error can never leak a count, and the
value can never go negative.
"""
from __future__ import annotations

import asyncio


class InUseCounters:
    """Plain-integer busy counters for the health probe."""

    def __init__(self) -> None:
        self.gateway_streams = 0
        self.agent_turns = 0
        self._lock = asyncio.Lock()

    async def increment_gateway_stream(self) -> None:
        async with self._lock:
            self.gateway_streams += 1

    async def decrement_gateway_stream(self) -> None:
        async with self._lock:
            if self.gateway_streams > 0:
                self.gateway_streams -= 1

    async def increment_agent_turn(self) -> None:
        async with self._lock:
            self.agent_turns += 1

    async def decrement_agent_turn(self) -> None:
        async with self._lock:
            if self.agent_turns > 0:
                self.agent_turns -= 1

    def snapshot(self) -> dict:
        return {
            "gateway_streams": self.gateway_streams,
            "agent_turns": self.agent_turns,
        }
