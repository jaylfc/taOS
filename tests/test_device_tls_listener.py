"""RED-FIRST: TLS-only device listener :6974 + server-cert fingerprint in pairing."""

from __future__ import annotations

import asyncio
import hashlib
import ssl
from pathlib import Path

import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from httpx import ASGITransport, AsyncClient

from tinyagentos.device_tls import load_or_create_device_tls_cert


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# ---------------------------------------------------------------------------
# (a) cert generated once and persists
# ---------------------------------------------------------------------------

def test_cert_generated_once_and_persists(tmp_path):
    _, _, fp1 = load_or_create_device_tls_cert(tmp_path)
    _, _, fp2 = load_or_create_device_tls_cert(tmp_path)
    assert fp1 == fp2


# ---------------------------------------------------------------------------
# Shared app fixture with all stores initialised
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def app(tmp_path):
    from tinyagentos.app import create_app
    from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(__import__("yaml").safe_dump(config))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    app.state.vapid_keypair = load_or_create_vapid_keypair(tmp_path)

    # Initialise stores that the routes under test need.
    await app.state.device_store.init()
    await app.state.decision_store.init()
    await app.state.device_pair_requests.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    app.state._startup_complete = True
    return app


def _make_client(app, base="http://test", cookies=None):
    return AsyncClient(transport=ASGITransport(app=app), base_url=base, cookies=cookies)


