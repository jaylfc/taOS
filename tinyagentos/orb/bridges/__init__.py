"""Orb bridge adapters.

Adapters are PURE. They take the lists that
`tinyagentos.orb.discovery.browse_lan_orbs()` and `probe_agent_buddy()`
already return and never call discovery, the network or a subprocess.

Shapes (from tinyagentos/orb/discovery.py on dev):
- LAN orb: {"name": str, "addresses": list[str], "port": int, "board": str|None, "app": str|None, "version": str|None}
- Agent Buddy reply: {"device_id": str, "hostname": str, "address": str, "port": int, "boot": int}
"""

import importlib
import logging
import pkgutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BridgeResult:
    bridge_id: str
    title: str
    installed: bool | None
    targets: tuple[dict, ...]
    instruction: str

    def to_dict(self) -> dict:
        return {
            "bridge_id": self.bridge_id,
            "title": self.title,
            "installed": self.installed,
            "targets": [dict(t) for t in self.targets],
            "instruction": self.instruction,
        }


def orbs_running(lan_orbs: list[dict], app_id: str) -> list[dict]:
    """Return orbs whose .get('app') == app_id, keeping input order."""
    return [orb for orb in lan_orbs if orb.get("app") == app_id]


def orb_host(orb: dict) -> str | None:
    """Extract a host address from an orb dict.

    Priority:
    1. First IPv4 address (no colon) from addresses
    2. First IPv6 address (contains colon) wrapped in brackets
    3. Fallback to {name}.local if name is non-empty
    4. None otherwise
    """
    addresses = orb.get("addresses") or []
    for addr in addresses:
        if ":" not in addr:
            return addr
    for addr in addresses:
        if ":" in addr:
            return f"[{addr}]"
    name = orb.get("name")
    if isinstance(name, str) and name:
        return f"{name}.local"
    return None


def target(orb_name: str, address: str, instruction: str) -> dict:
    """Build a target dict with fixed shape."""
    return {"orb": orb_name, "address": address, "instruction": instruction}


def adapters() -> list:
    """Discover and load bridge adapter modules.

    Iterates pkgutil.iter_modules(__path__), skips names starting with '_',
    imports each module, and validates it has BRIDGE_ID (str), TITLE (str),
    and a callable evaluate. Returns modules sorted by BRIDGE_ID.
    """
    modules = []
    for module_info in pkgutil.iter_modules(__path__):
        name = module_info.name
        if name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{name}")
        if not hasattr(mod, "BRIDGE_ID") or not hasattr(mod, "TITLE") or not hasattr(mod, "evaluate"):
            raise TypeError(f"bridge adapter {name} missing BRIDGE_ID/TITLE/evaluate")
        if not callable(mod.evaluate):
            raise TypeError(f"bridge adapter {name} missing BRIDGE_ID/TITLE/evaluate")
        modules.append(mod)
    modules.sort(key=lambda m: m.BRIDGE_ID)
    return modules


def evaluate_all(lan_orbs: list[dict], agent_buddy: list[dict]) -> list[dict]:
    """Evaluate all bridge adapters.

    For each adapter, calls mod.evaluate(lan_orbs, agent_buddy).to_dict().
    If an exception is raised, logs it and returns an error dict instead.
    One broken adapter must not hide the others.
    """
    results = []
    for mod in adapters():
        try:
            results.append(mod.evaluate(lan_orbs, agent_buddy).to_dict())
        except Exception:
            logger.exception("bridge adapter %s failed", mod.BRIDGE_ID)
            results.append({
                "bridge_id": mod.BRIDGE_ID,
                "title": mod.TITLE,
                "error": "adapter_failed",
            })
    return results