#!/usr/bin/env python3
"""Tests for tinyagentos/orb/catalog_sig."""

import base64
import hashlib
import json
import pytest
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tinyagentos.orb.frames import OrbError


# Fixture for test key
@pytest.fixture
def test_key():
    """Generate a test Ed25519 keypair and return (private_key, public_key_raw, key_id)."""
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes_raw()
    key_id = hashlib.sha256(public_key_raw).hexdigest()[:16]
    return private_key, public_key_raw, key_id


@pytest.fixture
def keys_dict(test_key):
    """Build keys dict mapping key_id to base64-encoded raw public key."""
    _, public_key_raw, key_id = test_key
    return {key_id: base64.b64encode(public_key_raw).decode("ascii")}


def make_sig_json(key_id: str, sig: bytes) -> bytes:
    """Helper to create sig JSON bytes."""
    return json.dumps({"key_id": key_id, "sig": base64.b64encode(sig).decode("ascii")}).encode("utf-8")


def sign_catalog(private_key: Ed25519PrivateKey, catalog_bytes: bytes) -> bytes:
    """Sign catalog bytes and return sig JSON bytes."""
    sig = private_key.sign(catalog_bytes)
    return make_sig_json(hashlib.sha256(private_key.public_key().public_bytes_raw()).hexdigest()[:16], sig)


from tinyagentos.orb import catalog_sig


# Exception hierarchy tests
def test_all_exceptions_are_orb_error_subclasses():
    """Every new exception is an OrbError subclass."""
    for exc_name in [
        "CatalogFetchError",
        "CatalogSignatureMissing",
        "CatalogKeyUnknown",
        "CatalogSignatureInvalid",
        "CatalogSerialRegression",
        "CatalogStateCorrupt",
        "CatalogKindInvalid",
        "CatalogInvalid",
    ]:
        exc_cls = getattr(catalog_sig, exc_name)
        assert issubclass(exc_cls, OrbError), f"{exc_name} must be OrbError subclass"


def test_verify_catalog_not_json_object_raises_catalog_invalid(keys_dict, test_key):
    """Catalog not a JSON object -> CatalogInvalid (still OrbError subclass)."""
    private_key, _, _ = test_key
    catalog_bytes = b'[1, 2, 3]'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    with pytest.raises(catalog_sig.CatalogInvalid, match="catalog is not a JSON object"):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)


def test_verify_catalog_expires_invalid_type_raises_catalog_invalid(keys_dict, test_key):
    """expires as number -> CatalogInvalid."""
    private_key, _, _ = test_key
    catalog_bytes = b'{"serial": 1, "expires": 123}'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    with pytest.raises(catalog_sig.CatalogInvalid, match="catalog expires is not ISO 8601"):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)


# key_id_for tests
def test_key_id_for_is_sha256_truncated():
    """key_id_for returns first 16 chars of sha256 of raw public key."""
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes_raw()
    expected = hashlib.sha256(public_key_raw).hexdigest()[:16]
    assert catalog_sig.key_id_for(public_key_raw) == expected


# default_state_path tests
def test_default_state_path_returns_orb_subdir(tmp_path):
    """default_state_path returns data_dir / 'orb' / 'catalog_state.json'."""
    state_path = catalog_sig.default_state_path(tmp_path)
    assert state_path == tmp_path / "orb" / "catalog_state.json"


# verify_catalog tests
def test_verify_catalog_good_sig_serial_3(keys_dict, test_key):
    """Good sig + serial 3 -> returns parsed dict."""
    private_key, _, key_id = test_key
    catalog = {"serial": 3, "apps": {"foo": {"versions": []}}}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    result = catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)
    assert result == catalog
    assert result["serial"] == 3


def test_verify_catalog_sig_none_raises_missing(keys_dict):
    """sig_bytes None -> CatalogSignatureMissing."""
    catalog_bytes = b'{"serial": 1}'
    with pytest.raises(catalog_sig.CatalogSignatureMissing):
        catalog_sig.verify_catalog(catalog_bytes, None, keys_dict)


