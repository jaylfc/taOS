"""Hub identity keystore -- hub social network slice 1.

Registering the free taos.my username mints, *on the user's node*, two keypairs
(see ``docs/design/hub-social-network-foundation.md``, "Identity and keys"):

- **Signing key** (Ed25519): signs every object the user publishes (profile
  versions, posts, follows, circle grants, tombstones, rotation statements).
- **Encryption key** (X25519): receives wrapped content keys for friends-only
  content addressed to this user.

Private keys are generated locally and never leave the node. They persist in a
single 0600 file under the data dir (``<data_dir>/hub/identity.json``),
following the ``mesh_credentials.py`` pattern: atomic write (temp + os.replace),
an allowlist of persisted fields, and the ``TAOS_DATA_DIR`` override so local
dev and tests keep working. The node registers the two *public* keys with the
directory, proving key possession with a signature over a server-issued
challenge.

The canonical author identifier is the signing public key fingerprint (SHA-256
of the raw public-key bytes), never the username: the directory maps username to
key, so a username policy change (rename, reclamation) can never retroactively
re-attribute signed content.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

logger = logging.getLogger(__name__)

_FILENAME = "identity.json"

# The fields we persist. Private key material stays local (0600); the public
# keys and creation time round-trip so a restart resolves the same identity.
_FIELDS = (
    "signing_private",
    "signing_public",
    "encryption_private",
    "encryption_public",
    "created_at",
)


def _data_dir() -> Path:
    env = os.environ.get("TAOS_DATA_DIR")
    if env:
        return Path(env)
    # tinyagentos/hub/identity.py -> project root is two parents up.
    return Path(__file__).resolve().parent.parent.parent / "data"


def _hub_dir() -> Path:
    return _data_dir() / "hub"


def _path() -> Path:
    return _hub_dir() / _FILENAME


# --- low-level key encoding (raw bytes <-> hex) --------------------------------


def _priv_raw(key) -> bytes:
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def _pub_raw(key) -> bytes:
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def _save(identity: dict) -> None:
    """Persist the keystore, mode 0600, atomically.

    Keeps only the ``_FIELDS`` allowlist. Atomic (write temp + ``os.replace``)
    so a crash mid-write never leaves a partial file. The parent dir is created
    0700; the file is created 0600 so private key material is never group- or
    world-readable.
    """
    creds = {k: identity.get(k) for k in _FIELDS}

    d = _hub_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:  # pragma: no cover - best effort on odd filesystems
        pass

    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    data = json.dumps(creds).encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:  # pragma: no cover
        pass


def _load() -> Optional[dict]:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return None
    # A corrupt or externally-edited file could parse to a non-object; treat any
    # non-dict, or one missing the private signing key, as absent (fail closed to
    # "no identity yet") rather than raising from the getters.
    if not isinstance(data, dict) or not data.get("signing_private"):
        return None
    return data


# --- public API ---------------------------------------------------------------


def exists() -> bool:
    """True once this node has minted a hub identity."""
    return _load() is not None


def load_or_create() -> dict:
    """Return this node's identity, minting the keypairs on first use.

    Idempotent: once the keystore exists it is returned unchanged, so the node
    keeps the same identity (and therefore the same author fingerprint) across
    restarts. The returned dict includes private key material; callers that only
    need the registerable view should use :func:`public_identity`.
    """
    existing = _load()
    if existing is not None:
        return existing

    signing = Ed25519PrivateKey.generate()
    encryption = X25519PrivateKey.generate()
    identity = {
        "signing_private": _priv_raw(signing).hex(),
        "signing_public": _pub_raw(signing.public_key()).hex(),
        "encryption_private": _priv_raw(encryption).hex(),
        "encryption_public": _pub_raw(encryption.public_key()).hex(),
        "created_at": time.time(),
    }
    _save(identity)
    return identity


def signing_fingerprint() -> str:
    """The canonical author id: SHA-256 (hex) of the raw signing public key."""
    identity = load_or_create()
    return fingerprint(identity["signing_public"])


def public_identity() -> dict:
    """The directory-registerable view: public keys and the author fingerprint.

    No private key material. This is what a client renders and what the register
    call anchors to the account's username.
    """
    identity = load_or_create()
    return {
        "signing_pubkey": identity["signing_public"],
        "encryption_pubkey": identity["encryption_public"],
        "fingerprint": fingerprint(identity["signing_public"]),
    }


def sign(data: bytes) -> str:
    """Sign raw bytes with this node's Ed25519 signing key; returns hex."""
    identity = load_or_create()
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(identity["signing_private"]))
    return key.sign(data).hex()


def build_registration(challenge: str) -> dict:
    """Build the ``POST /api/hub/identity/register`` body.

    ``proof`` is a signature over the server-issued challenge, showing the node
    holds the signing private key. The directory verifies it against the
    submitted ``signing_pubkey`` before anchoring the identity to the username.
    """
    pub = public_identity()
    return {
        "signing_pubkey": pub["signing_pubkey"],
        "encryption_pubkey": pub["encryption_pubkey"],
        "proof": sign(challenge.encode("utf-8")),
    }


def fingerprint(signing_pubkey_hex: str) -> str:
    """SHA-256 (hex) of a raw signing public key -- the canonical author id."""
    return hashlib.sha256(bytes.fromhex(signing_pubkey_hex)).hexdigest()


def verify_signature(signing_pubkey_hex: str, data: bytes, sig_hex: str) -> bool:
    """Verify an Ed25519 signature over ``data`` against a hex public key.

    Returns False (never raises) on a bad signature, a wrong key, or malformed
    hex, so the directory-side challenge check and any client-side object
    verification degrade cleanly. This is the check a directory runs on the
    registration proof: a proof signed by the wrong key does not verify.
    """
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(signing_pubkey_hex))
        pub.verify(bytes.fromhex(sig_hex), data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def clear() -> None:
    """Forget the persisted identity. Intended for tests; no-op if absent."""
    try:
        _path().unlink()
    except OSError:
        pass
