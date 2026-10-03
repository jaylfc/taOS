"""Named scopes for device bearer tokens (taosdev_...).

Every device-bearer route requires exactly one named scope. A device row stores
its scopes as a sorted, space-separated string in ``devices.scopes``; a NULL
column is the pre-scopes marker and is resolved by ``effective_scopes`` (never
read as "all" and never as "none" for the legacy platforms).
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

AGENTS_READ = "agents:read"
DECISIONS_ANSWER = "decisions:answer"
CHAT_SEND = "chat:send"
AGENTS_CONTROL = "agents:control"
VOICE_STT = "voice:stt"
VOICE_TTS = "voice:tts"
LIBRARY_INGEST = "library:ingest"
FILES_UPLOAD = "files:upload"
PUSH_REGISTER = "push:register"

ALL_SCOPES = frozenset({
    AGENTS_READ, DECISIONS_ANSWER, CHAT_SEND, AGENTS_CONTROL,
    VOICE_STT, VOICE_TTS, LIBRARY_INGEST, FILES_UPLOAD, PUSH_REGISTER,
})

# Platforms whose tokens shipped before scopes existed. A NULL scope list on
# one of these means the legacy set; on any other platform it means nothing.
LEGACY_PLATFORMS = frozenset({"ios", "watchos", "android"})

# Spelled out literally: it must not be derived from ALL_SCOPES, so a scope
# added later is NOT silently granted to every pre-S1 token.
LEGACY_SCOPES = frozenset({
    "push:register", "agents:read", "decisions:answer",
    "library:ingest", "files:upload", "chat:send",
})

# Embedded (ESP32 class) devices: glance + answer by default.
EMBEDDED_DEFAULT = frozenset({"agents:read", "decisions:answer"})
# Never grantable to embedded, whatever is stored.
EMBEDDED_NEVER = frozenset({"library:ingest", "files:upload", "push:register"})
# "Talk": granted together, never by default.
TALK = frozenset({"voice:stt", "voice:tts", "chat:send"})

_DEFAULT_TLS_PORT = 6974


def device_tls_port() -> int:
    """Port of the TLS-only device listener (TAOS_DEVICE_TLS_PORT, default 6974)."""
    raw = os.environ.get("TAOS_DEVICE_TLS_PORT", "")
    try:
        port = int(raw) if raw.strip() else _DEFAULT_TLS_PORT
    except ValueError:
        return _DEFAULT_TLS_PORT
    return port if 0 < port < 65536 else _DEFAULT_TLS_PORT


def default_scopes(platform: str) -> frozenset[str]:
    if platform in ("ios", "watchos", "android", "wearos"):
        return LEGACY_SCOPES
    if platform == "embedded":
        return EMBEDDED_DEFAULT
    return frozenset()


def serialize_scopes(scopes: Iterable[str]) -> str:
    """Validate and serialise to the stored form (sorted, space-separated).

    Raises ValueError on an unknown scope name. An empty set serialises to ''
    (an explicit "no scopes"; NULL is reserved for the legacy marker).
    """
    if isinstance(scopes, str):
        raise ValueError("scopes must be an iterable of scope names, not a string")
    names = set(scopes)
    unknown = names - ALL_SCOPES
    if unknown:
        raise ValueError(f"unknown scope(s): {sorted(unknown)}")
    return " ".join(sorted(names))


def effective_scopes(device: Mapping) -> frozenset[str]:
    """The scopes a device row actually holds. Fails closed."""
    platform = device.get("platform")
    stored = device.get("scopes")
    if stored is None:
        result = LEGACY_SCOPES if platform in LEGACY_PLATFORMS else frozenset()
    else:
        result = frozenset(str(stored).split()) & ALL_SCOPES
    if platform == "embedded":
        result = result - EMBEDDED_NEVER
    return frozenset(result)
