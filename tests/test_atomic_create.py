"""``atomic_create_bytes``: durable *create-if-absent* for one-time key material.

``atomic_write_bytes`` is a durable *replace* -- exactly right for a state file
that any writer may legitimately overwrite, and exactly wrong for the one-time
creation of persistent key material.  Two processes sharing a data dir can both
observe an absent ``.secrets_key`` (or ``hub/identity.json``), both generate,
and both write.  Each write is atomic, so the file is never corrupt, but the
last one wins: the *losing* process carries on using key material that is not on
disk, and every secret it encrypted becomes unreadable after a restart.

The fix is a primitive that cannot replace: the name is claimed with
``os.link``, which fails ``EEXIST`` instead of clobbering, so exactly one
writer's bytes are ever persisted and every other writer is handed those same
bytes back.
"""
from __future__ import annotations

import errno
import os
import stat
import threading
import time
from pathlib import Path

import pytest

import tinyagentos.atomic_io as atomic_io
from tinyagentos.atomic_io import atomic_create_bytes


class TestAtomicCreateBytes:
    def test_creates_the_file_and_returns_the_written_bytes(self, tmp_path: Path) -> None:
        target = tmp_path / "key.bin"
        returned = atomic_create_bytes(target, b"first")
        assert returned == b"first"
        assert target.read_bytes() == b"first"

    def test_an_existing_file_is_never_replaced(self, tmp_path: Path) -> None:
        """The whole point: a second creator must not clobber the first."""
        target = tmp_path / "key.bin"
        atomic_create_bytes(target, b"first")

        returned = atomic_create_bytes(target, b"second")

        assert target.read_bytes() == b"first", (
            "atomic_create_bytes replaced key material that was already "
            "persisted -- the first writer's secrets are now undecryptable"
        )
        assert returned == b"first", (
            "the losing creator must be handed the persisted bytes, not its "
            "own, or it keeps encrypting with a key that is not on disk"
        )

    def test_a_file_created_during_the_call_still_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The race is decided by the kernel, not by our existence check.

        Fault-injects the real interleave: another process creates the file in
        the window between this call's check and its own write.
        """
        target = tmp_path / "key.bin"
        real_fsync = os.fsync

        def racing_fsync(fd: int) -> None:
            if not target.exists():
                target.write_bytes(b"rival")
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", racing_fsync)

        returned = atomic_create_bytes(target, b"ours")

        assert target.read_bytes() == b"rival"
        assert returned == b"rival"

    def test_creation_is_durable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two fsyncs: the temp file, then the parent directory.

        Without both, the created file can come back NUL-filled or the name can
        be lost outright -- the 2026-08-21 failure mode this module exists for.
        """
        calls: list[int] = []
        real_fsync = os.fsync

        def counting_fsync(fd: int) -> None:
            calls.append(fd)
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", counting_fsync)
        atomic_create_bytes(tmp_path / "key.bin", b"x" * 32)

        assert len(calls) == 2, (
            f"expected os.fsync called 2 times, got {len(calls)} -- "
            "atomic_create_bytes must fsync the temp file and its parent dir"
        )

    def test_mode_is_applied_before_the_name_appears(self, tmp_path: Path) -> None:
        target = tmp_path / "key.bin"
        atomic_create_bytes(target, b"k" * 32, mode=0o600)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_leaves_no_temp_file_behind(self, tmp_path: Path) -> None:
        target = tmp_path / "key.bin"
        atomic_create_bytes(target, b"first")
        atomic_create_bytes(target, b"second")
        assert [p.name for p in tmp_path.iterdir()] == ["key.bin"]

    def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "deeper" / "key.bin"
        assert atomic_create_bytes(target, b"k") == b"k"
        assert target.read_bytes() == b"k"


def _no_hardlinks(*_a, **_kw) -> None:
    raise OSError(errno.EPERM, "no hard links on this filesystem")


