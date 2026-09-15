"""Regression test for the single-branch clone branch-switch bug in
install-server.sh.

Background: a fresh `git clone --depth 1 --branch master` produces a
single-branch clone whose refspec only maps `refs/heads/master`. Re-running
the installer against a different `TAOS_BRANCH` (e.g. `dev`) used to do:

    git fetch --depth 1 origin "$BRANCH" && git reset --hard "origin/$BRANCH"

The fetch succeeded but only wrote FETCH_HEAD because the configured refspec
does not cover the new branch. `refs/remotes/origin/$BRANCH` was never
created, so the reset aborted with:

    fatal: ambiguous argument 'origin/dev': unknown revision or path not in the working tree.

Fix: both arms of the update branch now run
`git remote set-branches origin "$BRANCH"` before the fetch (idempotent,
no `--add`) and reset to `FETCH_HEAD` instead of `origin/$BRANCH`.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install-server.sh"


def _extract_plain_update_snippet() -> str:
    """Return the complete if/else/fi update block from install-server.sh.

    The production script guards the git update with:

        if [[ ! -d "$INSTALL_DIR/.git" ]]; then
            git clone --depth 1 --branch "$BRANCH" "$REPO" "$INSTALL_DIR"
        else
            if [[ "$(id -u)" == "0" && ... ]]; then
                sudo -u "$_repo_owner" git -C "$INSTALL_DIR" fetch ...
            else
                (cd "$INSTALL_DIR" && git fetch ... && git reset ...)
            fi
        fi

    We extract the outer if/else/fi so the behavioural test runs the
    production code verbatim.  At runtime the inner `if` branch is taken
    only when the repo is owned by a non-root user and we are root; in the
    test environment (unprivileged) the inner `else` arm runs, which
    exercises the single-branch clone branch-switch path.
    """
    text = SCRIPT.read_text()
    start_m = re.search(r"^\s*if\s+\[\[ ! -d \"\$INSTALL_DIR/\.git\" \]\];\s*then\s*$", text, re.MULTILINE)
    assert start_m, "could not locate the clone/update if-block in install-server.sh"
    start = start_m.start()
    # Walk forward to the matching outer fi.
    depth = 0
    i = start_m.end()
    while i < len(text) - 2:
        if text[i] == "\n" and text[i + 1 : i + 3] == "if":
            depth += 1
        elif text[i] == "\n" and text[i + 1 : i + 3] == "fi":
            if depth == 0:
                return text[start : i + 3]
            depth -= 1
        i += 1
    raise AssertionError("could not find closing fi of the clone/update block")


def _run_update_snippet(clone_dir: Path, branch: str, snippet: str) -> subprocess.CompletedProcess:
    """Run the extracted update snippet against *clone_dir* with BRANCH=*branch*."""
    script = "\n".join([
        "set -euo pipefail",
        'log()  { printf "[log] %s\\n" "$*" >&2; }',
        'warn() { printf "[warn] %s\\n" "$*" >&2; }',
        'die()  { printf "[die] %s\\n" "$*" >&2; exit 1; }',
        f'INSTALL_DIR="{clone_dir}"',
        f'BRANCH="{branch}"',
        snippet,
    ])
    return subprocess.run(
        ["/usr/bin/env", "bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _make_two_branch_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a real bare origin repo with `master` and `dev` branches.

    Returns (origin_dir, worktree_dir).  The worktree_dir is a normal clone
    that tests can mutate.
    """
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

    # Seed the repo with a file on master, then create dev from it.
    work = tmp_path / "seed"
    work.mkdir()
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], check=True, cwd=work)
    subprocess.run(["git", "config", "user.name", "Test"], check=True, cwd=work)
    (work / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=work)
    subprocess.run(["git", "commit", "-m", "seed"], check=True, cwd=work)
    subprocess.run(["git", "push", "origin", "master"], check=True, cwd=work)

    # Create dev branch in the origin.
    subprocess.run(["git", "branch", "dev", "master"], check=True, cwd=work)
    subprocess.run(["git", "push", "origin", "dev"], check=True, cwd=work)
    return origin, work


