"""ESP-Claw bridge adapter.

Reference adapter for the ESP-Claw web UI interface.
ESP-Claw has no host-side tool; installed() returns None.
"""

from . import BridgeResult, orbs_running, orb_host, target

BRIDGE_ID = "esp-claw"
TITLE = "ESP-Claw"
APP_ID = "esp-claw"


def installed() -> bool | None:
    """ESP-Claw is configured through the device's own web UI, no host tool."""
    return None


def evaluate(lan_orbs: list[dict], agent_buddy: list[dict]) -> BridgeResult:
    """Evaluate ESP-Claw bridges from discovered LAN orbs.

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
                    f"Open http://{host}/ in a browser to configure it",
                )
            )
    targets_tuple = tuple(targets)
    if targets:
        instruction = "Open the device web UI shown for your Orb."
    else:
        instruction = "No Orb running ESP-Claw found on the LAN."
    return BridgeResult(
        bridge_id=BRIDGE_ID,
        title=TITLE,
        installed=installed(),
        targets=targets_tuple,
        instruction=instruction,
    )