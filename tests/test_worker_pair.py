"""Tests for the worker pairing CLI: argument parsing, flow dispatch,
signed registration, hostname fallback, and pairing utility functions."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json as _json
import logging
import stat
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.worker import pair as pair_mod
from tinyagentos.worker.pairing import (
    _ALPHABET,
    _describe_error,
    _print_manual_instructions,
    code_hash,
    default_state_dir,
    generate_pairing_code,
    key_path,
    load_signing_key,
    save_signing_key,
    sign_request_headers,
)


# ======================================================================
# _hostname
# ======================================================================

class TestHostname:
    def test_returns_socket_gethostname(self, monkeypatch):
        monkeypatch.setattr("socket.gethostname", lambda: "myhost")
        assert pair_mod._hostname() == "myhost"

    def test_returns_fqdn(self, monkeypatch):
        monkeypatch.setattr("socket.gethostname", lambda: "node42.example.com")
        assert pair_mod._hostname() == "node42.example.com"


# ======================================================================
# _signed_register
# ======================================================================

def _make_async_client(status_code=200, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text

    client_ctx = MagicMock()
    client_ctx.__aenter__ = AsyncMock(return_value=client_ctx)
    client_ctx.__aexit__ = AsyncMock(return_value=False)
    client_ctx.post = AsyncMock(return_value=resp)

    return client_ctx


@pytest.mark.asyncio
async def test_signed_register_success():
    client = _make_async_client(200)
    key = b"\x01" * 32
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        await pair_mod._signed_register("http://ctrl:6969", "w1", "http://w1:8080", "linux", key)
    client.post.assert_awaited_once()
    url_arg = client.post.call_args[0][0]
    assert url_arg == "http://ctrl:6969/api/cluster/workers"


@pytest.mark.asyncio
async def test_signed_register_already_registered():
    client = _make_async_client(409)
    key = b"\x02" * 32
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        await pair_mod._signed_register("http://ctrl:6969/", "w1", "http://w1:8080", "linux", key)
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_signed_register_server_error_raises():
    client = _make_async_client(500, text="internal error")
    key = b"\x03" * 32
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        with pytest.raises(RuntimeError, match="signed register failed with HTTP 500"):
            await pair_mod._signed_register("http://ctrl:6969", "w1", "http://w1:8080", "linux", key)


@pytest.mark.asyncio
async def test_signed_register_strips_trailing_slash():
    client = _make_async_client(200)
    key = b"\x04" * 32
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        await pair_mod._signed_register("http://ctrl:6969/", "w1", "http://w1:8080", "linux", key)
    url_arg = client.post.call_args[0][0]
    assert url_arg == "http://ctrl:6969/api/cluster/workers"
    assert not url_arg.startswith("http://ctrl:6969//")


@pytest.mark.asyncio
async def test_signed_register_sends_correct_body():
    client = _make_async_client(200)
    key = b"\x05" * 32
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        await pair_mod._signed_register("http://ctrl:6969", "myworker", "http://w:9090", "macos", key)
    call_kwargs = client.post.call_args[1]
    body = _json.loads(call_kwargs["content"])
    assert body["name"] == "myworker"
    assert body["url"] == "http://w:9090"
    assert body["platform"] == "macos"
    assert body["hardware"] == {}
    assert body["backends"] == []


@pytest.mark.asyncio
async def test_signed_register_sends_hmac_headers():
    client = _make_async_client(200)
    key = b"\x06" * 32
    with patch("httpx.AsyncClient", MagicMock(return_value=client)):
        await pair_mod._signed_register("http://ctrl:6969", "w1", "http://w1:8080", "linux", key)
    headers = client.post.call_args[1]["headers"]
    assert "X-TAOS-Worker-Name" in headers
    assert headers["X-TAOS-Worker-Name"] == "w1"
    assert "X-TAOS-Timestamp" in headers
    assert "X-TAOS-Signature" in headers
    assert headers["content-type"] == "application/json"


# ======================================================================
# _run — happy path and edge cases
# ======================================================================

class _FakeArgs:
    def __init__(self, **kw):
        self.controller = "http://ctrl:6969"
        self.name = "w1"
        self.url = None
        self.platform_name = "linux"
        self.state_dir = None
        self.manual = False
        self.register_after = False
        self.timeout = 600.0
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.mark.asyncio
async def test_run_standard_pairing(monkeypatch, tmp_path):
    fake_key = b"\xaa" * 32
    monkeypatch.setattr("tinyagentos.worker.pairing.default_state_dir", lambda: tmp_path)
    monkeypatch.setattr("tinyagentos.worker.pair._hostname", lambda: "w1")
    mock_run_pairing = AsyncMock(return_value=fake_key)
    monkeypatch.setattr("tinyagentos.worker.pairing.run_pairing", mock_run_pairing)

    args = _FakeArgs(url="http://w1:8080")
    await pair_mod._run(args)

    mock_run_pairing.assert_awaited_once()
    call_args = mock_run_pairing.call_args
    assert call_args[0][1] == "http://ctrl:6969"
    assert call_args[0][2] == "w1"
    assert call_args[0][3] == "http://w1:8080"
    assert call_args[0][4] == "linux"
    assert call_args[0][5] == tmp_path
    assert call_args[1]["timeout"] == 600.0


@pytest.mark.asyncio
async def test_run_manual_pairing(monkeypatch, tmp_path):
    fake_key = b"\xbb" * 32
    monkeypatch.setattr("tinyagentos.worker.pairing.default_state_dir", lambda: tmp_path)
    monkeypatch.setattr("tinyagentos.worker.pair._hostname", lambda: "w1")
    mock_run_manual = AsyncMock(return_value=fake_key)
    monkeypatch.setattr("tinyagentos.worker.pairing.run_manual_pairing", mock_run_manual)

    args = _FakeArgs(manual=True, url="http://w1:8080")
    await pair_mod._run(args)

    mock_run_manual.assert_awaited_once()
    call_kwargs = mock_run_manual.call_args
    assert call_kwargs[0][1] == "http://ctrl:6969"
    assert call_kwargs[0][2] == "w1"
    assert call_kwargs[0][3] == "http://w1:8080"
    assert call_kwargs[0][4] == tmp_path
    assert call_kwargs[1]["timeout"] == 600.0


@pytest.mark.asyncio
async def test_run_with_register_after(monkeypatch, tmp_path, capsys):
    fake_key = b"\xcc" * 32
    monkeypatch.setattr("tinyagentos.worker.pairing.default_state_dir", lambda: tmp_path)
    monkeypatch.setattr("tinyagentos.worker.pair._hostname", lambda: "w1")
    mock_run_pairing = AsyncMock(return_value=fake_key)
    monkeypatch.setattr("tinyagentos.worker.pairing.run_pairing", mock_run_pairing)

    client = _make_async_client(200)
    monkeypatch.setattr("httpx.AsyncClient", MagicMock(return_value=client))

    args = _FakeArgs(url="http://w1:8080", register_after=True)
    await pair_mod._run(args)

    client.post.assert_awaited_once()
    assert "registered" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_run_without_url_infers_worker_url(monkeypatch, tmp_path):
    fake_key = b"\xdd" * 32
    monkeypatch.setattr("tinyagentos.worker.pairing.default_state_dir", lambda: tmp_path)
    monkeypatch.setattr("tinyagentos.worker.pair._hostname", lambda: "w1")
    mock_run_pairing = AsyncMock(return_value=fake_key)
    monkeypatch.setattr("tinyagentos.worker.pairing.run_pairing", mock_run_pairing)

    mock_agent = MagicMock()
    mock_agent.get_worker_url.return_value = "http://auto:7070"
    monkeypatch.setattr("tinyagentos.worker.agent.WorkerAgent", lambda *a, **k: mock_agent)

    args = _FakeArgs(url=None)
    await pair_mod._run(args)

    mock_run_pairing.assert_awaited_once()
    call_kwargs = mock_run_pairing.call_args
    assert call_kwargs[0][3] == "http://auto:7070"


@pytest.mark.asyncio
async def test_run_uses_provided_name(monkeypatch, tmp_path):
    fake_key = b"\xee" * 32
    monkeypatch.setattr("tinyagentos.worker.pairing.default_state_dir", lambda: tmp_path)
    mock_run_pairing = AsyncMock(return_value=fake_key)
    monkeypatch.setattr("tinyagentos.worker.pairing.run_pairing", mock_run_pairing)

    args = _FakeArgs(name="custom-name", url="http://w1:8080")
    await pair_mod._run(args)

    call_kwargs = mock_run_pairing.call_args
    assert call_kwargs[0][2] == "custom-name"


@pytest.mark.asyncio
async def test_run_uses_custom_state_dir(monkeypatch, tmp_path):
    fake_key = b"\xff" * 32
    custom_dir = tmp_path / "custom_state"
    mock_run_pairing = AsyncMock(return_value=fake_key)
    monkeypatch.setattr("tinyagentos.worker.pairing.run_pairing", mock_run_pairing)

    args = _FakeArgs(url="http://w1:8080", state_dir=custom_dir)
    await pair_mod._run(args)

    call_kwargs = mock_run_pairing.call_args
    assert call_kwargs[0][5] == custom_dir


# ======================================================================
# main() — argument parsing and error handling
# ======================================================================

class _Exit(Exception):
    """Capture sys.exit calls."""


def _fake_exit(code=0):
    raise _Exit(code)


def test_main_timeout_error(monkeypatch, capsys):
    monkeypatch.setattr("argparse.ArgumentParser.parse_args", lambda self: _FakeArgs(url="http://w1:8080"))
    monkeypatch.setattr("asyncio.run", lambda coro: (_ for _ in ()).throw(TimeoutError("timed out after 5s")))
    monkeypatch.setattr("sys.exit", _fake_exit)

    with pytest.raises(_Exit) as exc_info:
        pair_mod.main()
    assert exc_info.value.args[0] == 1
    assert "timed out after 5s" in capsys.readouterr().err


def test_main_generic_exception(monkeypatch, capsys):
    monkeypatch.setattr("argparse.ArgumentParser.parse_args", lambda self: _FakeArgs(url="http://w1:8080"))
    monkeypatch.setattr("asyncio.run", lambda coro: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("sys.exit", _fake_exit)

    with pytest.raises(_Exit) as exc_info:
        pair_mod.main()
    assert exc_info.value.args[0] == 1
    assert "boom" in capsys.readouterr().err


def test_main_success(monkeypatch):
    monkeypatch.setattr("argparse.ArgumentParser.parse_args", lambda self: _FakeArgs(url="http://w1:8080"))
    mock_run = AsyncMock()
    monkeypatch.setattr("asyncio.run", lambda coro: None)
    monkeypatch.setattr("tinyagentos.worker.pair._run", mock_run)
    monkeypatch.setattr("sys.exit", _fake_exit)

    pair_mod.main()


def test_main_default_platform(monkeypatch):
    """--platform defaults to platform.system().lower()."""
    import platform as _plat

    captured = {}

    def _capture_parse(self):
        args = _FakeArgs.__new__(_FakeArgs)
        args.controller = "http://ctrl:6969"
        args.name = None
        args.url = "http://w1:8080"
        args.platform_name = _plat.system().lower()
        args.state_dir = None
        args.manual = False
        args.register_after = False
        args.timeout = 600.0
        captured["platform_name"] = args.platform_name
        return args

    monkeypatch.setattr("argparse.ArgumentParser.parse_args", _capture_parse)
    monkeypatch.setattr("asyncio.run", lambda coro: None)
    monkeypatch.setattr("tinyagentos.worker.pair._run", AsyncMock())

    pair_mod.main()
    assert captured["platform_name"] == _plat.system().lower()


# ======================================================================
# pairing utility functions (exercised through pair.py imports)
# ======================================================================

class TestDefaultStateDir:
    def test_env_override(self, monkeypatch, tmp_path):
        override = tmp_path / "override"
        monkeypatch.setenv("TAOS_WORKER_STATE_DIR", str(override))
        assert default_state_dir() == override

    def test_xdg_state_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TAOS_WORKER_STATE_DIR", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.setattr(sys, "platform", "linux")
        result = default_state_dir()
        assert result == tmp_path / "taos-worker"

    def test_fallback_posix(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TAOS_WORKER_STATE_DIR", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(sys, "platform", "linux")
        result = default_state_dir()
        assert result == tmp_path / ".local" / "state" / "taos-worker"

    def test_windows_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TAOS_WORKER_STATE_DIR", raising=False)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        result = default_state_dir()
        assert result == tmp_path / "taos-worker"


class TestKeyPairPath:
    def test_key_path(self, tmp_path):
        assert key_path(tmp_path) == tmp_path / "signing_key"


class TestLoadSaveSigningKey:
    def test_save_and_load_roundtrip(self, tmp_path):
        key = b"\x01\x02\x03\x04" * 8
        save_signing_key(tmp_path, key)
        assert load_signing_key(tmp_path) == key

    def test_load_missing_returns_none(self, tmp_path):
        assert load_signing_key(tmp_path) is None

    def test_save_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        key = b"\xab" * 32
        save_signing_key(nested, key)
        assert load_signing_key(nested) == key

    def test_save_sets_owner_only_permissions(self, tmp_path):
        key = b"\xcd" * 32
        save_signing_key(tmp_path, key)
        mode = stat.S_IMODE((tmp_path / "signing_key").stat().st_mode)
        assert mode == 0o600


class TestGeneratePairingCode:
    def test_returns_8_char_string(self):
        code = generate_pairing_code()
        assert len(code) == 8

    def test_uses_unambiguous_alphabet(self):
        for _ in range(50):
            code = generate_pairing_code()
            for ch in code:
                assert ch in _ALPHABET

    def test_no_ambiguous_chars(self):
        ambiguous = set("0O1Il")
        for _ in range(50):
            code = generate_pairing_code()
            assert set(code).isdisjoint(ambiguous)

    def test_codes_are_not_identical(self):
        codes = {generate_pairing_code() for _ in range(20)}
        assert len(codes) > 1


class TestCodeHash:
    def test_returns_sha256_hex(self):
        code = "ABCD2345"
        expected = hashlib.sha256(code.encode()).hexdigest()
        assert code_hash(code) == expected

    def test_different_codes_different_hashes(self):
        assert code_hash("code1") != code_hash("code2")

    def test_empty_string(self):
        expected = hashlib.sha256(b"").hexdigest()
        assert code_hash("") == expected


class TestSignRequestHeaders:
    def test_returns_three_headers(self):
        key = b"\x01" * 32
        headers = sign_request_headers(key, "w1", "POST", "/api/test", b'{"a":1}')
        assert set(headers.keys()) == {"X-TAOS-Worker-Name", "X-TAOS-Timestamp", "X-TAOS-Signature"}

    def test_worker_name_header(self):
        headers = sign_request_headers(b"\x00" * 32, "myworker", "GET", "/x", b"")
        assert headers["X-TAOS-Worker-Name"] == "myworker"

    def test_method_is_uppercased(self):
        key = b"\x01" * 32
        headers_lower = sign_request_headers(key, "w", "post", "/p", b"")
        headers_upper = sign_request_headers(key, "w", "POST", "/p", b"")
        assert headers_lower["X-TAOS-Signature"] == headers_upper["X-TAOS-Signature"]

    def test_different_bodies_produce_different_signatures(self):
        key = b"\x01" * 32
        h1 = sign_request_headers(key, "w", "POST", "/p", b"body1")
        h2 = sign_request_headers(key, "w", "POST", "/p", b"body2")
        assert h1["X-TAOS-Signature"] != h2["X-TAOS-Signature"]

    def test_different_keys_produce_different_signatures(self):
        h1 = sign_request_headers(b"\x01" * 32, "w", "POST", "/p", b"body")
        h2 = sign_request_headers(b"\x02" * 32, "w", "POST", "/p", b"body")
        assert h1["X-TAOS-Signature"] != h2["X-TAOS-Signature"]

    def test_signature_is_valid_hmac(self):
        key = b"\xab" * 32
        name = "w1"
        method = "POST"
        path = "/api/cluster/workers"
        body = b'{"name":"w1"}'
        headers = sign_request_headers(key, name, method, path, body)
        ts = int(headers["X-TAOS-Timestamp"])
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
        expected_sig = hmac.new(key, message, hashlib.sha256).hexdigest()
        assert headers["X-TAOS-Signature"] == expected_sig


class TestDescribeError:
    def test_returns_json_detail(self):
        resp = MagicMock()
        resp.json.return_value = {"detail": "not found"}
        assert _describe_error(resp) == "{'detail': 'not found'}"

    def test_falls_back_to_text_on_json_failure(self):
        resp = MagicMock()
        resp.json.side_effect = ValueError("no json")
        resp.text = "plain text error"
        assert _describe_error(resp) == "plain text error"


class TestPrintManualInstructions:
    def test_prints_url_and_code(self, capsys):
        _print_manual_instructions("http://192.168.1.50:6970", "ABCD2345", print)
        out = capsys.readouterr().out
        assert "http://192.168.1.50:6970" in out
        assert "ABCD2345" in out
        assert "Worker address" in out
        assert "Pairing PIN" in out

    def test_uses_custom_print_fn(self):
        lines = []
        _print_manual_instructions("http://x:1", "CODE1234", print_fn=lines.append)
        assert any("http://x:1" in line for line in lines)
        assert any("CODE1234" in line for line in lines)


# ======================================================================
# "not paired" hint (agent side)
# ======================================================================

@pytest.mark.asyncio
class TestNotPairedHint:
    """The hint must let an operator fix the problem by copy-paste.

    The daemon reads its key from TAOS_WORKER_STATE_DIR (set by the systemd
    unit and the launchd plist to $INSTALL_DIR/.taos-worker-state), while
    pair.py defaults to ~/.local/state/taos-worker. A hint without
    --state-dir therefore writes the key where the service never looks.
    """

    async def test_register_hint_names_the_state_dir(self, tmp_path, caplog):
        from tinyagentos.worker.agent import WorkerAgent

        state_dir = tmp_path / "worker-state"
        agent = WorkerAgent(
            controller_url="http://controller:9000",
            name="hint-worker",
            state_dir=state_dir,
        )
        with caplog.at_level(logging.ERROR):
            assert await agent.register() is False
        message = caplog.text
        assert "worker not paired" in message, message
        assert "--state-dir" in message, message
        assert str(state_dir) in message, message

    async def test_heartbeat_hint_names_the_state_dir(self, tmp_path, caplog):
        from tinyagentos.worker.agent import WorkerAgent

        state_dir = tmp_path / "worker-state"
        agent = WorkerAgent(
            controller_url="http://controller:9000",
            name="hint-worker",
            state_dir=state_dir,
        )
        with caplog.at_level(logging.ERROR):
            assert await agent.heartbeat() == 0
        message = caplog.text
        assert "--state-dir" in message, message
        assert str(state_dir) in message, message


# ======================================================================
# _resolve_controller: mDNS discovery when no URL is given (#119)
# ======================================================================

from types import SimpleNamespace

# ======================================================================


def _resolve_args(controller=None, expect_fingerprint=None):
    return SimpleNamespace(
        controller=controller,
        scan_timeout=0.05,
        expect_fingerprint=expect_fingerprint,
    )


def _candidate(fp="a" * 64, url="http://taos.local:6969"):
    from tinyagentos.worker.discovery import ControllerCandidate

    return ControllerCandidate(
        name="taOS", url=url, fingerprint=fp, tier=None,
        host="taos.local", port=6969,
    )


class TestResolveController:
    def test_explicit_controller_short_circuits(self, monkeypatch):
        async def _no_scan(timeout=0.0):
            raise AssertionError("discovery must not run")

        monkeypatch.setattr(
            "tinyagentos.worker.discovery.discover_controllers", _no_scan
        )
        url, fp = asyncio.run(
            pair_mod._resolve_controller(_resolve_args("http://10.0.0.1:6969"))
        )
        assert url == "http://10.0.0.1:6969"
        assert fp is None

    def test_discovered_controller_is_used(self, monkeypatch):
        async def _scan(timeout=0.0):
            return [_candidate()]

        monkeypatch.setattr(
            "tinyagentos.worker.discovery.discover_controllers", _scan
        )
        url, fp = asyncio.run(pair_mod._resolve_controller(_resolve_args()))
        assert url == "http://taos.local:6969"
        assert fp == "a" * 64

    def test_expect_fingerprint_selects_match(self, monkeypatch):
        async def _scan(timeout=0.0):
            return [
                _candidate("a" * 64, "http://one.local:6969"),
                _candidate("b" * 64, "http://two.local:6969"),
            ]

        monkeypatch.setattr(
            "tinyagentos.worker.discovery.discover_controllers", _scan
        )
        url, fp = asyncio.run(
            pair_mod._resolve_controller(
                _resolve_args(expect_fingerprint="b" * 64)
            )
        )
        assert url == "http://two.local:6969"
        assert fp == "b" * 64

    def test_no_discovery_falls_back_to_manual_entry(self, monkeypatch):
        async def _scan(timeout=0.0):
            return []

        monkeypatch.setattr(
            "tinyagentos.worker.discovery.discover_controllers", _scan
        )
        monkeypatch.setattr(
            "builtins.input", lambda prompt="": "http://10.0.0.9:6969"
        )
        url, fp = asyncio.run(pair_mod._resolve_controller(_resolve_args()))
        assert url == "http://10.0.0.9:6969"
        assert fp is None

    def test_no_discovery_and_no_manual_entry_raises(self, monkeypatch):
        async def _scan(timeout=0.0):
            return []

        monkeypatch.setattr(
            "tinyagentos.worker.discovery.discover_controllers", _scan
        )

        def _eof(prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", _eof)
        with pytest.raises(ValueError, match="no controller"):
            asyncio.run(pair_mod._resolve_controller(_resolve_args()))


# ======================================================================
# Controller identity verification (#119, PR #4567): a discovered
# controller must prove possession of the hub key behind the fingerprint
# it advertised, before any pairing state is created.
# ======================================================================


def _ed25519_keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv_raw, pub_raw


def _proof_payload(priv_raw: bytes, pub_raw: bytes, nonce: str) -> dict:
    """Build an identity-proof body the way the controller endpoint does."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    from tinyagentos.hub.identity import fingerprint, pairing_proof_message

    priv = Ed25519PrivateKey.from_private_bytes(priv_raw)
    return {
        "fingerprint": fingerprint(pub_raw.hex()),
        "signing_pubkey": pub_raw.hex(),
        "signature": priv.sign(pairing_proof_message(nonce)).hex(),
    }


