"""Agent Buddy bridge adapter.

Reference adapter for the Agent Buddy probe replies.
Agent Buddy has no host-side tool; installed() returns None.
"""

from . import BridgeResult, target

BRIDGE_ID = "agent-buddy"
TITLE = "Agent Buddy"
APP_ID = "agent-buddy"


def installed() -> bool | None:
    """Agent Buddy upstream runs the daemon from its repo with npm, there is no binary on PATH to look for."""
    return None


def evaluate(lan_orbs: list[dict], agent_buddy: list[dict]) -> BridgeResult:
    """Evaluate Agent Buddy bridges from probe replies.

    This adapter matches the Agent Buddy probe replies, not lan_orbs
    (spec s5.1 exception; lan_orbs is accepted and ignored).
    """
    targets = []
    for reply in agent_buddy:
        address = reply.get("address")
        if not isinstance(address, str) or not address:
            continue
        hostname = reply.get("hostname")
        targets.append(
            target(
                hostname,
                address,
                f"In your agent-buddy folder run: npm run pair -- <code shown on the Orb> --host {address}",
            )
        )
    targets_tuple = tuple(targets)
    if targets:
        instruction = "Pair each Orb with the command shown, using the 8-digit code on its screen."
    else:
        instruction = "No Orb running Agent Buddy found on the LAN."
    return BridgeResult(
        bridge_id=BRIDGE_ID,
        title=TITLE,
        installed=installed(),
        targets=targets_tuple,
        instruction=instruction,
    )