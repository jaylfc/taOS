from __future__ import annotations

"""Agent Registry store - canonical agent-identity persistence.

Each registered agent gets a unique canonical_id minted once (immutable) and
a signed JWT-style token issued at registration time.  The signing key is an
Ed25519 keypair persisted to disk on first use (mirroring the VAPID keypair
approach in routes/desktop_browser/vapid.py).

The public key is exposed via GET /api/agents/registry/pubkey so the A2A bus
(taOSmd) can verify tokens independently without needing the private key.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id    TEXT    NOT NULL UNIQUE,
    display_name    TEXT    NOT NULL DEFAULT '',
    framework       TEXT    NOT NULL DEFAULT '',
    user_id         TEXT    NOT NULL DEFAULT '',
    origin          TEXT    NOT NULL DEFAULT 'taos-deployed',
    handle          TEXT    NOT NULL DEFAULT '',
    role            TEXT,
    capabilities    TEXT    NOT NULL DEFAULT '[]',
    created_ts      TEXT    NOT NULL,
    revoked_at      TEXT,
    status          TEXT    NOT NULL DEFAULT 'active'
);
"""

# Partial unique index: at most ONE active agent may own any given non-empty
# handle. SQLite enforces this atomically, so two concurrent consent approvals
# of the same identity_claim cannot both flip to 'active' with the same handle.
# Pending / suspended / revoked rows and empty handles are excluded from the
# index, so (a) a handle can be reused once its owner leaves 'active', and (b)
# pre-existing empty-handle active agents never block the index's creation.
#
# This index references the ``status`` column, which is added by
# _migration_v1_add_status on the migration path.  Per BaseStore's contract it
# therefore CANNOT live in SCHEMA (the executescript runs before migrations and
# would crash on a pre-status table); it is created in _post_init after the
# migration guarantees the column exists.
ACTIVE_HANDLE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_active_handle
    ON agent_registry(handle)
    WHERE status = 'active' AND handle != '';
