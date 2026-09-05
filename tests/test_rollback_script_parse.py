"""Tests for how ``scripts/rollback.sh`` reads the ``.taos-rollback`` record.

The record lives in the install dir, which the installer chowns to the ``taos``
service account, and the rollback script escalates with ``sudo`` when it
restarts the unit. So the record must be *parsed as data* -- never executed --
and a corrupt or truncated record must fall through to the recovery-tag path
instead of dead-ending on "cannot resolve".
"""

import os
import subprocess
from pathlib import Path

import pytest

ROLLBACK_SH = Path(__file__).resolve().parent.parent / "scripts" / "rollback.sh"
RECOVERY_TAG = "taos-pre-update-main-1700000000"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A tiny git checkout with two commits and one taos-pre-update-* tag."""
    checkout = tmp_path / "install"
    checkout.mkdir()
    subprocess.check_call(
        ["git", "init", "-q", "-b", "main", str(checkout)],
        stdout=subprocess.DEVNULL,
    )
    _git(checkout, "config", "user.name", "taos test")
    _git(checkout, "config", "user.email", "test@example.invalid")
    (checkout / "VERSION").write_text("old\n")
    _git(checkout, "add", "VERSION")
    _git(checkout, "commit", "-qm", "old version")
    _git(checkout, "tag", RECOVERY_TAG)
    (checkout / "VERSION").write_text("new\n")
    _git(checkout, "commit", "-qam", "new version")
    return checkout


@pytest.fixture()
def stub_bin(tmp_path: Path) -> Path:
    """PATH shim so the script's service-restart step is a no-op in the test."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("systemctl", "launchctl", "sudo", "pgrep"):
        stub = bindir / name
        stub.write_text("#!/bin/sh\nexit 1\n")
        stub.chmod(0o755)
    return bindir


def _run_rollback(repo: Path, stub_bin: Path, tmp_path: Path, *args: str):
    env = {
        "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "TAOS_INSTALL_DIR": str(repo),
    }
    return subprocess.run(
        ["bash", str(ROLLBACK_SH), *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_payload_in_record_is_not_executed(repo, stub_bin, tmp_path):
    """A record carrying shell metacharacters must never run as code."""
    sentinel_sub = tmp_path / "pwned-substitution"
    sentinel_cmd = tmp_path / "pwned-command"
    (repo / ".taos-rollback").write_text(
        "# taOS rollback target\n"
        "prev_branch='main'\n"
        f"prev_sha=$(touch '{sentinel_sub}')\n"
        f"touch '{sentinel_cmd}'\n"
    )

    _run_rollback(repo, stub_bin, tmp_path)

    assert not sentinel_sub.exists(), (
        f"sentinel file {sentinel_sub} was created (expected: not created) -- "
        "rollback.sh executed the record file"
    )
    assert not sentinel_cmd.exists(), (
        f"sentinel file {sentinel_cmd} was created (expected: not created) -- "
        "rollback.sh executed the record file"
    )


def test_truncated_record_falls_back_to_recovery_tag(repo, stub_bin, tmp_path):
    """A half-written record (power cut mid-write) must use the recovery tag."""
    tag_sha = _git(repo, "rev-parse", RECOVERY_TAG)
    (repo / ".taos-rollback").write_text(
        "# taOS rollback target\nprev_branch='main'\nprev_sha='ab"
    )

    result = _run_rollback(repo, stub_bin, tmp_path)

    combined = result.stdout + result.stderr
    assert "cannot resolve" not in combined, (
        f'got "cannot resolve", expected the recovery-tag path; output:\n{combined}'
    )
    assert _git(repo, "rev-parse", "HEAD") == tag_sha, (
        f"expected rollback to the recovery tag {tag_sha[:12]}; output:\n{combined}"
    )


def test_record_without_sha_falls_back_to_recovery_tag(repo, stub_bin, tmp_path):
    """A record whose prev_sha line never landed must use the recovery tag."""
    tag_sha = _git(repo, "rev-parse", RECOVERY_TAG)
    (repo / ".taos-rollback").write_text("# taOS rollback target\nprev_branch='main'\n")

    result = _run_rollback(repo, stub_bin, tmp_path)

    combined = result.stdout + result.stderr
    assert _git(repo, "rev-parse", "HEAD") == tag_sha, (
        f"expected rollback to the recovery tag {tag_sha[:12]}; output:\n{combined}"
    )


def test_non_hex_sha_falls_back_to_recovery_tag(repo, stub_bin, tmp_path):
    """prev_sha must be a hex SHA; anything else is treated as no record."""
    tag_sha = _git(repo, "rev-parse", RECOVERY_TAG)
    (repo / ".taos-rollback").write_text(
        "# taOS rollback target\nprev_branch='main'\nprev_sha='--upload-pack=touch'\n"
    )

    result = _run_rollback(repo, stub_bin, tmp_path)

    combined = result.stdout + result.stderr
    assert _git(repo, "rev-parse", "HEAD") == tag_sha, (
        f"expected rollback to the recovery tag {tag_sha[:12]}; output:\n{combined}"
    )


def test_wellformed_record_restores_branch_and_commit(repo, stub_bin, tmp_path):
    """The happy path keeps working: both branch and commit come back."""
    old_sha = _git(repo, "rev-parse", "HEAD~1")
    (repo / ".taos-rollback").write_text(
        "# taOS rollback target\n"
        "prev_branch='main'\n"
        f"prev_sha='{old_sha}'\n"
        "prev_ts='1700000000'\n"
    )

    result = _run_rollback(repo, stub_bin, tmp_path)

    combined = result.stdout + result.stderr
    assert _git(repo, "rev-parse", "HEAD") == old_sha, combined
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main", combined


def test_explicit_target_still_wins(repo, stub_bin, tmp_path):
    """An explicit ref argument bypasses the record entirely, as before."""
    old_sha = _git(repo, "rev-parse", "HEAD~1")
    (repo / ".taos-rollback").write_text(
        "# taOS rollback target\nprev_branch='main'\nprev_sha='ab"
    )

    result = _run_rollback(repo, stub_bin, tmp_path, old_sha)

    assert _git(repo, "rev-parse", "HEAD") == old_sha, result.stdout + result.stderr
