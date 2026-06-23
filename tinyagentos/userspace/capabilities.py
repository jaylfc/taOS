"""Closed capability vocabulary for userspace apps (#56, decision 6).

The single source of truth for which capabilities a userspace app may declare in
its manifest `permissions:` and request at runtime. The broker enforces these at
call time; the package parser validates a manifest's declared permissions against
the same set so an app can never ship a typo'd or made-up capability that only
fails later at runtime.

This is deliberately the EXISTING shipped vocabulary (the broker already gates on
FREE_CAPS / GATED_CAPS), not a new naming scheme: introducing parallel names
would break every installed app. A closed set is cheap to extend when a new
capability is added to the broker.

Forms:
- A bare capability namespace, e.g. `app.kv` (free) or `app.net` (gated).
- A parametrized `network:<origin>` grant, where <origin> is an https/wss origin
  validated separately by the package parser before it reaches the sandbox CSP.
"""
from __future__ import annotations

# Granted to every app without consent.
FREE_CAPS = frozenset({"app.kv", "app.table", "app.files", "app.notify", "app.window"})
# Require an explicit granted permission (runtime consent via the Decisions flow).
GATED_CAPS = frozenset({"app.net", "app.agent", "app.llm", "app.memory"})
# The whole closed set of bare capability namespaces.
KNOWN_CAPS = FREE_CAPS | GATED_CAPS

# Parametrized capability prefix: `network:<origin>` allowlists a single origin.
NET_PREFIX = "network:"


def is_known_capability(perm: str) -> bool:
    """True if `perm` is a recognised capability the vocabulary permits.

    Accepts a bare namespace in KNOWN_CAPS or a `network:<origin>` grant. The
    <origin> portion's format is validated by the package parser, not here; this
    only classifies whether the capability itself is one the system understands.
    """
    if not isinstance(perm, str):
        return False
    return perm in KNOWN_CAPS or perm.startswith(NET_PREFIX)
