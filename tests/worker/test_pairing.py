"""Tests for tinyagentos.worker.pairing and the pair CLI.

Coverage:
- sign_request_headers produces headers that PASS require_worker_hmac (round-trip)
- tampered body -> 401 bad_signature (round-trip against real app)
- save/load signing key round-trip + 0600 permissions on POSIX
- generate_pairing_code: correct length, unambiguous alphabet only
- code_hash: matches the controller-side sha256 expectation
- default_state_dir: respects TAOS_WORKER_STATE_DIR and XDG_STATE_HOME
- run_pairing happy path: announce -> 202 poll -> 200, key saved, returned
- run_pairing 410 re-announce path: re-announces with fresh code
- run_pairing timeout: raises TimeoutError
- run_pairing already-paired short-circuit: returns existing key
- WorkerAgent.register sends signed headers when key is present
- WorkerAgent.heartbeat sends signed headers when key is present
- WorkerAgent.register logs clear error when no key is loaded
"""
from __future__ import annotations

import hashlib
import hmac
import json as _json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(status_code: int, json_data: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json = MagicMock(return_value=json_data)
    return r


# ---------------------------------------------------------------------------
# unit: sign_request_headers
# ---------------------------------------------------------------------------

class TestSignRequestHeaders:
    def test_returns_three_headers(self):
        from tinyagentos.worker.pairing import sign_request_headers
        key = b"\x01" * 32
        h = sign_request_headers(key, "w1", "POST", "/api/cluster/workers", b'{"name":"w1"}')
        assert set(h) == {"X-TAOS-Worker-Name", "X-TAOS-Timestamp", "X-TAOS-Signature"}

    def test_worker_name_in_header(self):
        from tinyagentos.worker.pairing import sign_request_headers
        h = sign_request_headers(b"\x02" * 32, "my-worker", "POST", "/path", b"body")
        assert h["X-TAOS-Worker-Name"] == "my-worker"

    def test_timestamp_is_recent_unix_seconds(self):
        from tinyagentos.worker.pairing import sign_request_headers
        before = int(time.time())
        h = sign_request_headers(b"\x03" * 32, "w", "POST", "/path", b"")
        after = int(time.time())
        ts = int(h["X-TAOS-Timestamp"])
        assert before <= ts <= after

    def test_signature_matches_independent_hmac(self):
        from tinyagentos.worker.pairing import sign_request_headers
        key = b"\x04" * 32
        body = b'{"name":"w","url":"http://x"}'
        h = sign_request_headers(key, "w", "POST", "/api/cluster/workers", body)
        ts = h["X-TAOS-Timestamp"]
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{ts}.POST./api/cluster/workers.{body_hash}".encode()
        expected = hmac.new(key, message, hashlib.sha256).hexdigest()
        assert h["X-TAOS-Signature"] == expected

    def test_method_uppercased(self):
        from tinyagentos.worker.pairing import sign_request_headers
        key = b"\x05" * 32
        body = b"x"
        body_hash = hashlib.sha256(body).hexdigest()
        # Both a lowercase and an already-uppercase method must sign over the
        # normalised "POST" message, so the controller (which sees the real
        # request method) and the worker agree regardless of caller casing.
        for method in ("post", "POST"):
            h = sign_request_headers(key, "w", method, "/p", body)
            ts = h["X-TAOS-Timestamp"]
            message = f"{ts}.POST./p.{body_hash}".encode()
            expected = hmac.new(key, message, hashlib.sha256).hexdigest()
            assert h["X-TAOS-Signature"] == expected


# ---------------------------------------------------------------------------
# round-trip: sign_request_headers <-> require_worker_hmac (real app)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sign_request_headers_passes_hmac_gate(app, client):
    """Headers from sign_request_headers must satisfy require_worker_hmac."""
    from tinyagentos.worker.pairing import sign_request_headers

    # Pair a worker so the pairing store has a known key
    await app.state.cluster_pairing.init()
    code = "roundtrip-code-1"
    ch = hashlib.sha256(code.encode()).hexdigest()
    await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "rt-worker", "url": "http://10.9.0.1:9000",
              "platform": "linux", "code_hash": ch},
    )
    await client.post(
        "/api/cluster/pairing/confirm",
        json={"name": "rt-worker", "code": code},
    )
    resp = await client.post(
        "/api/cluster/pairing/claim",
        json={"name": "rt-worker", "code": code},
    )
    assert resp.status_code == 200
    key = bytes.fromhex(resp.json()["signing_key"])

    # Use sign_request_headers to sign a register request
    reg_body = _json.dumps({
        "name": "rt-worker", "url": "http://10.9.0.1:9000", "platform": "linux",
    }).encode()
    headers = sign_request_headers(key, "rt-worker", "POST", "/api/cluster/workers", reg_body)

    resp = await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={**headers, "content-type": "application/json"},
    )
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert resp.json()["status"] == "registered"

    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_tampered_body_returns_401_bad_signature(app, client):
    """A body tampered after signing must return 401 bad_signature."""
    from tinyagentos.worker.pairing import sign_request_headers

    await app.state.cluster_pairing.init()
    code = "roundtrip-code-2"
    ch = hashlib.sha256(code.encode()).hexdigest()
    await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "tamper-worker", "url": "http://10.9.0.2:9000",
              "platform": "linux", "code_hash": ch},
    )
    await client.post(
        "/api/cluster/pairing/confirm",
        json={"name": "tamper-worker", "code": code},
    )
    resp = await client.post(
        "/api/cluster/pairing/claim",
        json={"name": "tamper-worker", "code": code},
    )
    key = bytes.fromhex(resp.json()["signing_key"])

    # Sign the real body, but send a different (tampered) body
    real_body = _json.dumps({
        "name": "tamper-worker", "url": "http://10.9.0.2:9000",
    }).encode()
    headers = sign_request_headers(key, "tamper-worker", "POST",
                                   "/api/cluster/workers", real_body)

    tampered_body = _json.dumps({
        "name": "tamper-worker", "url": "http://evil.example.com:9000",
    }).encode()
    resp = await client.post(
        "/api/cluster/workers",
        content=tampered_body,
        headers={**headers, "content-type": "application/json"},
    )
    assert resp.status_code == 401
    assert resp.json().get("code") == "bad_signature"

    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# unit: key persistence
