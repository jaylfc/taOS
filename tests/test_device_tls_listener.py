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
async def test_mitm_proxy_fingerprint_differs(app):
    import datetime
    import socket

    import uvicorn

    server_cert_path = app.state.device_tls_cert_path
    server_key_path = app.state.device_tls_key_path
    assert server_cert_path is not None

    server_cert_pem = server_cert_path.read_bytes()
    server_cert = x509.load_pem_x509_certificate(server_cert_pem)
    server_fp = _fingerprint_from_der(server_cert.public_bytes(serialization.Encoding.DER))

    proxy_dir = Path("/tmp")
    proxy_dir.mkdir(parents=True, exist_ok=True)
    proxy_cert_path = proxy_dir / "proxy_tls.crt"
    proxy_key_path = proxy_dir / "proxy_tls.key"

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

    proxy_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    proxy_ctx.load_cert_chain(str(proxy_cert_path), str(proxy_key_path))

    backend_ctx = ssl.create_default_context()
    backend_ctx.check_hostname = False
    backend_ctx.verify_mode = ssl.CERT_NONE
    backend_ctx.load_cert_chain(str(server_cert_path), str(server_key_path))

    async def _handle(client_reader, client_writer):
        try:
            backend_reader, backend_writer = await asyncio.open_connection(
                "127.0.0.1", 6974, ssl=backend_ctx
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
        app, host="127.0.0.1", port=6974,
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
# Utility
# ---------------------------------------------------------------------------

def _fingerprint_from_der(cert_der: bytes) -> str:
    digest = hashlib.sha256(cert_der).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))
