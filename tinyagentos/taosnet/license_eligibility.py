"""Decide whether a model's weights may be redistributed over taOSnet.

taOSnet seeds model weights peer-to-peer. We may only do that for models whose
licence permits redistribution. This module is the single source of truth for
that decision; the catalog-publish CLI calls it to set the manifest's
``license_allows_redistribution`` flag, which the download client
(``should_use_torrent``) requires before it will touch the swarm.

Policy (conservative by default):

1. If the licence carries any restrictive marker (non-commercial, research-only,
   commercial-requires-agreement, gated, S-Lab), it is NOT redistributable.
2. Otherwise, if the licence exactly matches a known-redistributable licence
   (permissive, plus the RAIL / Gemma / Llama-community family that permit
   redistribution with their terms attached), it IS redistributable.
3. Otherwise it is NOT redistributable, and the caller should surface it for a
   human to review and, if appropriate, add to ``PERMISSIVE_LICENSES``.

Missing a genuinely-redistributable model only costs us an HTTP-only download
(no harm). Wrongly redistributing a restricted one is a legal problem. So the
default is always False.
"""
from __future__ import annotations

# Substrings (matched against the normalised licence) that force a False result,
# checked BEFORE the allow-list so a dual or annotated licence cannot slip through.
_RESTRICTIVE_MARKERS: tuple[str, ...] = (
    "non-commercial",
    "noncommercial",
    "by-nc",        # CC BY-NC / CC-BY-NC-SA
    "-nc-",         # sai-nc-community, stable-cascade-nc-community
    "nc-community",
    "research",     # Qwen Research License
    "commercial requires",
    "requires agreement",
    "s-lab",        # S-Lab License: research/non-commercial only
    "gated",
)

# Licences that permit redistribution. Normalised (lower-case, quotes stripped,
# whitespace collapsed, any trailing "(...)" note removed). Matched exactly, so a
# dual licence like "Apache-2.0 / MiniCPM Model License" does NOT match "apache-2.0".
PERMISSIVE_LICENSES: frozenset[str] = frozenset(
    {
        # Permissive / public
        "apache-2.0",
        "mit",
        "bsd-3-clause",
        "cc-by-4.0",
        # RAIL family: redistribution permitted, use-restrictions ride along
        "openrail",
        "openrail++",
        "openrail-m",
        "creativeml openrail-m",
        # Vendor community licences that permit redistribution with their terms
        "gemma",
        "gemma terms of use",
        "llama 3.1 community license",
        "llama 3.3 community license",
    }
)


def normalize_license(raw: str) -> str:
    """Lower-case, strip surrounding quotes, drop a trailing ``(...)`` note, and
    collapse internal whitespace, so catalog licence strings compare stably."""
    text = raw.strip().strip('"').strip("'").lower()
    # Drop a single trailing parenthetical note, e.g. "openrail++ (commercial use allowed)".
    if text.endswith(")") and "(" in text:
        text = text[: text.rindex("(")].strip()
    return " ".join(text.split())


def license_allows_redistribution(raw: str | None) -> bool:
    """True only when the licence is known to permit redistributing the weights.

    Conservative: an unknown, empty, or restrictively-marked licence returns
    False. See module docstring for the policy.
    """
    if not raw or not raw.strip():
        return False
    normalized = normalize_license(raw)
    if any(marker in normalized for marker in _RESTRICTIVE_MARKERS):
        return False
    return normalized in PERMISSIVE_LICENSES


def classify_manifest(manifest: dict) -> bool:
    """Convenience wrapper: read ``manifest['license']`` and classify it."""
    return license_allows_redistribution(manifest.get("license"))