# ---------------------------------------------------------------------------

class TestKeyPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        from tinyagentos.worker.pairing import save_signing_key, load_signing_key, key_path
        key = b"\xab" * 32
        save_signing_key(tmp_path, key)
        loaded = load_signing_key(tmp_path)
        assert loaded == key

    def test_load_returns_none_when_missing(self, tmp_path):
        from tinyagentos.worker.pairing import load_signing_key
        assert load_signing_key(tmp_path) is None

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod not applicable on Windows")
    def test_save_sets_0600_permissions(self, tmp_path):
        from tinyagentos.worker.pairing import save_signing_key, key_path
        save_signing_key(tmp_path, b"\x00" * 32)
        mode = key_path(tmp_path).stat().st_mode & 0o777
        assert mode == 0o600

    def test_key_path_is_inside_state_dir(self, tmp_path):
        from tinyagentos.worker.pairing import key_path
        p = key_path(tmp_path)
        assert str(p).startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# unit: generate_pairing_code + code_hash
# ---------------------------------------------------------------------------

class TestPairingCode:
    _UNAMBIGUOUS = set("23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz")

    def test_length_8(self):
        from tinyagentos.worker.pairing import generate_pairing_code
        code = generate_pairing_code()
        assert len(code) == 8

    def test_only_unambiguous_chars(self):
        from tinyagentos.worker.pairing import generate_pairing_code
        for _ in range(50):
            code = generate_pairing_code()
            for ch in code:
                assert ch in self._UNAMBIGUOUS, f"ambiguous char {ch!r} in code {code!r}"

    def test_returns_string(self):
        from tinyagentos.worker.pairing import generate_pairing_code
        assert isinstance(generate_pairing_code(), str)

    def test_code_hash_matches_sha256(self):
        from tinyagentos.worker.pairing import code_hash
        code = "ABCD1234"
        expected = hashlib.sha256(code.encode()).hexdigest()
        assert code_hash(code) == expected

    def test_code_hash_is_64_hex_chars(self):
        from tinyagentos.worker.pairing import code_hash
        h = code_hash("X")
        assert len(h) == 64
        int(h, 16)  # raises if not hex


