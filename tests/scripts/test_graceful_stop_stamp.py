"""Tests for the dedupe stamp in scripts/taos-graceful-stop.sh (tsk-gkupkv).

The stamp is a kill switch. If any local user can create the file the script
looks at, ``/api/system/prepare-shutdown`` is never called and agents never
drain on restart or reboot -- silently, because the script believes a sibling
invocation already stamped. These tests pin the stamp to a directory only the
service can write, and prove a foreign file at the old location cannot suppress
the drain.
"""
from __future__ import annotations

import os
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "taos-graceful-stop.sh"

# Where the stamp used to live: /run when writable, else /tmp. Both are shared
# with every other account on the box and /tmp is world-writable (1777), so a
# squat there is what the fix has to stop honouring.
LEGACY_STAMPS = (
    Path("/run/taos-prepare-shutdown.stamp"),
    Path("/tmp/taos-prepare-shutdown.stamp"),
)

CURL_STUB = '#!/bin/sh\necho "$@" >> "$CURL_LOG"\nexit "${CURL_EXIT:-0}"\n'


class FakeHost:
    """A throwaway install layout plus a curl stub that records every call."""

    def __init__(self, tmp_path: Path) -> None:
        self.runtime = tmp_path / "run" / "taos"      # systemd RuntimeDirectory=taos
        self.runtime.mkdir(parents=True)
        self.runtime.chmod(0o750)
        self.data = tmp_path / "install" / "data"     # no-systemd nohup install
        self.data.mkdir(parents=True)
        self.data.chmod(0o700)
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.curl_log = tmp_path / "curl.log"
        bindir = tmp_path / "bin"
        bindir.mkdir()
        curl = bindir / "curl"
        curl.write_text(CURL_STUB)
        curl.chmod(0o755)
        self._bindir = bindir

    def run(self, **overrides) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PATH"] = f"{self._bindir}:{env['PATH']}"
        env["HOME"] = str(self.home)
        env["CURL_LOG"] = str(self.curl_log)
        env["RUNTIME_DIRECTORY"] = str(self.runtime)
        env["TAOS_INSTALL_DIR"] = str(self.data.parent)
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = str(value)
        return subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60
        )

    @property
    def drain_calls(self) -> list[str]:
        if not self.curl_log.exists():
            return []
        return [ln for ln in self.curl_log.read_text().splitlines() if ln.strip()]

    def stamp_written_since(self, since: float) -> Path | None:
        """The stamp file this run created or refreshed, wherever it landed."""
        candidates = [
            self.runtime / "prepare-shutdown.stamp",
            self.data / "prepare-shutdown.stamp",
            *LEGACY_STAMPS,
        ]
        for path in candidates:
            try:
                if path.is_file() and path.stat().st_mtime >= since:
                    return path
            except OSError:
                continue
        return None


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    return FakeHost(tmp_path)


@contextmanager
def squatted_legacy_stamps():
    """Plant a foreign, fresh stamp at every legacy path we can write.

    Models the attack from the card: any local user creates the file (the /tmp
    sticky bit blocks deletion, not creation) and a cron job keeps it fresh.
    """
    saved: dict[Path, bytes] = {}
    planted: list[Path] = []
    for path in LEGACY_STAMPS:
        try:
            if path.exists():
                saved[path] = path.read_bytes()
            path.write_text(f"{int(time.time())}\n")
        except OSError:
            continue  # not our box to squat on (e.g. /run owned by root)
        planted.append(path)
    if not planted:
        pytest.skip("no legacy stamp path is writable here")
    try:
        yield planted
    finally:
        for path in planted:
            if path in saved:
                path.write_bytes(saved[path])
            else:
                path.unlink(missing_ok=True)


def assert_private_dir(directory: Path) -> None:
    mode = stat.S_IMODE(directory.stat().st_mode)
    assert not mode & 0o022, (
        f"stamp dir {directory} mode {mode:04o}, expected 0750 or stricter"
    )


def test_foreign_stamp_does_not_suppress_prepare_shutdown(host: FakeHost) -> None:
    with squatted_legacy_stamps():
        result = host.run()
    assert result.returncode == 0, result.stderr
    assert host.drain_calls, (
        "/api/system/prepare-shutdown was not called (expected: called)"
    )


