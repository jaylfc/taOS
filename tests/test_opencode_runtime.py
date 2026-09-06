"""Tests for tinyagentos.opencode_runtime.

Covers:
  - OpenCodeServer.write_config: JSON structure (provider block + models).
  - OpenCodeServer.ensure_running idempotency: process only spawned once when
    already healthy.
  - OpenCodeServer.ensure_running timeout: raises TimeoutError when /doc never 200.
  - drive_turn sink wiring: fake adapter streams replies; all reach the sink.
  - drive_turn error isolation: adapter raises; exactly one error dict delivered
    to the sink; drive_turn does not re-raise.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.opencode_runtime import (
    OpenCodeServer,
    OpenCodeServerConfig,
    drive_turn,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_server_cfg(tmp_path, port: int = 5900) -> OpenCodeServerConfig:
    return OpenCodeServerConfig(
        home=str(tmp_path),
        port=port,
        server_password=None,
        litellm_base_url="http://127.0.0.1:4000/v1",
        litellm_key="sk-test",
        model_ids=["gpt-4o", "gpt-3.5-turbo"],
    )


# ---------------------------------------------------------------------------
# write_config
# ---------------------------------------------------------------------------

class TestWriteConfig:
    def test_creates_file_with_expected_structure(self, tmp_path):
        cfg = _make_server_cfg(tmp_path)
        server = OpenCodeServer(cfg)
        server.write_config()

        config_path = tmp_path / ".config" / "opencode" / "opencode.json"
        assert config_path.exists()

        data = json.loads(config_path.read_text())
        provider = data["provider"]["litellm"]

        assert provider["npm"] == "@ai-sdk/openai-compatible"
        assert provider["options"]["baseURL"] == "http://127.0.0.1:4000/v1"
        assert provider["options"]["apiKey"] == "sk-test"
        assert "gpt-4o" in provider["models"]
        assert "gpt-3.5-turbo" in provider["models"]

    def test_config_is_owner_only_readable(self, tmp_path):
        # The config embeds the LiteLLM key in plaintext — must be 0600.
        import os
        cfg = _make_server_cfg(tmp_path)
        OpenCodeServer(cfg).write_config()
        config_path = tmp_path / ".config" / "opencode" / "opencode.json"
        assert oct(os.stat(config_path).st_mode & 0o777) == "0o600"

    def test_creates_parent_dirs(self, tmp_path):
        # No .config/opencode directory exists yet — write_config must mkdir.
        nested = tmp_path / "nested" / "home"
        cfg = OpenCodeServerConfig(
            home=str(nested),
            port=5900,
            server_password=None,
            litellm_base_url="http://127.0.0.1:4000/v1",
            litellm_key="sk-test",
            model_ids=["m1"],
        )
        server = OpenCodeServer(cfg)
        server.write_config()  # must not raise
        assert (nested / ".config" / "opencode" / "opencode.json").exists()

    def test_single_model_id(self, tmp_path):
        cfg = OpenCodeServerConfig(
            home=str(tmp_path),
            port=5900,
            server_password=None,
            litellm_base_url="http://localhost:4000/v1",
            litellm_key="key",
            model_ids=["only-model"],
        )
        server = OpenCodeServer(cfg)
        server.write_config()
        data = json.loads(
            (tmp_path / ".config" / "opencode" / "opencode.json").read_text()
        )
        models = data["provider"]["litellm"]["models"]
        assert list(models.keys()) == ["only-model"]
        assert models["only-model"] == {}

    def test_idempotent_overwrite(self, tmp_path):
        cfg = _make_server_cfg(tmp_path)
        server = OpenCodeServer(cfg)
        server.write_config()
        # Change key, write again — file should reflect the new value.
        cfg.litellm_key = "sk-new"
        server.write_config()
        data = json.loads(
            (tmp_path / ".config" / "opencode" / "opencode.json").read_text()
        )
        assert data["provider"]["litellm"]["options"]["apiKey"] == "sk-new"


# ---------------------------------------------------------------------------
# ensure_running — idempotency
# ---------------------------------------------------------------------------

class _FakeProcess:
    """Minimal fake asyncio subprocess.Process."""
    def __init__(self):
        self.returncode = None
        self.pid = 12345

    async def wait(self):
        pass


@pytest.mark.asyncio
async def test_ensure_running_idempotent(tmp_path, monkeypatch):
    """When the process is already alive and /doc returns 200, the subprocess
    must NOT be spawned a second time."""
    cfg = _make_server_cfg(tmp_path)
    server = OpenCodeServer(cfg)

    spawn_count = 0

    async def fake_create_subprocess(*args, **kwargs):
        nonlocal spawn_count
        spawn_count += 1
        return _FakeProcess()

    # /doc always healthy
    async def fake_health(self_inner):
        return True

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", fake_create_subprocess
    )
    monkeypatch.setattr(OpenCodeServer, "_health_check", fake_health)

    await server.ensure_running()
    assert spawn_count == 1

    # Second call: already running + healthy → must not re-spawn.
    await server.ensure_running()
    assert spawn_count == 1


@pytest.mark.asyncio
async def test_ensure_running_spawns_on_dead_process(tmp_path, monkeypatch):
    """If the previous process died (returncode set), a new one is spawned."""
    cfg = _make_server_cfg(tmp_path)
    server = OpenCodeServer(cfg)

    spawn_count = 0

    async def fake_create_subprocess(*args, **kwargs):
        nonlocal spawn_count
        spawn_count += 1
        proc = _FakeProcess()
        # Simulate process alive after spawn.
        return proc

    health_calls = 0

    async def fake_health(self_inner):
        nonlocal health_calls
        health_calls += 1
        # Only healthy on first call in second ensure_running.
        return health_calls > 1

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)
    monkeypatch.setattr(OpenCodeServer, "_health_check", fake_health)

    # First ensure_running: process starts but health fails once then succeeds.
    await server.ensure_running(poll_s=0.0)
    assert spawn_count == 1

    # Simulate process death.
    server._proc.returncode = 1

    # Second ensure_running: process is dead → must re-spawn.
    health_calls = 0
    await server.ensure_running(poll_s=0.0)
    assert spawn_count == 2


# ---------------------------------------------------------------------------
# ensure_running — timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_running_raises_on_timeout(tmp_path, monkeypatch):
    """/doc never returns 200 → TimeoutError raised within the injected deadline."""
    cfg = _make_server_cfg(tmp_path)
    server = OpenCodeServer(cfg)

    async def fake_create_subprocess(*args, **kwargs):
        return _FakeProcess()

    async def fake_health(self_inner):
        return False  # never healthy

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)
    monkeypatch.setattr(OpenCodeServer, "_health_check", fake_health)

    with pytest.raises(TimeoutError):
        await server.ensure_running(deadline_s=0.05, poll_s=0.01)


# ---------------------------------------------------------------------------
# resolve_opencode_binary / OpenCodeBinaryNotFoundError (#1616)
#
# opencode's official installer places the binary at ~/.opencode/bin/opencode
# and only makes it reachable via a shell-rc PATH export, which a systemd
# service never sources. resolve_opencode_binary() must fall back to that
# fixed location, and ensure_running() must translate a genuine "not found"
# into a distinct, actionable exception rather than a generic subprocess error.
# ---------------------------------------------------------------------------

class TestResolveOpencodeBinary:
    def test_prefers_path_lookup(self, monkeypatch):
        from tinyagentos import opencode_runtime as ocr

        monkeypatch.delenv("TAOS_OPENCODE_BIN", raising=False)
        monkeypatch.setattr(ocr.shutil, "which", lambda name: "/usr/local/bin/opencode")
        assert ocr.resolve_opencode_binary() == "/usr/local/bin/opencode"

    def test_env_override_wins(self, tmp_path, monkeypatch):
        """TAOS_OPENCODE_BIN is the explicit operator override and beats PATH."""
        from tinyagentos import opencode_runtime as ocr

        binary = tmp_path / "my-opencode"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        monkeypatch.setenv("TAOS_OPENCODE_BIN", str(binary))
        # Even with a PATH hit, the override wins.
        monkeypatch.setattr(ocr.shutil, "which", lambda name: "/usr/local/bin/opencode")
        assert ocr.resolve_opencode_binary() == str(binary)

    def test_falls_back_to_installer_default_location(self, tmp_path, monkeypatch):
        from tinyagentos import opencode_runtime as ocr

        fake_home = tmp_path / "home"
        opencode_dir = fake_home / ".opencode" / "bin"
        opencode_dir.mkdir(parents=True)
        binary = opencode_dir / "opencode"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        monkeypatch.delenv("TAOS_OPENCODE_BIN", raising=False)
        monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
        monkeypatch.setattr(ocr.Path, "home", classmethod(lambda cls: fake_home))

        assert ocr.resolve_opencode_binary() == str(binary)

    def test_finds_first_executable_candidate(self, tmp_path, monkeypatch):
        """resolve_opencode_binary walks the trusted candidate list and returns
        the first existing executable, skipping earlier non-existent ones (e.g.
        the service user's own home has none, but a system path does)."""
        from tinyagentos import opencode_runtime as ocr

        system_bin = tmp_path / "usr" / "local" / "bin" / "opencode"
        system_bin.parent.mkdir(parents=True)
        system_bin.write_text("#!/bin/sh\n")
        system_bin.chmod(0o755)

        monkeypatch.delenv("TAOS_OPENCODE_BIN", raising=False)
        monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            ocr, "_opencode_candidate_paths",
            lambda: [tmp_path / "taos-home" / ".opencode" / "bin" / "opencode", system_bin],
        )
        assert ocr.resolve_opencode_binary() == str(system_bin)

    def test_does_not_probe_arbitrary_user_homes(self, monkeypatch):
        """Security: the candidate list must not glob /home/*, since running a binary
        from a non-privileged user's home is a privilege-escalation vector on a
        multi-user box (#1616 security review). The service user's own home
        (e.g. /opt/taos) is fine; only *arbitrary* /home/* is banned, so pin
        home outside /home to isolate it from the CI runner's /home/runner."""
        from tinyagentos import opencode_runtime as ocr

        monkeypatch.setattr(ocr.Path, "home", classmethod(lambda cls: Path("/opt/taos")))
        for candidate in ocr._opencode_candidate_paths():
            assert not str(candidate).startswith("/home/"), (
                f"must not probe arbitrary user home: {candidate}"
            )

    def test_returns_none_when_not_installed_anywhere(self, tmp_path, monkeypatch):
        from tinyagentos import opencode_runtime as ocr

        monkeypatch.delenv("TAOS_OPENCODE_BIN", raising=False)
        monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
        # Neutralise the host filesystem so a real opencode on the CI runner
        # doesn't leak into this "nothing installed" case.
        monkeypatch.setattr(ocr, "_opencode_candidate_paths", lambda: [])

        assert ocr.resolve_opencode_binary() is None