def test_verify_catalog_sig_empty_raises_missing(keys_dict):
    """sig_bytes empty -> CatalogSignatureMissing."""
    catalog_bytes = b'{"serial": 1}'
    with pytest.raises(catalog_sig.CatalogSignatureMissing):
        catalog_sig.verify_catalog(catalog_bytes, b"", keys_dict)


def test_verify_catalog_sig_wrong_bytes_raises_invalid(keys_dict, test_key):
    """Sig over different bytes -> CatalogSignatureInvalid."""
    private_key, _, key_id = test_key
    catalog_bytes = b'{"serial": 1}'
    other_bytes = b'{"serial": 2}'
    sig_bytes = sign_catalog(private_key, other_bytes)
    with pytest.raises(catalog_sig.CatalogSignatureInvalid):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)


@pytest.mark.parametrize("bad_sig", [
    # Extra key in sig JSON
    b'{"key_id": "abcdef1234567890", "sig": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "extra": 1}',
    # Non-JSON
    b'not json',
    # Uppercase key_id
    b'{"key_id": "ABCDEF1234567890", "sig": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
    # 15-char key_id
    b'{"key_id": "abcdef123456789", "sig": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
    # Bad base64
    b'{"key_id": "abcdef1234567890", "sig": "!!!not base64!!!"}',
    # 63-byte sig (should be 64)
    b'{"key_id": "abcdef1234567890", "sig": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="}',
])
def test_verify_catalog_bad_sig_formats_raise_invalid(keys_dict, bad_sig):
    """Various bad sig formats -> CatalogSignatureInvalid."""
    catalog_bytes = b'{"serial": 1}'
    with pytest.raises(catalog_sig.CatalogSignatureInvalid):
        catalog_sig.verify_catalog(catalog_bytes, bad_sig, keys_dict)


def test_verify_catalog_unknown_key_id_raises_key_unknown(keys_dict):
    """key_id not in keys -> CatalogKeyUnknown."""
    catalog_bytes = b'{"serial": 1}'
    sig_bytes = json.dumps({"key_id": "ffffffffffffffff", "sig": base64.b64encode(b"x" * 64).decode()}).encode()
    with pytest.raises(catalog_sig.CatalogKeyUnknown):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)


def test_verify_catalog_empty_keys_raises_key_unknown():
    """keys={} -> CatalogKeyUnknown."""
    catalog_bytes = b'{"serial": 1}'
    sig_bytes = json.dumps({"key_id": "abcdef1234567890", "sig": base64.b64encode(b"x" * 64).decode()}).encode()
    with pytest.raises(catalog_sig.CatalogKeyUnknown):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, {})


def test_verify_catalog_two_keys_sig_by_key_b_with_key_a_id_raises_invalid(test_key):
    """Two pinned keys, sig made by key B but key_id of key A -> CatalogSignatureInvalid (no try-all fallback)."""
    # Create two keys
    private_key_a = Ed25519PrivateKey.generate()
    public_key_a_raw = private_key_a.public_key().public_bytes_raw()
    key_id_a = hashlib.sha256(public_key_a_raw).hexdigest()[:16]
    
    private_key_b = Ed25519PrivateKey.generate()
    public_key_b_raw = private_key_b.public_key().public_bytes_raw()
    key_id_b = hashlib.sha256(public_key_b_raw).hexdigest()[:16]
    
    keys = {
        key_id_a: base64.b64encode(public_key_a_raw).decode("ascii"),
        key_id_b: base64.b64encode(public_key_b_raw).decode("ascii"),
    }
    
    catalog_bytes = b'{"serial": 1}'
    # Sign with key B but use key_id of key A
    sig = private_key_b.sign(catalog_bytes)
    sig_bytes = make_sig_json(key_id_a, sig)
    
    with pytest.raises(catalog_sig.CatalogSignatureInvalid):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys)


