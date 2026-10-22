"""Orb catalog signing and serial high-water state (SPEC-orb-companion.md s10)."""
import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from .frames import OrbError


class CatalogFetchError(OrbError):
    """Error fetching catalog from remote."""


class CatalogSignatureMissing(OrbError):
    """Catalog signature is missing or empty."""


class CatalogKeyUnknown(OrbError):
    """Catalog signature key_id not in pinned keys."""


class CatalogSignatureInvalid(OrbError):
    """Catalog signature is malformed or invalid."""


class CatalogSerialRegression(OrbError):
    """Catalog serial goes backwards or is invalid."""


class CatalogStateCorrupt(OrbError):
    """Catalog state file is corrupt or unreadable."""


CATALOG_KEYS: dict[str, str] = {}


def key_id_for(public_key_raw: bytes) -> str:
    """Return first 16 chars of sha256 of raw public key."""
    return hashlib.sha256(public_key_raw).hexdigest()[:16]


def default_state_path(data_dir) -> Path:
    """Return data_dir / 'orb' / 'catalog_state.json'."""
    return Path(data_dir) / "orb" / "catalog_state.json"


def verify_catalog(catalog_bytes: bytes, sig_bytes: bytes | None, keys: dict[str, str] | None = None) -> dict:
    """Verify catalog signature and return parsed catalog dict.

    Args:
        catalog_bytes: Raw catalog JSON bytes.
        sig_bytes: Signature JSON bytes (UTF-8), or None/empty.
        keys: Optional key mapping (key_id -> base64 raw public key). Defaults to CATALOG_KEYS.

    Returns:
        Parsed catalog dict with valid serial.

    Raises:
        CatalogSignatureMissing: sig_bytes is None or empty.
        CatalogSignatureInvalid: sig_bytes malformed, bad base64, wrong length, or invalid signature.
        CatalogKeyUnknown: key_id not in keys.
        CatalogSerialRegression: serial missing, not int, bool, or < 0.
        OrbError: catalog not a JSON object, or expires not ISO 8601.
    """
    if keys is None:
        keys = CATALOG_KEYS

    if not sig_bytes:
        raise CatalogSignatureMissing("catalog signature missing")

    # Parse sig JSON
    try:
        sig_obj = json.loads(sig_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CatalogSignatureInvalid("signature is not valid JSON")

    if not isinstance(sig_obj, dict) or set(sig_obj.keys()) != {"key_id", "sig"}:
        raise CatalogSignatureInvalid("signature JSON must have exactly key_id and sig")

    key_id = sig_obj["key_id"]
    sig_b64 = sig_obj["sig"]

    if not isinstance(key_id, str) or not re.fullmatch(r"[0-9a-f]{16}", key_id):
        raise CatalogSignatureInvalid("key_id must be 16 lowercase hex chars")

    if not isinstance(sig_b64, str):
        raise CatalogSignatureInvalid("sig must be string")

    # Decode signature
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except (base64.binascii.Error, ValueError):
        raise CatalogSignatureInvalid("sig is not valid base64")

    if len(sig) != 64:
        raise CatalogSignatureInvalid("signature must be 64 bytes")

    # Look up key
    if key_id not in keys:
        raise CatalogKeyUnknown(f"key {key_id} not pinned")

    # Load public key and verify
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature

    try:
        pub_key_bytes = base64.b64decode(keys[key_id], validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(pub_key_bytes)
        public_key.verify(sig, catalog_bytes)
    except InvalidSignature:
        raise CatalogSignatureInvalid("signature verification failed")
    except (base64.binascii.Error, ValueError):
        raise CatalogSignatureInvalid("pinned key is not valid base64")

    # Parse catalog JSON (only after good signature)
    try:
        catalog = json.loads(catalog_bytes)
    except ValueError:
        raise OrbError("catalog is not a JSON object")

    if not isinstance(catalog, dict):
        raise OrbError("catalog is not a JSON object")

    # Validate serial
    serial = catalog.get("serial")
    if not isinstance(serial, int) or isinstance(serial, bool) or serial < 0:
        raise CatalogSerialRegression("catalog serial missing or invalid")

    # Validate expires if present
    expires = catalog.get("expires")
    if expires is not None:
        if not isinstance(expires, str):
            raise OrbError("catalog expires is not ISO 8601")
        try:
            datetime.fromisoformat(expires)
        except ValueError:
            raise OrbError("catalog expires is not ISO 8601")

    return catalog


def check_serial(state_path, serial: int, digest: str) -> bool:
    """Check if catalog serial is acceptable (high-water mark).

    Args:
        state_path: Path to catalog state file.
        serial: Catalog serial number.
        digest: sha256 hex of catalog bytes.

    Returns:
        True if catalog should be accepted and recorded, False if already recorded.

    Raises:
        CatalogSerialRegression: serial goes backwards or same serial with different bytes.
        CatalogStateCorrupt: state file exists but is corrupt.
    """
    state_path = Path(state_path)

    if not state_path.exists():
        return True

    try:
        state_text = state_path.read_text()
        state = json.loads(state_text)
    except (OSError, ValueError):
        raise CatalogStateCorrupt("catalog state file corrupt")

    if not isinstance(state, dict):
        raise CatalogStateCorrupt("catalog state is not a JSON object")

    stored_serial = state.get("serial")
    stored_sha256 = state.get("sha256")

    if not isinstance(stored_serial, int) or isinstance(stored_serial, bool):
        raise CatalogStateCorrupt("catalog state serial missing or invalid")

    if not isinstance(stored_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", stored_sha256):
        raise CatalogStateCorrupt("catalog state sha256 missing or invalid")

    if serial < stored_serial:
        raise CatalogSerialRegression("catalog serial goes backwards")

    if serial == stored_serial:
        if digest != stored_sha256:
            raise CatalogSerialRegression("same serial, different catalog bytes")
        return False

    return True


def record_serial(state_path, serial: int, digest: str) -> None:
    """Record catalog serial and digest to state file atomically.

    Args:
        state_path: Path to catalog state file.
        serial: Catalog serial number.
        digest: sha256 hex of catalog bytes.
    """
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    from tinyagentos.atomic_io import atomic_write_bytes
    state_json = json.dumps({"serial": serial, "sha256": digest}).encode()
    atomic_write_bytes(state_path, state_json, mode=0o600)


def accept_catalog(catalog_bytes: bytes, sig_bytes: bytes | None, state_path, keys: dict[str, str] | None = None) -> dict:
    """Verify catalog, check serial, and record if new.

    Args:
        catalog_bytes: Raw catalog JSON bytes.
        sig_bytes: Signature JSON bytes (UTF-8), or None/empty.
        state_path: Path to catalog state file.
        keys: Optional key mapping. Defaults to CATALOG_KEYS.

    Returns:
        Parsed catalog dict.

    Raises:
        CatalogSignatureMissing, CatalogSignatureInvalid, CatalogKeyUnknown,
        CatalogSerialRegression, CatalogStateCorrupt, OrbError: from verify_catalog or check_serial.
    """
    catalog = verify_catalog(catalog_bytes, sig_bytes, keys)
    digest = hashlib.sha256(catalog_bytes).hexdigest()

    if check_serial(state_path, catalog["serial"], digest):
        record_serial(state_path, catalog["serial"], digest)

    return catalog


def reset_state(state_path) -> None:
    """Admin-only reset: unlink state file if it exists (missing_ok=True)."""
    Path(state_path).unlink(missing_ok=True)