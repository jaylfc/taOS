"""Device TLS: self-signed cert generation and persistence for the :6974 listener."""

from __future__ import annotations

import datetime
import hashlib
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from tinyagentos.atomic_io import atomic_write_bytes

_CERT_NAME = "device_tls.crt"
_KEY_NAME = "device_tls.key"


def _fingerprint_from_der(cert_der: bytes) -> str:
    digest = hashlib.sha256(cert_der).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def _write_file_atomic_0600(path: Path, data: bytes) -> None:
    """Write data to path atomically with mode 0o600.

    ``atomic_write_bytes`` is the only writer allowed to promote a temp file
    (see ``tests/test_config_atomic.py``): every hand-rolled copy of
    temp-file-plus-replace has dropped the ``fsync`` of the file and of the
    parent directory, so a power cut could leave the private key truncated or
    NUL-filled while its metadata looked intact.

    The mode is passed to ``atomic_write_bytes`` rather than applied with a
    ``chmod`` after the write: the private key must never exist on disk with a
    wider mode, not even for the instant between the write and the chmod.
    ``atomic_write_bytes`` therefore creates the temp file 0o600 (os.open's
    ``mode`` argument, which the umask can only narrow) and chmods it to 0o600
    before the rename, since os.open honours the umask and chmod does not.
    """
    atomic_write_bytes(path, data, mode=0o600)


def load_or_create_device_tls_cert(data_dir: Path) -> tuple[Path, Path, str]:
    cert_path = data_dir / _CERT_NAME
    key_path = data_dir / _KEY_NAME

    if cert_path.exists() and key_path.exists():
        try:
            cert_pem = cert_path.read_bytes()
            cert = x509.load_pem_x509_certificate(cert_pem)
            key_pem = key_path.read_bytes()
            key = serialization.load_pem_private_key(key_pem, password=None)
            if key.public_key().public_numbers() == cert.public_key().public_numbers():
                fp = _fingerprint_from_der(cert.public_bytes(serialization.Encoding.DER))
                return cert_path, key_path, fp
        except (ValueError, TypeError):
            # Cert or key parse failure, fall through to regenerate both
            pass
        # Mismatch or parsing failure, fall through to regenerate both

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

    _write_file_atomic_0600(key_path, key_pem)
    _write_file_atomic_0600(cert_path, cert_pem)

    fp = _fingerprint_from_der(cert.public_bytes(serialization.Encoding.DER))
    return cert_path, key_path, fp