def test_verify_catalog_serial_missing_raises_regression(keys_dict, test_key):
    """serial missing -> CatalogSerialRegression."""
    private_key, _, _ = test_key
    catalog_bytes = b'{"apps": {}}'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    with pytest.raises(catalog_sig.CatalogSerialRegression, match="catalog serial missing or invalid"):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)


def test_verify_catalog_serial_string_raises_regression(keys_dict, test_key):
    """serial as string -> CatalogSerialRegression."""
    private_key, _, _ = test_key
    catalog_bytes = b'{"serial": "3"}'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    with pytest.raises(catalog_sig.CatalogSerialRegression):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)


def test_verify_catalog_serial_true_raises_regression(keys_dict, test_key):
    """serial as True (bool) -> CatalogSerialRegression."""
    private_key, _, _ = test_key
    catalog_bytes = b'{"serial": true}'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    with pytest.raises(catalog_sig.CatalogSerialRegression):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)


def test_verify_catalog_serial_negative_raises_regression(keys_dict, test_key):
    """serial negative -> CatalogSerialRegression."""
    private_key, _, _ = test_key
    catalog_bytes = b'{"serial": -1}'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    with pytest.raises(catalog_sig.CatalogSerialRegression):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)


def test_verify_catalog_not_json_object_raises_orberror(keys_dict, test_key):
    """Catalog not a JSON object -> OrbError."""
    private_key, _, _ = test_key
    catalog_bytes = b'[1, 2, 3]'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    with pytest.raises(OrbError, match="catalog is not a JSON object"):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)


def test_verify_catalog_expires_valid_iso_accepted(keys_dict, test_key):
    """expires valid ISO 8601 -> accepted."""
    private_key, _, _ = test_key
    catalog_bytes = b'{"serial": 1, "expires": "2027-01-01T00:00:00+00:00"}'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    result = catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)
    assert result["serial"] == 1
    assert result["expires"] == "2027-01-01T00:00:00+00:00"


def test_verify_catalog_expires_past_still_accepted(keys_dict, test_key):
    """expires in the past -> still accepted (not enforced)."""
    private_key, _, _ = test_key
    catalog_bytes = b'{"serial": 1, "expires": "2020-01-01T00:00:00+00:00"}'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    result = catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)
    assert result["serial"] == 1


def test_verify_catalog_expires_invalid_type_raises_orberror(keys_dict, test_key):
    """expires as number -> OrbError."""
    private_key, _, _ = test_key
    catalog_bytes = b'{"serial": 1, "expires": 123}'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    with pytest.raises(OrbError, match="catalog expires is not ISO 8601"):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, keys_dict)


# check_serial tests
def test_check_serial_no_state_file_returns_true(tmp_path):
    """No state file -> return True (first accept)."""
    state_path = tmp_path / "catalog_state.json"
    digest = hashlib.sha256(b'{"serial": 1}').hexdigest()
    assert catalog_sig.check_serial(state_path, 1, digest) is True


def test_check_serial_state_serial_5_catalog_4_raises_regression(tmp_path):
    """State serial 5, catalog serial 4 -> CatalogSerialRegression and state unchanged."""
    state_path = tmp_path / "catalog_state.json"
    catalog_bytes_5 = b'{"serial": 5}'
    digest_5 = hashlib.sha256(catalog_bytes_5).hexdigest()
    catalog_sig.record_serial(state_path, 5, digest_5)
    
    catalog_bytes_4 = b'{"serial": 4}'
    digest_4 = hashlib.sha256(catalog_bytes_4).hexdigest()
    
    with pytest.raises(catalog_sig.CatalogSerialRegression):
        catalog_sig.check_serial(state_path, 4, digest_4)
    
    # State unchanged
    state = json.loads(state_path.read_text())
    assert state["serial"] == 5
    assert state["sha256"] == digest_5