@pytest.mark.skipif(shutil.which("git") is None, reason="git required")
def test_branch_switch_from_master_to_dev(tmp_path: Path) -> None:
    """After cloning `--depth 1 --branch master`, switching to `dev` must
    succeed.  On the unfixed script this fails with the exact
    `ambiguous argument 'origin/dev'` error observed on the device."""
    if shutil.which("git") is None:
        pytest.skip("git not installed")

    origin, _ = _make_two_branch_repo(tmp_path)
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", "master", str(origin), str(clone)],
        check=True,
        capture_output=True,
    )

    snippet = _extract_plain_update_snippet()
    result = _run_update_snippet(clone, "dev", snippet)

    assert result.returncode == 0, (
        "branch-switch update failed -- this is the pre-fix failure mode "
        "(unfixed script leaves refs/remotes/origin/dev unresolved):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # Confirm the working tree is now at the dev commit.
    head = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    dev_commit = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "origin/dev"],
        capture_output=True, text=True, check=True,
    )
    assert head.stdout.strip() == dev_commit.stdout.strip(), (
        f"HEAD ({head.stdout.strip()}) is not at the dev commit "
        f"({dev_commit.stdout.strip()}) after update"
    )


def test_both_update_arms_use_fetch_head_and_set_branches() -> None:
    """Static assertion: both the sudo arm and the plain arm must contain
    `remote set-branches origin "$BRANCH"` before the fetch, and must reset
    to `FETCH_HEAD` rather than `origin/$BRANCH`."""
    text = SCRIPT.read_text()

    # The sudo arm: contains `sudo -u "$_repo_owner" git -C "$INSTALL_DIR"`
    sudo_arm_pattern = re.compile(
        r"sudo -u \"\$_repo_owner\" git -C \"\$INSTALL_DIR\" remote set-branches origin \"\$BRANCH\""
        r".*?"
        r"sudo -u \"\$_repo_owner\" git -C \"\$INSTALL_DIR\" fetch --depth 1 origin \"\$BRANCH\""
        r".*?"
        r"sudo -u \"\$_repo_owner\" git -C \"\$INSTALL_DIR\" reset --hard FETCH_HEAD",
        re.DOTALL,
    )
    # Find all occurrences and check each one.
    for m in sudo_arm_pattern.finditer(text):
        arm = m.group(0)
        assert "remote set-branches origin \"$BRANCH\"" in arm, (
            "sudo arm missing `git remote set-branches origin \"$BRANCH\"`"
        )
        assert "reset --hard FETCH_HEAD" in arm, (
            "sudo arm still resets to `origin/$BRANCH` instead of `FETCH_HEAD`"
        )

    # The plain arm: `(cd "$INSTALL_DIR" && git ... && git reset ...)`
    plain_arm_pattern = re.compile(
        r"\(cd \"\$INSTALL_DIR\" && "
        r".*?git remote set-branches origin \"\$BRANCH\".*?"
        r"git fetch --depth 1 origin \"\$BRANCH\".*?"
        r"git reset --hard FETCH_HEAD"
        r".*?\)",
        re.DOTALL,
    )
    for m in plain_arm_pattern.finditer(text):
        arm = m.group(0)
        assert "git remote set-branches origin \"$BRANCH\"" in arm, (
            "plain arm missing `git remote set-branches origin \"$BRANCH\"`"
        )
        assert "git reset --hard FETCH_HEAD" in arm, (
            "plain arm still resets to `origin/$BRANCH` instead of `FETCH_HEAD`"
        )

    # At least one arm must exist in each form (we need both arms in the script).
    assert "sudo -u \"$_repo_owner\" git -C \"$INSTALL_DIR\" fetch" in text, (
        "sudo arm of update block not found in install-server.sh"
    )
    assert "(cd \"$INSTALL_DIR\" && git remote set-branches origin \"$BRANCH\"" in text, (
        "plain arm of update block not found in install-server.sh"
    )
