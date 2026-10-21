"""Sign-in broker store (tsk-diqs7d).

An agent that has to sign in to a Claude subscription (``claude setup-token``
prints a URL and waits for a pasted code) posts the URL here. The user opens it
from a taOS sign-in card, pastes the code back into the card, and the agent
reads that code ONCE. When the agent has its long-lived token it hands it to
the broker, which stores it in Secrets scoped to that agent only.

No callback ever has to reach the agent, so the same flow works for the root
taOS Agent on the host and for an agent inside a container.

Lifecycle: ``pending`` -> ``code_ready`` (user pasted the code) -> ``code_read``
(the agent took it; the code is cleared) -> ``completed`` (token stored).
A request expires ``EXPIRY_SECONDS`` after it was created; once the agent has
read the code it has another ``EXPIRY_SECONDS`` to post the token.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from urllib.parse import urlsplit

import aiosqlite

from tinyagentos.base_store import BaseStore

EXPIRY_SECONDS = 600

# A sign-in card opens this URL in the user's browser, so the agent must never
# choose the site: exact https hosts only (no subdomains, no userinfo, no port).
ALLOWED_SIGNIN_HOSTS = frozenset({"claude.ai", "console.anthropic.com"})

KIND_CLAUDE_SUBSCRIPTION = "claude-subscription"
KINDS = frozenset({KIND_CLAUDE_SUBSCRIPTION})

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_broker_requests (
    id TEXT PRIMARY KEY,
    requester TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    code TEXT,
    created_at REAL NOT NULL,
    code_set_at REAL,
    code_read_at REAL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_auth_broker_requester ON auth_broker_requests(requester);
"""


def _now() -> float:
    return time.time()


def signin_host(url: str) -> str | None:
    """Return the host of *url* when it is an allowed sign-in URL, else None."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password:
        return None
    if port not in (None, 443):
        return None
    host = (parts.hostname or "").lower()
    return host if host in ALLOWED_SIGNIN_HOSTS else None


def _new_id() -> str:
    return "sib-" + "".join(secrets.choice(_ALPHABET) for _ in range(12))


def _row(row: aiosqlite.Row) -> dict:
    return {k: row[k] for k in row.keys()}


class AuthBrokerStore(BaseStore):
    SCHEMA = SCHEMA

    _lock: asyncio.Lock

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row
        self._lock = asyncio.Lock()

    def _require_db(self) -> None:
        if self._db is None:
            raise RuntimeError("AuthBrokerStore not initialised: call init() first")

    @staticmethod
    def expired(rec: dict, now: float | None = None) -> bool:
        """True when *rec* can no longer progress."""
        now = _now() if now is None else now
        if rec["status"] == "completed":
            return False
        if rec["status"] == "code_read":
            return now - (rec["code_read_at"] or 0) > EXPIRY_SECONDS
        return now - rec["created_at"] > EXPIRY_SECONDS

    async def create(self, *, requester: str, kind: str, url: str, prompt: str = "") -> dict:
        self._require_db()
        if kind not in KINDS:
            raise ValueError(f"unknown kind: {kind}")
        if signin_host(url) is None:
            raise ValueError("url is not an allowed sign-in url")
        rec = {
            "id": _new_id(),
            "requester": requester,
            "kind": kind,
            "url": url,
            "prompt": prompt,
            "status": "pending",
            "created_at": _now(),
        }
        async with self._lock:
            await self._db.execute(
                "INSERT INTO auth_broker_requests (id, requester, kind, url, prompt, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rec["id"], requester, kind, url, prompt, "pending", rec["created_at"]),
            )
            await self._db.commit()
        return await self.get(rec["id"])

    async def get(self, request_id: str) -> dict | None:
        self._require_db()
        async with self._db.execute(
            "SELECT * FROM auth_broker_requests WHERE id = ?", (request_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row(row) if row else None

    async def set_code(self, request_id: str, code: str) -> str:
        """Store the user's pasted code. Returns ``ok``, ``missing``,
        ``expired`` or ``conflict`` (a code was already given)."""
        self._require_db()
        async with self._lock:
            rec = await self.get(request_id)
            if rec is None:
                return "missing"
            if self.expired(rec):
                return "expired"
            if rec["status"] != "pending":
                return "conflict"
            await self._db.execute(
                "UPDATE auth_broker_requests SET status = 'code_ready', code = ?, code_set_at = ?"
                " WHERE id = ? AND status = 'pending'",
                (code, _now(), request_id),
            )
            await self._db.commit()
        return "ok"

    async def take_code(self, request_id: str) -> tuple[dict, str | None]:
        """Return (record, code). The code is returned once, then cleared."""
        self._require_db()
        async with self._lock:
            rec = await self.get(request_id)
            if rec is None or rec["status"] != "code_ready" or self.expired(rec):
                return rec, None
            code = rec["code"]
            await self._db.execute(
                "UPDATE auth_broker_requests SET status = 'code_read', code = NULL, code_read_at = ?"
                " WHERE id = ? AND status = 'code_ready'",
                (_now(), request_id),
            )
            await self._db.commit()
            return await self.get(request_id), code

    async def complete(self, request_id: str) -> str:
        """Mark the request completed. Returns ``ok``, ``missing``, ``expired``
        or ``conflict`` (the code was never read, or a token was already stored)."""
        self._require_db()
        async with self._lock:
            rec = await self.get(request_id)
            if rec is None:
                return "missing"
            if rec["status"] != "code_read":
                return "conflict"
            if self.expired(rec):
                return "expired"
            await self._db.execute(
                "UPDATE auth_broker_requests SET status = 'completed', completed_at = ?"
                " WHERE id = ? AND status = 'code_read'",
                (_now(), request_id),
            )
            await self._db.commit()
        return "ok"

    async def release(self, request_id: str) -> None:
        """Hand a claimed request back (``completed`` -> ``code_read``) after the
        secret write failed, so the agent can post its token again."""
        self._require_db()
        async with self._lock:
            await self._db.execute(
                "UPDATE auth_broker_requests SET status = 'code_read', completed_at = NULL"
                " WHERE id = ? AND status = 'completed'",
                (request_id,),
            )
            await self._db.commit()
