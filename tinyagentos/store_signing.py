"""Store manifest signing — Ed25519 signatures for catalog integrity.

Mirrors the hub identity pattern: an Ed25519 keypair is generated on first
use and persisted to disk.  Every catalog manifest is signed at load time;
the install-v2 endpoint verifies the signature before allowing an install
to proceed, so a compromised catalog entry (post-boot tampering, MITM,
supply-chain injection) is caught before any script or image is pulled.

The public key is exposed via ``GET /api/store/signing-pubkey`` so clients
and auditing tools can verify signatures independently.

Design decisions (see #647):

- **One signing key per taOS instance.**  Generated on first boot; the
  private key never leaves the node.  This matches the self-hosted model:
  each instance trusts its own catalog.  A future shared-catalog model
  (e.g. a taOS App Store) would use a network-fetched public key.

- **Signatures live in the manifest YAML.**  A ``_signature`` field at the
  root of the manifest holds the hex-encoded Ed25519 signature over the
  canonical bytes of the manifest *with that field stripped*.  At load
  time, the registry strips ``_signature``, computes the signature, and
  stores it in-memory.  At verify time the same stripped view is used, so
  flipping the signature doesn't change the bytes being verified.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

logger = logging.getLogger(__name__)

# Filename under <data_dir>/ for the persisted keypair.
_KEYPAIR_FILE = "store_signing_key.json"

# Field name in the manifest YAML that holds the detached signature.
SIGNATURE_FIELD = "_signature"


# ---------------------------------------------------------------------------
# Keypair lifecycle
# ---------------------------------------------------------------------------


def generate_signing_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh Ed25519 keypair.

    Returns ``(private_pem, public_pem)`` — PEM-encoded byte strings
    without passphrase encryption (the private key is stored in a 0600
    file and the security model is local-tamper-detection, not secrecy).
    """
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        encoding=Encoding.PEM,
        format=PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def _enforce_permissions(keyfile: Path) -> None:
    """Ensure the keyfile is owner-read/write only (0600).

    Called on every load so a migration, backup-restore, or manual
    ``chmod`` cannot leave the private key world/group-readable.
    """
    try:
        st = keyfile.stat()
        if (st.st_mode & 0o777) != 0o600:
            os.chmod(keyfile, 0o600)
    except OSError:
        pass  # non-fatal on exotic filesystems


def load_or_create_signing_keypair(data_dir: Path) -> tuple[bytes, bytes]:
    """Return ``(private_pem, public_pem)``, minting the keypair on first use.

    Idempotent: once the keystore exists it is returned unchanged, so the
    instance keeps the same signing identity across restarts.
    """
    keyfile = data_dir / _KEYPAIR_FILE
    if keyfile.exists():
        try:
            data = json.loads(keyfile.read_text())
            priv = data["private_pem"].encode()
            # Enforce restrictive permissions on every load so a
            # migration / backup-restore / manual chmod cannot leave
            # the private key world/group-readable.
            _enforce_permissions(keyfile)
            # Derive the public key from the loaded private key so
            # a keyfile whose public_pem was replaced still yields
            # the correct keypair (the private key is the root of
            # trust, not its companion field).
            key = _load_private_key(priv)
            pub = key.public_key().public_bytes(
                encoding=Encoding.PEM,
                format=PublicFormat.SubjectPublicKeyInfo,
            )
            return priv, pub
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning(
                "store signing keyfile corrupt (%s), regenerating", exc,
            )

    priv, pub = generate_signing_keypair()
    keyfile.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"private_pem": priv.decode(), "public_pem": pub.decode()},
    )

    # Atomic creation with exclusive open + restrictive permissions.
    # O_CREAT|O_EXCL guarantees at most one process wins the race;
    # the loser loads the winner's key instead of overwriting it.
    # The file descriptor starts with mode 0o600 so there is never
    # a world-readable window — no separate chmod call is needed.
    tmp = keyfile.with_suffix(keyfile.suffix + ".tmp")
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Another process is creating the keypair.  Wait for the
        # winner to promote tmp→keyfile, then load its result.
        for _ in range(30):  # up to 3 s
            time.sleep(0.1)
            if keyfile.exists():
                return load_or_create_signing_keypair(data_dir)
        # After a reasonable wait the keyfile still does not exist.
        # Remove the stale tmp (the competing process may have
        # crashed or is stuck) and create our own.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        if keyfile.exists():
            return load_or_create_signing_keypair(data_dir)
        # Retry with a unique tmp path so we never collide with a
        # still-running winner that hasn't called os.replace yet.
        tmp = keyfile.with_suffix(f"{keyfile.suffix}.tmp.{os.getpid()}")
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if keyfile.exists():
                return load_or_create_signing_keypair(data_dir)
            raise
    # Write and atomically promote.
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, keyfile)
    logger.info("store signing keypair created at %s", keyfile)
    return priv, pub


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _load_private_key(private_pem: bytes) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(
        _raw_private_bytes(private_pem),
    )


def _load_public_key(public_pem: bytes) -> Ed25519PublicKey:
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    key = load_pem_public_key(public_pem)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError(f"expected Ed25519PublicKey, got {type(key).__name__}")
    return key


def _raw_private_bytes(private_pem: bytes) -> bytes:
    """Extract the 32-byte raw seed from a PKCS8 PEM."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(private_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"expected Ed25519PrivateKey, got {type(key).__name__}")
    # Ed25519PrivateKey.private_bytes_raw() returns the 32-byte seed.
    return key.private_bytes_raw()


def _canonical_manifest_bytes(manifest_dict: dict) -> bytes:
    """Deterministic byte representation of a manifest for signing.

    Strips ``_signature`` if present, then serialises as canonical JSON
    (sorted keys, no trailing whitespace, UTF-8).  This is stable across
    YAML load/save cycles as long as the YAML library does not re-order
    keys or change scalar representations.
    """
    stripped = {k: v for k, v in manifest_dict.items() if k != SIGNATURE_FIELD}
    return json.dumps(stripped, sort_keys=True, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Sign / verify
# ---------------------------------------------------------------------------


def sign_manifest(manifest_dict: dict, private_pem: bytes) -> str:
    """Return a hex-encoded Ed25519 signature over *manifest_dict*.

    ``_signature`` is stripped before signing so the field can be embedded
    in the same dict without creating a circular dependency.
    """
    data = _canonical_manifest_bytes(manifest_dict)
    key = _load_private_key(private_pem)
    return key.sign(data).hex()


def verify_manifest_signature(
    manifest_dict: dict,
    signature_hex: str,
    public_pem: bytes,
) -> bool:
    """Verify an Ed25519 signature over *manifest_dict*.

    Returns ``True`` when the signature is valid.  Returns ``False`` (never
    raises) on a bad signature, a wrong key, or malformed hex — the caller
    can treat verification failure as a hard block.
    """
    if not signature_hex:
        return False
    try:
        sig_bytes = bytes.fromhex(signature_hex)
        data = _canonical_manifest_bytes(manifest_dict)
        key = _load_public_key(public_pem)
        key.verify(sig_bytes, data)
        return True
    except (ValueError, InvalidSignature):
        return False
    except Exception:
        logger.exception("unexpected error during manifest verification")
        return False
