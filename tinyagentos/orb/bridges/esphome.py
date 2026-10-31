"""ESPHome bridge adapter.

Reference adapter for the ESPHome logs interface.
ESPHome ships a host-side tool; installed() checks shutil.which.
"""

import shutil

from . import BridgeResult, orbs_running, orb_host, target

BRIDGE_ID = "esphome"
TITLE = "ESPHome"
APP_ID = "esphome"


def installed() -> bool | None:
    """Best effort for the host running the core, spec s5.1."""
    return shutil.which("esphome") is not None


def evaluate(lan_orbs: list[dict], agent_buddy: list[dict]) -> BridgeResult:
    """Evaluate ESPHome bridges from discovered LAN orbs.

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
                    f"esphome logs <your-config>.yaml --device {host}",
                )
            )
    targets_tuple = tuple(targets)
    if targets:
        instruction = "Run the command shown with your ESPHome config file."
        if not installed():
            instruction += " Install ESPHome first: pip install esphome."
    else:
        instruction = "No Orb running ESPHome found on the LAN."
    return BridgeResult(
        bridge_id=BRIDGE_ID,
        title=TITLE,
        installed=installed(),
        targets=targets_tuple,
        instruction=instruction,
    )
