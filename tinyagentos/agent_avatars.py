from __future__ import annotations

import hashlib
import os
from pathlib import Path

#: Where lock-screen agent avatars are read from. One flat directory of
#: "<slug>.jpg" files, slug being the agent name lowercased with non-alphanumerics
#: collapsed to "-". Overridable so a packaged install can point it at its own
#: data dir rather than this default.
LOCK_AVATAR_DIR = os.environ.get("TAOS_LOCK_AVATAR_DIR", "/var/lib/taos/lock-avatars")


def _avatar_slug(name: str) -> str:
    """Slug for an agent name, restricted to characters that cannot traverse.

    Anything outside [a-z0-9-] is dropped rather than escaped: this value is
    used to build a filesystem path, so a conservative whitelist is the control
    that keeps "../" and absolute paths out, not a sanitiser that tries to spot
    bad input.
    """
    out = []
    for ch in name.strip().lower():
        if ch.isalnum() and ch.isascii():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def avatar_source_path(name: str) -> Path | None:
    """Path to the avatar image file for *name*, or None if it is not installed."""
    path = Path(LOCK_AVATAR_DIR) / f"{_avatar_slug(name)}.jpg"
    return path if path.is_file() else None


def avatar_hash(name: str) -> str | None:
    """SHA-256 hex of the avatar image bytes, first 16 chars, or None."""
    path = avatar_source_path(name)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()[:16]