def test_check_serial_state_serial_5_catalog_5_same_bytes_returns_false(tmp_path):
    """State serial 5, catalog serial 5 same bytes -> return False (accepted, nothing new)."""
    state_path = tmp_path / "catalog_state.json"
    catalog_bytes = b'{"serial": 5}'
    digest = hashlib.sha256(catalog_bytes).hexdigest()
    catalog_sig.record_serial(state_path, 5, digest)
    
    assert catalog_sig.check_serial(state_path, 5, digest) is False
    
    # State unchanged
    state = json.loads(state_path.read_text())
    assert state["serial"] == 5
    assert state["sha256"] == digest


def test_check_serial_state_serial_5_catalog_5_different_bytes_raises_regression(tmp_path):
    """State serial 5, catalog serial 5 different bytes -> CatalogSerialRegression."""
    state_path = tmp_path / "catalog_state.json"
    catalog_bytes_5a = b'{"serial": 5, "a": 1}'
    digest_5a = hashlib.sha256(catalog_bytes_5a).hexdigest()
    catalog_sig.record_serial(state_path, 5, digest_5a)
    
    catalog_bytes_5b = b'{"serial": 5, "b": 2}'
    digest_5b = hashlib.sha256(catalog_bytes_5b).hexdigest()
    
    with pytest.raises(catalog_sig.CatalogSerialRegression, match="same serial, different catalog bytes"):
        catalog_sig.check_serial(state_path, 5, digest_5b)
    
    # State unchanged
    state = json.loads(state_path.read_text())
    assert state["serial"] == 5
    assert state["sha256"] == digest_5a


def test_check_serial_serial_6_after_state_5_returns_true(tmp_path):
    """Serial 6 after state 5 -> return True."""
    state_path = tmp_path / "catalog_state.json"
    catalog_bytes_5 = b'{"serial": 5}'
    digest_5 = hashlib.sha256(catalog_bytes_5).hexdigest()
    catalog_sig.record_serial(state_path, 5, digest_5)
    
    catalog_bytes_6 = b'{"serial": 6}'
    digest_6 = hashlib.sha256(catalog_bytes_6).hexdigest()
    
    assert catalog_sig.check_serial(state_path, 6, digest_6) is True


@pytest.mark.parametrize("corrupt_content", [
    b"garbage bytes",
    b"",
    b'{"serial": "5"}',  # serial as string
    b'{"sha256": "not64chars"}',  # missing serial, bad sha256
    b'{"serial": 5, "sha256": "' + b"g" * 64 + b'"}',  # sha256 not hex
    b'{"serial": 5, "sha256": "' + b"G" * 64 + b'"}',  # sha256 uppercase
    b'{"serial": 5, "sha256": "' + b"0" * 63 + b'"}',  # sha256 too short
    b'{"serial": true}',  # serial is bool
])
def test_check_serial_corrupt_state_raises_state_corrupt(tmp_path, corrupt_content):
    """Corrupt state file -> CatalogStateCorrupt (never fall back to serial 0)."""
    state_path = tmp_path / "catalog_state.json"
    state_path.write_bytes(corrupt_content)
    
    digest = hashlib.sha256(b'{"serial": 1}').hexdigest()
    with pytest.raises(catalog_sig.CatalogStateCorrupt):
        catalog_sig.check_serial(state_path, 1, digest)


# record_serial tests
def test_record_serial_creates_parent_dirs_and_writes_atomic(tmp_path):
    """record_serial creates parent dirs and writes atomically with mode 0o600."""
    state_path = tmp_path / "orb" / "catalog_state.json"
    digest = hashlib.sha256(b'{"serial": 1}').hexdigest()
    catalog_sig.record_serial(state_path, 1, digest)
    
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["serial"] == 1
    assert state["sha256"] == digest
    # Check mode is 0o600
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


