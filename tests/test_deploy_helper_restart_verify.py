"""Tests for the deploy helper's post-restart health verification.

jaylfc's 2026-10-11 review (PR #1910), item 2: the stale-marker rollback in
``self_update.post_update_startup`` runs *inside* the new code, so when the new
code never boots (an import error, say) nothing rolls back.  The fix moves that
decision into the helper's detached restart: it restarts the worker, waits for
it to come healthy, and rolls back to the checkpoint when it does not.

These tests execute the real shell script -- ``systemctl``, ``ss``, ``git``,
``at`` and ``systemd-run`` are stubbed on PATH -- because whether a detached
job survives a failed boot is not something a Python mock can establish.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts" / "taos-deploy-helper.sh"

CHECKPOINT_TAG = "taos-worker-pre-update-20261017-120000"


def _write_fake(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\nset -u\n" + body)
    path.chmod(0o755)


def _fake_bin(tmp_path: Path, *, healthy_after: int | None, record_at: bool = False) -> tuple[Path, Path, Path]:
    """Build a PATH directory of stubbed external commands.

    Args:
        healthy_after: how many ``systemctl is-active`` probes the worker takes
            to come up, or None for "never comes up".
        record_at: when True the ``at`` stub records its stdin (the detached
            job body) and succeeds; otherwise ``at`` always fails so the
            scheduler falls through to the next rung.

    Returns (bin_dir, calls_log).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    calls.write_text("")
    probes = tmp_path / "probes"
    probes.write_text("0")
    at_records = tmp_path / "at.records"

    if healthy_after is None:
        active_check = "exit 1"
    else:
        active_check = f'if [ "$n" -gt {healthy_after} ]; then exit 0; fi\nexit 1'

    _write_fake(bin_dir, "systemctl", f"""echo "systemctl $*" >> "{calls}"
if [ "${{1:-}}" = "is-active" ]; then
    n=$(cat "{probes}" 2>/dev/null || echo 0)
    n=$((n + 1))
    echo "$n" > "{probes}"
    {active_check}
fi
exit 0
""")

    # Always reports the worker port as listening, so "healthy" is decided by
    # the service state alone.
    _write_fake(bin_dir, "ss", 'echo "LISTEN 0 128 0.0.0.0:9898 0.0.0.0:*"\nexit 0\n')

    _write_fake(bin_dir, "git", f'echo "git $*" >> "{calls}"\nexit 0\n')

    if record_at:
        _write_fake(bin_dir, "at", f'cat >> "{at_records}"\nexit 0\n')
    else:
        _write_fake(bin_dir, "at", "exit 1\n")

    # Never let a real transient unit be started from a test run.
    _write_fake(bin_dir, "systemd-run", "exit 1\n")

    return bin_dir, calls, at_records


def _install_tree(tmp_path: Path) -> Path:
    """Create a fake worker install dir with a checkpoint manifest."""
    install = tmp_path / "install"
    repo = install / "tinyagentos"
    (repo / ".git").mkdir(parents=True)
    (install / "rollback-manifest.json").write_text(json.dumps({
        "checkpoint_tag": CHECKPOINT_TAG,
        "git_sha": "from-sha",
        "git_branch": "dev",
        "package_manager": "pip",
        "deps_snapshot": "",
        "created_at": "2026-10-17T12:00:00+00:00",
        "hostname": "test",
    }))
    return install


def _run_helper(tmp_path: Path, bin_dir: Path, install: Path, argv: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["TAOS_INSTALL_DIR"] = str(install)
    env["TAOS_WORKER_PORT"] = "9898"
    # Keep the verify window short: the loop still runs at least one probe.
    env["TAOS_RESTART_VERIFY_TIMEOUT"] = "1"
    env["TAOS_RESTART_VERIFY_INTERVAL"] = "1"
    env.pop("SUDO_USER", None)
    # BASH_ENV / ENV make a non-interactive shell source a site file that can
    # reset PATH; the stubs have to win or the helper would call the real
    # systemctl.
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)
    return subprocess.run(
        ["bash", str(HELPER), *argv],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_rolls_back_when_the_worker_never_becomes_healthy(tmp_path: Path):
    """The new code did not boot -- the detached job must roll back.

    This is the failure the in-process stale-marker guard cannot catch: the
    worker never comes up, so no Python runs and the update-in-progress marker
    would otherwise sit on disk forever.
    """
    bin_dir, calls, _ = _fake_bin(tmp_path, healthy_after=None)
    install = _install_tree(tmp_path)

    proc = _run_helper(tmp_path, bin_dir, install, ["restart-and-verify"])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "worker did not become healthy" in proc.stdout
    assert "rolling back" in proc.stdout
    # The rollback really restored the checkpointed code.
    assert f"git -C {install / 'tinyagentos'} checkout --quiet {CHECKPOINT_TAG}" in calls.read_text()


def test_does_not_roll_back_when_the_worker_comes_up(tmp_path: Path):
    """Control: a healthy restart must not touch the checkpoint."""
    bin_dir, calls, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)

    proc = _run_helper(tmp_path, bin_dir, install, ["restart-and-verify"])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "worker healthy after" in proc.stdout
    assert "rolling back" not in proc.stdout
    assert "checkout" not in calls.read_text()


def test_restart_self_schedules_the_health_verified_restart(tmp_path: Path):
    """``restart-self`` must schedule the verifying job, not a bare restart.

    If it scheduled a bare ``systemctl restart`` the worker would still come
    back from a failed boot with no rollback, which is the whole point.
    """
    bin_dir, _, at_records = _fake_bin(tmp_path, healthy_after=0, record_at=True)
    install = _install_tree(tmp_path)

    proc = _run_helper(tmp_path, bin_dir, install, ["restart-self"])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    scheduled = at_records.read_text()
    assert "restart-and-verify" in scheduled
    assert str(HELPER) in scheduled
    # The detached job runs under a different HOME and a minimal environment,
    # so the resolved install dir and worker port have to be carried across
    # explicitly or the verification would probe (and roll back) the wrong
    # target.
    assert f"TAOS_INSTALL_DIR={install}" in scheduled
    assert "TAOS_WORKER_PORT=9898" in scheduled
