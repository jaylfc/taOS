"""Tests for the deploy helper's post-restart health verification.

jaylfc's 2026-10-11 review (PR #1910), item 2: the stale-marker rollback in
``self_update.post_update_startup`` runs *inside* the new code, so when the new
code never boots (an import error, say) nothing rolls back.  The fix moves that
decision into the helper's detached restart: it restarts the worker, waits for
it to come healthy, and rolls back to the checkpoint when it does not.

These tests execute the real shell script -- ``systemctl``, ``git``, ``at`` and ``systemd-run`` are stubbed on PATH;
the health check's process inputs (the unit's MainPID, /proc, ps) are real -- because whether a detached
job survives a failed boot is not something a Python mock can establish.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

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
    mainpid = tmp_path / "mainpid"
    mainpid.write_text("0")
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
if [ "${{1:-}}" = "show" ]; then
    cat "{mainpid}" 2>/dev/null || echo 0
    exit 0
fi
exit 0
""")


    _write_fake(bin_dir, "git", f'echo "git $*" >> "{calls}"\nexit 0\n')

    if record_at:
        _write_fake(bin_dir, "at", f'cat >> "{at_records}"\nexit 0\n')
    else:
        _write_fake(bin_dir, "at", "exit 1\n")

    # Never let a real transient unit be started from a test run.
    _write_fake(bin_dir, "systemd-run", "exit 1\n")

    return bin_dir, calls, at_records


@contextlib.contextmanager
def _fake_worker(tmp_path: Path, *, as_worker: bool = True):
    """Spawn a real process for the health check to inspect.

    With ``as_worker`` its cmdline is the worker entrypoint (via
    ``exec -a``); without, it is a plain ``sleep``.  The PID is written to
    the ``mainpid`` file the stubbed ``systemctl show`` reads, so the check
    runs against a genuine process, not a stubbed probe.
    """
    argv0 = "python3 -m tinyagentos.worker --headless" if as_worker else "sleep"
    proc = subprocess.Popen(
        ["bash", "-c", f"exec -a '{argv0}' sleep 300"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (tmp_path / "mainpid").write_text(str(proc.pid))
    try:
        yield proc
    finally:
        proc.terminate()
        proc.wait(timeout=10)


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


# The heartbeat-ok timestamp the worker rewrites in its state dir after
# every successful heartbeat (tinyagentos/worker/agent.py,
# HEARTBEAT_OK_FILENAME).  The helper's health check resolves the state
# dir as $TAOS_WORKER_STATE_DIR, defaulting to
# $INSTALL_DIR/.taos-worker-state -- which is where these tests plant it.
HEARTBEAT_OK_NAME = "last-heartbeat-ok"


def _plant_heartbeat_ok(install: Path, *, age_s: int = 0) -> Path:
    """Plant a heartbeat-ok timestamp as the worker would write it.

    A scenario that is meant to be healthy (or to fail for exactly one
    other reason) needs this artifact: without it the check correctly
    reports that no successful heartbeat has happened.
    """
    state_dir = install / ".taos-worker-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    hb = state_dir / HEARTBEAT_OK_NAME
    hb.write_text(f"{int(time.time()) - age_s}\n")
    return hb


def _run_helper(
    tmp_path: Path,
    bin_dir: Path,
    install: Path,
    argv: list[str],
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["TAOS_INSTALL_DIR"] = str(install)
    # Freshly spawned test processes must pass the uptime floor; the
    # floor itself is covered by its own test.
    env["TAOS_HEALTH_MIN_UPTIME_S"] = "0"
    # Keep the verify window short: the loop still runs at least one probe.
    env["TAOS_RESTART_VERIFY_TIMEOUT"] = "1"
    env["TAOS_RESTART_VERIFY_INTERVAL"] = "1"
    env.pop("SUDO_USER", None)
    # BASH_ENV / ENV make a non-interactive shell source a site file that can
    # reset PATH; the stubs have to win or the helper would call the real
    # systemctl.
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)
    env.update(extra_env or {})
    return subprocess.run(
        [shutil.which("bash") or "bash", str(HELPER), *argv],
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
    assert f"git -C {install / 'tinyagentos'} checkout --quiet -B dev {CHECKPOINT_TAG}" in calls.read_text()


def test_does_not_roll_back_when_the_worker_comes_up(tmp_path: Path):
    """Control: a healthy restart must not touch the checkpoint."""
    bin_dir, calls, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)
    _plant_heartbeat_ok(install)

    with _fake_worker(tmp_path):
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

    proc = _run_helper(
        tmp_path, bin_dir, install, ["restart-self"],
        extra_env={"TAOS_HEALTH_HEARTBEAT_MAX_AGE_S": "45"},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    scheduled = at_records.read_text()
    assert "restart-and-verify" in scheduled
    assert str(HELPER) in scheduled
    assert "TAOS_HEALTH_HEARTBEAT_MAX_AGE_S=45" in scheduled
    # The detached job runs under a different HOME and a minimal environment,
    # so the resolved install dir and health-check tuning have to be carried across
    # explicitly or the verification would probe (and roll back) the wrong
    # target.
    assert f"TAOS_INSTALL_DIR={install}" in scheduled
    assert "TAOS_HEALTH_MIN_UPTIME_S=0" in scheduled


def test_rollback_stays_on_the_recorded_branch(tmp_path: Path):
    """A bare checkout of the tag detaches HEAD, and update-worker's
    `git pull --ff-only origin <branch>` then fails on every later deploy."""
    bin_dir, calls, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)

    proc = _run_helper(tmp_path, bin_dir, install, ["rollback", CHECKPOINT_TAG])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"checkout --quiet -B dev {CHECKPOINT_TAG}" in calls.read_text()


def test_rollback_of_a_detached_checkpoint_stays_detached(tmp_path: Path):
    """`rev-parse --abbrev-ref HEAD` records "HEAD" for a detached worker; that
    is not a branch name to create."""
    bin_dir, calls, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)
    manifest = install / "rollback-manifest.json"
    manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "git_branch": "HEAD"}))

    proc = _run_helper(tmp_path, bin_dir, install, ["rollback", CHECKPOINT_TAG])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"checkout --quiet {CHECKPOINT_TAG}" in calls.read_text()
    assert "-B" not in calls.read_text()