# accept_catalog tests
def test_accept_catalog_good_sig_serial_3_writes_state(keys_dict, test_key, tmp_path):
    """accept_catalog with good sig + serial 3 -> returns dict and writes state."""
    private_key, _, _ = test_key
    state_path = tmp_path / "orb" / "catalog_state.json"
    catalog = {"serial": 3, "apps": {"foo": {"versions": []}}}
    catalog_bytes = json.dumps(catalog).encode("utf-8")
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    
    result = catalog_sig.accept_catalog(catalog_bytes, sig_bytes, state_path, keys_dict)
    assert result == catalog
    
    # State written
    state = json.loads(state_path.read_text())
    assert state["serial"] == 3
    assert state["sha256"] == hashlib.sha256(catalog_bytes).hexdigest()


def test_accept_catalog_sig_none_raises_missing(keys_dict, tmp_path):
    """accept_catalog with sig None -> CatalogSignatureMissing."""
    state_path = tmp_path / "orb" / "catalog_state.json"
    catalog_bytes = b'{"serial": 1}'
    with pytest.raises(catalog_sig.CatalogSignatureMissing):
        catalog_sig.accept_catalog(catalog_bytes, None, state_path, keys_dict)


def test_accept_catalog_does_not_write_state_on_regression(tmp_path, keys_dict, test_key):
    """accept_catalog does not write state when check_serial fails."""
    private_key, _, _ = test_key
    state_path = tmp_path / "orb" / "catalog_state.json"
    # Pre-populate state with serial 5
    catalog_bytes_5 = b'{"serial": 5}'
    digest_5 = hashlib.sha256(catalog_bytes_5).hexdigest()
    catalog_sig.record_serial(state_path, 5, digest_5)
    
    # Try to accept serial 4
    catalog_bytes_4 = b'{"serial": 4}'
    sig_bytes = sign_catalog(private_key, catalog_bytes_4)
    
    with pytest.raises(catalog_sig.CatalogSerialRegression):
        catalog_sig.accept_catalog(catalog_bytes_4, sig_bytes, state_path, keys_dict)
    
    # State unchanged
    state = json.loads(state_path.read_text())
    assert state["serial"] == 5
    assert state["sha256"] == digest_5


# reset_state tests
def test_reset_state_unlinks_file_if_exists(tmp_path):
    """reset_state unlinks state file if it exists."""
    state_path = tmp_path / "orb" / "catalog_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"serial": 5, "sha256": "abc"}')
    
    catalog_sig.reset_state(state_path)
    assert not state_path.exists()


def test_reset_state_missing_ok(tmp_path):
    """reset_state does nothing if file does not exist (missing_ok=True)."""
    state_path = tmp_path / "orb" / "catalog_state.json"
    # Should not raise
    catalog_sig.reset_state(state_path)


def test_reset_state_then_serial_1_accepted(keys_dict, test_key, tmp_path):
    """reset_state then serial 1 -> accepted."""
    private_key, _, _ = test_key
    state_path = tmp_path / "orb" / "catalog_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"serial": 5, "sha256": "abc"}')
    
    catalog_sig.reset_state(state_path)
    assert not state_path.exists()
    
    catalog_bytes = b'{"serial": 1}'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    result = catalog_sig.accept_catalog(catalog_bytes, sig_bytes, state_path, keys_dict)
    assert result["serial"] == 1


# CATALOG_KEYS tests
def test_CATALOG_KEYS_is_empty_dict():
    """CATALOG_KEYS is an empty dict by default."""
    assert catalog_sig.CATALOG_KEYS == {}


# verify_catalog uses CATALOG_KEYS when keys=None
def test_verify_catalog_keys_none_uses_CATALOG_KEYS(test_key):
    """verify_catalog with keys=None uses CATALOG_KEYS."""
    private_key, _, _ = test_key
    catalog_bytes = b'{"serial": 1}'
    sig_bytes = sign_catalog(private_key, catalog_bytes)
    # CATALOG_KEYS is empty, so should raise CatalogKeyUnknown
    with pytest.raises(catalog_sig.CatalogKeyUnknown):
        catalog_sig.verify_catalog(catalog_bytes, sig_bytes, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])