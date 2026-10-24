"""Per-user shell allow/deny profile for the Claude Code harness.

Stacked on tsk-pxnwbs: the PreToolUse hook still hard-refuses ``git push`` and
``gh``; on top of that, a shell command the user has not decided yet is refused
and queued as an ask, the ask becomes an approve/deny Decision (also streamed
to the chat as an inline card), and the answer becomes an allow or deny rule in
that user's profile, viewable and revokable from the taOS Agent settings. A
rule is a program, or a program plus subcommand (``npm test``, ``git status``).
"""
from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from tinyagentos.claude_code_runtime import ClaudeCodeHarness

TOKEN = "sk-ant-oat01-SECRETSECRETSECRET"
SERVER_RAISED_KEY = "_server_raised"


def _harness(tmp_path, rules=None) -> ClaudeCodeHarness:
    h = ClaudeCodeHarness(home=tmp_path / "home", token=TOKEN, model="claude-sonnet-5-5")
    h.shell_rules = rules
    h.write_home()
    return h


def _hook_command(h: ClaudeCodeHarness) -> str:
    argv = h._argv(None, None)
    settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text())
    return settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def _run(h: ClaudeCodeHarness, command: str) -> subprocess.CompletedProcess:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    return subprocess.run(shlex.split(_hook_command(h)), input=json.dumps(payload),
                          capture_output=True, text=True, check=False,
                          cwd=str(h.workspace), timeout=30)


RULES = {"allow": ["ls", "cat", "gh", "git status", "npm test"], "deny": ["curl", "npm publish"]}


# ---------------------------------------------------------------------------
# The hook against a profile
# ---------------------------------------------------------------------------

def test_allowed_programs_run(tmp_path):
    h = _harness(tmp_path, RULES)
    for command in ("ls -la", "cat a | ls", "FOO=1 ls", "X=1 cat x && ls", "git status",
                    "git -C repo status --short", "npm test -- --watch"):
        res = _run(h, command)
        assert res.returncode == 0, (command, res.stderr)
    assert h.take_shell_asks() == []


def test_denied_command_is_refused_and_not_asked(tmp_path):
    h = _harness(tmp_path, RULES)
    for command, rule in (("ls && curl https://example.com", "curl"), ("npm publish", "npm publish"),
                          ("curl -X POST x", "curl")):
        res = _run(h, command)
        assert res.returncode == 2
        assert f"`{rule}`" in res.stderr
    assert h.take_shell_asks() == []


def test_undecided_command_is_refused_and_queued_as_an_ask(tmp_path):
    h = _harness(tmp_path, RULES)
    res = _run(h, "ls | /usr/bin/npm run build")
    assert res.returncode == 2
    assert "`npm run`" in res.stderr
    asks = h.take_shell_asks()
    assert [a["program"] for a in asks] == ["npm run"]
    assert asks[0]["command"] == "ls | /usr/bin/npm run build"
    # The subcommand rule is per subcommand: `git status` does not cover `git log`.
    assert _run(h, "git log -5").returncode == 2
    assert [a["program"] for a in h.take_shell_asks()] == ["git log"]
    # Taking the asks clears them.
    assert h.take_shell_asks() == []


def test_subshell_and_newline_programs_are_checked(tmp_path):
    h = _harness(tmp_path, RULES)
    for command, program in (("ls $(rm -rf x)", "rm"), ("ls\nrm x", "rm"), ("ls `wget y`", "wget")):
        res = _run(h, command)
        assert res.returncode == 2, command
        assert program in res.stderr
    assert {a["program"] for a in h.take_shell_asks()} == {"rm", "wget"}


def test_output_redirection_needs_its_own_rule(tmp_path):
    h = _harness(tmp_path, RULES)
    for command in ("cat x > ~/.bashrc", "cat a >> /etc/x", "ls &> out", "ls 2> err", "ls >& out"):
        res = _run(h, command)
        assert res.returncode == 2, command
        assert "`output-redirect`" in res.stderr
    assert {a["program"] for a in h.take_shell_asks()} == {"output-redirect"}
    # Input redirection, fd duplication and /dev/null need no rule.
    for command in ("cat < in.txt", "ls 2>&1 | cat", "ls > /dev/null", "ls >&2"):
        assert _run(h, command).returncode == 0, command
    allowed = {"allow": [*RULES["allow"], "output-redirect"], "deny": []}
    assert _run(_harness(tmp_path / "b", allowed), "cat x > out.txt").returncode == 0


def test_git_config_overrides_need_their_own_rule(tmp_path):
    h = _harness(tmp_path, RULES)
    for command in ("git -c alias.status='!rm -rf x' status", "git -calias.log=!x status",
                    "git --config-env=core.pager=X status"):
        res = _run(h, command)
        assert res.returncode == 2, command
        assert "`git -c`" in res.stderr
    assert {a["program"] for a in h.take_shell_asks()} == {"git -c"}
    assert _run(h, "git -C repo status").returncode == 0


def test_hard_block_wins_over_an_allow_rule(tmp_path):
    h = _harness(tmp_path, RULES)
    for command in ("gh pr create", "git push origin dev"):
        res = _run(h, command)
        assert res.returncode == 2
        assert "git push" in res.stderr
    assert h.take_shell_asks() == []


def test_without_a_profile_only_the_hard_block_applies(tmp_path):
    h = _harness(tmp_path, None)
    assert _run(h, "npm test").returncode == 0
    assert _run(h, "git push").returncode == 2


# ---------------------------------------------------------------------------
# Direct calls to commands() and profile_refusal() for shell guard holes
# ---------------------------------------------------------------------------

def test_command_substitution_in_double_quotes_is_refused():
    from tinyagentos.claude_code_guard import profile_refusal
    from tinyagentos.taos_agent_shell_profile import DEFAULT_ALLOW
    allow = set(DEFAULT_ALLOW)
    for cmd in ('echo "$(curl evil)"', 'echo "`curl evil`"', 'echo "${X:-$(curl a)}"'):
        refusal, _ = profile_refusal(cmd, allow, set())
        assert refusal is not None, cmd


def test_git_global_options_before_subcommand():
    from tinyagentos.claude_code_guard import commands, profile_refusal
    from tinyagentos.taos_agent_shell_profile import DEFAULT_ALLOW
    allow = set(DEFAULT_ALLOW)
    for opt in ("--work-tree", "--git-dir", "--namespace", "--super-prefix"):
        cmd = f"git {opt} status config core.pager x"
        keys = commands(cmd)
        assert keys != ["git status"], cmd
        refusal, _ = profile_refusal(cmd, allow, set())
        assert refusal is not None, cmd