# ---------------------------------------------------------------------------
# unit: default_state_dir
# ---------------------------------------------------------------------------

class TestDefaultStateDir:
    def test_env_override(self, monkeypatch, tmp_path):
        from tinyagentos.worker import pairing as _p
        monkeypatch.setenv("TAOS_WORKER_STATE_DIR", str(tmp_path))
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        d = _p.default_state_dir()
        assert d == tmp_path

    @pytest.mark.skipif(sys.platform == "win32", reason="XDG not relevant on Windows")
    def test_xdg_state_home(self, monkeypatch, tmp_path):
        from tinyagentos.worker import pairing as _p
        monkeypatch.delenv("TAOS_WORKER_STATE_DIR", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        d = _p.default_state_dir()
        assert d == tmp_path / "taos-worker"

    def test_returns_path(self, monkeypatch):
        from tinyagentos.worker import pairing as _p
        monkeypatch.delenv("TAOS_WORKER_STATE_DIR", raising=False)
        d = _p.default_state_dir()
        assert isinstance(d, Path)


# ---------------------------------------------------------------------------
# async unit: run_pairing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_pairing_happy_path(tmp_path):
    """announce -> 202 -> 200 with key -> key saved and returned."""
    import secrets
    from tinyagentos.worker.pairing import run_pairing

    fake_key = secrets.token_bytes(32)
    call_count = 0

    async def mock_post(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "/pairing/announce" in url:
            return _make_mock_response(200, {"status": "pending"})
        if "/pairing/claim" in url:
            if call_count <= 2:
                # first claim -> 202 awaiting
                return _make_mock_response(202, {"status": "awaiting_confirm"})
            # second claim -> 200 with key
            return _make_mock_response(200, {"signing_key": fake_key.hex()})
        raise AssertionError(f"unexpected POST to {url}")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=mock_post)

    printed = []
    key = await run_pairing(
        mock_client,
        "http://controller:6969",
        "test-worker",
        "http://10.0.0.1:9000",
        "linux",
        tmp_path,
        poll_interval=0.01,
        timeout=10.0,
        print_fn=printed.append,
    )

    assert key == fake_key
    # Key must be persisted
    from tinyagentos.worker.pairing import load_signing_key
    assert load_signing_key(tmp_path) == fake_key
    # Pairing code banner must have been printed
    assert any("Pairing code" in str(m) or "pairing code" in str(m).lower() for m in printed)


@pytest.mark.asyncio
async def test_run_pairing_already_paired_short_circuit(tmp_path):
    """If a key already exists, run_pairing returns it without any HTTP calls."""
    from tinyagentos.worker.pairing import run_pairing, save_signing_key

    existing = b"\xcc" * 32
    save_signing_key(tmp_path, existing)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=AssertionError("should not be called"))

    key = await run_pairing(
        mock_client,
        "http://controller:6969",
        "w",
        "http://x",
        "linux",
        tmp_path,
        poll_interval=0.01,
        timeout=5.0,
    )
    assert key == existing
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_run_pairing_410_re_announces(tmp_path):
    """On 410 from claim, run_pairing re-announces with a fresh code and keeps going."""
    import secrets
    from tinyagentos.worker.pairing import run_pairing

    fake_key = secrets.token_bytes(32)
    announce_count = 0
    claim_count = 0

    async def mock_post(url, **kwargs):
        nonlocal announce_count, claim_count
        if "/pairing/announce" in url:
            announce_count += 1
            return _make_mock_response(200, {"status": "pending"})
        if "/pairing/claim" in url:
            claim_count += 1
            if claim_count == 1:
                return _make_mock_response(410, {"error": "expired"})
            if claim_count == 2:
                # After re-announce, poll once as awaiting
                return _make_mock_response(202, {"status": "awaiting_confirm"})
            return _make_mock_response(200, {"signing_key": fake_key.hex()})
        raise AssertionError(f"unexpected POST to {url}")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=mock_post)

    key = await run_pairing(
        mock_client,
        "http://controller:6969",
        "w",
        "http://x",
        "linux",
        tmp_path,
        poll_interval=0.01,
        timeout=10.0,
    )
    assert key == fake_key
    assert announce_count == 2  # re-announced once after 410


