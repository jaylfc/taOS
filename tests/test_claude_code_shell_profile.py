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
    for bad in ("a%20b%20c", "-rf", "x%3By"):
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
