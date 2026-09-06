"""Tests for the GitHub App authentication module."""
from __future__ import annotations

import json
import base64

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tinyagentos.github_app import generate_jwt, _b64url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_rsa_key() -> str:
    """Return a PEM-encoded RSA private key for testing."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestB64Url:
    def test_encodes_basic(self):
        assert _b64url(b"test") == "dGVzdA"

    def test_no_padding(self):
        # Standard base64 adds '=', b64url strips it
        result = _b64url(b"abc")
        assert "=" not in result


class TestGenerateJwt:
    def test_returns_three_part_token(self):
        pem = _generate_rsa_key()
        jwt = generate_jwt("12345", pem)
        parts = jwt.split(".")
        assert len(parts) == 3

    def test_header_is_valid_rs256(self):
        pem = _generate_rsa_key()
        jwt = generate_jwt("12345", pem)
        parts = jwt.split(".")
        header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
        assert header["alg"] == "RS256"
        assert header["typ"] == "JWT"

    def test_claims_include_app_id_as_iss(self):
        pem = _generate_rsa_key()
        jwt = generate_jwt("67890", pem)
        parts = jwt.split(".")
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        assert claims["iss"] == "67890"

    def test_claims_have_iat_and_exp(self):
        pem = _generate_rsa_key()
        jwt = generate_jwt("12345", pem)
        parts = jwt.split(".")
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        assert "iat" in claims
        assert "exp" in claims
        assert claims["exp"] > claims["iat"]

    def test_exp_is_within_10_minutes(self):
        pem = _generate_rsa_key()
        jwt = generate_jwt("12345", pem)
        parts = jwt.split(".")
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        # Expiry should be close to iat + 600 (10 min)
        assert 540 < claims["exp"] - claims["iat"] <= 660

    def test_signature_is_base64url(self):
        pem = _generate_rsa_key()
        jwt = generate_jwt("12345", pem)
        parts = jwt.split(".")
        # Signature should be valid base64url (no padding)
        sig = parts[2]
        decoded = base64.urlsafe_b64decode(sig + "==")
        assert len(decoded) > 0

    def test_same_input_produces_different_jwt(self):
        pem = _generate_rsa_key()
        jwt1 = generate_jwt("12345", pem)
        import time
        time.sleep(1.5)  # Ensure timestamp changes (second-level granularity)
        jwt2 = generate_jwt("12345", pem)
        # Different timestamps → different JWTs
        assert jwt1 != jwt2

    def test_rejects_non_rsa_key(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        ec_key = ec.generate_private_key(ec.SECP256R1())
        pem = ec_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        with pytest.raises(ValueError, match="RSA"):
            generate_jwt("12345", pem)

    def test_app_id_is_cast_to_string(self):
        pem = _generate_rsa_key()
        jwt = generate_jwt(12345, pem)  # type: ignore — test int coercion
        parts = jwt.split(".")
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        assert claims["iss"] == "12345"


class TestJwtHeaderClaimsDecoding:
    def test_iat_is_reasonable(self):
        """iat should be within 2 minutes of now."""
        import time
        pem = _generate_rsa_key()
        jwt = generate_jwt("12345", pem)
        parts = jwt.split(".")
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        now = int(time.time())
        assert abs(claims["iat"] - now) < 120
