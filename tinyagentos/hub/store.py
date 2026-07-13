"""Local hub object store + canonical-JSON helpers -- hub social slice 2.

See ``docs/design/hub-social-network-foundation.md`` ("Data model" and the
slice plan). This is the node's own content store: the SQLite home for every
signed object the node holds (its own and, later, peers'), the raw blobs those
objects reference, and the authors (username -> key) it has resolved. Slice 2
makes the *profile* object real end to end (build, sign, store, render) and lays
the object/blob/author tables the later chain and sync slices build on. There is
no peer networking here; the only remote calls remain slice 1's directory calls
through ``account_proxy.py``.

Every object is:

- **canonical-JSON encoded** (sorted keys, compact separators) so the same
  logical object always serialises to the same bytes on every node;
- **content-addressed** by the SHA-256 of those canonical bytes *excluding the
  signature*, so the id can be computed before signing and a cacher cannot alter
  what it serves without the hash breaking;
- **signed** by the author's Ed25519 signing key (slice 1's keystore) over the
  same canonical bytes, so anyone holding the author's public key can verify it.

The canonical author identifier inside an object is the signing-key fingerprint
(slice 1), never the username, so a username policy change can never
retroactively re-attribute content.

Profiles are the one *mutable* object: highest signed ``version`` wins and older
versions are superseded (design "Profile"). We keep every version in the
content-addressed object store (append-only, cheap) and resolve the current
profile as the highest version for an author, so ``put_profile`` is idempotent
and a stale (lower-or-equal version) replica can never clobber a newer one.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from tinyagentos.base_store import BaseStore
from tinyagentos.hub import identity

# Profiles are personal pages or business pages -- same object, different render
# (design "Profile"). Any other kind is rejected before signing.
PROFILE_KINDS = ("personal", "business")


# --- canonical encoding / hashing / signing -----------------------------------


def canonical_json(obj: dict) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8 preserved.

    The same logical object serialises to the same string on every node, which
    is what makes content addressing and signature verification portable.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_bytes(obj: dict) -> bytes:
    """Canonical bytes of an object *excluding* its ``sig`` field.

    Both the content address and the signature are computed over these bytes, so
    the id is stable whether or not the object is signed yet, and verification
    recomputes exactly what was signed.
    """
    payload = {k: v for k, v in obj.items() if k != "sig"}
    return canonical_json(payload).encode("utf-8")


def object_hash(obj: dict) -> str:
    """SHA-256 (hex) of the canonical bytes -- the object's content address."""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def sign_object(obj: dict) -> dict:
    """Return a copy of ``obj`` signed by this node's signing key.

    Any pre-existing ``sig`` is dropped before signing so re-signing is safe.
    The signature covers the canonical bytes (sig excluded), the same bytes
    :func:`object_hash` addresses.
    """
    unsigned = {k: v for k, v in obj.items() if k != "sig"}
    unsigned["sig"] = identity.sign(canonical_bytes(unsigned))
    return unsigned


def verify_object(obj: dict, signing_pubkey_hex: str) -> bool:
    """Verify an object's signature against a signing public key.

    Returns False (never raises) on a missing or malformed signature so callers
    verifying arbitrary peer objects degrade cleanly. Tampering with any signed
    field changes the canonical bytes and fails the check.
    """
    sig = obj.get("sig")
    if not isinstance(sig, str) or not sig:
        return False
    return identity.verify_signature(signing_pubkey_hex, canonical_bytes(obj), sig)


# --- profile object -----------------------------------------------------------


def build_profile(
    *,
    version: int,
    kind: str,
    display_name: str,
    bio: str = "",
    avatar: Optional[str] = None,
    links: Optional[list] = None,
    author: Optional[str] = None,
) -> dict:
    """Build an *unsigned* profile object (design "Profile").

    ``author`` defaults to this node's signing fingerprint, so the common case
    (rendering/updating your own profile) needs no key handling from the caller.
    Pass :func:`sign_object` over the result to sign it.
    """
    if kind not in PROFILE_KINDS:
        raise ValueError(f"invalid kind {kind!r}; expected one of {PROFILE_KINDS}")
    return {
        "type": "profile",
        "author": author or identity.signing_fingerprint(),
        "version": int(version),
        "kind": kind,
        "display_name": display_name,
        "bio": bio,
        "avatar": avatar,
        "links": links or [],
    }


# --- SQLite store -------------------------------------------------------------


