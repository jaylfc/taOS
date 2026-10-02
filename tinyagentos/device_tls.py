"""Device TLS: self-signed cert generation and persistence for the :6974 listener."""

from __future__ import annotations

import datetime
import hashlib
import os
import tempfile
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


def _write_file_atomic_0600(path: Path, data: bytes) -> None:
    """Write data to path atomically with mode 0o600.

    Creates a temporary file in the same directory, writes data, then
    atomically replaces the target. The file is created with 0o600 from
    the start (no umask window). Any failure to restrict permissions is
    raised, not swallowed.
    """
    dir_fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, delete=False, prefix=f".{path.name}.", suffix=".tmp"
        ) as tmp:
            tmp_fd = tmp.fileno()
            # Ensure the temp file is 0o600 from creation
            os.fchmod(tmp_fd, 0o600)
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp_fd)
            tmp_name = tmp.name
        # Atomic replace
        os.replace(tmp_name, path)
    finally:
        os.close(dir_fd)


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

    _write_file_atomic_0600(cert_path, cert_pem)
    _write_file_atomic_0600(key_path, key_pem)

    fp = _fingerprint_from_der(cert.public_bytes(serialization.Encoding.DER))
    return cert_path, key_path, fp