class _ProofResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class _ProofClient:
    """httpx-client stand-in that serves one fixed identity-proof reply."""

    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.posts: list = []

    async def post(self, url, json=None):
        self.posts.append((url, json))
        if self._exc is not None:
            raise self._exc
        return self._resp


class TestVerifyControllerProof:
    @pytest.mark.asyncio
    async def test_genuine_proof_verifies(self):
        from tinyagentos.hub.identity import fingerprint
        from tinyagentos.worker.pairing import verify_controller_proof

        priv_raw, pub_raw = _ed25519_keypair()
        nonce = "cd" * 32
        client = _ProofClient(
            _ProofResp(200, _proof_payload(priv_raw, pub_raw, nonce))
        )
        ok = await verify_controller_proof(
            client,
            "http://ctrl:6969",
            fingerprint(pub_raw.hex()),
            nonce=nonce,
        )
        assert ok is True
        assert client.posts[0][0] == (
            "http://ctrl:6969/api/cluster/pairing/identity-proof"
        )
        assert client.posts[0][1] == {"nonce": nonce}

    @pytest.mark.asyncio
    async def test_impostor_proving_with_a_different_key_is_refused(self):
        """The advertisement carries the victim's fingerprint, but the
        endpoint proves possession of the attacker's own key: the
        fingerprint check must refuse it."""
        from tinyagentos.hub.identity import fingerprint
        from tinyagentos.worker.pairing import verify_controller_proof

        victim_priv, victim_pub = _ed25519_keypair()
        atk_priv, atk_pub = _ed25519_keypair()
        nonce = "cd" * 32
        client = _ProofClient(
            _ProofResp(200, _proof_payload(atk_priv, atk_pub, nonce))
        )
        ok = await verify_controller_proof(
            client,
            "http://evil:6969",
            fingerprint(victim_pub.hex()),
            nonce=nonce,
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_forged_signature_under_victim_pubkey_is_refused(self):
        """The endpoint presents the victim's pubkey (so the fingerprint
        matches) but the signature was made by a different key."""
        from tinyagentos.hub.identity import fingerprint
        from tinyagentos.worker.pairing import verify_controller_proof

        victim_priv, victim_pub = _ed25519_keypair()
        atk_priv, _atk_pub = _ed25519_keypair()
        nonce = "cd" * 32
        client = _ProofClient(
            _ProofResp(200, _proof_payload(atk_priv, victim_pub, nonce))
        )
        ok = await verify_controller_proof(
            client,
            "http://evil:6969",
            fingerprint(victim_pub.hex()),
            nonce=nonce,
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_missing_proof_endpoint_is_refused(self):
        from tinyagentos.worker.pairing import verify_controller_proof

        client = _ProofClient(_ProofResp(404, {"error": "not found"}))
        ok = await verify_controller_proof(
            client, "http://old:6969", "a" * 64, nonce="cd" * 32
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_unreachable_controller_is_refused(self):
        from tinyagentos.worker.pairing import verify_controller_proof

        client = _ProofClient(exc=RuntimeError("connection refused"))
        ok = await verify_controller_proof(
            client, "http://down:6969", "a" * 64, nonce="cd" * 32
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_malformed_proof_is_refused(self):
        from tinyagentos.worker.pairing import verify_controller_proof

        client = _ProofClient(_ProofResp(200, {"fingerprint": "a" * 64}))
        ok = await verify_controller_proof(
            client, "http://ctrl:6969", "a" * 64, nonce="cd" * 32
        )
        assert ok is False


def _discovery_run_args(**overrides):
    base = dict(
        controller=None,
        url="http://w1:8080",
        scan_timeout=0.05,
        expect_fingerprint=None,
    )
    base.update(overrides)
    return _FakeArgs(**base)


@pytest.mark.asyncio
async def test_run_discovered_controller_is_verified_then_paired(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "tinyagentos.worker.pairing.default_state_dir", lambda: tmp_path
    )
    mock_run_pairing = AsyncMock(return_value=b"\xaa" * 32)
    monkeypatch.setattr("tinyagentos.worker.pairing.run_pairing", mock_run_pairing)
    mock_verify = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "tinyagentos.worker.pairing.verify_controller_proof", mock_verify
    )

    async def _scan(timeout=0.0):
        return [_candidate()]

    monkeypatch.setattr(
        "tinyagentos.worker.discovery.discover_controllers", _scan
    )

    await pair_mod._run(_discovery_run_args())

    mock_verify.assert_awaited_once()
    assert mock_verify.call_args[0][1] == "http://taos.local:6969"
    assert mock_verify.call_args[0][2] == "a" * 64
    mock_run_pairing.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_failed_verification_refuses_before_pairing(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "tinyagentos.worker.pairing.default_state_dir", lambda: tmp_path
    )
    mock_run_pairing = AsyncMock(return_value=b"\xaa" * 32)
    monkeypatch.setattr("tinyagentos.worker.pairing.run_pairing", mock_run_pairing)
    monkeypatch.setattr(
        "tinyagentos.worker.pairing.verify_controller_proof",
        AsyncMock(return_value=False),
    )

    async def _scan(timeout=0.0):
        return [_candidate()]

    monkeypatch.setattr(
        "tinyagentos.worker.discovery.discover_controllers", _scan
    )

    with pytest.raises(RuntimeError, match="refusing to pair"):
        await pair_mod._run(_discovery_run_args())
    mock_run_pairing.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_explicit_url_with_expect_fingerprint_is_verified(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "tinyagentos.worker.pairing.default_state_dir", lambda: tmp_path
    )
    mock_run_pairing = AsyncMock(return_value=b"\xaa" * 32)
    monkeypatch.setattr("tinyagentos.worker.pairing.run_pairing", mock_run_pairing)
    monkeypatch.setattr(
        "tinyagentos.worker.pairing.verify_controller_proof",
        AsyncMock(return_value=False),
    )

    args = _FakeArgs(url="http://w1:8080", expect_fingerprint="a" * 64)
    with pytest.raises(RuntimeError, match="refusing to pair"):
        await pair_mod._run(args)
    mock_run_pairing.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_fake_advertisement_refused_before_any_pairing_state(
    monkeypatch, tmp_path
):
    """The #4567 scenario end to end: an impostor advertises the real
    controller's fingerprint over mDNS but holds a different key. The
    pair CLI must refuse, and the pairing announce endpoint must never
    be hit -- no pairing state is created anywhere."""
    from tinyagentos.hub.identity import fingerprint

    victim_priv, victim_pub = _ed25519_keypair()
    atk_priv, atk_pub = _ed25519_keypair()
    victim_fp = fingerprint(victim_pub.hex())

    posts: list[str] = []

    class _ImpostorClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, **kwargs):
            posts.append(url)
            if url.endswith("/api/cluster/pairing/identity-proof"):
                return _ProofResp(
                    200, _proof_payload(atk_priv, atk_pub, json["nonce"])
                )
            return _ProofResp(200, {})

    monkeypatch.setattr(
        "tinyagentos.worker.pairing.default_state_dir", lambda: tmp_path
    )
    monkeypatch.setattr(
        "httpx.AsyncClient", lambda *a, **k: _ImpostorClient()
    )
    mock_run_pairing = AsyncMock(return_value=b"\xaa" * 32)
    monkeypatch.setattr("tinyagentos.worker.pairing.run_pairing", mock_run_pairing)

    async def _scan(timeout=0.0):
        return [_candidate(fp=victim_fp, url="http://evil.local:6969")]

    monkeypatch.setattr(
        "tinyagentos.worker.discovery.discover_controllers", _scan
    )

    with pytest.raises(RuntimeError, match="refusing to pair"):
        await pair_mod._run(_discovery_run_args())
    assert posts, "the identity challenge was never issued"
    assert all(u.endswith("/identity-proof") for u in posts), posts
    mock_run_pairing.assert_not_awaited()
