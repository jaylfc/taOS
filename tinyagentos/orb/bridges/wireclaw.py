"""WireClaw bridge adapter.

Reference adapter for the WireClaw web configuration interface.
WireClaw has no host-side tool; installed() returns bool based on nats CLI availability.
"""

from . import BridgeResult, orbs_running, orb_host, target
import shutil

BRIDGE_ID = "wireclaw"
TITLE = "WireClaw"
APP_ID = "wireclaw"


def installed() -> bool:
    """Best effort for the host running the core, spec s5.1."""
    return shutil.which("nats") is not None


def evaluate(lan_orbs: list[dict], agent_buddy: list[dict]) -> BridgeResult:
    """Evaluate WireClaw bridges from discovered LAN orbs.

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
                    f"Open http://{host}/ to configure it",
                )
            )
    targets_tuple = tuple(targets)
    if targets:
        instruction = "Open the device page shown. For wc.sh, set WIRECLAW_NATS_URL to your NATS server (it runs on your LAN, not on the Orb)."
    else:
        instruction = "No Orb running WireClaw found on the LAN."
    return BridgeResult(
        bridge_id=BRIDGE_ID,
        title=TITLE,
        installed=installed(),
        targets=targets_tuple,
        instruction=instruction,
    )
