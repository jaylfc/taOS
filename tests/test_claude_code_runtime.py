"""Tests for the Claude Code harness of the system taOS Agent.

Covers decide_framework with the claude_code override, the harness's minimal
environment, and run_turn against a fake ``claude`` executable.
"""
from __future__ import annotations

import stat

import pytest

from tinyagentos.claude_code_runtime import (
    CLAUDE_CODE_TOKEN_SECRET,
    ClaudeCodeBinaryNotFoundError,
    ClaudeCodeHarness,
    home_for,
    scrub_home,
)
from tinyagentos.taos_agent_runtime import (
    FRAMEWORK_CHOICES,
    FRAMEWORK_CLAUDE_CODE,
    REASON_NO_CLAUDE_BINARY,
    REASON_NO_CLAUDE_TOKEN,
    decide_framework,
)

TOKEN = "sk-ant-oat01-SECRETSECRETSECRET"


def _decide(**over):
    kw = {
        "framework_setting": "claude_code",
        "device_class_setting": "auto",
        "detected_device_class": "desktop",
        "gateway_enabled": True,
        "picoclaw_available": False,
        "claude_code_available": True,
        "claude_code_token_present": True,
    }
    kw.update(over)
    return decide_framework(**kw)


# ---------------------------------------------------------------- decision
def test_choices_include_claude_code():
    assert FRAMEWORK_CLAUDE_CODE == "claude_code"
    assert "claude_code" in FRAMEWORK_CHOICES


def test_claude_code_chosen_when_binary_and_token_present():
    d = _decide()
    assert d.framework == "claude_code"
    assert d.preference == "claude_code"


def test_claude_code_without_token_falls_back_to_opencode():
    d = _decide(claude_code_token_present=False)
    assert d.framework == "opencode"
    assert d.preference == "claude_code"
    assert d.reason == REASON_NO_CLAUDE_TOKEN
    assert d.reason == "claude_code preferred, no claude_code_oauth_token secret, using opencode"


def test_claude_code_without_binary_falls_back_to_opencode():
    d = _decide(claude_code_available=False)
    assert d.framework == "opencode"
    assert d.reason == REASON_NO_CLAUDE_BINARY
    assert d.reason == "claude_code preferred, claude binary not found, using opencode"


def test_claude_code_does_not_need_the_gateway():
    assert _decide(gateway_enabled=False).framework == "claude_code"


def test_auto_on_mobile_still_prefers_picoclaw():
    d = _decide(framework_setting="auto", detected_device_class="mobile", picoclaw_available=True)
    assert d.framework == "picoclaw"


def test_auto_on_desktop_is_opencode_even_with_claude_available():
    assert _decide(framework_setting="auto").framework == "opencode"


def test_existing_callers_without_the_new_kwargs_still_work():
    d = decide_framework(
        framework_setting="opencode", device_class_setting="auto",
        detected_device_class="desktop", gateway_enabled=True, picoclaw_available=False,
    )
    assert d.framework == "opencode"


# ---------------------------------------------------------------- harness
def _harness(tmp_path, binary="claude", **kw) -> ClaudeCodeHarness:
    return ClaudeCodeHarness(home=tmp_path / "home", token=TOKEN, model="claude-sonnet-5-5",
                             binary=binary, **kw)


def test_home_for_is_under_data_dir(tmp_path):
    assert home_for(tmp_path) == tmp_path / "taos-agent-claude-code"
    assert CLAUDE_CODE_TOKEN_SECRET == "claude_code_oauth_token"


def test_write_home_is_0700_with_workspace(tmp_path):
    h = _harness(tmp_path)
    h.write_home()
    assert stat.S_IMODE(h.home.stat().st_mode) == 0o700
    assert h.workspace == h.home / "workspace"
    assert h.workspace.is_dir()


def test_repr_hides_the_token(tmp_path):
    assert TOKEN not in repr(_harness(tmp_path))


def test_env_is_exactly_the_minimal_set(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_SECRET_PROBE", "x")
    monkeypatch.delenv("TZ", raising=False)
    env = _harness(tmp_path)._env()
    assert set(env) == {"HOME", "PATH", "LANG", "NO_COLOR", "CLAUDE_CODE_OAUTH_TOKEN"}
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert env["HOME"] == str(tmp_path / "home")
    assert env["NO_COLOR"] == "1"
    assert "TAOS_SECRET_PROBE" not in env


def test_env_carries_tz_only_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "Europe/London")
    assert _harness(tmp_path)._env()["TZ"] == "Europe/London"


