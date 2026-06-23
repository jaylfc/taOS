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

import re

# Granted to every app without consent.
FREE_CAPS = frozenset({"app.kv", "app.table", "app.files", "app.notify", "app.window"})
# Require an explicit granted permission (runtime consent via the Decisions flow).
GATED_CAPS = frozenset({"app.net", "app.agent", "app.llm", "app.memory"})
# The whole closed set of bare capability namespaces.
KNOWN_CAPS = FREE_CAPS | GATED_CAPS

# Parametrized capability prefix: `network:<origin>` allowlists a single origin.
NET_PREFIX = "network:"

# Strict origin format for a `network:<origin>` grant: scheme://host with an
# optional leading "*." subdomain wildcard and an optional :port, nothing else
# (no spaces, semicolons, quotes, paths, or newlines) so it can never inject
# extra sandbox CSP directives. \A and \Z anchor the WHOLE string. This is the
# single source of truth, reused by the package parser and the grant API.
NET_ORIGIN_RE = re.compile(r"\A(?:wss|https)://(?:\*\.)?[A-Za-z0-9.-]+(?::\d+)?\Z")


def is_valid_network_grant(perm: str) -> bool:
    """True if `perm` is a `network:<origin>` grant with a well-formed origin."""
    if not isinstance(perm, str) or not perm.startswith(NET_PREFIX):
        return False
    return bool(NET_ORIGIN_RE.match(perm[len(NET_PREFIX):]))


def is_known_capability(perm: str) -> bool:
    """True if `perm` is a recognised capability the vocabulary permits.

    Accepts a bare namespace in KNOWN_CAPS or a `network:<origin>` grant. This
    only classifies the capability namespace; callers that record or enforce a
    grant should additionally check is_valid_network_grant for the parametrized
    form so a malformed origin is not accepted.
    """
    if not isinstance(perm, str):
        return False
    return perm in KNOWN_CAPS or perm.startswith(NET_PREFIX)