"""

# ---------------------------------------------------------------------------
# Lifecycle state machine
# ---------------------------------------------------------------------------

VALID_STATUSES = frozenset({"pending", "active", "suspended", "revoked", "rejected"})

# Maps (from_status, to_status) → True for allowed transitions.
_VALID_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("pending",   "active"),      # approve
    ("pending",   "rejected"),    # reject
    ("active",    "suspended"),   # suspend
    ("suspended", "active"),      # reactivate
    ("active",    "revoked"),     # revoke (terminal)
    ("suspended", "revoked"),     # revoke (terminal)
    ("pending",   "revoked"),     # revoke (terminal)
    ("rejected",  "revoked"),     # revoke (terminal) - any non-terminal → revoked
    ("rejected",  "pending"),     # undo denial → re-open
    ("rejected",  "active"),      # undo denial → directly approve
})


def _assert_valid_transition(before: str, after: str) -> None:
    """Raise ValueError if the transition is not allowed."""
    if after not in VALID_STATUSES:
        raise ValueError(f"unknown status {after!r}; valid: {sorted(VALID_STATUSES)}")
    if (before, after) not in _VALID_TRANSITIONS:
        raise ValueError(
            f"invalid lifecycle transition: {before!r} → {after!r}"
        )


# ---------------------------------------------------------------------------
# Migration: add status column + backfill
# ---------------------------------------------------------------------------

async def _migration_v1_add_status(conn) -> None:
    """Add status column (idempotent) and backfill existing rows."""
    # Check if the column already exists (SQLite has no IF NOT EXISTS for ADD COLUMN
    # prior to 3.37 - use PRAGMA instead for broad compatibility).
    existing_cols = {
        row[1]
        for row in await (
            await conn.execute("PRAGMA table_info(agent_registry)")
        ).fetchall()
    }
    if "status" not in existing_cols:
        await conn.execute(
            "ALTER TABLE agent_registry ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        )
    # Backfill: rows with revoked_at set → 'revoked'; others → 'active'.
    await conn.execute(
        "UPDATE agent_registry SET status = 'revoked' WHERE revoked_at IS NOT NULL AND status = 'active'"
    )
    await conn.commit()


async def _migration_v2_strip_at_display_name(conn) -> None:
    """Strip a leading '@' from display_name for all rows (idempotent).

    The '@' sigil is bus-addressing syntax only; it must never be stored
    in display_name.  Safe to run every startup - rows without a leading
    '@' are untouched by the WHERE clause.
    """
    # '@_%' requires at least one character after '@', so bare '@'-only rows
    # are intentionally left unchanged and cannot be produced as empty strings.
    await conn.execute(
        "UPDATE agent_registry SET display_name = substr(display_name, 2) "
        "WHERE display_name LIKE '@_%'"
    )
    await conn.commit()


async def _migration_v3_add_org_fields(conn) -> None:
    """Add title + reports_to columns (idempotent) for the org model (#161).

    ``reports_to`` references another row's canonical_id (the agent's
    manager); NULL means the agent has no manager (an org-tree root).
    """
    existing_cols = {
        row[1]
        for row in await (
            await conn.execute("PRAGMA table_info(agent_registry)")
        ).fetchall()
    }
    if "title" not in existing_cols:
        await conn.execute("ALTER TABLE agent_registry ADD COLUMN title TEXT")
    if "reports_to" not in existing_cols:
        await conn.execute("ALTER TABLE agent_registry ADD COLUMN reports_to TEXT")
    await conn.commit()


async def _migration_v4_dedupe_active_handles(conn) -> None:
    """Blank duplicate active handles so ux_agent_active_handle can be created.

    Handle uniqueness among active agents is a NEW invariant (the consent flow
    added it).  Databases created before it can legally hold two active rows
    with the same non-empty handle.  Creating the partial unique index over
    such a DB raises IntegrityError, and since this runs in lifespan the whole
    controller would fail to boot on every restart until manual DB surgery --
    unacceptable for a no-terminal appliance.

    Heal it non-destructively: keep the handle on the OLDEST active row (lowest
    id, the most likely original owner) and blank it on the rest.  The rows
    survive as active agents; empty handles are excluded from the index, so a
    freed handle can be reclaimed later.  Every change is logged so an operator
    can reconcile.  Idempotent: a clean DB has no duplicates and is untouched.
    """
    rows = await (
        await conn.execute(
            "SELECT id, canonical_id, handle FROM agent_registry "
            "WHERE status = 'active' AND handle != '' ORDER BY handle, id"
        )
    ).fetchall()

    seen_handles: set[str] = set()
    demoted: list[tuple[str, str]] = []  # (canonical_id, handle)
    for row in rows:
        handle = row[2]
        if handle in seen_handles:
            demoted.append((row[1], handle))
        else:
            seen_handles.add(handle)

    for canonical_id, handle in demoted:
        await conn.execute(
            "UPDATE agent_registry SET handle = '' WHERE canonical_id = ?",
            (canonical_id,),
        )
        logger.warning(
            "registry heal: blanked duplicate active handle %r on agent %s so the "
            "active-handle unique index can be created; reassign a handle if needed",
            handle,
            canonical_id,
        )
    if demoted:
        await conn.commit()


# ---------------------------------------------------------------------------
# Signing-key helpers (Ed25519, persisted to disk)
# ---------------------------------------------------------------------------

_REGISTRY_KEY_FILENAME = "agent_registry_signing.pem"


def load_or_create_signing_keypair(data_dir: Path) -> tuple[bytes, bytes]:
    """Return (private_key_pem_bytes, public_key_pem_bytes).

    Generates an Ed25519 keypair on first call and persists the private key
    PEM to ``<data_dir>/agent_registry_signing.pem`` with mode 0600.
    Subsequent calls load and return the same keypair.  Idempotent under
    concurrent processes - the writer uses O_EXCL so only one process
    creates the file.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
        load_pem_private_key,
    )

    data_dir.mkdir(parents=True, exist_ok=True)
    pem_path = data_dir / _REGISTRY_KEY_FILENAME

    if pem_path.exists():
        private_key = load_pem_private_key(pem_path.read_bytes(), password=None)
    else:
        private_key = Ed25519PrivateKey.generate()
        pem_bytes = private_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
        try:
            fd = os.open(str(pem_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, pem_bytes)
            finally:
                os.close(fd)
        except FileExistsError:
            # Lost the race - load the winner's key instead.
            private_key = load_pem_private_key(pem_path.read_bytes(), password=None)

    private_pem = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    public_pem = private_key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


# ---------------------------------------------------------------------------
# Token minting (compact JWT-style - header.payload.signature, base64url)
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    import base64
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def mint_registry_token(
    canonical_id: str,
    private_key_pem: bytes,
    *,
    user_id: str = "",
    framework: str = "",
    project_id: Optional[str] = None,
) -> str:
    """Return a signed compact EdDSA JWT: <header>.<payload>.<signature> (base64url).

    The JWT header is exactly ``{"alg":"EdDSA","typ":"JWT"}`` so any standard
    Ed25519 JWT verifier (e.g. PyJWT, jose, or the cryptography lib directly)
    can verify it without importing tinyagentos code.

    Claims:
      sub        - canonical_id (immutable agent identity)
      iss        - "taos-registry"
      iat        - unix timestamp of issuance
      user_id    - owning user_id at registration time
      framework  - agent framework at registration time
      project_id - project binding, present only when non-empty; absent means
                   the token is global (not bound to any project)

    Signed with Ed25519 over the UTF-8 bytes of ``<header_b64url>.<payload_b64url>``.
    """
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    private_key = load_pem_private_key(private_key_pem, password=None)

    header = _b64url_encode(
        json.dumps({"alg": "EdDSA", "typ": "JWT"}, separators=(",", ":")).encode()
    )
    claims: dict = {
        "sub": canonical_id,
        "iss": "taos-registry",
        "iat": int(time.time()),
        "jti": uuid.uuid4().hex,
        "user_id": user_id,
        "framework": framework,
    }
    if project_id:
        claims["project_id"] = project_id
    payload = _b64url_encode(
        json.dumps(claims, separators=(",", ":")).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = _b64url_encode(private_key.sign(signing_input))
    return f"{header}.{payload}.{signature}"


def verify_registry_token(token: str, public_key_pem: bytes) -> dict:
    """Verify *token* against *public_key_pem*.

    Returns the decoded payload dict on success.
    Raises ``ValueError`` on invalid format or bad signature.
    """
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.exceptions import InvalidSignature

    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("token must have three dot-separated parts")

    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig_bytes = _b64url_decode(sig_b64)

    public_key = load_pem_public_key(public_key_pem)
    try:
        public_key.verify(sig_bytes, signing_input)
    except InvalidSignature:
        raise ValueError("token signature verification failed") from None

    payload = json.loads(_b64url_decode(payload_b64))
    return payload


# ---------------------------------------------------------------------------
# Canonical-ID minting
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower().strip()).strip("-") or "agent"


def mint_canonical_id(slug: str, ts: datetime) -> str:
    """Return ``{slug}-{YYYYMMDD}-{HHMMSS}`` from *ts* (UTC)."""
    date_part = ts.strftime("%Y%m%d")
    time_part = ts.strftime("%H%M%S")
    return f"{slug}-{date_part}-{time_part}"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _row_to_dict(row: aiosqlite.Row) -> dict:
    d = {k: row[k] for k in row.keys()}
    # Deserialise capabilities JSON list
    try:
        d["capabilities"] = json.loads(d.get("capabilities") or "[]")
    except (ValueError, TypeError):
        d["capabilities"] = []
    return d


class AgentRegistryStore(BaseStore):
    """Persistent store for the canonical agent registry.

    Keeps track of registered agents (canonical_id, signing token, metadata).
    The signing keypair lives on disk; this store only holds the data.
    """

    SCHEMA = SCHEMA

    def __init__(self, db_path: Path):
        super().__init__(db_path)
        # Serializes set_reporting's cycle-check-then-write so two concurrent
        # org edits (A->B and B->A) can't both pass the cycle guard and then
        # both write a persisted cycle. The shared aiosqlite connection yields
        # on every await inside the check, so the read chain and the write must
        # be held together. Created at construction (not in init()) so it always
        # exists before any caller, independent of init ordering.
        self._reporting_lock = asyncio.Lock()

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row

    async def _post_init(self) -> None:
        """Idempotently ensure the status column exists and is backfilled."""
        await _migration_v1_add_status(self._db)
        await _migration_v2_strip_at_display_name(self._db)
        await _migration_v3_add_org_fields(self._db)
        # Dedupe BEFORE the index so a pre-invariant DB with duplicate active
        # handles cannot make the CREATE UNIQUE INDEX (hence boot) fail.
        await _migration_v4_dedupe_active_handles(self._db)
        # Created after the status migration so the partial index's WHERE clause
        # can reference the status column on the pre-status migration path.
        # Guard the index creation too: if some path we did not anticipate still
        # leaves a duplicate, warn and continue rather than bricking boot -- the
        # uniqueness is also enforced at write time (register/activation).
        try:
            await self._db.executescript(ACTIVE_HANDLE_INDEX)
        except aiosqlite.IntegrityError:
            logger.error(
                "registry: could not create ux_agent_active_handle (duplicate "
                "active handles remain); continuing without it -- write-time "
                "uniqueness still applies. Reconcile duplicate handles manually."
            )

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register(
        self,
        *,
        framework: str,
        display_name: str = "",
        user_id: str = "",
        origin: str = "taos-deployed",
        handle: str = "",
        role: Optional[str] = None,
        title: Optional[str] = None,
        reports_to: Optional[str] = None,
        capabilities: Optional[list[str]] = None,
    ) -> dict:
        """Mint a canonical_id, persist the record, and return it.

        ``reports_to`` is stored as-is with no validation here (the row does
        not exist yet, so it cannot be part of an existing cycle) - use
        ``set_reporting`` after registration to validate a manager change.

        Raises ``RuntimeError`` if the store is not initialised.
        """
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised - call init() first")

        capabilities = capabilities or []
        now_utc = datetime.now(timezone.utc)
        slug = _slugify(display_name) if display_name else _slugify(framework)
        base_id = mint_canonical_id(slug, now_utc)
        canonical_id = base_id
        created_ts = now_utc.isoformat()

        # Collision guard: if the same slug+second already exists, append a
        # 2-char hex suffix to break the tie.
        suffix_n = 0
        while True:
            existing = await (
                await self._db.execute(
                    "SELECT id FROM agent_registry WHERE canonical_id = ?",
                    (canonical_id,),
                )
            ).fetchone()
            if existing is None:
                break
            suffix_n += 1
            canonical_id = f"{base_id}-{suffix_n:02x}"

        caps_json = json.dumps(capabilities)
        initial_status = "pending" if origin == "external-selfjoin" else "active"
        await self._db.execute(
            """
            INSERT INTO agent_registry
                (canonical_id, display_name, framework, user_id, origin,
                 handle, role, title, reports_to, capabilities, created_ts, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (canonical_id, display_name, framework, user_id, origin,
             handle, role, title, reports_to, caps_json, created_ts, initial_status),
        )
        await self._db.commit()

        row = await (
            await self._db.execute(
                "SELECT * FROM agent_registry WHERE canonical_id = ?",
                (canonical_id,),
            )
        ).fetchone()
        return _row_to_dict(row)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def rollback(self) -> None:
        """Roll back the current transaction, clearing any failed write so the
        connection can accept new statements.  No-op if the store is closed or
        no transaction is open.
        """
        if self._db is not None:
            await self._db.rollback()

    async def delete(self, canonical_id: str) -> None:
        """Permanently remove *canonical_id* (used to clean up a half-registered
        row when a concurrent approve wins the active-handle race).  Returns
        silently if *canonical_id* does not exist.
        """
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        await self._db.execute(
            "DELETE FROM agent_registry WHERE canonical_id = ?", (canonical_id,)
        )
        await self._db.commit()

    async def get(self, canonical_id: str) -> Optional[dict]:
        """Return the record for *canonical_id*, or ``None``."""
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        row = await (
            await self._db.execute(
                "SELECT * FROM agent_registry WHERE canonical_id = ?",
                (canonical_id,),
            )
        ).fetchone()
        return _row_to_dict(row) if row else None

    async def get_by_handle(self, handle: str, *, status: str = "active") -> Optional[dict]:
        """Return the oldest entry with *handle* and *status*, or ``None``.

        Used to make internal-identity minting idempotent by handle: when an
        active agent already owns the handle its canonical_id is reused instead
        of registering a duplicate row.  Pass ``status=None`` to match any
        status.
        """
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        if status is None:
            cursor = await self._db.execute(
                "SELECT * FROM agent_registry WHERE handle = ? ORDER BY id LIMIT 1",
                (handle,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM agent_registry WHERE handle = ? AND status = ? ORDER BY id LIMIT 1",
                (handle, status),
            )
        row = await cursor.fetchone()
        return _row_to_dict(row) if row else None

    async def list_all(self, *, status: Optional[str] = None) -> list[dict]:
        """Return all registry records, optionally filtered by *status*."""
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        if status is not None:
            cursor = await self._db.execute(
                "SELECT * FROM agent_registry WHERE status = ? ORDER BY id",
                (status,),
            )
        else:
            cursor = await self._db.execute("SELECT * FROM agent_registry ORDER BY id")
        rows = await cursor.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def list_for_user(self, user_id: str, *, status: Optional[str] = None) -> list[dict]:
        """Return all registry records owned by *user_id*, optionally filtered by *status*."""
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        if status is not None:
            cursor = await self._db.execute(
                "SELECT * FROM agent_registry WHERE user_id = ? AND status = ? ORDER BY id",
                (user_id, status),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM agent_registry WHERE user_id = ? ORDER BY id",
                (user_id,),
            )
        rows = await cursor.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def list_revoked(self) -> list[dict]:
        """Return [{canonical_id, revoked_at}] for all revoked entries (back-compat feed)."""
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        cursor = await self._db.execute(
            "SELECT canonical_id, revoked_at FROM agent_registry "
            "WHERE revoked_at IS NOT NULL ORDER BY revoked_at",
        )
        rows = await cursor.fetchall()
        return [{"canonical_id": r["canonical_id"], "revoked_at": r["revoked_at"]} for r in rows]

    async def list_inactive(self) -> list[dict]:
        """Return [{canonical_id, status}] for all non-active entries."""
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        cursor = await self._db.execute(
            "SELECT canonical_id, status FROM agent_registry "
            "WHERE status != 'active' ORDER BY id",
        )
        rows = await cursor.fetchall()
        return [{"canonical_id": r["canonical_id"], "status": r["status"]} for r in rows]

    # ------------------------------------------------------------------
    # Lifecycle state machine
    # ------------------------------------------------------------------

    async def set_status(
        self,
        canonical_id: str,
        new_status: str,
        *,
        actor: str = "",
    ) -> dict:
        """Transition *canonical_id* to *new_status*, enforcing valid transitions.

        Returns the updated record.
        Raises ``ValueError`` on invalid/disallowed transition.
        Raises ``KeyError`` if *canonical_id* does not exist.
        """
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")

        record = await self.get(canonical_id)
        if record is None:
            raise KeyError(canonical_id)

        before_status = record.get("status") or "active"
        _assert_valid_transition(before_status, new_status)

        now = datetime.now(timezone.utc).isoformat()
        # Atomic: the UPDATE is conditional on the status still being
        # ``before_status``, so two concurrent transitions cannot both win a
        # read/validate/write race - the loser's WHERE matches 0 rows. This
        # also guarantees the returned/audited before_status is accurate.
        if new_status == "revoked":
            cur = await self._db.execute(
                "UPDATE agent_registry SET status = ?, revoked_at = COALESCE(revoked_at, ?) "
                "WHERE canonical_id = ? AND status = ?",
                (new_status, now, canonical_id, before_status),
            )
        else:
            cur = await self._db.execute(
                "UPDATE agent_registry SET status = ? "
                "WHERE canonical_id = ? AND status = ?",
                (new_status, canonical_id, before_status),
            )
        await self._db.commit()
        if cur.rowcount == 0:
            # Status changed under us between the read and the write.
            raise ValueError(
                f"lifecycle transition conflict: {canonical_id!r} is no longer "
                f"in state {before_status!r}"
            )
        return await self.get(canonical_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Revoke
    # ------------------------------------------------------------------

    async def update(
        self,
        canonical_id: str,
        *,
        display_name: Optional[str] = None,
        handle: Optional[str] = None,
        role: Optional[str] = None,
        title: Optional[str] = None,
        capabilities: Optional[list[str]] = None,
    ) -> Optional[dict]:
        """Update mutable metadata fields on *canonical_id*.

        Only the provided (non-None) fields are changed.
        Status, user_id, framework, canonical_id, and timestamps are immutable.
        ``reports_to`` is deliberately NOT settable here: all reporting-line
        changes must go through ``set_reporting`` so the self-report, cycle,
        and manager-exists checks can never be bypassed.
        Returns the updated record, or None if *canonical_id* does not exist.
        """
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        record = await self.get(canonical_id)
        if record is None:
            return None

        cols: list[str] = []
        vals: list = []
        if display_name is not None:
            cols.append("display_name = ?")
            vals.append(display_name)
        if handle is not None:
            cols.append("handle = ?")
            vals.append(handle)
        if role is not None:
            cols.append("role = ?")
            vals.append(role)
        if title is not None:
            cols.append("title = ?")
            vals.append(title)
        if capabilities is not None:
            cols.append("capabilities = ?")
            vals.append(json.dumps(capabilities))
        if not cols:
            return record
        vals.append(canonical_id)
        await self._db.execute(
            f"UPDATE agent_registry SET {', '.join(cols)} WHERE canonical_id = ?",
            vals,
        )
        await self._db.commit()
        return await self.get(canonical_id)

    async def revoke(self, canonical_id: str) -> Optional[dict]:
        """Set revoked_at on *canonical_id*.  Returns updated record or None."""
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        record = await self.get(canonical_id)
        if record is None:
            return None
        if record.get("revoked_at"):
            # Already revoked - return the existing record unchanged.
            return record
        now = datetime.now(timezone.utc).isoformat()
        # Atomic: only the first concurrent revoke matches (revoked_at IS NULL);
        # a second one no-ops and returns the already-revoked record.
        await self._db.execute(
            "UPDATE agent_registry SET revoked_at = ?, status = 'revoked' "
            "WHERE canonical_id = ? AND revoked_at IS NULL",
            (now, canonical_id),
        )
        await self._db.commit()
        return await self.get(canonical_id)

    # ------------------------------------------------------------------
    # Org model (#161): reporting lines, roles/titles, org tree
    # ------------------------------------------------------------------

    # Cap on the reports_to chain walk used by both the cycle guard in
    # set_reporting and the tree-building recursion in get_org_tree - bounds
    # the cost of a corrupt or pathological chain instead of looping forever.
    _MAX_REPORTS_TO_WALK = 50

    async def set_role_title(
        self,
        canonical_id: str,
        *,
        role: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Optional[dict]:
        """Set *role* and/or *title* on *canonical_id* (free-form strings).

        Only the provided (non-None) fields are changed. A provided empty
        string clears the field (stores NULL), matching the ""-clears
        convention used for reports_to. Returns the updated record, or None if
        *canonical_id* does not exist.
        """
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        record = await self.get(canonical_id)
        if record is None:
            return None

        cols: list[str] = []
        vals: list = []
        if role is not None:
            cols.append("role = ?")
            vals.append(role or None)
        if title is not None:
            cols.append("title = ?")
            vals.append(title or None)
        if not cols:
            return record
        vals.append(canonical_id)
        await self._db.execute(
            f"UPDATE agent_registry SET {', '.join(cols)} WHERE canonical_id = ?",
            vals,
        )
        await self._db.commit()
        return await self.get(canonical_id)

    async def set_reporting(
        self, canonical_id: str, reports_to: Optional[str]
    ) -> dict:
        """Set (or, with ``None``, clear) *canonical_id*'s manager.

        Validates:
          - *canonical_id* exists (``KeyError`` otherwise)
          - *reports_to* (when not ``None``) refers to an existing agent
            (``ValueError`` otherwise)
          - *reports_to* is not *canonical_id* itself (no self-report)
          - assigning *reports_to* does not create a reporting cycle - walked
            by following the candidate manager's own reports_to chain and
            checking whether it ever reaches back to *canonical_id*

        Returns the updated record.
        """
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")

        # Hold the lock across the whole cycle-check-then-write. Every await in
        # the check yields the shared aiosqlite connection, so without this two
        # concurrent edits (A->B and B->A) could both pass the guard and then
        # both write, persisting a cycle.
        async with self._reporting_lock:
            record = await self.get(canonical_id)
            if record is None:
                raise KeyError(canonical_id)

            if reports_to is not None:
                if reports_to == canonical_id:
                    raise ValueError("an agent cannot report to itself")
                manager = await self.get(reports_to)
                if manager is None:
                    raise ValueError(f"reports_to manager not found: {reports_to!r}")

                # Cycle guard: walk the candidate manager's existing reports_to
                # chain; if it ever reaches canonical_id, this edge would close a
                # loop. `seen` guards against looping forever on an already-
                # corrupt chain independent of the depth cap.
                seen: set[str] = set()
                current: Optional[str] = reports_to
                depth = 0
                while current is not None and depth < self._MAX_REPORTS_TO_WALK:
                    if current == canonical_id:
                        raise ValueError(
                            f"assigning reports_to={reports_to!r} would create "
                            f"a reporting cycle"
                        )
                    if current in seen:
                        break
                    seen.add(current)
                    current_record = await self.get(current)
                    current = current_record.get("reports_to") if current_record else None
                    depth += 1

            await self._db.execute(
                "UPDATE agent_registry SET reports_to = ? WHERE canonical_id = ?",
                (reports_to, canonical_id),
            )
            await self._db.commit()
            return await self.get(canonical_id)  # type: ignore[return-value]

    async def direct_reports(self, canonical_id: str) -> list[dict]:
        """Return the agents whose reports_to is *canonical_id*, oldest first."""
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        cursor = await self._db.execute(
            "SELECT * FROM agent_registry WHERE reports_to = ? ORDER BY id",
            (canonical_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def get_org_tree(self) -> list[dict]:
        """Return the reporting forest: agents with no manager (or whose
        manager row does not exist) are roots; each node nests its direct
        reports recursively.

        Each node: {canonical_id, display_name, role, title, reports_to,
        reports: [...]}.  Depth-capped and cycle-guarded so a corrupt chain
        (written outside ``set_reporting``) cannot recurse forever.
        """
        if self._db is None:
            raise RuntimeError("AgentRegistryStore not initialised")
        all_rows = await self.list_all()
        by_id = {r["canonical_id"]: r for r in all_rows}
        children: dict[str, list[dict]] = {}
        roots: list[dict] = []
        for row in all_rows:
            reports_to = row.get("reports_to")
            if reports_to and reports_to in by_id:
                children.setdefault(reports_to, []).append(row)
            else:
                roots.append(row)

        def _node(row: dict, depth: int, seen: frozenset) -> dict:
            cid = row["canonical_id"]
            seen = seen | {cid}
            kids: list[dict] = []
            if depth < self._MAX_REPORTS_TO_WALK:
                for child in children.get(cid, []):
                    if child["canonical_id"] not in seen:
                        kids.append(_node(child, depth + 1, seen))
            return {
                "canonical_id": cid,
                "display_name": row.get("display_name"),
                "role": row.get("role"),
                "title": row.get("title"),
                "reports_to": row.get("reports_to"),
                "reports": kids,
            }

        return [_node(r, 0, frozenset()) for r in roots]