def test_redact_replaces_the_token(tmp_path):
    assert _harness(tmp_path).redact(f"boom {TOKEN} end") == "boom [redacted] end"


def test_scrub_home_deletes_nothing(tmp_path):
    h = _harness(tmp_path)
    h.write_home()
    (h.workspace / "keep.txt").write_text("x")
    scrub_home(tmp_path)
    assert (h.workspace / "keep.txt").exists()


# ---------------------------------------------------------------- run_turn
_FAKE_OK = r'''#!/bin/bash
out="$(dirname "$0")/dump"
printf '%s\n' "$@" > "$out.argv"
env > "$out.env"
cat > "$out.stdin"
pwd > "$out.cwd"
echo '{"type":"system","subtype":"init","session_id":"sess-1"}'
echo '{"type":"assistant","session_id":"sess-1","message":{"content":[{"type":"text","text":"hel"}]}}'
echo '{"type":"result","subtype":"success","is_error":false,"result":"ok","session_id":"sess-1"}'
'''


def _fake(tmp_path, body):
    p = tmp_path / "claude"
    p.write_text(body)
    p.chmod(0o755)
    return str(p)


@pytest.mark.asyncio
async def test_run_turn_first_and_resumed(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_SECRET_PROBE", "leak")
    h = _harness(tmp_path, binary=_fake(tmp_path, _FAKE_OK), system="be nice")
    h.write_home()
    events: list[dict] = []
    res = await h.run_turn("hello there", None, on_event=events.append)
    assert res.returncode == 0
    assert res.reply == "ok"
    assert res.session_id == "sess-1"
    assert not res.timed_out
    assert [e["type"] for e in events] == ["system", "assistant", "result"]
    assert (tmp_path / "dump.stdin").read_text() == "hello there"
    argv = (tmp_path / "dump.argv").read_text().split("\n")[:-1]
    assert argv[:2] == ["-p", "--output-format"]
    for flag, val in (("--output-format", "stream-json"), ("--model", "claude-sonnet-5-5"),
                      ("--permission-mode", "bypassPermissions"), ("--append-system-prompt", "be nice")):
        assert argv[argv.index(flag) + 1] == val
    assert "--verbose" in argv
    assert "--resume" not in argv
    assert (tmp_path / "dump.cwd").read_text().strip() == str(h.workspace)
    env = (tmp_path / "dump.env").read_text()
    assert f"CLAUDE_CODE_OAUTH_TOKEN={TOKEN}" in env
    assert "TAOS_SECRET_PROBE" not in env

    res2 = await h.run_turn("again", "sess-1")
    argv2 = (tmp_path / "dump.argv").read_text().split("\n")[:-1]
    assert argv2[argv2.index("--resume") + 1] == "sess-1"
    assert res2.session_id == "sess-1"


@pytest.mark.asyncio
async def test_no_system_flag_when_system_is_none(tmp_path):
    h = _harness(tmp_path, binary=_fake(tmp_path, _FAKE_OK))
    h.write_home()
    await h.run_turn("x", None)
    assert "--append-system-prompt" not in (tmp_path / "dump.argv").read_text()


@pytest.mark.asyncio
async def test_token_never_in_detail(tmp_path):
    body = f'#!/bin/bash\ncat > /dev/null\necho "auth failed for {TOKEN}" >&2\nexit 1\n'
    h = _harness(tmp_path, binary=_fake(tmp_path, body))
    h.write_home()
    res = await h.run_turn("x", None)
    assert res.returncode == 1
    assert res.reply == ""
    assert TOKEN not in res.detail
    assert "[redacted]" in res.detail


@pytest.mark.asyncio
async def test_error_result_gives_no_reply_but_a_detail(tmp_path):
    body = ('#!/bin/bash\ncat > /dev/null\n'
            'echo \'{"type":"result","is_error":true,"result":"rate limited","session_id":"s"}\'\n')
    h = _harness(tmp_path, binary=_fake(tmp_path, body))
    h.write_home()
    res = await h.run_turn("x", None)
    assert res.reply == ""
    assert res.detail


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
    with pytest.raises(ClaudeCodeBinaryNotFoundError):
        await h.run_turn("x", None)