@pytest.mark.asyncio
async def test_ensure_running_raises_binary_not_found_error(tmp_path, monkeypatch):
    """A bare exec failure (binary missing everywhere) surfaces as
    OpenCodeBinaryNotFoundError, distinguishable from a start/health failure."""
    from tinyagentos.opencode_runtime import OpenCodeBinaryNotFoundError

    cfg = _make_server_cfg(tmp_path)
    server = OpenCodeServer(cfg)

    async def fake_create_subprocess(*args, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'opencode'")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)
    monkeypatch.setattr(
        "tinyagentos.opencode_runtime.resolve_opencode_binary", lambda: None
    )

    with pytest.raises(OpenCodeBinaryNotFoundError):
        await server.ensure_running(deadline_s=0.05, poll_s=0.01)


# ---------------------------------------------------------------------------
# drive_turn — sink wiring
# ---------------------------------------------------------------------------

class _FakeAdapter:
    """Stub adapter that emits two reply dicts to the sink, then closes."""

    def __init__(self, cfg, sink):
        self.cfg = cfg
        self.sink = sink
        self.closed = False
        self.session_ensured = False

    async def ensure_session(self):
        self.session_ensured = True

    async def prompt(self, text, trace_id=None):
        await self.sink({"kind": "delta", "trace_id": trace_id, "content": "chunk"})
        await self.sink({"kind": "final", "trace_id": trace_id, "content": "chunk"})

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_drive_turn_wires_sink():
    received = []

    async def sink(reply: dict):
        received.append(reply)

    holder = {}

    def factory(cfg, sink_arg):
        holder["a"] = _FakeAdapter(cfg, sink_arg)
        return holder["a"]

    await drive_turn(
        "hello", "t1", sink,
        base_url="http://127.0.0.1:5900",
        model_id="gpt-4o",
        adapter_factory=factory,
    )

    adapter = holder["a"]
    assert adapter.session_ensured
    assert adapter.closed
    assert [r["kind"] for r in received] == ["delta", "final"]
    assert all(r["trace_id"] == "t1" for r in received)


@pytest.mark.asyncio
async def test_drive_turn_config_passed_to_adapter():
    """The config forwarded to the adapter must reflect drive_turn's kwargs."""
    captured_cfg = {}

    class _CapturingAdapter(_FakeAdapter):
        def __init__(self, cfg, sink):
            super().__init__(cfg, sink)
            captured_cfg["cfg"] = cfg

    await drive_turn(
        "hi", None, lambda r: None,
        base_url="http://127.0.0.1:5901",
        model_id="my-model",
        model_provider_id="litellm",
        server_password="s3cr3t",
        adapter_factory=_CapturingAdapter,
    )

    cfg = captured_cfg["cfg"]
    assert cfg.base_url == "http://127.0.0.1:5901"
    assert cfg.model_id == "my-model"
    assert cfg.model_provider_id == "litellm"
    assert cfg.server_password == "s3cr3t"


# ---------------------------------------------------------------------------
# drive_turn — error isolation
# ---------------------------------------------------------------------------

class _BoomAdapter(_FakeAdapter):
    """Adapter whose prompt() raises unconditionally."""

    async def prompt(self, text, trace_id=None):
        raise RuntimeError("adapter exploded")


@pytest.mark.asyncio
async def test_drive_turn_does_not_raise_on_adapter_failure():
    """drive_turn must not propagate adapter exceptions to the caller."""
    received = []

    async def sink(reply: dict):
        received.append(reply)

    # Should not raise.
    await drive_turn(
        "text", "t2", sink,
        base_url="http://127.0.0.1:5900",
        model_id="gpt-4o",
        adapter_factory=lambda cfg, s: _BoomAdapter(cfg, s),
    )

    error_replies = [r for r in received if r["kind"] == "error"]
    assert len(error_replies) == 1
    assert error_replies[0]["trace_id"] == "t2"


@pytest.mark.asyncio
async def test_drive_turn_emits_exactly_one_error_on_failure():
    """Exactly one error dict reaches the sink when the adapter raises, not more."""
    received = []

    async def sink(reply: dict):
        received.append(reply)

    await drive_turn(
        "text", "tx", sink,
        base_url="http://127.0.0.1:5900",
        model_id="gpt-4o",
        adapter_factory=lambda cfg, s: _BoomAdapter(cfg, s),
    )

    assert len([r for r in received if r["kind"] == "error"]) == 1


@pytest.mark.asyncio
async def test_drive_turn_ensure_session_failure_emits_error():
    """If ensure_session() raises, drive_turn still emits an error and returns."""

    class _NoSession(_FakeAdapter):
        async def ensure_session(self):
            raise ConnectionError("no server")

    received = []

    async def sink(reply: dict):
        received.append(reply)

    await drive_turn(
        "text", "t3", sink,
        base_url="http://127.0.0.1:5900",
        model_id="gpt-4o",
        adapter_factory=lambda cfg, s: _NoSession(cfg, s),
    )

    assert any(r["kind"] == "error" for r in received)


# ---------------------------------------------------------------------------
# drive_turn — timeout
# ---------------------------------------------------------------------------

class _HangingAdapter(_FakeAdapter):
    """Adapter whose prompt() blocks forever (simulates a hung LLM)."""

    async def prompt(self, text, trace_id=None):
        # Block forever — never completes.
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_drive_turn_timeout_emits_error():
    """With a short timeout, drive_turn must emit an error, not hang."""
    received = []

    async def sink(reply: dict):
        received.append(reply)

    await drive_turn(
        "text", "tx", sink,
        base_url="http://127.0.0.1:5900",
        model_id="gpt-4o",
        adapter_factory=lambda cfg, s: _HangingAdapter(cfg, s),
        turn_timeout=0.1,
    )

    error_replies = [r for r in received if r["kind"] == "error"]
    assert len(error_replies) == 1
    assert error_replies[0]["trace_id"] == "tx"


@pytest.mark.asyncio
async def test_drive_turn_timeout_message_is_descriptive():
    """The error reply must mention timeout, not the generic transport failure."""
    received = []

    async def sink(reply: dict):
        received.append(reply)

    await drive_turn(
        "text", "ty", sink,
        base_url="http://127.0.0.1:5900",
        model_id="gpt-4o",
        adapter_factory=lambda cfg, s: _HangingAdapter(cfg, s),
        turn_timeout=0.1,
    )

    error = next(r for r in received if r["kind"] == "error")
    assert "timed out" in error["error"]
    assert "limit:" in error["error"]


@pytest.mark.asyncio
async def test_drive_turn_default_timeout_does_not_trigger_on_fast_turn():
    """When the adapter completes promptly, the timeout must not fire."""
    received = []

    async def sink(reply: dict):
        received.append(reply)

    await drive_turn(
        "hello", "t1", sink,
        base_url="http://127.0.0.1:5900",
        model_id="gpt-4o",
        adapter_factory=_FakeAdapter,
    )

    error_replies = [r for r in received if r["kind"] == "error"]
    assert len(error_replies) == 0, (
        f"timeout should not fire on a fast turn, got {error_replies}"
    )
    assert len(received) == 2  # delta + final from _FakeAdapter


@pytest.mark.asyncio
async def test_drive_turn_rejects_non_positive_timeout():
    """turn_timeout <= 0 must raise ValueError immediately, not degrade."""
    with pytest.raises(ValueError, match="turn_timeout must be positive"):
        await drive_turn(
            "text", "tx",
            sink=lambda r: None,
            base_url="http://127.0.0.1:5900",
            model_id="gpt-4o",
            turn_timeout=0,
        )
