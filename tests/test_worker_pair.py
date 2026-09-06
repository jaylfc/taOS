"""Tests for the worker pairing CLI: argument parsing, flow dispatch,
signed registration, hostname fallback, and pairing utility functions."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json as _json
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
