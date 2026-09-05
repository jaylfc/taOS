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

import os
import stat
from pathlib import Path

import pytest

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