HUB_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS hub_authors (
    fingerprint       TEXT PRIMARY KEY,
    username          TEXT,
    signing_pubkey    TEXT,
    encryption_pubkey TEXT,
    updated_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS hub_objects (
    hash       TEXT PRIMARY KEY,
    author     TEXT NOT NULL,
    type       TEXT NOT NULL,
    seq        INTEGER,   -- chain position for posts/tombstones; NULL for profiles
    version    INTEGER,   -- profile version; NULL for chain objects
    body       TEXT NOT NULL,   -- canonical JSON of the full signed object
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hub_objects_author_type
    ON hub_objects(author, type);
CREATE INDEX IF NOT EXISTS idx_hub_objects_profile_version
    ON hub_objects(author, type, version);

CREATE TABLE IF NOT EXISTS hub_blobs (
    hash       TEXT PRIMARY KEY,
    size       INTEGER NOT NULL,
    mime       TEXT,
    data       BLOB NOT NULL,
    created_at REAL NOT NULL
);
"""


def default_db_path() -> Path:
    """The node's hub store path, colocated with the slice-1 identity keystore.

    Resolved the same way as ``identity.py`` (``TAOS_DATA_DIR`` override, else the
    project ``data`` dir), so the identity file and the object store live side by
    side under ``<data_dir>/hub/`` and tests get a hermetic dir for free.
    """
    return identity._hub_dir() / "hub.db"


def _row(cursor_desc, row) -> dict:
    return dict(zip([c[0] for c in cursor_desc], row))


class HubStore(BaseStore):
    """SQLite-backed store for hub objects, blobs, and resolved authors."""

    SCHEMA = HUB_STORE_SCHEMA

    # ---------------------------------------------------------------- objects
    async def put_object(self, obj: dict) -> str:
        """Store a signed object, returning its content address.

        Idempotent: content addressing means re-storing the same object is a
        no-op (``INSERT OR IGNORE``). Requires ``author``, ``type``, and ``sig``
        so an unsigned or malformed object never reaches the store.
        """
        for field in ("author", "type", "sig"):
            if not obj.get(field):
                raise ValueError(f"object missing required field {field!r}")
        h = object_hash(obj)
        await self._db.execute(
            "INSERT OR IGNORE INTO hub_objects "
            "(hash, author, type, seq, version, body, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                h,
                obj["author"],
                obj["type"],
                obj.get("seq"),
                obj.get("version"),
                canonical_json(obj),
                time.time(),
            ),
        )
        await self._db.commit()
        return h

    async def get_object(self, obj_hash: str) -> dict | None:
        cur = await self._db.execute(
            "SELECT body FROM hub_objects WHERE hash = ?", (obj_hash,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    # ---------------------------------------------------------------- blobs
    async def put_blob(self, data: bytes, mime: str | None = None) -> str:
        """Store raw blob bytes content-addressed by SHA-256; idempotent."""
        h = hashlib.sha256(data).hexdigest()
        await self._db.execute(
            "INSERT OR IGNORE INTO hub_blobs (hash, size, mime, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (h, len(data), mime, data, time.time()),
        )
        await self._db.commit()
        return h

    async def get_blob(self, blob_hash: str) -> dict | None:
        cur = await self._db.execute(
            "SELECT hash, size, mime, data FROM hub_blobs WHERE hash = ?",
            (blob_hash,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return {"hash": row[0], "size": row[1], "mime": row[2], "data": row[3]}

    # ---------------------------------------------------------------- authors
    async def upsert_author(
        self,
        fingerprint: str,
        *,
        username: str | None = None,
        signing_pubkey: str | None = None,
        encryption_pubkey: str | None = None,
    ) -> None:
        """Record or refresh the username -> key mapping for an author."""
        await self._db.execute(
            "INSERT INTO hub_authors "
            "(fingerprint, username, signing_pubkey, encryption_pubkey, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(fingerprint) DO UPDATE SET "
            "username=excluded.username, "
            "signing_pubkey=excluded.signing_pubkey, "
            "encryption_pubkey=excluded.encryption_pubkey, "
            "updated_at=excluded.updated_at",
            (fingerprint, username, signing_pubkey, encryption_pubkey, time.time()),
        )
        await self._db.commit()

    async def get_author(self, fingerprint: str) -> dict | None:
        cur = await self._db.execute(
            "SELECT * FROM hub_authors WHERE fingerprint = ?", (fingerprint,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return _row(cur.description, row)

    # ---------------------------------------------------------------- profiles
    async def get_profile(self, author: str) -> dict | None:
        """The current (highest-version) profile object for an author."""
        cur = await self._db.execute(
            "SELECT body FROM hub_objects "
            "WHERE author = ? AND type = 'profile' "
            "ORDER BY version DESC LIMIT 1",
            (author,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    async def put_profile(self, profile: dict) -> dict:
        """Store a signed profile with version-wins semantics.

        A profile whose version is not strictly greater than the stored current
        one is ignored (design: "highest signed version wins, replicas overwrite
        older versions"), and the existing current profile is returned unchanged.
        Returns the profile that is current after the call.
        """
        if profile.get("type") != "profile":
            raise ValueError("not a profile object")
        version = profile.get("version")
        if not isinstance(version, int):
            raise ValueError("profile missing integer version")
        current = await self.get_profile(profile["author"])
        if current is not None and version <= current["version"]:
            return current
        await self.put_object(profile)
        return profile