def test_stamp_lands_in_a_directory_no_other_user_can_write(host: FakeHost) -> None:
    since = time.time() - 1
    result = host.run()
    assert result.returncode == 0, result.stderr
    stamp = host.stamp_written_since(since)
    assert stamp is not None, "the successful drain wrote no dedupe stamp"
    assert_private_dir(stamp.parent)
    assert stamp.parent == host.runtime, (
        f"stamp landed in {stamp.parent}, expected the private runtime dir {host.runtime}"
    )
    for legacy in LEGACY_STAMPS:
        assert host.stamp_written_since(since) != legacy


def test_stamp_records_its_own_epoch_so_the_age_check_is_portable(host: FakeHost) -> None:
    """`stat -c %Y` is GNU-only; on macOS/BSD it errors and the dedupe never fires."""
    host.run()
    stamp = host.runtime / "prepare-shutdown.stamp"
    assert stamp.is_file(), "no stamp written to the runtime dir"
    assert stamp.read_text().strip().isdigit(), (
        f"stamp does not carry its own epoch: {stamp.read_text()!r}"
    )

    # An old epoch inside a file whose mtime is fresh must NOT dedupe: the age
    # comes from the recorded epoch, not from the filesystem timestamp.
    stamp.write_text(f"{int(time.time()) - 3600}\n")
    os.utime(stamp, None)
    before = len(host.drain_calls)
    host.run()
    assert len(host.drain_calls) == before + 1, "an expired stamp suppressed the drain"


def test_future_dated_stamp_does_not_suppress_forever(host: FakeHost) -> None:
    """A future epoch must not dedupe: `date +%s - stamp_epoch` can go negative,
    and a negative number is always `-lt 60`. RTC-less Pis routinely step the
    clock forward by minutes to hours after an NTP sync post power-cut, so a
    stamp written just before that step reads as "in the future" afterwards --
    on the exact failure mode the dedupe exists to prevent, a clock step means
    a successful drain gets silently re-deduped on the next reboot hook.
    """
    host.run()
    stamp = host.runtime / "prepare-shutdown.stamp"
    assert stamp.is_file(), "no stamp written to the runtime dir"

    stamp.write_text(f"{int(time.time()) + 3600}\n")
    before = len(host.drain_calls)
    host.run()
    assert len(host.drain_calls) == before + 1, (
        "a future-dated stamp suppressed the drain"
    )


def test_second_run_within_60s_is_deduped(host: FakeHost) -> None:
    host.run()
    host.run()
    assert len(host.drain_calls) == 1, (
        f"expected one drain, got {len(host.drain_calls)}: {host.drain_calls}"
    )


@pytest.mark.skipif(
    os.access("/run/taos", os.W_OK),
    reason="an eligible /run/taos on this host wins the candidate lookup before hostile",
)
def test_world_writable_runtime_dir_is_refused(host: FakeHost, tmp_path: Path) -> None:
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    hostile.chmod(0o777)
    since = time.time() - 1
    host.run(RUNTIME_DIRECTORY=hostile)
    stamp = host.stamp_written_since(since)
    assert stamp is not None, "the successful drain wrote no dedupe stamp"
    assert stamp.parent != hostile, f"stamped into a world-writable dir: {hostile}"
    assert_private_dir(stamp.parent)


@pytest.mark.skipif(
    os.access("/run/taos", os.W_OK), reason="a real /run/taos on this host wins the lookup"
)
def test_nohup_install_stamps_under_the_data_dir(host: FakeHost) -> None:
    """No systemd, so no RuntimeDirectory: fall back to data/, installed 0700."""
    since = time.time() - 1
    host.run(RUNTIME_DIRECTORY=None)
    stamp = host.stamp_written_since(since)
    assert stamp is not None, "the successful drain wrote no dedupe stamp"
    assert stamp.parent == host.data, f"stamp landed in {stamp.parent}, expected {host.data}"
    assert_private_dir(stamp.parent)


def test_failed_drain_does_not_stamp(host: FakeHost) -> None:
    since = time.time() - 1
    host.run(CURL_EXIT=1)
    assert host.stamp_written_since(since) is None, (
        "a failed prepare-shutdown stamped, so the next invocation would skip draining"
    )
    host.run()
    assert len(host.drain_calls) == 2, "the retry after a failed drain was skipped"
