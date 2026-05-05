"""VAPID keypair bootstrap for Web Push.

One P-256 keypair per host install. The private key is persisted to
``<data_dir>/vapid.pem`` on first call (mode 0600) and reloaded on
subsequent calls. Idempotent — repeated calls return the same keys.

The public key is returned in the uncompressed-point base64url-without-
padding form that the browser's PushManager.subscribe() expects as its
``applicationServerKey``.
"""
from __future__ import annotations

import base64
import os
import pathlib

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid01

_VAPID_FILENAME = "vapid.pem"


def load_or_create_vapid_keypair(data_dir: pathlib.Path) -> tuple[str, str]:
    """Return (public_key_b64url, private_key_pem_str).

    Loads from <data_dir>/vapid.pem if present; otherwise generates a fresh
    P-256 keypair, persists the private key PEM to disk with mode 0600, and
    returns both keys. Idempotent — second call returns same keys.

    The public key is returned in uncompressed-point base64url-without-padding
    form (the format applicationServerKey expects in PushManager.subscribe).
    """
    pem_path = data_dir / _VAPID_FILENAME

    if pem_path.exists():
        vapid = Vapid01.from_pem(pem_path.read_bytes())
    else:
        vapid = Vapid01()
        vapid.generate_keys()
        pem_bytes = vapid.private_pem()
        # Write with mode 0600 atomically using os.open so no window exists
        # where the file is readable by other users.
        fd = os.open(str(pem_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, pem_bytes)
        finally:
            os.close(fd)

    pub_bytes = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    public_key_b64url = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode("ascii")
    private_key_pem_str = pem_path.read_text()

    return public_key_b64url, private_key_pem_str