# ---------------------------------------------------------------------------
# (b) embedded token refused on plain listener
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embedded_token_refused_on_plain_listener(app):
    assert hasattr(app.state, "device_tls_fingerprint")
    assert app.state.device_tls_fingerprint is not None

    dev = await app.state.device_store.register(user_id="u1", platform="embedded")
    phone = await app.state.device_store.register(user_id="u1", platform="ios")

    async with _make_client(app) as c:
        r = await c.get("/api/decisions", headers=_bearer(dev["scoped_token"]))
    assert r.status_code == 403
    assert r.json()["detail"] == {"error": "device_tls_required"}

    async with _make_client(app) as c:
        r = await c.get("/api/decisions", headers=_bearer(phone["scoped_token"]))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# (c) pair decision carries server cert fingerprint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pair_decision_carries_server_cert_fingerprint(app):
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography import x509

    cert_path = app.state.device_tls_cert_path
    assert cert_path is not None
    cert_pem = cert_path.read_bytes()
    cert = x509.load_pem_x509_certificate(cert_pem)
    expected_fp = _fingerprint_from_der(cert.public_bytes(serialization.Encoding.DER))

    record = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=record["id"], long_lived=True)
    cookies = {"taos_session": token}

    async with _make_client(app, cookies=cookies) as c:
        r = await c.post(
            "/api/devices/pair-requests",
            json={"platform": "ios", "display_name": "Test Phone"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body.get("server_cert_fingerprint") == expected_fp

    async with _make_client(app, cookies=cookies) as c:
        items = (await c.get("/api/decisions")).json()["items"]
    matches = [
        d for d in items
        if (d.get("metadata") or {}).get("kind") == "device_pairing"
        and (d.get("metadata") or {}).get("pair_request_id") == body["pair_request_id"]
    ]
    assert len(matches) == 1
    assert matches[0]["metadata"].get("server_cert_fingerprint") == expected_fp


# ---------------------------------------------------------------------------
# (d) MITM proxy fingerprint differs from server cert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mitm_proxy_fingerprint_differs(app, tmp_path):
    import datetime
    import socket

    import uvicorn

    server_cert_path = app.state.device_tls_cert_path
    server_key_path = app.state.device_tls_key_path
    assert server_cert_path is not None

    server_cert_pem = server_cert_path.read_bytes()
    server_cert = x509.load_pem_x509_certificate(server_cert_pem)
    server_fp = _fingerprint_from_der(server_cert.public_bytes(serialization.Encoding.DER))

    proxy_cert_path = tmp_path / "proxy_tls.crt"
    proxy_key_path = tmp_path / "proxy_tls.key"

    proxy_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MITM proxy")])
    now = datetime.datetime.now(datetime.timezone.utc)
    proxy_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(proxy_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(proxy_key, hashes.SHA256())
    )
    proxy_cert_path.write_bytes(proxy_cert.public_bytes(serialization.Encoding.PEM))
    proxy_key_path.write_bytes(
        proxy_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    proxy_fp = _fingerprint_from_der(proxy_cert.public_bytes(serialization.Encoding.DER))

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        proxy_port = s.getsockname()[1]

    # Bind backend server to ephemeral port
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        backend_port = s.getsockname()[1]

    proxy_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    proxy_ctx.load_cert_chain(str(proxy_cert_path), str(proxy_key_path))

    backend_ctx = ssl.create_default_context()
    backend_ctx.check_hostname = False
    backend_ctx.verify_mode = ssl.CERT_NONE
    backend_ctx.load_cert_chain(str(server_cert_path), str(server_key_path))

    async def _handle(client_reader, client_writer):
        try:
            backend_reader, backend_writer = await asyncio.open_connection(
                "127.0.0.1", backend_port, ssl=backend_ctx
            )
            async def pipe(src, dst):
                try:
                    while True:
                        data = await src.read(65536)
                        if not data:
                            break
                        dst.write(data)
                        await dst.drain()
                except Exception:
                    pass
                finally:
                    dst.close()

            await asyncio.gather(pipe(client_reader, backend_writer), pipe(backend_reader, client_writer))
        except Exception:
            pass
        finally:
            client_writer.close()

    proxy_server = await asyncio.start_server(_handle, "127.0.0.1", proxy_port, ssl=proxy_ctx)

    config = uvicorn.Config(
        app, host="127.0.0.1", port=backend_port,
        ssl_certfile=str(server_cert_path), ssl_keyfile=str(server_key_path),
        lifespan="off", log_level="warning",
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        for _ in range(300):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started, "uvicorn did not start"

        import httpx

        record = app.state.auth.find_user("admin")
        token = app.state.auth.create_session(user_id=record["id"], long_lived=True)
        cookies = {"taos_session": token}

        async with httpx.AsyncClient(verify=False, timeout=10, cookies=cookies) as c:
            r = await c.post(
                f"https://127.0.0.1:{proxy_port}/api/devices/pair-requests",
                json={"platform": "ios", "display_name": "MITM Test"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body.get("server_cert_fingerprint") == server_fp
        assert proxy_fp != server_fp
    finally:
        server.should_exit = True
        proxy_server.close()
        await proxy_server.wait_closed()
        await asyncio.wait_for(serve_task, 10)


# ---------------------------------------------------------------------------
# (e) RED-FIRST: TLS config must have lifespan="off" to not re-run lifespan
# ---------------------------------------------------------------------------

def test_tls_listener_does_not_rerun_lifespan(tmp_path):
    """TLS listener config must have lifespan="off" so it doesn't re-run app lifespan."""
    from unittest.mock import patch, MagicMock

    from tinyagentos.__main__ import _serve_dual_port
    from tinyagentos.app import create_app

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(__import__("yaml").safe_dump(config))
    (tmp_path / ".setup_complete").touch()

    app = create_app(data_dir=tmp_path)
    from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair
    app.state.vapid_keypair = load_or_create_vapid_keypair(tmp_path)

    # Capture the uvicorn.Config objects passed to the server class
    captured_configs = {}

    import uvicorn

    original_server_init = uvicorn.Server.__init__

    def capturing_init(self, config):
        captured_configs[id(config)] = config
        return original_server_init(self, config)

    with patch("uvicorn.Server.__init__", capturing_init):
        with patch("tinyagentos.__main__._serve_until_first_exit") as mock_serve:
            mock_serve.return_value = True
            _serve_dual_port(app, host="127.0.0.1", port=6969, proxy_port=0, gateway_port=0, tls_port=6974)

    # Find the TLS config and check its lifespan
    tls_config = None
    for config_obj in captured_configs.values():
        if getattr(config_obj, "ssl_certfile", None) is not None:
            tls_config = config_obj
            break

    assert tls_config is not None, "TLS config should have been created"
    assert tls_config.lifespan == "off", f"TLS config lifespan should be 'off', got {tls_config.lifespan!r}"


# ---------------------------------------------------------------------------
# (f) RED-FIRST: TLS bind failure must not take controller down
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tls_listener_bind_failure_keeps_controller_up():
    """TLS server bind failure must not crash _serve_until_first_exit while main server runs."""
    import uvicorn
    import asyncio

    from tinyagentos.__main__ import _serve_until_first_exit

    # Local _NoSignalServer class (mirrors the one in __main__)
    import contextlib
    class _NoSignalServer(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):
            yield

    from unittest.mock import MagicMock

    main_config = uvicorn.Config(
        MagicMock(), host="127.0.0.1", port=6969, backlog=128
    )
    tls_config = uvicorn.Config(
        MagicMock(), host="127.0.0.1", port=6974, backlog=128,
        ssl_certfile="/tmp/cert.pem", ssl_keyfile="/tmp/key.pem",
    )

    main_server = _NoSignalServer(main_config)
    tls_server = _NoSignalServer(tls_config)

    # Make main server serve() succeed (reach started state)
    async def main_serve():
        main_server.started = True
        await asyncio.sleep(10)  # Keep running

    # Make TLS server serve() raise immediately (bind failure)
    async def tls_serve_fail():
        raise OSError("Address already in use")

    main_server.serve = main_serve
    tls_server.serve = tls_serve_fail

    # Should not raise; main server should still be considered "started"
    result = await _serve_until_first_exit(main_server, tls_server=tls_server)
    assert result is True, "Main server should be marked as started despite TLS failure"


# ---------------------------------------------------------------------------
# (g) RED-FIRST: Private key must be created with mode 0o600 atomically
# ---------------------------------------------------------------------------

def test_device_tls_key_created_0600(tmp_path):
    """Private key file must be created with 0o600 permissions atomically."""
    import os
    import stat

    from tinyagentos.device_tls import load_or_create_device_tls_cert

    cert_path, key_path, _ = load_or_create_device_tls_cert(tmp_path)

    # Check key file mode is 0o600 (owner read/write only)
    key_mode = os.stat(key_path).st_mode
    assert stat.S_IMODE(key_mode) == 0o600, f"Key file mode should be 0o600, got {oct(stat.S_IMODE(key_mode))}"

    # Check cert file mode is also 0o600
    cert_mode = os.stat(cert_path).st_mode
    assert stat.S_IMODE(cert_mode) == 0o600, f"Cert file mode should be 0o600, got {oct(stat.S_IMODE(cert_mode))}"


# ---------------------------------------------------------------------------
# (h) fix-forward tsk-kwa27g: cert/key written through atomic_io, key never
#     wider than 0o600, and both writes are crash-safe (file + dir fsync)
# ---------------------------------------------------------------------------

def test_device_tls_key_written_through_atomic_io_with_0600(tmp_path, monkeypatch):
    """Both files go through atomic_io with mode=0o600 as a write parameter.

    The mode is a parameter of the write rather than a chmod applied after it,
    so the key cannot exist on disk with a wider mode even for the instant
    between the write and the chmod. Asserting only the final mode on disk
    passes on a chmod-after-write regression.
    """
    import stat

    from tinyagentos import device_tls

    calls = []
    real_write = device_tls.atomic_write_bytes

    def spy(path, data, **kwargs):
        calls.append((path, kwargs.get("mode")))
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(device_tls, "atomic_write_bytes", spy)

    cert_path, key_path, _ = device_tls.load_or_create_device_tls_cert(tmp_path)

    assert calls == [(key_path, 0o600), (cert_path, 0o600)], (
        f"expected atomic_write_bytes called with mode=0o600 for both files, got {calls}"
    )
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600, (
        f"Key file mode should be 0o600, got {oct(stat.S_IMODE(key_path.stat().st_mode))}"
    )


def test_device_tls_creates_no_temp_file_with_a_wider_mode(tmp_path, monkeypatch):
    """No temp name is ever created with group or other bits set.

    os.open's mode argument is filtered by the umask, so asking for 0o600 can
    only ever land narrower; asking for 0o644 and chmodding afterwards is the
    shape this test exists to reject.
    """
    import os
    import stat

    from tinyagentos import device_tls

    created_modes = []
    chmods = []
    real_open = os.open
    real_chmod = os.chmod

    def spy_open(path, flags, mode=0o777, *a, **kw):
        if flags & os.O_CREAT:
            created_modes.append(stat.S_IMODE(mode))
        return real_open(path, flags, mode, *a, **kw)

    def spy_chmod(path, mode, *a, **kw):
        chmods.append((os.path.basename(str(path)), stat.S_IMODE(mode)))
        return real_chmod(path, mode, *a, **kw)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "chmod", spy_chmod)

    device_tls.load_or_create_device_tls_cert(tmp_path)

    assert created_modes, "expected the temp files to be created with O_CREAT"
    assert [oct(m) for m in created_modes] == ["0o600", "0o600"], (
        f"every temp file must be created 0o600, got {[oct(m) for m in created_modes]}"
    )
    assert [oct(m) for _name, m in chmods] == ["0o600", "0o600"], (
        f"the pre-rename chmod must be 0o600, got {chmods}"
    )


# ---------------------------------------------------------------------------
# (i) mismatched cert/key detection
# ---------------------------------------------------------------------------

def test_device_tls_mismatched_cert_key_detection(tmp_path):
    """When cert and key files exist but don't match, generate new pair."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    # Generate and write a cert from one key
    key1 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "taOS Orb device TLS")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert1 = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key1.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key1, hashes.SHA256())
    )
    cert1_pem = cert1.public_bytes(serialization.Encoding.PEM)
    key1_pem = key1.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    (tmp_path / "device_tls.crt").write_bytes(cert1_pem)
    (tmp_path / "device_tls.key").write_bytes(key1_pem)

    # Generate a different key and write it alongside the cert
    key2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (tmp_path / "device_tls.key").write_bytes(key2.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))

    # Call load_or_create_device_tls_cert - it should detect mismatch and generate new pair
    cert_path, key_path, fp1 = load_or_create_device_tls_cert(tmp_path)

    # Verify it returned a NEW fingerprint (different from cert1)
    expected_fp1 = _fingerprint_from_der(cert1.public_bytes(serialization.Encoding.DER))
    assert fp1 != expected_fp1, "Should return new fingerprint after mismatch detection"

    # Verify the stored key now matches the stored cert
    cert_pem = cert_path.read_bytes()
    stored_cert = x509.load_pem_x509_certificate(cert_pem)
    stored_key_pem = key_path.read_bytes()
    stored_key = serialization.load_pem_private_key(stored_key_pem, password=None)
    assert stored_key.public_key().public_numbers() == stored_cert.public_key().public_numbers(), \
        "Stored key should match stored cert after regeneration"


def test_device_tls_writes_are_crash_safe(tmp_path, monkeypatch):
    """Four fsyncs for two files: each temp file, then each parent directory.

    Syncing only the file still loses the rename; syncing only the directory
    still lets the target come back NUL-filled.
    """
    import os

    from tinyagentos import device_tls

    calls = []
    real_fsync = os.fsync

    def counting_fsync(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting_fsync)

    device_tls.load_or_create_device_tls_cert(tmp_path)

    assert len(calls) == 4, (
        f"expected os.fsync called 4 times, got {len(calls)} -- each of the "
        "cert and key writes must fsync its temp file and its parent directory"
    )


def test_device_tls_leaves_no_temp_file_behind(tmp_path):
    """No orphan temp file survives the write (the old writer leaked on failure)."""
    from tinyagentos.device_tls import load_or_create_device_tls_cert

    load_or_create_device_tls_cert(tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["device_tls.crt", "device_tls.key"]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _fingerprint_from_der(cert_der: bytes) -> str:
    digest = hashlib.sha256(cert_der).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))
