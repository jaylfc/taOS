"""Tests for the Kilo harness of the system taOS Agent.

Covers decide_framework with the kilo override, run_turn against a fake
``kilo`` executable, and key secrecy (the key is never logged).
"""
from __future__ import annotations

import asyncio
import logging
import os
import stat

import pytest

from tinyagentos.kilo_runtime import (
    DEFAULT_MODEL,
    KiloBinaryNotFoundError,
    KiloHarness,
    home_for,
    kilo_key_present,
    resolve_kilo_binary,
    scrub_home,
)
from tinyagentos.taos_agent_runtime import (
    FRAMEWORK_CHOICES,
    FRAMEWORK_KILO,
    REASON_NO_KILO_BINARY,
    REASON_NO_KILO_KEY,
    decide_framework,
    ensure_taos_kilo_harness,
)

SECRET = "sk-kilo-SECRETSECRETSECRET"


def _decide(**over):
    kw = {
        "framework_setting": "kilo",
        "device_class_setting": "auto",
        "detected_device_class": "desktop",
        "gateway_enabled": True,
        "picoclaw_available": False,
        "kilo_available": True,
        "kilo_key_present": True,
    }
    kw.update(over)
    return decide_framework(**kw)


# ---------------------------------------------------------------- decision
def test_choices_include_kilo():
    assert FRAMEWORK_KILO == "kilo"
    assert "kilo" in FRAMEWORK_CHOICES


def test_kilo_chosen_when_binary_and_key_present():
    d = _decide()
    assert d.framework == "kilo"
    assert d.preference == "kilo"


def test_kilo_without_key_falls_back_to_opencode():
    d = _decide(kilo_key_present=False)
    assert d.framework == "opencode"
    assert d.preference == "kilo"
    assert d.reason == REASON_NO_KILO_KEY
    assert d.reason == "kilo preferred, no kilo_api_key secret, using opencode"


def test_kilo_without_binary_falls_back_to_opencode():
    d = _decide(kilo_available=False)
    assert d.framework == "opencode"
    assert d.reason == REASON_NO_KILO_BINARY
    assert d.reason == "kilo preferred, kilo binary not found, using opencode"


def test_auto_on_desktop_is_opencode_even_with_kilo_available():
    assert _decide(framework_setting="auto").framework == "opencode"


def test_existing_callers_without_the_new_kwargs_still_work():
    d = decide_framework(
        framework_setting="opencode", device_class_setting="auto",
        detected_device_class="desktop", gateway_enabled=True, picoclaw_available=False,
    )
    assert d.framework == "opencode"


# ---------------------------------------------------------------- kilo_key_present
class _MockSecrets:
    def __init__(self, has_key: bool):
        self._has_key = has_key

    async def get(self, name: str):
        if name == "kilo_api_key" and self._has_key:
            return {"value": SECRET}
        return None


class _MockAppState:
    def __init__(self, has_key: bool = False):
        self.secrets = _MockSecrets(has_key)


@pytest.mark.asyncio
async def test_kilo_key_present_true_when_secret_exists():
    assert await kilo_key_present(_MockAppState(has_key=True)) is True


@pytest.mark.asyncio
async def test_kilo_key_present_false_when_secret_missing():
    assert await kilo_key_present(_MockAppState(has_key=False)) is False


@pytest.mark.asyncio
async def test_kilo_key_present_false_when_no_secrets_store():
    state = _MockAppState(has_key=True)
    state.secrets = None
    assert await kilo_key_present(state) is False


# ---------------------------------------------------------------- harness
def _harness(tmp_path, binary="kilo", **kw) -> KiloHarness:
    return KiloHarness(home=tmp_path / "home", key=SECRET, model="kilo/kilo-auto/free",
                       binary=binary, **kw)


def test_home_for_is_under_data_dir(tmp_path):
    assert home_for(tmp_path) == tmp_path / "taos-agent-kilo"


def test_write_home_is_0700_with_workspace_and_git(tmp_path):
    h = _harness(tmp_path)
    h.write_home()
    assert stat.S_IMODE(h.home.stat().st_mode) == 0o700
    assert h.workspace == h.home / "workspace"
    assert h.workspace.is_dir()
    assert (h.workspace / ".git").is_dir()


def test_repr_hides_the_key(tmp_path):
    assert SECRET not in repr(_harness(tmp_path))


