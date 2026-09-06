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


# One-line, human, non-technical description per capability, for the consent
# card (mirrors the agent SCOPE_DESCRIPTIONS). Every entry in KNOWN_CAPS has
# one; the test_capabilities suite enforces full coverage. Copy is a draft to
# be reworded by product.
CAPABILITY_DESCRIPTIONS: dict[str, str] = {
    "app.kv": "Store and read its own app data",
    "app.table": "Store and read its own structured data",
    "app.files": "Read and write files in its own app folder",
    "app.notify": "Send you notifications",
    "app.window": "Open and manage its own windows",
    "app.net": "Connect to the internet",
    "app.agent": "Ask your taOS agent for help",
    "app.llm": "Use a language model",
    "app.memory": "Read and write your memories",
}


def describe_capability(perm: str) -> str:
    """A human one-liner for a capability, for the consent card.

    Bare caps map through CAPABILITY_DESCRIPTIONS; a `network:<origin>` grant
    renders as "Connect to <origin>"; anything unrecognised falls back to the
    raw token so the card never shows an empty row.
    """
    if isinstance(perm, str) and perm.startswith(NET_PREFIX):
        return f"Connect to {perm[len(NET_PREFIX):]}"
    return CAPABILITY_DESCRIPTIONS.get(perm, perm)


# ---------------------------------------------------------------------------
# Provenance tiers -- where an installed app's code came from.
# ---------------------------------------------------------------------------
#
# Every app is classified into exactly one tier. The tier is the CEILING: the
# set of capabilities the app holds automatically, before the user has made
# any decision. Anything in KNOWN_CAPS not on a tier's ceiling still exists
# and can be granted through the existing consent flow (routes/app_permissions.py,
# the app_grant Decision, routes/userspace_apps.py's /permissions endpoint) --
# a tier never blocks a capability outright, it only decides whether holding
# it requires an explicit user grant. This is the single source of truth for
# the provenance -> capability model; the broker and the app_permissions API
# both read PROVENANCE_CEILINGS rather than hard-coding a tier's defaults.
PROVENANCE_TIERS = ("first-party", "ai-generated", "user-uploaded", "unknown")

# Fallback for an app whose provenance was never recorded or could not be
# classified -- the most restricted tier, so an unclassified app never
# accidentally inherits a permissive default.
DEFAULT_PROVENANCE = "unknown"

# The default (no-consent-required) capability ceiling per tier:
#
#  - first-party: built-in / signed taOS apps. Full trust -- every known
#    capability, matching the pre-existing trust="first-party" broker bypass.
#  - ai-generated: authored by an agent in App Studio. No network, no
#    storage, no cross-app data by default -- only the inert UI-local caps
#    (notify/window, no data leaves the app) are free. Everything else
#    (app.kv/app.table/app.files/app.net/app.agent/app.llm/app.memory) must
#    be explicitly requested and granted.
#  - user-uploaded: imported/side-loaded by the user. Same floor as
#    ai-generated -- imported code is exactly as untrusted as agent-authored
#    code, regardless of who clicked "install".
#  - unknown: default fallback for anything not classified. The most
#    restricted tier -- nothing is free, not even notify/window.
PROVENANCE_CEILINGS: dict[str, frozenset[str]] = {
    "first-party": KNOWN_CAPS,
    "ai-generated": frozenset({"app.notify", "app.window"}),
    "user-uploaded": frozenset({"app.notify", "app.window"}),
    "unknown": frozenset(),
}

# One-line description per tier, for the app_permissions API / UI badge.
PROVENANCE_DESCRIPTIONS: dict[str, str] = {
    "first-party": "Built into taOS",
    "ai-generated": "Built by an agent in App Studio",
    "user-uploaded": "Imported by you",
    "unknown": "Origin not verified",
}


def is_known_provenance(value: object) -> bool:
    """True if `value` is one of the four classified provenance tiers."""
    return isinstance(value, str) and value in PROVENANCE_TIERS


def capability_ceiling(provenance: str) -> frozenset[str]:
    """The tier's default (no-consent) capability ceiling.

    Falls back to DEFAULT_PROVENANCE's ceiling for an unrecognised value, so a
    typo'd or legacy provenance never resolves to an undefined (and possibly
    permissive) ceiling.
    """
    return PROVENANCE_CEILINGS.get(provenance, PROVENANCE_CEILINGS[DEFAULT_PROVENANCE])


def default_provenance_for_trust(trust: str | None) -> str:
    """Back-compat mapping from the legacy binary `trust` field to a tier.

    Used to classify rows written before the provenance column existed:
    trust="first-party" -> "first-party" (unchanged meaning); anything else
    (trust="community", or missing) -> "user-uploaded", since every app that
    reaches the userspace store today arrives either through the public
    install endpoint (side-loaded by the user) or boot-seeding (first-party).
    """
    return "first-party" if trust == "first-party" else "user-uploaded"


def capability_allowed(provenance: str, capability: str, granted: object) -> bool:
    """True if `capability` is available to an app of this provenance.

    An app can never exceed its tier's ceiling without an explicit grant: a
    capability namespace within the tier's ceiling is always available; one
    outside it is only available if it (or its namespace) appears in
    `granted`, the app's explicit grants (declared manifest permissions plus
    whatever the user has approved through the consent flow).
    """
    ns = _namespace(capability)
    if ns in capability_ceiling(provenance):
        return True
    granted_set = set(granted)
    return ns in granted_set or capability in granted_set


def _namespace(capability: str) -> str:
    """The `a.b` namespace of a capability, e.g. `app.kv.get` -> `app.kv`.

    Mirrors tinyagentos.userspace.broker._namespace; duplicated here (rather
    than imported) because broker.py imports FREE_CAPS/GATED_CAPS from this
    module, and this module must stay free of a reverse dependency on it.
    """
    if not isinstance(capability, str):
        return capability
    parts = capability.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else capability