def test_manifest_path_is_data_not_python_source(tmp_path: Path):
    """A quote in TAOS_CHECKPOINT_MANIFEST must not break out of the Python
    string literal the path used to be pasted into."""
    bin_dir, calls, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)
    odd = tmp_path / "it's"
    odd.mkdir()
    manifest = odd / "manifest.json"
    manifest.write_text((install / "rollback-manifest.json").read_text())

    proc = _run_helper(
        tmp_path, bin_dir, install, ["rollback"],
        extra_env={"TAOS_CHECKPOINT_MANIFEST": str(manifest)},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"checkout --quiet -B dev {CHECKPOINT_TAG}" in calls.read_text()


def _macos_like_path(bin_dir: Path, tmp_path: Path) -> None:
    """A PATH with no systemctl and no port tools: macOS has launchctl."""
    (bin_dir / "systemctl").unlink()
    _write_fake(bin_dir, "launchctl", f"""pid=$(cat "{tmp_path / 'mainpid'}" 2>/dev/null || echo -)
printf '%s\t0\tlocal.tinyagentos-worker\n' "$pid"
exit 0
""")
    for tool in ("bash", "grep", "python3", "dirname", "basename", "id", "uname", "cat", "date", "sed", "tr", "awk", "ps"):
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)


def test_health_check_uses_launchd_where_there_is_no_systemd(tmp_path: Path):
    """macOS has no systemctl (and the worker opens no port there either):
    the launchd entry's PID gets the same process checks."""
    bin_dir, _, _ = _fake_bin(tmp_path, healthy_after=0)
    _macos_like_path(bin_dir, tmp_path)
    install = _install_tree(tmp_path)
    _plant_heartbeat_ok(install)

    with _fake_worker(tmp_path):
        proc = _run_helper(
            tmp_path, bin_dir, install, ["health-check"], extra_env={"PATH": str(bin_dir)}
        )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "found in launchd" in proc.stdout


def test_health_check_passes_for_a_live_worker_with_nothing_on_the_worker_port(tmp_path: Path):
    """jaylfc (PR #1910): the old check probed port 9898, which the worker
    never opens -- a healthy worker failed it and every update rolled back.

    The check now inspects the unit's main process.  Nothing listens on
    9898 here and no ``ss`` is stubbed: the port is simply never consulted.
    A fresh heartbeat-ok timestamp is planted, as the real worker would
    maintain: alive process + fresh proof of heartbeat = healthy.
    """
    probe = socket.socket()
    try:
        if probe.connect_ex(("127.0.0.1", 9898)) == 0:
            pytest.skip("something is listening on 9898; cannot establish the scenario")
    finally:
        probe.close()
    bin_dir, _, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)
    _plant_heartbeat_ok(install)

    with _fake_worker(tmp_path):
        proc = _run_helper(tmp_path, bin_dir, install, ["health-check"])

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert '"healthy": true' in proc.stdout
    assert "not listening" not in proc.stdout + proc.stderr


def test_health_check_fails_when_the_main_process_is_gone(tmp_path: Path):
    """Service 'active' with a dead main process is not healthy."""
    bin_dir, _, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)
    _plant_heartbeat_ok(install)
    dead = subprocess.Popen(["sleep", "0.1"])
    dead.wait()
    (tmp_path / "mainpid").write_text(str(dead.pid))

    proc = _run_helper(tmp_path, bin_dir, install, ["health-check"])

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "not running" in proc.stdout + proc.stderr


def test_health_check_fails_when_the_main_process_is_not_the_worker(tmp_path: Path):
    """A live but unrelated main process must not pass as the worker."""
    bin_dir, _, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)
    _plant_heartbeat_ok(install)

    with _fake_worker(tmp_path, as_worker=False):
        proc = _run_helper(tmp_path, bin_dir, install, ["health-check"])

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "not the worker" in proc.stdout + proc.stderr