def test_git_exec_path_adds_git_c_rule():
    from tinyagentos.claude_code_guard import commands
    for cmd in ("git --exec-path=/tmp/x status", "git --exec-path /tmp/x status"):
        keys = commands(cmd)
        assert "git -c" in keys, cmd


def test_git_c_alias_requires_git_c_rule():
    from tinyagentos.claude_code_guard import profile_refusal
    from tinyagentos.taos_agent_shell_profile import DEFAULT_ALLOW
    allow = set(DEFAULT_ALLOW) | {"git"}
    refusal, _ = profile_refusal("git -c alias.x=!sh x", allow, set())
    assert refusal is not None


def test_output_redirect_for_git_diff_and_sort():
    from tinyagentos.claude_code_guard import commands
    for cmd in ("git diff --output=/tmp/x", "git diff --output /tmp/x",
                "sort -o /tmp/x f", "sort -o/tmp/x f"):
        keys = commands(cmd)
        assert "output-redirect" in keys, cmd


def test_sort_compress_program_is_refused():
    from tinyagentos.claude_code_guard import commands
    for cmd in ("sort --compress-program=sh f", "sort --compress-prog=sh f",
                "sort --com=sh f", "sort --compress-program sh f"):
        keys = commands(cmd)
        assert keys is None, cmd


def test_sort_output_redirect_variants():
    from tinyagentos.claude_code_guard import commands
    for cmd in ("sort -ro /tmp/x f", "sort -r -o /tmp/x f",
                "sort --outp=/tmp/x f", "sort --o=/tmp/x f"):
        keys = commands(cmd)
        assert "output-redirect" in keys, cmd


def test_separator_and_substitution_cases():
    from tinyagentos.claude_code_guard import commands
    assert 'rm' in commands('sort -o ; rm ls')
    assert 'sh' in commands('sort -o | sh ls')
    assert 'rm' in commands('sort --output ; rm ls')
    assert 'rm' in commands('git --output ; rm x')
    assert 'curl' in commands('cat < <(curl)')
    assert 'curl' in commands('echo x > >(curl)')
    assert 'rm' in commands('cat < <(rm cat)')
    assert 'rm' in commands('echo a#b ; rm x')


def test_uniq_output_file_positional():
    from tinyagentos.claude_code_guard import commands
    for cmd in ("uniq u /tmp/x", "uniq -c u /tmp/x",
                "uniq -f 1 u /tmp/x", "uniq -- u /tmp/x"):
        keys = commands(cmd)
        assert "output-redirect" in keys, cmd


def test_uniq_double_dash_positional_counting():
    from tinyagentos.claude_code_guard import commands
    # After --, first positional is input, second is output
    keys = commands("uniq -- u /tmp/x")
    assert "output-redirect" in keys
    # Only one positional after -- should not trigger output-redirect
    keys = commands("uniq -- /tmp/x")
    assert "output-redirect" not in keys
    # Two positionals without -- still works
    keys = commands("uniq u /tmp/x")
    assert "output-redirect" in keys


def test_uniq_output_file_attached_option_values_and_dash():
    from tinyagentos.claude_code_guard import commands
    # Attached option values should not swallow the input positional
    for cmd in ("uniq -f1 a /tmp/x", "uniq -s2 a /tmp/x", "uniq -w3 a /tmp/x",
                "uniq -cf1 a /tmp/x", "uniq --skip-fields 1 a /tmp/x", "uniq -cf 1 a /tmp/x"):
        keys = commands(cmd)
        assert "output-redirect" in keys, cmd
    # -- resets uniq_pos, positional before -- is dropped
    keys = commands("uniq a -- /tmp/x")
    assert "output-redirect" in keys
    # After --, dash-led token is a positional, not an option
    keys = commands("uniq -- -in /tmp/x")
    assert "output-redirect" in keys
    # Bare - (stdin) is a positional
    keys = commands("uniq - /tmp/x")
    assert "output-redirect" in keys


def test_uniq_value_option_at_end_swallows_next_command():
    from tinyagentos.claude_code_guard import commands, profile_refusal
    from tinyagentos.taos_agent_shell_profile import DEFAULT_ALLOW
    allow = set(DEFAULT_ALLOW)
    # Value-taking uniq options at end of command should not swallow the next command's program
    for c in ('uniq -f ; rm ls', 'uniq -s ; rm ls', 'uniq --skip-fields ; rm ls',
              'uniq -w || rm ls', 'uniq -f & rm ls', 'uniq -f | sh ls',
              '(uniq -f) ; rm ls', 'uniq -f\nrm ls'):
        cmds = commands(c)
        # The second program (rm or sh) must be in commands
        assert any(cmd in ('rm', 'sh') for cmd in cmds), f"second program missing in {c!r}: {cmds}"
        # profile_refusal must refuse (rm/sh not in DEFAULT_ALLOW)
        refusal, _ = profile_refusal(c, allow, set())
        assert refusal is not None, f"should refuse but got None for {c!r}: {cmds}"
    # Controls that must stay as they are
    assert commands('uniq -f 1 a') == ['uniq']
    assert 'output-redirect' not in commands('uniq -f 1 a')
    cmds = commands('ls ; cat a')
    assert 'ls' in cmds and 'cat' in cmds, cmds
    assert 'output-redirect' in commands('uniq a /tmp/x')


def test_hash_in_word_is_not_a_comment():
    from tinyagentos.claude_code_guard import commands, profile_refusal
    from tinyagentos.taos_agent_shell_profile import DEFAULT_ALLOW
    allow = set(DEFAULT_ALLOW)
    # '#' inside a word must not start a comment; the rest of the line is still parsed
    for c in ('echo a#b ; rm x', 'ls#;rm x', 'echo x#\nrm y', 'ls # ; rm x'):
        cmds = commands(c)
        assert 'rm' in cmds, f"rm missing in {c!r}: {cmds}"
        refusal, _ = profile_refusal(c, allow, set())
        assert refusal is not None, f"should refuse but got None for {c!r}: {cmds}"
    # Controls that must stay as they are
    assert commands('git status # note') == ['git status']
    assert commands('ls -la') == ['ls']
    assert 'rm' in commands('echo "#"; rm x')