class TestNoHardlinkFallback:
    """``os.link`` raising a non-``EEXIST`` ``OSError`` -- the exFAT/FAT shape.

    The winner still has to claim the name exclusively; on a filesystem
    without hard links that has to be a sidecar ``<name>.claim`` file rather
    than ``path`` itself, so ``path`` only ever exists once it is complete.
    """

    def test_never_exposes_a_partial_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A concurrent reader must never see ``path`` half-written.

        Forces the no-hard-link path and, from a second "writer"'s vantage
        point, checks the target the instant the winner starts writing its
        payload -- before the old fallback (which created ``path`` itself
        via ``O_CREAT|O_EXCL`` and wrote into it directly) had fsynced
        anything. On that old code this observes an empty ``path``; the
        sidecar-claim fallback writes only ever land on a temp file, so
        ``path`` must not exist at all until the write is complete.
        """
        target = tmp_path / "key.bin"
        payload = b"k" * 4096
        monkeypatch.setattr(os, "link", _no_hardlinks)

        observed: list[bytes] = []
        real_write = os.write

        def spying_write(fd, data):
            if target.exists():
                observed.append(target.read_bytes())
            return real_write(fd, data)

        monkeypatch.setattr(os, "write", spying_write)

        returned = atomic_create_bytes(target, payload)

        assert returned == payload
        assert target.read_bytes() == payload
        assert observed == [], (
            "a concurrent reader observed the target while it was still "
            f"incomplete: {observed!r}"
        )

    def test_two_writers_converge_on_one_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two processes racing the sidecar claim must agree on one file.

        Neither can use ``os.link`` (simulating exFAT/FAT). The second
        writer must poll the first's claim and adopt its bytes instead of
        clobbering them or minting its own file.
        """
        target = tmp_path / "key.bin"
        claim = target.with_name(target.name + ".claim")
        monkeypatch.setattr(os, "link", _no_hardlinks)
        monkeypatch.setattr(atomic_io, "_CLAIM_POLL_INTERVAL", 0.01)

        first_write_started = threading.Event()
        release_first_write = threading.Event()
        real_write = os.write

        def gated_write(fd, data):
            # Only gate the winner's durable write of `path` itself -- once
            # it holds the claim -- not either writer's private per-call tmp
            # file, and only the first time this happens.
            if claim.exists() and not first_write_started.is_set():
                first_write_started.set()
                release_first_write.wait(timeout=2)
            return real_write(fd, data)

        monkeypatch.setattr(os, "write", gated_write)

        results: dict[str, bytes] = {}

        def run_first_writer() -> None:
            results["first"] = atomic_create_bytes(target, b"first-writer")

        first_thread = threading.Thread(target=run_first_writer)
        first_thread.start()
        assert first_write_started.wait(timeout=2), "first writer never started writing"

        def release_soon() -> None:
            time.sleep(0.05)
            release_first_write.set()

        threading.Thread(target=release_soon).start()

        # The second writer must see the claim, poll it, and adopt the
        # winner's bytes rather than minting its own file.
        second = atomic_create_bytes(target, b"second-writer")

        first_thread.join(timeout=2)

        assert results["first"] == b"first-writer"
        assert second == b"first-writer", (
            "the losing writer on a no-hard-link filesystem must be handed "
            "the persisted bytes, not its own"
        )
        assert target.read_bytes() == b"first-writer"

    def test_stale_claim_is_reclaimed_and_the_fallback_retries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A claim left behind by a crashed winner must not wedge every reader."""
        target = tmp_path / "key.bin"
        claim = target.with_name(target.name + ".claim")
        claim.parent.mkdir(parents=True, exist_ok=True)
        claim.touch()  # a winner claimed the name, then died before writing

        monkeypatch.setattr(os, "link", _no_hardlinks)
        monkeypatch.setattr(atomic_io, "_CLAIM_POLL_ATTEMPTS", 2)
        monkeypatch.setattr(atomic_io, "_CLAIM_POLL_INTERVAL", 0.01)

        returned = atomic_create_bytes(target, b"recovered")

        assert returned == b"recovered"
        assert target.read_bytes() == b"recovered"
        assert not claim.exists()

    def test_operators_are_warned_once_about_the_degraded_mount(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        target = tmp_path / "key.bin"
        monkeypatch.setattr(os, "link", _no_hardlinks)

        with caplog.at_level("WARNING", logger="tinyagentos.atomic_io"):
            atomic_create_bytes(target, b"k" * 32)

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1, (
            f"expected exactly one warning about the no-hard-link fallback, got "
            f"{len(warnings)}"
        )
