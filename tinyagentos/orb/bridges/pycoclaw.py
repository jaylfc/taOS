"""PycoClaw bridge adapter.

Reference adapter for the PycoClaw WebREPL interface.
The host tool is the Scripto Studio PWA in a browser, nothing on PATH.
"""

from . import BridgeResult, orbs_running, orb_host, target

BRIDGE_ID = "pycoclaw"
TITLE = "PycoClaw"
APP_ID = "pycoclaw"


def installed() -> bool | None:
    """The host tool is the Scripto Studio PWA in a browser, nothing on PATH."""
    return None


def evaluate(lan_orbs: list[dict], agent_buddy: list[dict]) -> BridgeResult:
    """Evaluate PycoClaw bridges from discovered LAN orbs.

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
                    f"In Scripto Studio, enter the device URL https://{host}/webrepl",
                )
            )
    targets_tuple = tuple(targets)
    if targets:
        instruction = "Enter the URL shown in Scripto Studio (scriptostudio.com/app)."
    else:
        instruction = "No Orb running PycoClaw found on the LAN."
    return BridgeResult(
        bridge_id=BRIDGE_ID,
        title=TITLE,
        installed=installed(),
        targets=targets_tuple,
        instruction=instruction,
    )