def test_health_check_fails_until_the_worker_has_been_up_long_enough(tmp_path: Path):
    """The uptime floor is what catches a crash loop: each new main process
    is alive and is the worker, but none of them stays up."""
    bin_dir, _, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)
    _plant_heartbeat_ok(install)

    with _fake_worker(tmp_path):
        proc = _run_helper(
            tmp_path, bin_dir, install, ["health-check"],
            extra_env={"TAOS_HEALTH_MIN_UPTIME_S": "3600"},
        )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "crash-looping" in proc.stdout + proc.stderr


def test_rollback_refuses_a_tag_that_is_not_a_pre_update_checkpoint(tmp_path: Path):
    """jaylfc (PR #1910): `rollback <tag>` must only accept the helper's own
    checkpoint tags, or a deploy caller could check out any ref in the
    worker repo.  The refusal has to happen before any checkout."""
    bin_dir, calls, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)

    proc = _run_helper(tmp_path, bin_dir, install, ["rollback", "v1.2.3"])

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "taos-worker-pre-update-*" in proc.stdout + proc.stderr
    assert "checkout" not in calls.read_text()


def test_trigger_update_writes_the_file_the_worker_reads(tmp_path: Path):
    """The worker's `_check_update_trigger` reads `update-trigger.json` from
    its state dir and requires `target_ref` (and honours `graceful`).
    `trigger-update` is the writer that was missing."""
    bin_dir, _, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)
    state_dir = tmp_path / "state"

    proc = _run_helper(
        tmp_path, bin_dir, install, ["trigger-update", "v2.1.0"],
        extra_env={"TAOS_WORKER_STATE_DIR": str(state_dir)},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    trigger = json.loads((state_dir / "update-trigger.json").read_text())
    assert trigger["target_ref"] == "v2.1.0"
    assert trigger["graceful"] is True
    # Written atomically (temp file + rename): no residue beside the trigger.
    assert [entry.name for entry in state_dir.iterdir()] == ["update-trigger.json"]


def test_trigger_update_no_graceful_and_a_ref_is_required(tmp_path: Path):
    bin_dir, _, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)
    state_dir = tmp_path / "state"

    proc = _run_helper(
        tmp_path, bin_dir, install, ["trigger-update", "v2.1.0", "--no-graceful"],
        extra_env={"TAOS_WORKER_STATE_DIR": str(state_dir)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((state_dir / "update-trigger.json").read_text())["graceful"] is False

    proc = _run_helper(
        tmp_path, bin_dir, install, ["trigger-update"],
        extra_env={"TAOS_WORKER_STATE_DIR": str(state_dir)},
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr


def test_health_check_fails_when_no_heartbeat_has_happened(tmp_path: Path):
    """jaylfc (PR #1910): "Add a test where the process is alive but no
    heartbeat has happened."

    Every process check passes -- the service is active, the main process
    is the worker and has stayed up -- but the worker has never completed
    a heartbeat, so there is no heartbeat-ok timestamp in its state dir.
    A worker that is running but not reaching the controller is not
    healthy, however alive its process looks.
    """
    bin_dir, _, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)

    with _fake_worker(tmp_path):
        proc = _run_helper(tmp_path, bin_dir, install, ["health-check"])

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "no successful heartbeat" in proc.stdout + proc.stderr


def test_health_check_fails_when_the_last_heartbeat_is_stale(tmp_path: Path):
    """A heartbeat-ok timestamp far older than the freshness bound means
    the heartbeats stopped landing: the worker is wedged, or the
    controller has been unreachable for a while.  Either way the process
    being alive must not pass the check on its own."""
    bin_dir, _, _ = _fake_bin(tmp_path, healthy_after=0)
    install = _install_tree(tmp_path)
    _plant_heartbeat_ok(install, age_s=3600)

    with _fake_worker(tmp_path):
        proc = _run_helper(tmp_path, bin_dir, install, ["health-check"])

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "heartbeat" in proc.stdout + proc.stderr


def test_health_check_fails_when_the_launchd_entry_has_no_pid(tmp_path: Path):
    """jaylfc (PR #1910): on macOS a launchd entry with no PID (shown as
    "-") is loaded but not running.  It used to skip the process check
    entirely and count as healthy; with no process to inspect it must
    fail.  A fresh heartbeat is planted so the missing PID is the only
    failure in the scenario."""
    bin_dir, _, _ = _fake_bin(tmp_path, healthy_after=0)
    _macos_like_path(bin_dir, tmp_path)
    install = _install_tree(tmp_path)
    _plant_heartbeat_ok(install)
    (tmp_path / "mainpid").write_text("-")

    proc = _run_helper(
        tmp_path, bin_dir, install, ["health-check"], extra_env={"PATH": str(bin_dir)}
    )

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "no PID" in proc.stdout + proc.stderr
