from __future__ import annotations

# All valid device scopes. The order here is canonical for serialization.
_ALL_SCOPES = (
    "agents:read",
    "decisions:answer",
    "chat:send",
    "agents:control",
    "voice",
    "library:ingest",
    "files:upload",
    "push:register",
)

ALL_SCOPES = frozenset(_ALL_SCOPES)

# Legacy scope set granted to all pre-S1 tokens (NULL scopes column).
# LEGACY_SCOPES grants existing phone tokens agents:read, which will also
# admit them to the device/v1 state/events routes that later cards add.
# taOSc asked for this widening.
LEGACY_SCOPES = frozenset({
    "push:register",
    "agents:read",
    "decisions:answer",
    "library:ingest",
    "files:upload",
    "chat:send",
})

# Default scopes for new embedded platform registrations.
EMBEDDED_DEFAULT_SCOPES = frozenset({
    "agents:read",
    "decisions:answer",
})

# Default scopes for new ios/android/watchos/wearos registrations (legacy set).
MOBILE_DEFAULT_SCOPES = LEGACY_SCOPES


def normalize_scopes(scopes: str | None) -> frozenset[str]:
    """Parse a comma-separated scopes string into a validated frozenset.

    NULL/empty input returns LEGACY_SCOPES (never empty, never "all scopes").
    Unknown scopes are ignored (strict validation happens at write time).
    """
    if not scopes:
        return LEGACY_SCOPES
    return frozenset(s.strip() for s in scopes.split(",") if s.strip() in ALL_SCOPES)


def scopes_to_string(scopes: frozenset[str]) -> str:
    """Serialize a frozenset of scopes to the canonical comma-separated string."""
    return ",".join(sorted(scopes, key=_ALL_SCOPES.index))


def validate_scopes(scopes: frozenset[str]) -> None:
    """Raise ValueError if any scope is not in ALL_SCOPES."""
    invalid = scopes - ALL_SCOPES
    if invalid:
        raise ValueError(f"invalid scopes: {sorted(invalid)}")