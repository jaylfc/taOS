"""Unit tests for the agent state versioning git helpers."""
from __future__ import annotations

import subprocess

import pytest
from unittest.mock import AsyncMock, patch

from tinyagentos.agent_git import (
    ContainerUnreachableError,
    DirtyTreeError,
    NotAncestorError,
    _GITIGNORE_CONTENTS,
    git_diff,
    git_rev_parse,
    git_revert,
)


# Real git diagnostics observed against the actual binary (not guessed text):
#   git rev-parse --verify deadbeef^{commit}  -> "fatal: Needed a single revision"
#   git show deadbeef                         -> "fatal: bad object deadbeef" /
#                                                 "fatal: unknown revision or path
#                                                  not in the working tree."
_REAL_UNKNOWN_REVISION_MESSAGES = [
    "fatal: Needed a single revision\n",
    "fatal: ambiguous argument 'deadbeef': unknown revision or path not in the working tree.\n",
    "fatal: bad revision 'deadbeef'\n",
    "fatal: bad object deadbeef\n",
    # case-insensitivity
    "FATAL: NEEDED A SINGLE REVISION\n",
    "Fatal: Bad Object deadbeef\n",
]


@pytest.mark.asyncio
class TestGitRevParseUnknownRevisionClassification:
    async def test_git_rev_parse_classifies_real_messages_as_unknown_revision(self):
        for message in _REAL_UNKNOWN_REVISION_MESSAGES:
            with patch(
                "tinyagentos.agent_git.exec_in_container",
                new=AsyncMock(return_value=(128, message)),
            ):
                with pytest.raises(RuntimeError) as exc_info:
                    await git_rev_parse("some-container", "deadbeef")
                assert not isinstance(exc_info.value, ContainerUnreachableError)
                assert "unknown revision" in str(exc_info.value).lower()

    async def test_git_rev_parse_other_failures_still_raise_container_unreachable(self):
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(return_value=(255, "ssh: connect to host: Connection refused\n")),
        ):
            with pytest.raises(ContainerUnreachableError):
                await git_rev_parse("some-container", "deadbeef")


@pytest.mark.asyncio
class TestGitDiffUnknownRevisionClassification:
    async def test_git_diff_classifies_real_messages_as_unknown_revision(self):
        for message in _REAL_UNKNOWN_REVISION_MESSAGES:
            with patch(
                "tinyagentos.agent_git.exec_in_container",
                new=AsyncMock(return_value=(128, message)),
            ):
                with pytest.raises(RuntimeError) as exc_info:
                    await git_diff("some-container", "deadbeef")
                assert not isinstance(exc_info.value, ContainerUnreachableError)
                assert "unknown revision" in str(exc_info.value).lower()

    async def test_git_diff_other_failures_still_raise_container_unreachable(self):
        with patch(
            "tinyagentos.agent_git.exec_in_container",
            new=AsyncMock(return_value=(255, "ssh: connect to host: Connection refused\n")),
        ):
            with pytest.raises(ContainerUnreachableError):
                await git_diff("some-container", "deadbeef")


class TestGitignoreCoversEnvVariants:
    def test_gitignore_ignores_env_dotfile_variants(self, tmp_path):
        (tmp_path / ".gitignore").write_text(_GITIGNORE_CONTENTS)
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "agent@taos.local"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "test-agent"], cwd=tmp_path, check=True)
        (tmp_path / ".env.local").write_text("SECRET=1")
        (tmp_path / ".env.production").write_text("SECRET=2")
        (tmp_path / "keep.txt").write_text("fine")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=tmp_path, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        assert ".env.local" not in staged
        assert ".env.production" not in staged
        assert "keep.txt" in staged


@pytest.mark.asyncio
class TestGitRevertNoopUnderLock:
    async def test_locked_script_rc3_is_noop(self):
        calls = []

        async def fake_exec(container, cmd, timeout=60):
            calls.append(cmd)
            if cmd[:2] == ["git", "-C"] and cmd[3:5] == ["rev-parse", "--verify"]:
                return (0, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
            if cmd[:2] == ["git", "-C"] and cmd[3] == "merge-base":
                return (0, "")
            if cmd[0] == "bash":
                return (3, "")
            return (0, "ok")

        with patch("tinyagentos.agent_git.exec_in_container", new=fake_exec):
            status = await git_revert("some-container", "deadbeef")
        assert status == "noop"
        # The noop decision must be made inside the locked script, not by a
        # separate pre-lock "rev-parse HEAD" call.
        bare_head_reads = [
            c for c in calls
            if c[:2] == ["git", "-C"] and c[3:5] == ["rev-parse", "HEAD"]
        ]
        assert bare_head_reads == []

    async def test_locked_script_rc0_is_reverted(self):
        async def fake_exec(container, cmd, timeout=60):
            if cmd[:2] == ["git", "-C"] and cmd[3:5] == ["rev-parse", "--verify"]:
                return (0, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
            if cmd[:2] == ["git", "-C"] and cmd[3] == "merge-base":
                return (0, "")
            if cmd[0] == "bash":
                return (0, "")
            return (0, "ok")

        with patch("tinyagentos.agent_git.exec_in_container", new=fake_exec):
            status = await git_revert("some-container", "deadbeef")
        assert status == "reverted"

    async def test_locked_script_other_rc_raises_dirty_tree(self):
        async def fake_exec(container, cmd, timeout=60):
            if cmd[:2] == ["git", "-C"] and cmd[3:5] == ["rev-parse", "--verify"]:
                return (0, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
            if cmd[:2] == ["git", "-C"] and cmd[3] == "merge-base":
                return (0, "")
            if cmd[0] == "bash":
                return (1, "")
            return (0, "ok")

        with patch("tinyagentos.agent_git.exec_in_container", new=fake_exec):
            with pytest.raises(DirtyTreeError):
                await git_revert("some-container", "deadbeef")

    async def test_non_ancestor_raises_before_lock(self):
        async def fake_exec(container, cmd, timeout=60):
            if cmd[:2] == ["git", "-C"] and cmd[3:5] == ["rev-parse", "--verify"]:
                return (0, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
            if cmd[:2] == ["git", "-C"] and cmd[3] == "merge-base":
                return (1, "")
            if cmd[0] == "bash":
                pytest.fail("locked script should not run when sha is not an ancestor")
            return (0, "ok")

        with patch("tinyagentos.agent_git.exec_in_container", new=fake_exec):
            with pytest.raises(NotAncestorError):
                await git_revert("some-container", "deadbeef")