def test_uniq_control_commands_no_output_redirect():
    from tinyagentos.claude_code_guard import commands
    # These should NOT have output-redirect (only one positional or no second positional)
    for cmd in ("uniq a", "uniq -f1 a", "uniq -f 1 a", "uniq -cf1 a",
                "uniq -- a", "uniq -", "uniq --skip-fields=1 a",
                "uniq --skip-fields 1 a", "cat a | uniq -c"):
        keys = commands(cmd)
        assert "output-redirect" not in keys, cmd


def test_control_commands_are_allowed():
    from tinyagentos.claude_code_guard import profile_refusal
    from tinyagentos.taos_agent_shell_profile import DEFAULT_ALLOW
    allow = set(DEFAULT_ALLOW)
    for cmd in ('echo "hello world"', "echo '$(not expanded)'", "sort f",
                "sort -r f", "sort -k 2 f", "sort --reverse f",
                "uniq u", "uniq -c u", "uniq -f 1 u",
                "cat u | uniq", "sort f | uniq -c"):
        refusal, _ = profile_refusal(cmd, allow, set())
        assert refusal is None, cmd


# ---------------------------------------------------------------------------
# Wrapper programs, gh escaping, and fail-closed main() (tsk-lzyw3v)
# ---------------------------------------------------------------------------

def test_wrapper_programs_expose_real_program():
    from tinyagentos.claude_code_guard import commands
    # env
    cmds = commands("env curl x")
    assert "env" in cmds and "curl" in cmds, cmds
    # timeout: first non-option is duration, then program
    cmds = commands("timeout 60 curl x")
    assert "curl" in cmds, cmds
    # sudo -u bob curl x
    cmds = commands("sudo -u bob curl x")
    assert "curl" in cmds, cmds
    # xargs curl
    cmds = commands("xargs curl")
    assert "curl" in cmds, cmds


def test_wrapper_option_values_do_not_hide_program():
    from tinyagentos.claude_code_guard import commands
    cases = [
        ("nice -n 10 curl x", "10"),
        ("ionice -c 3 curl x", "3"),
        ("stdbuf -o 0 curl x", "0"),
        ("xargs -n 1 curl", "1"),
        ("timeout -s KILL 60 curl x", "60"),
        ("chroot /newroot curl x", "newroot"),
        ("chroot / curl x", "/"),
    ]
    for cmd, bad in cases:
        cmds = commands(cmd)
        assert "curl" in cmds, f"curl missing in {cmd!r}: {cmds}"
        assert bad not in cmds, f"option value {bad!r} leaked as key in {cmd!r}: {cmds}"


def test_hard_refusal_sees_program_behind_wrapper_values():
    from tinyagentos.claude_code_guard import refusal
    cases = [
        'chroot / g""h pr merge',
        'chroot /newroot g""h pr merge',
        'nice -n 10 g""h pr merge',
        'timeout -s KILL 60 g""h pr merge',
        'ionice -c 3 g""h pr merge',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        msg = refusal(payload)
        assert msg is not None, f"should refuse but got None for {cmd!r}"
    # positive control: sudo -u already fixed
    payload = {"tool_name": "Bash", "tool_input": {"command": 'sudo -u x g""h pr merge'}}
    assert refusal(payload) is not None


def test_hard_refusal_chroot_root_operand_bypass():
    from tinyagentos.claude_code_guard import refusal
    # chroot root operand parsed as a wrapper: /tmp/timeout has basename "timeout"
    payload = {"tool_name": "Bash", "tool_input": {"command": 'chroot /tmp/timeout g""h pr merge'}}
    assert refusal(payload) is not None


def test_hard_refusal_exec_a_option_bypass():
    from tinyagentos.claude_code_guard import refusal
    # exec -a NAME: -a takes a value but exec not in _WRAPPER_OPTS_WITH_VALUE
    payload = {"tool_name": "Bash", "tool_input": {"command": 'exec -a masked g""h pr merge'}}
    assert refusal(payload) is not None
    # exec -aNAME attached form
    payload = {"tool_name": "Bash", "tool_input": {"command": 'exec -amasked g""h pr merge'}}
    assert refusal(payload) is not None


def test_hard_refusal_env_split_string_bypass():
    from tinyagentos.claude_code_guard import refusal
    # env -S 'g""h pr merge' (separate value)
    payload = {"tool_name": "Bash", "tool_input": {"command": 'env -S \'g""h pr merge\''}}
    assert refusal(payload) is not None
    # env -Sg""h pr merge (attached value)
    payload = {"tool_name": "Bash", "tool_input": {"command": 'env -Sg""h pr merge'}}
    assert refusal(payload) is not None
    # env --split-string='g""h pr merge' (long form attached)
    payload = {"tool_name": "Bash", "tool_input": {"command": 'env --split-string=\'g""h pr merge\''}}
    assert refusal(payload) is not None
    # env -S with env var expansion (fail closed)
    payload = {"tool_name": "Bash", "tool_input": {"command": r"env -S '${X} pr merge'"}}
    assert refusal(payload) is not None


def test_hard_refusal_wrapper_bypass_positive_controls():
    from tinyagentos.claude_code_guard import refusal
    # chroot with normal root dir should still allow ls
    payload = {"tool_name": "Bash", "tool_input": {"command": "chroot /srv/root ls -la"}}
    assert refusal(payload) is None
    # env -S with normal command should still allow ls
    payload = {"tool_name": "Bash", "tool_input": {"command": "env -S 'ls -la'"}}
    assert refusal(payload) is None


def test_hard_gh_refusal_on_parsed_keys():
    from tinyagentos.claude_code_guard import refusal
    # g""h and 'gh' should be refused same as gh
    payload_gh = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "gh pr list"}}
    payload_g_empty = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": 'g""h pr list'}}
    payload_g_single = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "'gh' pr list"}}
    msg_gh = refusal(payload_gh)
    msg_g_empty = refusal(payload_g_empty)
    msg_g_single = refusal(payload_g_single)
    assert msg_gh is not None
    assert msg_g_empty == msg_gh
    assert msg_g_single == msg_gh
    # git push also
    payload_git = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push origin main"}}
    assert refusal(payload_git) == msg_gh


# ---------------------------------------------------------------------------
# Wrapper bypass vulnerabilities that must be REFUSED (tsk-r3knpi)
# ---------------------------------------------------------------------------