@pytest.mark.asyncio
async def test_run_pairing_timeout(tmp_path):
    """run_pairing raises TimeoutError if the claim never returns 200."""
    from tinyagentos.worker.pairing import run_pairing

    async def mock_post(url, **kwargs):
        if "/pairing/announce" in url:
            return _make_mock_response(200, {"status": "pending"})
        if "/pairing/claim" in url:
            return _make_mock_response(202, {"status": "awaiting_confirm"})
        raise AssertionError(f"unexpected POST to {url}")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=mock_post)

    with pytest.raises(TimeoutError):
        await run_pairing(
            mock_client,
            "http://controller:6969",
            "w",
            "http://x",
            "linux",
            tmp_path,
            poll_interval=0.01,
            timeout=0.1,
        )


# ---------------------------------------------------------------------------
# WorkerAgent: signed register / heartbeat
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_agent_register_sends_signed_headers(tmp_path):
    """WorkerAgent.register must attach HMAC headers when a key is present."""
    import secrets
    from tinyagentos.worker.pairing import save_signing_key
    from tinyagentos.worker.agent import WorkerAgent

    key = secrets.token_bytes(32)
    save_signing_key(tmp_path, key)

    agent = WorkerAgent("http://controller:6969", name="signed-worker", state_dir=tmp_path)

    captured = {}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    async def mock_post(url, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        captured["content"] = kwargs.get("content")
        return mock_response

    with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_cls:
        mock_c = AsyncMock()
        mock_c.__aenter__ = AsyncMock(return_value=mock_c)
        mock_c.__aexit__ = AsyncMock(return_value=False)
        mock_c.post = AsyncMock(side_effect=mock_post)
        mock_c.get = AsyncMock(side_effect=Exception("not running"))
        mock_cls.return_value = mock_c

        with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
            result = await agent.register()

    assert result is True
    assert "X-TAOS-Worker-Name" in captured["headers"]
    assert "X-TAOS-Timestamp" in captured["headers"]
    assert "X-TAOS-Signature" in captured["headers"]
    assert captured["headers"]["X-TAOS-Worker-Name"] == "signed-worker"
    # Body sent as bytes (content=), not json=
    assert isinstance(captured["content"], bytes)


@pytest.mark.asyncio
async def test_worker_agent_heartbeat_sends_signed_headers(tmp_path):
    """WorkerAgent.heartbeat must attach HMAC headers when a key is present."""
    import secrets
    from tinyagentos.worker.pairing import save_signing_key
    from tinyagentos.worker.agent import WorkerAgent

    key = secrets.token_bytes(32)
    save_signing_key(tmp_path, key)

    agent = WorkerAgent("http://controller:6969", name="hb-worker", state_dir=tmp_path)

    captured = {}

    mock_response = MagicMock()
    mock_response.status_code = 200

    async def mock_post(url, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        captured["content"] = kwargs.get("content")
        return mock_response

    with patch("tinyagentos.worker.agent.httpx.AsyncClient") as mock_cls:
        mock_c = AsyncMock()
        mock_c.__aenter__ = AsyncMock(return_value=mock_c)
        mock_c.__aexit__ = AsyncMock(return_value=False)
        mock_c.post = AsyncMock(side_effect=mock_post)
        mock_cls.return_value = mock_c

        with patch("tinyagentos.worker.agent.psutil.cpu_percent", return_value=10.0):
            with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
                result = await agent.heartbeat()

    assert result == 200
    assert "X-TAOS-Worker-Name" in captured["headers"]
    assert "X-TAOS-Signature" in captured["headers"]
    assert isinstance(captured["content"], bytes)


@pytest.mark.asyncio
async def test_worker_agent_register_logs_error_when_no_key(tmp_path, caplog):
    """WorkerAgent.register logs a clear actionable error if no signing key is present."""
    import logging
    from tinyagentos.worker.agent import WorkerAgent

    agent = WorkerAgent("http://controller:6969", name="unpaired-worker", state_dir=tmp_path)

    with caplog.at_level(logging.ERROR, logger="tinyagentos.worker.agent"):
        with patch("tinyagentos.worker.agent.WorkerAgent.detect_backends", return_value=[]):
            result = await agent.register()

    assert result is False
    # Check a clear error was logged mentioning pairing
    combined = " ".join(r.message for r in caplog.records).lower()
    assert "paired" in combined or "signing key" in combined
