"""Permission hardening of the Claude Code harness (tsk-pxnwbs).

The harness runs ``--permission-mode default`` with an ``--allowedTools`` list,
never ``bypassPermissions``, and passes a settings file whose PreToolUse hook
refuses ``git push`` and ``gh`` from the shell, so the only route to GitHub is
the gated taOS endpoint.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from tinyagentos.claude_code_runtime import ClaudeCodeHarness

TOKEN = "sk-ant-oat01-SECRETSECRETSECRET"


def _harness(tmp_path) -> ClaudeCodeHarness:
    h = ClaudeCodeHarness(home=tmp_path / "home", token=TOKEN, model="claude-sonnet-5-5")
    h.write_home()
    return h


def _flag(argv: list[str], flag: str) -> str:
    assert flag in argv, f"{flag} missing from {argv}"
    return argv[argv.index(flag) + 1]


def test_argv_uses_default_permission_mode_never_bypass(tmp_path):
    h = _harness(tmp_path)
    for argv in (h._argv(None, None), h._argv("sess-1", "/tmp/sys.md")):
        assert _flag(argv, "--permission-mode") == "default"
        assert "bypassPermissions" not in " ".join(argv)
        assert "--dangerously-skip-permissions" not in argv


def test_argv_carries_an_allowed_tools_list(tmp_path):
    h = _harness(tmp_path)
    tools = _flag(h._argv(None, None), "--allowedTools").split(",")
    for name in ("Read", "Grep", "Glob", "Bash"):
        assert name in tools
    ws = str(h.workspace)
    assert f"Edit(/{ws}/**)" in tools
    assert f"Write(/{ws}/**)" in tools
    # Unscoped writes would reach the harness's own settings file.
    assert "Edit" not in tools
    assert "Write" not in tools


def test_argv_passes_the_hook_settings_file(tmp_path):
    h = _harness(tmp_path)
    settings = json.loads(Path(_flag(h._argv(None, None), "--settings")).read_text())
    groups = settings["hooks"]["PreToolUse"]
    assert any(g["matcher"] == "Bash" and g["hooks"][0]["type"] == "command" for g in groups)


def _run_hook(tmp_path, payload) -> subprocess.CompletedProcess:
    h = _harness(tmp_path)
    settings = json.loads(Path(_flag(h._argv(None, None), "--settings")).read_text())
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(shlex.split(command), input=data, capture_output=True, text=True, check=False,
                          cwd=str(h.workspace), timeout=30)


def _bash(command: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}}


@pytest.mark.parametrize("command", [
    "git push origin main",
    "git push --force origin HEAD:dev",
    "git -C repo push",
    "cd repo && git commit -m x && git push",
    "gh pr create --fill",
    "gh issue create -t bug",
    "/usr/bin/gh api repos/jaylfc/taOS/pulls",
    "bash -c 'git push'",
    "echo hi; gh pr merge 1",
    "FOO=1 git push",
])
def test_hook_refuses_git_push_and_gh(tmp_path, command):
    res = _run_hook(tmp_path, _bash(command))
    assert res.returncode == 2, (command, res.returncode, res.stderr)
    assert "taOS" in res.stderr


@pytest.mark.parametrize("command", [
    "git status",
    "git log --oneline -5",
    "ls -la",
    "python3 -c 'print(1)'",
    "echo pushing ahead",
])
def test_hook_allows_other_shell_commands(tmp_path, command):
    res = _run_hook(tmp_path, _bash(command))
    assert res.returncode == 0, (command, res.stderr)


def test_hook_allows_non_shell_tools(tmp_path):
    res = _run_hook(tmp_path, {"tool_name": "Read", "tool_input": {"file_path": "x"}})
    assert res.returncode == 0


def test_hook_fails_closed_on_unreadable_input(tmp_path):
    res = _run_hook(tmp_path, "not json")
    assert res.returncode == 2
