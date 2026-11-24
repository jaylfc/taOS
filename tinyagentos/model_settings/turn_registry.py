"""Turn Registry - tracks which agent is answering in which channel for chat-level settings."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class TurnRegistry:
    """Tracks active turns per agent to enable chat-level settings without explicit headers.
    
    When the chat hub delivers a message to an agent, it calls start_turn().
    When the agent's turn completes, it calls end_turn().
    The gateway can then check get_channel_for_agent() to see if there's exactly
    one active turn for that agent, and use that channel_id for chat-level settings.
    """
    
    def __init__(self):
        # agent_id -> set of channel_ids where turns are active
        self._active_turns: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()
    
    async def start_turn(self, agent_id: str, channel_id: str) -> None:
        """Record that an agent has started a turn in a channel."""
        async with self._lock:
            self._active_turns[agent_id].add(channel_id)
    
    async def end_turn(self, agent_id: str, channel_id: str) -> None:
        """Record that an agent's turn in a channel has ended."""
        async with self._lock:
            if agent_id in self._active_turns:
                self._active_turns[agent_id].discard(channel_id)
                if not self._active_turns[agent_id]:
                    del self._active_turns[agent_id]
    
    async def get_channel_for_agent(self, agent_id: str) -> str | None:
        """Get the channel_id for an agent's active turn.
        
        Returns:
            - The channel_id if exactly one turn is active
            - "ambiguous" if multiple turns are active (chat level not applied)
            - None if no turns are active
        """
        async with self._lock:
            channels = self._active_turns.get(agent_id, set())
            if len(channels) == 1:
                return next(iter(channels))
            elif len(channels) > 1:
                return "ambiguous"
            return None
    
    async def get_active_channels(self, agent_id: str) -> set[str]:
        """Get all channels where the agent has active turns."""
        async with self._lock:
            return set(self._active_turns.get(agent_id, set()))
    
    async def has_active_turn(self, agent_id: str, channel_id: str) -> bool:
        """Check if a specific turn is active."""
        async with self._lock:
            return channel_id in self._active_turns.get(agent_id, set())


# Global instance for the process
_turn_registry: TurnRegistry | None = None


def get_turn_registry() -> TurnRegistry:
    """Get the global turn registry instance."""
    global _turn_registry
    if _turn_registry is None:
        _turn_registry = TurnRegistry()
    return _turn_registry


def set_turn_registry(registry: TurnRegistry) -> None:
    """Set the global turn registry (for testing)."""
    global _turn_registry
    _turn_registry = registry