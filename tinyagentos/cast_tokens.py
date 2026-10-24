"""Cast tokens: the only credential a TV ever presents to taOS.

A Chromecast or Apple TV fetches cast media ITSELF. It carries no taOS
session, cookie or bearer, so the media route (``/cast/media/``) is outside the
session gate and is gated solely by a cast token. That makes this module an
authentication boundary. The token is:

* a separate random secret (``tcast_`` + 32 random bytes), never a session
  token or an agent registry JWT, and accepted by nothing but the cast media
  route;
* stored only as a SHA-256 hash, so a dump of the store yields nothing usable;
* short-lived (``ttl_seconds``, minutes) and refreshed by the cast owner while
  the cast is active. An expired token cannot be revived: start a new cast;
* scoped to ONE view (e.g. ``canvas:<project_id>``), never a general session;
* bound to the target device id, and to its LAN address when the caller knows
  it;
* revoked on cast-end (``revoke``) AND, separately, when the device unpairs
  (``revoke_for_device``, the hook the paired-device registry's unpair calls).

State is in memory on purpose: a restart revokes every cast, which fails
closed. Casts are minutes long and the sender re-mints on restart.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

CAST_TOKEN_PREFIX = "tcast_"
DEFAULT_TTL_SECONDS = 300
_TOKEN_IN_TEXT = re.compile(re.escape(CAST_TOKEN_PREFIX) + r"[A-Za-z0-9_-]+")


def hash_cast_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class CastGrant:
    cast_id: str
    user_id: str
    view_kind: str
    view_id: str
    device_id: str
    device_address: str | None
    mode: str
    expires_at: float
    revoked: bool = False

    @property
    def view(self) -> str:
        return f"{self.view_kind}:{self.view_id}"


class CastTokenStore:
    """In-memory, hash-keyed store of active cast grants."""

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._by_hash: dict[str, CastGrant] = {}
        self._hash_by_cast: dict[str, str] = {}
        self._lock = threading.Lock()

    def mint(
        self,
        *,
        user_id: str,
        view_kind: str,
        view_id: str,
        device_id: str,
        device_address: str | None = None,
        mode: str = "still",
    ) -> tuple[str, CastGrant]:
        """Create a cast grant; return ``(plaintext_token, grant)``.

        The plaintext token is returned exactly once and never stored.
        """
        if not user_id or not view_kind or not view_id or not device_id:
            raise ValueError("user_id, view and device_id are required")
        token = CAST_TOKEN_PREFIX + secrets.token_urlsafe(32)
        grant = CastGrant(
            cast_id=uuid.uuid4().hex,
            user_id=user_id,
            view_kind=view_kind,
            view_id=view_id,
            device_id=device_id,
            device_address=device_address or None,
            mode=mode,
            expires_at=self.clock() + self.ttl_seconds,
        )
        h = hash_cast_token(token)
        with self._lock:
            self._prune_locked()
            self._by_hash[h] = grant
            self._hash_by_cast[grant.cast_id] = h
        return token, grant

    def resolve(self, token: str | None) -> CastGrant | None:
        """Return the live grant for *token*, or None if unknown/expired/revoked."""
        if not token or not token.startswith(CAST_TOKEN_PREFIX):
            return None
        h = hash_cast_token(token)
        with self._lock:
            grant = self._by_hash.get(h)
            if grant is None or grant.revoked:
                return None
            if self.clock() >= grant.expires_at:
                return None
            return grant

    def get(self, cast_id: str) -> CastGrant | None:
        with self._lock:
            h = self._hash_by_cast.get(cast_id)
            return self._by_hash.get(h) if h else None

    def is_active(self, grant: CastGrant) -> bool:
        return not grant.revoked and self.clock() < grant.expires_at

    def refresh(self, cast_id: str) -> CastGrant | None:
        """Extend an ACTIVE cast by one TTL. None if unknown, ended or expired."""
        with self._lock:
            h = self._hash_by_cast.get(cast_id)
            grant = self._by_hash.get(h) if h else None
            if grant is None or grant.revoked or self.clock() >= grant.expires_at:
                return None
            grant.expires_at = self.clock() + self.ttl_seconds
            return grant

    def revoke(self, cast_id: str) -> bool:
        """Cast-end: revoke one cast. True if it existed."""
        with self._lock:
            h = self._hash_by_cast.pop(cast_id, None)
            grant = self._by_hash.pop(h, None) if h else None
            if grant is None:
                return False
            grant.revoked = True
            return True

    def revoke_for_device(self, device_id: str) -> int:
        """Unpair hook: revoke every cast bound to *device_id*. Returns the count.

        The paired-device registry's unpair path MUST call this; cast-end
        revocation alone does not cover a device that is unpaired mid-cast.
        """
        with self._lock:
            doomed = [
                h for h, g in self._by_hash.items() if g.device_id == device_id
            ]
            for h in doomed:
                grant = self._by_hash.pop(h)
                grant.revoked = True
                self._hash_by_cast.pop(grant.cast_id, None)
            return len(doomed)

    def _prune_locked(self) -> None:
        now = self.clock()
        stale = [h for h, g in self._by_hash.items() if now >= g.expires_at]
        for h in stale:
            grant = self._by_hash.pop(h)
            self._hash_by_cast.pop(grant.cast_id, None)


def _redact(value):
    if isinstance(value, str):
        return _TOKEN_IN_TEXT.sub(CAST_TOKEN_PREFIX + "[redacted]", value)
    return value


class CastTokenLogRedactor(logging.Filter):
    """Strip cast tokens from log records before any handler formats them.

    A TV cannot send headers, so the token rides in ``?token=`` and uvicorn's
    access log would otherwise record it with the request line. A refreshed
    token stays live, so a logged one is a live media credential.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact(a) for a in record.args)
        return True


_ACCESS_LOG_REDACTOR = CastTokenLogRedactor()


def install_access_log_redaction() -> None:
    """Attach the redactor to uvicorn's access logger (idempotent).

    A logger-level filter survives uvicorn's own dictConfig, which replaces
    handlers but never removes filters it did not configure.
    """
    logging.getLogger("uvicorn.access").addFilter(_ACCESS_LOG_REDACTOR)