def test_bypass_exec_clustered_short_flag():
    from tinyagentos.claude_code_guard import refusal
    # These clustered short flags should be REFUSED but are currently ALLOWED
    cases = [
        'exec -la masked g""h pr merge',
        'exec -ca masked g""h pr merge',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        msg = refusal(payload)
        assert msg is not None, f"should refuse but got None for {cmd!r}"


def test_bypass_sudo_clustered_short_flag():
    from tinyagentos.claude_code_guard import refusal
    # These clustered short flags should be REFUSED but are currently ALLOWED
    cases = [
        'sudo -Eu root g""h pr merge',
        'sudo -nu root g""h pr merge',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        msg = refusal(payload)
        assert msg is not None, f"should refuse but got None for {cmd!r}"


def test_bypass_chroot_option_forms():
    from tinyagentos.claude_code_guard import refusal
    # These attached and separate-value forms should be REFUSED but are currently ALLOWED
    cases = [
        'chroot --userspec=root:root /tmp/x g""h pr merge',
        'chroot --groups=g /tmp/x g""h pr merge',
        'chroot --userspec root:root /tmp/x g""h pr merge',
        'chroot --groups g /tmp/x g""h pr merge',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        msg = refusal(payload)
        assert msg is not None, f"should refuse but got None for {cmd!r}"


def test_bypass_env_split_string_state_leak():
    from tinyagentos.claude_code_guard import refusal
    # env -S split-string state leak: word after split string is swallowed
    cases = [
        'env -S \'ls\' g""h pr merge',
        'env -S \'timeout 60\' g""h pr merge',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        msg = refusal(payload)
        assert msg is not None, f"should refuse but got None for {cmd!r}"


def test_bypass_env_attached_split_string_wrapper_end():
    from tinyagentos.claude_code_guard import refusal
    # env -Stimeout 60 / --split-string=timeout 60: split string ends on wrapper needing operand
    cases = [
        'env -Stimeout 60 g""h pr merge',
        'env --split-string=timeout 60 g""h pr merge',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        msg = refusal(payload)
        assert msg is not None, f"should refuse but got None for {cmd!r}"


# ---------- Issue F: ${...} in double quotes unparseable command ----------
def test_bypass_home_in_double_quotes():
    from tinyagentos.claude_code_guard import refusal, commands
    # ${HOME} in double quotes should be parseable (not treated as substitution)
    keys = commands('echo "${HOME}"')
    assert keys == ['echo'], f"expected ['echo'], got {keys!r}"
    refusal_msg = refusal({"tool_name": "Bash", "tool_input": {"command": 'echo "${HOME}"'}})
    assert refusal_msg is None, f"should allow but got refusal {refusal_msg!r}"

    # ${HOME} + g""h should be refused (keys ['echo', 'gh'])
    keys2 = commands('echo "${HOME}"; g""h pr merge')
    assert 'gh' in keys2, f"expected gh in keys, got {keys2!r}"
    refusal_msg2 = refusal({"tool_name": "Bash", "tool_input": {"command": 'echo "${HOME}"; g""h pr merge'}})
    assert refusal_msg2 is not None, f"should refuse but got None"


# ---------- Issue G: clustered env -S with separate split string ----------
def test_bypass_env_clustered_S():
    from tinyagentos.claude_code_guard import refusal, commands
    # env -iS 'ls' g""h pr merge -> should be refused (gh exposed)
    keys = commands("env -iS 'ls' g\"\"h pr merge")
    assert 'gh' in keys, "expected gh in keys, got " + str(keys)
    refusal_msg = refusal({"tool_name": "Bash", "tool_input": {"command": "env -iS 'ls' g\"\"h pr merge"}})
    assert refusal_msg is not None, "should refuse but got None"

    # env -iS 'g""h pr' -> should expose g""h pr and be refused
    keys2 = commands("env -iS 'g\"\"h pr'")
    keys2_str = str(keys2)
    assert 'g\"\"h' in keys2_str or 'gh' in keys2_str, "expected g\"\"h pr in keys, got " + keys2_str
    refusal_msg2 = refusal({"tool_name": "Bash", "tool_input": {"command": "env -iS 'g\"\"h pr'"}})
    assert refusal_msg2 is not None, "should refuse but got None"

    # Benign: env -iS 'ls' ls must stay allowed
    refusal_benign = refusal({"tool_name": "Bash", "tool_input": {"command": "env -iS 'ls' ls"}})
    assert refusal_benign is None, "should allow but got refusal"

    # Benign: env -iSls must stay allowed
    refusal_benign2 = refusal({"tool_name": "Bash", "tool_input": {"command": "env -iSls ls"}})
    assert refusal_benign2 is None, "should allow but got refusal"

    # Benign: env -i -S 'ls' ls must stay allowed
    refusal_benign3 = refusal({"tool_name": "Bash", "tool_input": {"command": "env -i -S 'ls' ls"}})
    assert refusal_benign3 is None, "should allow but got refusal"


# ---------- Issue H: sudo long options with separate value ----------
def test_bypass_sudo_long_opts():
    from tinyagentos.claude_code_guard import refusal
    # sudo --close-from 5 g""h pr merge
    refusal_msg = refusal({"tool_name": "Bash", "tool_input": {"command": 'sudo --close-from 5 g""h pr merge'}})
    assert refusal_msg is not None, f"should refuse but got None {refusal_msg!r}"

    # sudo --host x g""h pr merge
    refusal_msg2 = refusal({"tool_name": "Bash", "tool_input": {"command": 'sudo --host x g""h pr merge'}})
    assert refusal_msg2 is not None, f"should refuse but got None {refusal_msg2!r}"

    # sudo --role r g""h pr merge
    refusal_msg3 = refusal({"tool_name": "Bash", "tool_input": {"command": 'sudo --role r g""h pr merge'}})
    assert refusal_msg3 is not None, f"should refuse but got None {refusal_msg3!r}"

    # sudo --type t g""h pr merge
    refusal_msg4 = refusal({"tool_name": "Bash", "tool_input": {"command": 'sudo --type t g""h pr merge'}})
    assert refusal_msg4 is not None, f"should refuse but got None {refusal_msg4!r}"

    # sudo --command-timeout 5 g""h pr merge
    refusal_msg5 = refusal({"tool_name": "Bash", "tool_input": {"command": 'sudo --command-timeout 5 g""h pr merge'}})
    assert refusal_msg5 is not None, f"should refuse but got None {refusal_msg5!r}"

    # sudo --other-user u g""h pr merge
    refusal_msg6 = refusal({"tool_name": "Bash", "tool_input": {"command": 'sudo --other-user u g""h pr merge'}})
    assert refusal_msg6 is not None, f"should refuse but got None {refusal_msg6!r}"

    # Benign: sudo --host x ls must stay allowed
    refusal_benign = refusal({"tool_name": "Bash", "tool_input": {"command": "sudo --host x ls"}})
    assert refusal_benign is None, f"should allow but got refusal {refusal_benign!r}"

    # Benign: env -a x ls must stay allowed
    refusal_benign2 = refusal({"tool_name": "Bash", "tool_input": {"command": "env -a x ls"}})
    assert refusal_benign2 is None, f"should allow but got refusal {refusal_benign2!r}"

    # Benign: env --argv0 x ls must stay allowed
    refusal_benign3 = refusal({"tool_name": "Bash", "tool_input": {"command": "env --argv0 x ls"}})
    assert refusal_benign3 is None, f"should allow but got refusal {refusal_benign3!r}"


# ---------- Issue H: env short opt with value 'a' ----------
def test_bypass_env_short_opt_a():
    from tinyagentos.claude_code_guard import refusal
    # env -a x g""h pr merge
    refusal_msg = refusal({"tool_name": "Bash", "tool_input": {"command": 'env -a x g""h pr merge'}})
    assert refusal_msg is not None, f"should refuse but got None {refusal_msg!r}"

    # Benign: env -a pam ls must stay allowed
    refusal_benign = refusal({"tool_name": "Bash", "tool_input": {"command": "env -a pam ls"}})
    assert refusal_benign is None, f"should allow but got refusal {refusal_benign!r}"


# ---------- Parser test: commands with ${} and g'''h ----------
def test_parser_gh_substitution():
    from tinyagentos.claude_code_guard import commands
    keys = commands('echo "${HOME}"; g""h pr merge')
    assert keys == ['echo', 'gh'], f"expected ['echo', 'gh'], got {keys!r}"
    keys = commands('echo "$(id)"')
    assert keys is None, f"expected None for echo $(id), got {keys!r}"
    from tinyagentos.claude_code_guard import refusal
    # sudo -a, -c, -R take values and are missing from _WRAPPER_SHORT_OPTS_WITH_VALUE["sudo"]
    cases = [
        'sudo -a pam g""h pr merge',
        'sudo -c cls g""h pr merge',
        'sudo -R /x g""h pr merge',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        msg = refusal(payload)
        assert msg is not None, f"should refuse but got None for {cmd!r}"


def test_commands_parser_contains_gh_for_env_split_string_bypass():
    from tinyagentos.claude_code_guard import commands
    # The parser itself must see gh, not just the refusal
    keys = commands('env -S \'ls\' g""h pr merge')
    assert 'gh' in keys, f"gh missing in {keys!r}"


# ---------------------------------------------------------------------------
# Positive controls that must stay ALLOWED (benign)
# ---------------------------------------------------------------------------

def test_benign_exec_clustered_short_flag():
    from tinyagentos.claude_code_guard import refusal
    # These benign cases should stay ALLOWED
    cases = [
        'exec -la foo ls',
        'exec -ca foo ls',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        assert refusal(payload) is None, f"should allow but got refusal for {cmd!r}"


def test_benign_sudo_clustered_short_flag():
    from tinyagentos.claude_code_guard import refusal
    # These benign cases should stay ALLOWED
    cases = [
        'sudo -Eu root ls',
        'sudo -nu root ls',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        assert refusal(payload) is None, f"should allow but got refusal for {cmd!r}"


def test_benign_chroot_option_forms():
    from tinyagentos.claude_code_guard import refusal
    # These benign attached and separate-value forms should stay ALLOWED
    cases = [
        'chroot --userspec=root:root /tmp/x ls',
        'chroot --groups=g /tmp/x ls',
        'chroot --userspec root:root /tmp/x ls',
        'chroot --groups g /tmp/x ls',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        assert refusal(payload) is None, f"should allow but got refusal for {cmd!r}"


def test_benign_env_split_string():
    from tinyagentos.claude_code_guard import refusal
    # env -S 'ls -la' and env -S 'ls' should stay ALLOWED
    cases = [
        "env -S 'ls -la'",
        "env -S 'ls' ls",
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        assert refusal(payload) is None, f"should allow but got refusal for {cmd!r}"


def test_benign_env_clustered_short_options():
    from tinyagentos.claude_code_guard import refusal
    # env -iu FOO ls should stay ALLOWED
    payload = {"tool_name": "Bash", "tool_input": {"command": "env -iu FOO ls"}}
    assert refusal(payload) is None


def test_benign_sudo_short_options_with_value():
    from tinyagentos.claude_code_guard import refusal
    # sudo -a pam ls and sudo -Eu root ls should stay ALLOWED
    cases = [
        'sudo -a pam ls',
        'sudo -Eu root ls',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        assert refusal(payload) is None, f"should allow but got refusal for {cmd!r}"


def test_benign_ls():
    from tinyagentos.claude_code_guard import refusal
    # ls -la should stay ALLOWED
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
    assert refusal(payload) is None


def test_main_fails_closed_on_exception(tmp_path, monkeypatch):
    from tinyagentos.claude_code_guard import main
    import json
    import sys
    from io import StringIO
    # Monkeypatch the rules loader path to raise
    def raise_runtime(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr("builtins.open", raise_runtime)
    # Prepare stdin with a payload that triggers profile check
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    old_stdin = sys.stdin
    sys.stdin = StringIO(payload)
    try:
        # main with --rules pointing to a file (will hit the patched open)
        rc = main(["--rules", "/nonexistent/rules.json"])
    finally:
        sys.stdin = old_stdin
    assert rc == 2


# ---------------------------------------------------------------------------
# Profile store, asks -> Decisions, answers -> rules, settings routes
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=False)
async def ds(client):
    store = client._transport.app.state.desktop_settings
    if store._db is not None:
        await store.close()
    await store.init()
    yield store
    await store.close()


def _admin_uid(app) -> str:
    return app.state.auth.find_user("admin")["id"]


@pytest.mark.asyncio
async def test_new_profile_is_seeded_with_read_only_programs(client, ds):
    from tinyagentos.taos_agent_shell_profile import DEFAULT_ALLOW, load_profile
    profile = await load_profile(ds, "u1")
    assert set(DEFAULT_ALLOW) <= set(profile["allow"])
    assert "ls" in profile["allow"] and "git status" in profile["allow"]
    assert profile["deny"] == []
    for risky in ("rm", "curl", "sudo", "git", "git push", "gh", "bash", "python3", "npm"):
        assert risky not in profile["allow"]


@pytest.mark.asyncio
async def test_asks_become_one_decision_per_program_for_the_user(client, ds, tmp_path):
    from tinyagentos.taos_agent_shell_profile import load_profile, raise_shell_asks
    app = client._transport.app
    uid = _admin_uid(app)
    h = _harness(tmp_path, RULES)
    _run(h, "npm run build")
    _run(h, "npm run lint")
    asked = await raise_shell_asks(app.state, h, uid)
    assert [a["program"] for a in asked] == ["npm run"]
    assert asked[0]["decision_id"]
    pending = [d for d in await app.state.decision_store.list(status="pending")
               if (d.get("metadata") or {}).get("kind") == "shell_command"]
    assert len(pending) == 1
    d = pending[0]
    assert d["user_id"] == uid
    assert d["type"] == "approve_deny"
    assert d["metadata"][SERVER_RAISED_KEY] is True
    assert d["id"] == asked[0]["decision_id"]
    assert d["metadata"]["program"] == "npm run"
    assert "npm run build" in d["context"]
    # Asking again for the same rule while one is pending does not duplicate.
    _run(h, "npm run x")
    assert await raise_shell_asks(app.state, h, uid) == []
    assert "npm run" in (await load_profile(ds, uid))["asked"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,effect", [("approve", "allow"), ("deny", "deny")])
async def test_answering_the_decision_writes_the_rule(client, ds, tmp_path, answer, effect):
    from tinyagentos.taos_agent_shell_profile import load_profile, raise_shell_asks
    app = client._transport.app
    uid = _admin_uid(app)
    h = _harness(tmp_path, RULES)
    _run(h, "npm run build")
    await raise_shell_asks(app.state, h, uid)
    d = next(d for d in await app.state.decision_store.list(status="pending")
             if (d.get("metadata") or {}).get("kind") == "shell_command")
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": answer})
    assert resp.status_code == 200
    profile = await load_profile(ds, uid)
    assert "npm run" in profile[effect]
    assert "npm run" not in profile["asked"]


@pytest.mark.asyncio
async def test_caller_supplied_shell_command_decision_writes_no_rule(client, ds):
    from tinyagentos.taos_agent_shell_profile import load_profile
    app = client._transport.app
    uid = _admin_uid(app)
    resp = await client.post("/api/decisions", json={
        "from_agent": "taos-agent", "question": "ok?", "type": "approve_deny",
        "metadata": {"kind": "shell_command", "program": "rm", "user_id": uid},
    })
    assert resp.status_code == 200
    resp = await client.post(f"/api/decisions/{resp.json()['id']}/answer", json={"value": "approve"})
    assert resp.status_code == 200
    assert "rm" not in (await load_profile(ds, uid))["allow"]


@pytest.mark.asyncio
async def test_device_bearer_cannot_answer_a_shell_command_decision(client, ds):
    app = client._transport.app
    uid = _admin_uid(app)
    device = await app.state.device_store.register(user_id=uid, platform="ios", display_name="x")
    d = await app.state.decision_store.create(
        from_agent="taos-agent", question="q", type="approve_deny", user_id=uid,
        metadata={SERVER_RAISED_KEY: True, "kind": "shell_command", "program": "rm", "user_id": uid},
    )
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "approve"},
                             headers={"Authorization": f"Bearer {device['scoped_token']}"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_settings_routes_list_set_and_revoke(client, ds):
    resp = await client.get("/api/taos-agent/shell-profile")
    assert resp.status_code == 200
    body = resp.json()
    assert "ls" in body["allow"]

    resp = await client.put("/api/taos-agent/shell-profile/npm", json={"effect": "deny"})
    assert resp.status_code == 200
    assert "npm" in resp.json()["deny"]
    resp = await client.put("/api/taos-agent/shell-profile/npm", json={"effect": "allow"})
    assert "npm" in resp.json()["allow"]
    assert "npm" not in resp.json()["deny"]

    resp = await client.delete("/api/taos-agent/shell-profile/ls")
    assert resp.status_code == 200
    assert "ls" not in resp.json()["allow"]
    # A revoked seed stays revoked: the user's choice wins over the defaults.
    assert "ls" not in (await client.get("/api/taos-agent/shell-profile")).json()["allow"]

    assert (await client.put("/api/taos-agent/shell-profile/npm", json={"effect": "maybe"})).status_code == 400
    resp = await client.put("/api/taos-agent/shell-profile/npm%20test", json={"effect": "allow"})
    assert "npm test" in resp.json()["allow"]
    resp = await client.put("/api/taos-agent/shell-profile/git%20-c", json={"effect": "deny"})
    assert "git -c" in resp.json()["deny"]
    for bad in ("a%20b%20c", "-rf", "x%3By", "git%20-x"):
        assert (await client.put(f"/api/taos-agent/shell-profile/{bad}", json={"effect": "allow"})).status_code == 400
    assert (await client.delete("/api/taos-agent/shell-profile/nothere")).status_code == 404


@pytest.mark.asyncio
async def test_profile_feeds_the_harness_rules(client, ds):
    from tinyagentos.taos_agent_shell_profile import rules_for, set_rule
    await set_rule(ds, "u1", "npm", "allow")
    await set_rule(ds, "u1", "curl", "deny")
    rules = await rules_for(ds, "u1")
    assert "npm" in rules["allow"] and "ls" in rules["allow"]
    assert rules["deny"] == ["curl"]
    # Another user's profile is separate.
    assert "npm" not in (await rules_for(ds, "u2"))["allow"]


def test_bypass_env_clustered_short_options():
    from tinyagentos.claude_code_guard import refusal
    # env -iu FOO: clustered short options -i -u, -u takes value
    payload = {"tool_name": "Bash", "tool_input": {"command": 'env -iu FOO g""h pr merge'}}
    assert refusal(payload) is not None


def test_bypass_sudo_short_options_with_value():
    from tinyagentos.claude_code_guard import refusal
    # sudo -a, -c, -R take values and are missing from _WRAPPER_SHORT_OPTS_WITH_VALUE["sudo"]
    cases = [
        'sudo -a pam g""h pr merge',
        'sudo -c cls g""h pr merge',
        'sudo -R /x g""h pr merge',
    ]
    for cmd in cases:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        msg = refusal(payload)
        assert msg is not None, f"should refuse but got None for {cmd!r}"


# ---------------------------------------------------------------------------
# Cancel/hang inside raise_shell_asks must not skip the safe point
# ---------------------------------------------------------------------------
# The chat endpoint's _drive() finally drains shell asks (profile +
# decision-store I/O) and only then reaches the agent-loop safe point. A cancel
# that lands while raise_shell_asks is awaiting propagates CancelledError, which
# `except Exception` does not catch, so reach_safe_point() is skipped and the
# loop wedges in WORKING: every later chat request answers with the queued
# message notice forever. reach_safe_point() must run BEFORE the ask drain so a
# late cancel or hang cannot strand the loop. See card tsk-grcgcw.


@pytest.mark.asyncio
async def test_cancel_during_raise_shell_asks_reaches_safe_point(client, ds, app, monkeypatch):
    """A cancel landing while raise_shell_asks awaits must still reach the safe point.

    Reproduces the sink's early-_DONE path: the adapter emits an error reply
    (so the generator sees _DONE and cancels the drive task), and raise_shell_asks
    is blocked on a never-set Event so the cancellation lands inside it. Before
    the fix reach_safe_point() is skipped (spy count 0, loop stays WORKING);
    after the fix it runs first (spy count 1, loop returns to IDLE).
    """
    import tinyagentos.routes.taos_agent as ta_module
    from tinyagentos.agent_loop import AgentLoop
    from tinyagentos.taos_agent_runtime import FRAMEWORK_CLAUDE_CODE

    monkeypatch.setattr(ta_module, "system_agent_framework", lambda app_state: FRAMEWORK_CLAUDE_CODE)

    # Never set: keeps raise_shell_asks suspended at an await point so a
    # cancellation can land inside it (mirrors pending asks doing store I/O).
    blk = asyncio.Event()

    async def fake_raise_shell_asks(app_state, harness, user_id):
        await blk.wait()
        return []

    async def fake_rules_for(store, shell_user):
        return {"allow": ["ls"], "deny": []}

    monkeypatch.setattr(ta_module, "shell_profile", SimpleNamespace(
        rules_for=AsyncMock(side_effect=fake_rules_for),
        raise_shell_asks=fake_raise_shell_asks,
    ))

    fake_harness = SimpleNamespace(
        shell_rules={"allow": ["ls"], "deny": []},
        redact=lambda s: s,
    )
    monkeypatch.setattr(
        ta_module, "ensure_taos_claude_code_harness",
        AsyncMock(return_value=fake_harness),
    )

    class _ErrorAdapter:
        # An error reply makes the sink enqueue an early _DONE, which is what
        # makes the generator cancel the drive task while it is suspended inside
        # raise_shell_asks.
        def __init__(self, cfg, sink):
            self._sink = sink
            self.session_id = None

        async def ensure_session(self):
            self.session_id = "ses_cancel"

        async def prompt(self, text, trace_id=None, attachments=None):
            self._sink({"kind": "error", "error": "boom"})

        async def close(self):
            pass

    monkeypatch.setattr(ta_module, "ClaudeCodeCliAdapter", _ErrorAdapter)

    loop = AgentLoop()
    calls = {"reach": 0}
    real_reach = loop.reach_safe_point

    async def spy_reach(*args, **kwargs):
        calls["reach"] += 1
        return await real_reach(*args, **kwargs)

    loop.reach_safe_point = spy_reach
    app.state.taos_agent_loop = loop
    app.state.taos_claude_code_session_id = None
    app.state.taos_opencode_password = "testpw"

    await client.patch("/api/taos-agent/settings", json={"model": "claude-3-7-sonnet-20250219"})

    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "Hi"}]},
    )

    assert resp.status_code == 200
    # (a) the safe point must be reached exactly once, to un-wedge the loop.
    assert calls["reach"] == 1, f"reach_safe_point awaited {calls['reach']} time(s)"
    # (b) the loop must not be left wedged in WORKING.
    assert app.state.taos_agent_loop.state.value == "idle", \
        f"loop stuck in {app.state.taos_agent_loop.state.value}"


# ---------------------------------------------------------------------------
# F2: time and xargs value options (tsk-gdxqyz)
# ---------------------------------------------------------------------------

def test_bypass_time_and_xargs_value_options():
    from tinyagentos.claude_code_guard import refusal, commands
    # time -f %e g""h pr merge
    payload = {"tool_name": "Bash", "tool_input": {"command": '/usr/bin/time -f %e g""h pr merge'}}
    assert refusal(payload) is not None, "time -f should refuse"
    keys = commands('/usr/bin/time -f %e g""h pr merge')
    assert 'gh' in keys, f"expected gh in keys, got {keys!r}"

    # time -o /tmp/x g''h pr merge
    payload = {"tool_name": "Bash", "tool_input": {"command": "time -o /tmp/x g''h pr merge"}}
    assert refusal(payload) is not None, "time -o should refuse"
    keys = commands("time -o /tmp/x g''h pr merge")
    assert 'gh' in keys, f"expected gh in keys, got {keys!r}"

    # xargs --process-slot-var NAME g""h pr merge
    payload = {"tool_name": "Bash", "tool_input": {"command": 'xargs --process-slot-var NAME g""h pr merge'}}
    assert refusal(payload) is not None, "xargs --process-slot-var should refuse"
    keys = commands('xargs --process-slot-var NAME g""h pr merge')
    assert 'gh' in keys, f"expected gh in keys, got {keys!r}"


# ---------------------------------------------------------------------------
# F3: env clustered S only in short option clusters (tsk-gdxqyz)
# ---------------------------------------------------------------------------

def test_env_cluster_s_only_in_short_option_clusters():
    from tinyagentos.claude_code_guard import refusal, commands
    # env MAKES g""h pr merge -> S in MAKES should not trigger split-string handling
    # MAKES is the program, g""h is an argument; command should be allowed
    keys = commands('env MAKES g""h pr merge')
    assert keys == ['env', 'MAKES'], f"expected ['env', 'MAKES'], got {keys!r}"
    refusal_msg = refusal({"tool_name": "Bash", "tool_input": {"command": 'env MAKES g""h pr merge'}})
    assert refusal_msg is None, "env MAKES should be allowed (g\"\"h is an arg, not a command)"

    # env --default-signal=SIGPIPE ls -> should parse to ['env', 'ls']
    keys = commands('env --default-signal=SIGPIPE ls')
    assert keys == ['env', 'ls'], f"expected ['env', 'ls'], got {keys!r}"

    # env MyScript.sh x -> should parse to ['env', 'MyScript.sh'] (x is an argument)
    # Old buggy behavior: ['env', 'cript.sh', 'x'] (S in MyScript.sh swallowed My)
    keys = commands('env MyScript.sh x')
    assert keys == ['env', 'MyScript.sh'], f"expected ['env', 'MyScript.sh'], got {keys!r}"

    # env -uS ls -> should parse to ['env', 'ls'] (GNU env reads -uS as -u with value S)
    keys = commands('env -uS ls')
    assert keys == ['env', 'ls'], f"expected ['env', 'ls'], got {keys!r}"

    # env -iS "ls -l" -> should still return the split-string keys the existing test expects
    keys = commands('env -iS "ls -l"')
    assert 'ls' in keys, f"expected ls in keys for env -iS, got {keys!r}"
    # This is the benign control from test_bypass_env_clustered_S


# ---------------------------------------------------------------------------
# F4: unparseable command refusal message (tsk-gdxqyz)
# ---------------------------------------------------------------------------

def test_unparseable_command_refusal_message():
    from tinyagentos.claude_code_guard import refusal, UNPARSEABLE_REFUSAL
    # echo "$(date)" has command substitution in double quotes -> unparseable
    payload = {"tool_name": "Bash", "tool_input": {"command": 'echo "$(date)"'}}
    msg = refusal(payload)
    assert msg == UNPARSEABLE_REFUSAL, f"expected UNPARSEABLE_REFUSAL, got {msg!r}"
    assert "could not be parsed" in UNPARSEABLE_REFUSAL


# ---------------------------------------------------------------------------
# F5: command position parameter expansion refused (tsk-gdxqyz)
# ---------------------------------------------------------------------------

def test_command_position_parameter_expansion_refused():
    from tinyagentos.claude_code_guard import refusal, commands
    # g${X}h pr merge -> should be refused
    payload = {"tool_name": "Bash", "tool_input": {"command": 'g${X}h pr merge'}}
    assert refusal(payload) is not None, "g${X}h should refuse"

    # g$X""h pr merge -> should be refused
    payload = {"tool_name": "Bash", "tool_input": {"command": 'g$X""h pr merge'}}
    assert refusal(payload) is not None, 'g$X""h should refuse'

    # ${GH} pr merge -> should be refused
    payload = {"tool_name": "Bash", "tool_input": {"command": '${GH} pr merge'}}
    assert refusal(payload) is not None, '${GH} should refuse'

    # $HOME/.local/bin/uv sync -> path-prefix control, should be allowed (keys ['uv sync'])
    keys = commands('$HOME/.local/bin/uv sync')
    assert keys == ['uv sync'], f"expected ['uv sync'], got {keys!r}"

    # echo ${HOME} -> argument-position control, should be allowed (keys ['echo'])
    keys = commands('echo ${HOME}')
    assert keys == ['echo'], f"expected ['echo'], got {keys!r}"
    payload = {"tool_name": "Bash", "tool_input": {"command": 'echo ${HOME}'}}
    assert refusal(payload) is None, "echo ${HOME} should be allowed"


# ---------------------------------------------------------------------------
# tsk-ewkvsx: time -o/--output and tee file operands tag output-redirect
# ---------------------------------------------------------------------------


def test_time_output_option_tags_output_redirect():
    from tinyagentos.claude_code_guard import commands
    for cmd in ("time -o /tmp/x echo ok", "time -o/tmp/x echo ok",
                "time --output=/tmp/x echo ok", "time --output /tmp/x echo ok"):
        c = commands(cmd)
        assert c is not None
        assert "output-redirect" in c
        assert "echo" in c
    c = commands("time echo ok")
    assert c is not None
    assert "output-redirect" not in c


def test_tee_file_operand_tags_output_redirect():
    from tinyagentos.claude_code_guard import commands
    assert commands("tee /tmp/x") == ["tee", "output-redirect"]
    assert commands("tee -a /tmp/x") == ["tee", "output-redirect"]
    assert commands("tee") == ["tee"]


def test_time_tee_output_redirect_profile_refusal():
    from tinyagentos.claude_code_guard import profile_refusal
    msg, _ = profile_refusal("time -o /tmp/x echo ok", allow={"time", "echo", "tee"}, deny={"output-redirect"})
    assert msg is not None
    msg, _ = profile_refusal("tee /tmp/x", allow={"time", "echo", "tee"}, deny={"output-redirect"})
    assert msg is not None
    msg, _ = profile_refusal("time echo ok", allow={"time", "echo", "tee"}, deny={"output-redirect"})
    assert msg is None


# ---------------------------------------------------------------------------
# tsk-apy6di: time clustered short options (-ao, -po, -af, -apo) keep wrapped program
# ---------------------------------------------------------------------------

def test_time_clustered_output_option_keeps_wrapped_program():
    from tinyagentos.claude_code_guard import commands
    for cmd in ("time -ao /tmp/x echo ok", "time -po /tmp/x echo ok", "time -apo /tmp/x echo ok"):
        c = commands(cmd)
        assert c is not None, cmd
        assert "echo" in c, cmd
        assert "output-redirect" in c, cmd
        assert "x" not in c, cmd
    c = commands("time -af %e echo ok")
    assert c is not None
    assert "echo" in c
    assert "%e" not in c
    # Controls that must stay as they are
    assert commands("time -a echo ok") == ["time", "echo"]
    assert commands("time -ap echo ok") == ["time", "echo"]
    c = commands("time -ao/tmp/x echo ok")
    assert "echo" in c
    assert "output-redirect" in c


def test_time_clustered_output_option_profile_refusal():
    from tinyagentos.claude_code_guard import profile_refusal
    # Clustered option bypasses deny rule on wrapped program
    msg, _ = profile_refusal("time -ao /tmp/echo gh pr merge", allow={"time", "echo"}, deny={"gh"})
    assert msg is not None
    # Control: non-clustered form works correctly
    msg, _ = profile_refusal("time -a echo ok", allow={"time", "echo"}, deny={"gh"})
    assert msg is None
