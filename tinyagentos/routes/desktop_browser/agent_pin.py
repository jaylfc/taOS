"""Service layer for agent pin operations."""
from __future__ import annotations

from typing import Awaitable, Callable

from tinyagentos.routes.desktop_browser.store import BrowserStore


MAX_PINS_PER_TAB = 4


class TooManyPinsError(Exception):
    """Raised when adding a 5th pin to a tab."""


class AgentNotFoundError(Exception):
    """Raised when pinning an agent_id that doesn't exist on the system."""


async def pin_agent(
    browser_store: BrowserStore,
    *,
    user_id: str,
    profile_id: str,
    tab_id: str,
    agent_id: str,
    agent_exists: Callable[[str], Awaitable[bool]],
) -> bool:
    """Pin an agent to a tab. Auto-grants read_dom for any host while pinned.

    Returns True iff newly pinned. Raises TooManyPinsError if cap exceeded.
    Raises AgentNotFoundError if agent_exists(agent_id) is False.
    """
    if not await agent_exists(agent_id):
        raise AgentNotFoundError(agent_id)
    count = await browser_store.count_pins_for_tab(
        user_id=user_id, profile_id=profile_id, tab_id=tab_id,
    )
    if count >= MAX_PINS_PER_TAB:
        raise TooManyPinsError(MAX_PINS_PER_TAB)
    inserted = await browser_store.add_pin(
        user_id=user_id, profile_id=profile_id, tab_id=tab_id, agent_id=agent_id,
    )
    if inserted:
        # Auto-grant read_dom for any host. Drive/navigate/see_cookies are PR 7.
        await browser_store.add_capability(
            user_id=user_id, profile_id=profile_id, agent_id=agent_id,
            host_pattern="*", permissions="read_dom",
        )
    return inserted


async def unpin_agent(
    browser_store: BrowserStore,
    *,
    user_id: str,
    profile_id: str,
    tab_id: str,
    agent_id: str,
) -> bool:
    """Unpin. Returns True iff a row was deleted. Capability grants are NOT
    revoked here — they persist across re-pins (intentional)."""
    return await browser_store.delete_pin(
        user_id=user_id, profile_id=profile_id, tab_id=tab_id, agent_id=agent_id,
    )
