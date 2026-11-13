"""MimiClaw bridge adapter.

Reference adapter for the MimiClaw WebSocket interface.
MimiClaw has no host-side tool; installed() returns None.
"""

from . import BridgeResult, orbs_running, orb_host, target

BRIDGE_ID = "mimiclaw"
TITLE = "MimiClaw"
APP_ID = "mimiclaw"
WS_PORT = 18789  # upstream memovai/mimiclaw main/mimi_config.h MIMI_WS_PORT


def installed() -> bool | None:
    """MimiClaw has no host-side tool."""
    return None


def evaluate(lan_orbs: list[dict], agent_buddy: list[dict]) -> BridgeResult:
    """Evaluate MimiClaw bridges from discovered LAN orbs.

    agent_buddy is unused (kept for adapter signature compatibility).
    """
    matching_orbs = orbs_running(lan_orbs, APP_ID)
    targets = []
    for orb in matching_orbs:
        host = orb_host(orb)
        if host is not None:
            targets.append(
                target(
                    orb["name"],
                    host,
                    f"Connect a WebSocket client to ws://{host}:{WS_PORT}/",
                )
            )
    targets_tuple = tuple(targets)
    if targets:
        instruction = "Connect a WebSocket client to the URL shown for your Orb."
    else:
        instruction = "No Orb running MimiClaw found on the LAN."
    return BridgeResult(
        bridge_id=BRIDGE_ID,
        title=TITLE,
        installed=installed(),
        targets=targets_tuple,
        instruction=instruction,
    )