"""Browser cookie-DB key derivation.

The cookie database is SQLCipher-encrypted with a 256-bit key. The key
must be reproducible for the same (password, user_salt) pair so the
same user can reopen the DB across sessions, but it must never be
stored on disk in plaintext.

We use Argon2id (RFC 9106 winner) with parameters tuned for the host
machine: time_cost=3, memory_cost=64 MiB, parallelism=4. These are the
OWASP-recommended baseline for password-derived keys as of 2024 and
take ~150ms on a Pi 5, which is fine because key derivation runs once
per session unlock, not per request.
"""
from __future__ import annotations

from argon2.low_level import Type, hash_secret_raw

# OWASP baseline (2024) for Argon2id password-derived keys.
_TIME_COST = 3
_MEMORY_COST = 64 * 1024  # 64 MiB
_PARALLELISM = 4
_KEY_BYTES = 32           # 256 bits

# Per-user salt is generated at user creation and persisted alongside
# the user record. We require >= 16 bytes per RFC 9106.
_MIN_SALT_BYTES = 16


def derive_cookie_key(password: str, user_salt: bytes) -> str:
    """Derive a 256-bit SQLCipher key as a 64-char hex string.

    The hex form is what SQLCipher's `PRAGMA key = "x'…'"` expects.
    """
    if not password:
        raise ValueError("password must be a non-empty string")
    if len(user_salt) < _MIN_SALT_BYTES:
        raise ValueError(f"user_salt must be at least {_MIN_SALT_BYTES} bytes")

    raw = hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=user_salt,
        time_cost=_TIME_COST,
        memory_cost=_MEMORY_COST,
        parallelism=_PARALLELISM,
        hash_len=_KEY_BYTES,
        type=Type.ID,
    )
    return raw.hex()
