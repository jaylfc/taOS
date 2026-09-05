"""Tests for the agent state repo helpers in ``tinyagentos.agent_git``.

The repo root *is* the agent home (``/root``), so anything ``.gitignore``
fails to exclude enters git history inside the container the moment the
deployer or the auto-committer runs ``git add -A``.  The versioning-scope
tests below therefore build a throwaway repo from the module's real
``_GITIGNORE_CONTENTS`` and assert, one path at a time, which paths are
tracked — a coarser assertion (e.g. "no file named .env.local") cannot
catch the next framework's config file.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tinyagentos import agent_git
# Imported from the deployer (its public name) on purpose: the point of the
# test is that the versioned scope is derived from this map, not duplicated.
from tinyagentos.deployer import AGENTS_MD_PATHS


# Paths the deployer, the frameworks or the shell write into the agent home
# that must never be versioned: credentials on the left, bulk trees and
# machine-local noise on the right.
SECRET_AND_BULK_PATHS = [
    ".hermes/config.yaml",          # model.api_key — install_hermes.sh patches it in
    ".hermes/.env",                 # OPENAI_API_KEY / API_SERVER_KEY
    ".openclaw/env",                # TAOS_BRIDGE_TOKEN + OPENAI_API_KEY
    ".openclaw/openclaw.json",      # bridge connection info
    ".env",
    ".env.local",
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".config/gh/hosts.yml",         # GitHub OAuth token (Secrets app)
    ".kube/config",
    ".aws/credentials",
    ".ssh/id_ed25519",
    ".bash_history",                # every command the agent ran, inline tokens included
    ".cache/pip/wheel.whl",
    ".local/share/uv/tool.bin",
    ".venv/lib/site.py",
    ".npm/_cacache/index",
    ".taos/committer.log",          # the committer's own log — would dirty the tree it commits
    ".taos/trace/events.jsonl",     # bind mount from the host
]

# Agent state the feature exists to version.
STATE_PATHS = [
    ".gitignore",
    "AGENTS.md",
    "workspace/notes.md",
    "workspace/nested/deep/file.txt",
    "memory/facts.md",
    "memory/nested/deep/file.txt",
    *sorted(p.removeprefix("/root/") for p in AGENTS_MD_PATHS.values()),
]


def _write(repo: Path, rel: str, content: str = "x") -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


@pytest.fixture(scope="module")
def tracked_paths(tmp_path_factory) -> set[str]:
    """Paths ``git add -A`` tracks in a repo carrying the real .gitignore."""
    repo = tmp_path_factory.mktemp("agent_home")
    (repo / ".gitignore").write_text(agent_git._GITIGNORE_CONTENTS)
    for rel in SECRET_AND_BULK_PATHS + STATE_PATHS:
        if rel != ".gitignore":
            _write(repo, rel)
    assert _git(repo, "init", "-b", "main").returncode == 0
    _git(repo, "config", "user.email", "agent@taos.local")
    _git(repo, "config", "user.name", "test-agent")
    add = _git(repo, "add", "-A")
    assert add.returncode == 0, add.stderr
    listed = _git(repo, "ls-files")
    assert listed.returncode == 0, listed.stderr
    return {line.strip() for line in listed.stdout.splitlines() if line.strip()}


@pytest.mark.parametrize("rel", SECRET_AND_BULK_PATHS)
def test_secret_and_bulk_paths_are_not_versioned(rel, tracked_paths):
    assert rel not in tracked_paths, f"{rel} would be committed into agent state history"


@pytest.mark.parametrize("rel", STATE_PATHS)
def test_state_paths_are_versioned(rel, tracked_paths):
    assert rel in tracked_paths, f"{rel} is agent state but is not versioned"


def test_unknown_framework_config_is_excluded_by_default(tmp_path):
    """The scope is an allowlist: a framework nobody has written yet drops a
    config file with an api_key in it, and it must be out of scope without
    anyone adding a pattern for it."""
    repo = tmp_path / "home"
    repo.mkdir()
    (repo / ".gitignore").write_text(agent_git._GITIGNORE_CONTENTS)
    _write(repo, ".futureframework/settings.json", '{"api_key": "sk-live-abc"}')
    _write(repo, "future-agent-cli/credentials.toml", "token = 'abc'")
    assert _git(repo, "init", "-b", "main").returncode == 0
    assert _git(repo, "add", "-A").returncode == 0
    tracked = _git(repo, "ls-files").stdout
    assert ".futureframework/settings.json" not in tracked
    assert "future-agent-cli/credentials.toml" not in tracked


def test_versioned_scope_is_a_single_constant():
    """A new framework adds a state path, never a secret pattern."""
    assert agent_git._STATE_PATHS
    for framework_path in AGENTS_MD_PATHS.values():
        assert framework_path.removeprefix("/root/") in agent_git._STATE_PATHS


class TestUnknownRevisionDiagnostics:
    """`git show` and `git rev-parse` word a missing object differently across
    git versions; both must map to 404 (unknown revision), not to 409."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stderr",
        [
            "fatal: bad revision 'deadbeef'",
            "fatal: ambiguous argument 'deadbeef': unknown revision or path "
            "not in the working tree.",
            "fatal: bad object deadbeef",
        ],
    )
    async def test_git_diff_reports_unknown_revision(self, stderr, monkeypatch):
        async def fake_exec(container, cmd, timeout=60):
            return 128, stderr

        monkeypatch.setattr(agent_git, "exec_in_container", fake_exec)
        with pytest.raises(RuntimeError) as excinfo:
            await agent_git.git_diff("taos-agent-test", "deadbeef")
        assert not isinstance(excinfo.value, agent_git.ContainerUnreachableError)
        assert "unknown revision" in str(excinfo.value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stderr",
        [
            "fatal: bad revision 'deadbeef'",
            "fatal: ambiguous argument 'deadbeef': unknown revision or path "
            "not in the working tree.",
        ],
    )
    async def test_git_rev_parse_reports_unknown_revision(self, stderr, monkeypatch):
        async def fake_exec(container, cmd, timeout=60):
            return 128, stderr

        monkeypatch.setattr(agent_git, "exec_in_container", fake_exec)
        with pytest.raises(RuntimeError) as excinfo:
            await agent_git.git_rev_parse("taos-agent-test", "deadbeef")
        assert not isinstance(excinfo.value, agent_git.ContainerUnreachableError)
        assert "unknown revision" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_exec_failure_is_still_container_unreachable(self, monkeypatch):
        async def fake_exec(container, cmd, timeout=60):
            return 1, "Error: Instance is not running"

        monkeypatch.setattr(agent_git, "exec_in_container", fake_exec)
        with pytest.raises(agent_git.ContainerUnreachableError):
            await agent_git.git_rev_parse("taos-agent-test", "deadbeef")


    @pytest.mark.asyncio
    async def test_marker_outside_a_git_diagnostic_is_still_unreachable(self, monkeypatch):
        """The markers are git's words. A container-side failure that merely
        quotes one of them is not a missing object."""
        async def fake_exec(container, cmd, timeout=60):
            return 1, "Error: Instance is not running (ambiguous argument)"

        monkeypatch.setattr(agent_git, "exec_in_container", fake_exec)
        with pytest.raises(agent_git.ContainerUnreachableError):
            await agent_git.git_rev_parse("taos-agent-test", "deadbeef")


class TestRevertFailureClassification:
    @pytest.mark.asyncio
    async def test_failed_reset_raises_git_operation_error(self, monkeypatch):
        """A reset that fails on repo state is not an unreachable container:
        the container answered, git could not do the work."""
        async def fake_exec(container, cmd, timeout=60):
            if cmd[0] == "bash":
                return 1, "fatal: Unable to write new index file"
            if "merge-base" in cmd:
                return 0, ""
            return 0, "a" * 40

        monkeypatch.setattr(agent_git, "exec_in_container", fake_exec)
        with pytest.raises(agent_git.GitOperationError) as excinfo:
            await agent_git.git_revert("taos-agent-test", "a" * 40)
        assert not isinstance(excinfo.value, agent_git.ContainerUnreachableError)
        assert "Unable to write new index file" in str(excinfo.value)