def test_env_is_exactly_the_minimal_set(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_SECRET_PROBE", "x")
    monkeypatch.delenv("TZ", raising=False)
    env = _harness(tmp_path)._env()
    assert set(env) == {"HOME", "PATH", "LANG", "NO_COLOR", "KILO_API_KEY"}
    assert env["KILO_API_KEY"] == SECRET
    assert env["HOME"] == str(tmp_path / "home")
    assert env["NO_COLOR"] == "1"
    assert "TAOS_SECRET_PROBE" not in env


def test_env_carries_tz_only_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Europe/London")
    assert _harness(tmp_path)._env()["TZ"] == "Europe/London"


def test_scrub_home_deletes_nothing(tmp_path):
    h = _harness(tmp_path)
    h.write_home()
    (h.workspace / "keep.txt").write_text("x")
    scrub_home(tmp_path)
    assert (h.workspace / "keep.txt").exists()


# ---------------------------------------------------------------- run_turn
_FAKE_OK = r'''#!/bin/bash
test -n "$KILO_API_KEY" || { echo "KILO_API_KEY is not set" >&2; exit 1; }
[ -t 0 ] && { echo "stdin is a tty" >&2; exit 1; }
printf '%s\n' "$@" > "$(dirname "$0")/dump.argv"
env > "$(dirname "$0")/dump.env"
pwd > "$(dirname "$0")/dump.cwd"
echo '{"type":"step_start","sessionID":"ses-1"}'
echo '{"type":"text","sessionID":"ses-1","part":{"type":"text","text":"pong"}}'
echo '{"type":"step_finish","sessionID":"ses-1"}'
'''


def _fake(tmp_path, body):
    p = tmp_path / "kilo"
    p.write_text(body)
    p.chmod(0o755)
    return str(p)


@pytest.mark.asyncio
async def test_run_turn_first_and_resumed(tmp_path):
    h = _harness(tmp_path, binary=_fake(tmp_path, _FAKE_OK))
    h.write_home()
    res = await h.run_turn("hello there", None)
    assert res.returncode == 0
    assert res.reply == "pong"
    assert res.session_id == "ses-1"
    assert not res.timed_out
    argv = (tmp_path / "dump.argv").read_text().split("\n")[:-1]
    assert argv == ["run", "--format", "json", "--model", DEFAULT_MODEL,
                    "--dir", str(h.workspace), "hello there"]
    assert (tmp_path / "dump.cwd").read_text().strip() == str(h.workspace)
    env = (tmp_path / "dump.env").read_text()
    assert f"KILO_API_KEY={SECRET}" in env
    assert "TAOS_SECRET_PROBE" not in env

    res2 = await h.run_turn("again", "ses-1")
    argv2 = (tmp_path / "dump.argv").read_text().split("\n")[:-1]
    idx = argv2.index("-s")
    assert argv2[idx + 1] == "ses-1"
    assert res2.reply == "pong"
    assert res2.session_id == "ses-1"


@pytest.mark.asyncio
async def test_run_turn_uses_configured_model(tmp_path):
    h = KiloHarness(home=tmp_path / "home", key=SECRET, model="openai/gpt-4o",
                    binary=_fake(tmp_path, _FAKE_OK))
    h.write_home()
    res = await h.run_turn("hello there", None)
    assert res.returncode == 0
    argv = (tmp_path / "dump.argv").read_text().split("\n")[:-1]
    assert argv == ["run", "--format", "json", "--model", "openai/gpt-4o",
                    "--dir", str(h.workspace), "hello there"]


@pytest.mark.asyncio
async def test_error_event_surfaces_data_message_not_headers(tmp_path):
    body = ('#!/bin/bash\ntest -n "$KILO_API_KEY" || exit 1\n'
            "printf '%s\\n' '{\"type\":\"error\",\"sessionID\":\"ses-1\","
            "\"error\":{\"name\":\"APIError\",\"data\":{\"message\":\"rate limited\","
            "\"statusCode\":429,\"headers\":{\"x-ratelimit-limit\":\"10\"}}}}'\n"
            'exit 1\n')
    h = _harness(tmp_path, binary=_fake(tmp_path, body))
    h.write_home()
    res = await h.run_turn("x", None)
    assert res.returncode == 1
    assert res.reply == ""
    assert res.detail == "rate limited"
    assert "headers" not in res.detail
    assert "x-ratelimit" not in res.detail


@pytest.mark.asyncio
async def test_timeout(tmp_path):
    h = _harness(tmp_path, binary=_fake(tmp_path, "#!/bin/bash\nsleep 30\n"), turn_timeout=0.2)
    h.write_home()
    res = await h.run_turn("x", None)
    assert res.timed_out is True
    assert res.returncode == -1


@pytest.mark.asyncio
async def test_missing_binary_raises(tmp_path):
    h = _harness(tmp_path, binary=str(tmp_path / "nope"))
    h.write_home()
    with pytest.raises(KiloBinaryNotFoundError):
        await h.run_turn("x", None)


@pytest.mark.asyncio
async def test_key_never_in_logs(tmp_path, caplog):
    h = _harness(tmp_path, binary=_fake(tmp_path, _FAKE_OK))
    h.write_home()
    with caplog.at_level(logging.DEBUG, logger="tinyagentos.kilo_runtime"):
        await h.run_turn("x", None)
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_run_turn_large_output(tmp_path):
    large_text = "x" * 70000
    body = f'''#!/bin/bash
test -n "$KILO_API_KEY" || {{ echo "KILO_API_KEY is not set" >&2; exit 1; }}
[ -t 0 ] && {{ echo "stdin is a tty" >&2; exit 1; }}
printf '%s\\n' "$@" > "$(dirname "$0")/dump.argv"
env > "$(dirname "$0")/dump.env"
pwd > "$(dirname "$0")/dump.cwd"
echo '{{"type":"step_start","sessionID":"ses-1"}}'
echo '{{"type":"text","sessionID":"ses-1","part":{{"type":"text","text":"{large_text}"}}}}'
echo '{{"type":"step_finish","sessionID":"ses-1"}}'
exit 0
'''
    h = _harness(tmp_path, binary=_fake(tmp_path, body))
    h.write_home()
    res = await h.run_turn("test", None)
    assert res.returncode == 0
    assert len(res.reply) >= 70000
    assert res.session_id == "ses-1"
    assert not res.timed_out


@pytest.mark.asyncio
async def test_run_turn_stream_limit_overflow(tmp_path, monkeypatch):
    # Monkeypatch _STREAM_LIMIT to 1024 to trigger overflow
    from tinyagentos.kilo_runtime import _STREAM_LIMIT
    monkeypatch.setattr("tinyagentos.kilo_runtime._STREAM_LIMIT", 1024)
    
    body = f'''#!/bin/bash
test -n "$KILO_API_KEY" || {{ echo "KILO_API_KEY is not set" >&2; exit 1; }}
[ -t 0 ] && {{ echo "stdin is a tty" >&2; exit 1; }}
printf '%s\\n' "$@" > "$(dirname "$0")/dump.argv"
echo "$(dirname "$0")/pid" > "$(dirname "$0")/dump.pid"
echo "$$" > "$(dirname "$0")/dump.pid"
echo "{"x" * 4096}"
sleep 30
'''
    h = _harness(tmp_path, binary=_fake(tmp_path, body))
    h.write_home()
    res = await h.run_turn("test", None)
    assert res.returncode == -1
    assert not res.timed_out
    # Verify the stub process is no longer running
    with open(tmp_path / "dump.pid", "r") as f:
        pid = int(f.read().strip())
    import os
    with pytest.raises(OSError):
        os.kill(pid, 0)


# ---------------------------------------------------------------- ensure_taos_kilo_harness
def _kilo_state(tmp_path, monkeypatch):
    """Patch kilo_runtime so ensure_taos_kilo_harness builds a recording fake
    harness instead of touching the real kilo binary or secrets store."""
    import tinyagentos.kilo_runtime as kr

    built: list[str] = []

    class _FakeHarness:
        def __init__(self, home, key, binary, model):
            self.home = home
            self.key = key
            self.binary = binary
            self.model = model
            built.append(model)

        def write_home(self):
            pass

    monkeypatch.setattr(kr, "resolve_kilo_binary", lambda: "/usr/bin/kilo")
    monkeypatch.setattr(kr, "KiloHarness", _FakeHarness)

    state = _MockAppState(has_key=True)
    state.data_dir = tmp_path
    state.taos_kilo_harness = None
    return state, built


@pytest.mark.asyncio
async def test_ensure_taos_kilo_harness_uses_configured_model(tmp_path, monkeypatch):
    state, built = _kilo_state(tmp_path, monkeypatch)
    await ensure_taos_kilo_harness(state, "openai/gpt-4o", "the system prompt")
    assert built == ["openai/gpt-4o"]
    assert state.taos_kilo_harness.model == "openai/gpt-4o"


@pytest.mark.asyncio
async def test_ensure_taos_kilo_harness_defaults_empty_model(tmp_path, monkeypatch):
    state, built = _kilo_state(tmp_path, monkeypatch)
    await ensure_taos_kilo_harness(state, "", None)
    assert built == [DEFAULT_MODEL]
    assert state.taos_kilo_harness.model == DEFAULT_MODEL


@pytest.mark.asyncio
async def test_ensure_taos_kilo_harness_rebuilds_on_model_change(tmp_path, monkeypatch):
    state, built = _kilo_state(tmp_path, monkeypatch)
    await ensure_taos_kilo_harness(state, "model-a", None)
    await ensure_taos_kilo_harness(state, "model-a", None)  # cached: no rebuild
    await ensure_taos_kilo_harness(state, "model-b", None)  # model changed: rebuild
    assert built == ["model-a", "model-b"]
