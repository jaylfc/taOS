"""Per-user shell allow/deny profile for the Claude Code harness.

Stacked on tsk-pxnwbs: the PreToolUse hook still hard-refuses ``git push`` and
``gh``; on top of that, a shell command the user has not decided yet is refused
and queued as an ask, the ask becomes an approve/deny Decision (also streamed
to the chat as an inline card), and the answer becomes an allow or deny rule in
that user's profile, viewable and revokable from the taOS Agent settings. A
rule is a program, or a program plus subcommand (``npm test``, ``git status``).
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

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
