"""Device TLS: self-signed cert generation and persistence for the :6974 listener."""

from __future__ import annotations

import datetime
import hashlib
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_CERT_NAME = "device_tls.crt"
_KEY_NAME = "device_tls.key"


def _fingerprint_from_der(cert_der: bytes) -> str:
    digest = hashlib.sha256(cert_der).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def load_or_create_device_tls_cert(data_dir: Path) -> tuple[Path, Path, str]:
    cert_path = data_dir / _CERT_NAME
    key_path = data_dir / _KEY_NAME

    if cert_path.exists() and key_path.exists():
        cert_pem = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)
        fp = _fingerprint_from_der(cert.public_bytes(serialization.Encoding.DER))
        return cert_path, key_path, fp

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "taOS Orb device TLS"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    try:
        os.chmod(cert_path, 0o600)
        os.chmod(key_path, 0o600)
    except OSError:
        pass

    fp = _fingerprint_from_der(cert.public_bytes(serialization.Encoding.DER))
    return cert_path, key_path, fp
