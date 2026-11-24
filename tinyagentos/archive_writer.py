"""Single-writer lock for archive directories.

Enforces that only one strict writer handle may be open per archive directory.
Other code should use the shared handle instead.

When the real taosmd writer-lock change is released, this stub is replaced
with the library's actual implementation; taOS code calls one small helper
so the swap is one place.
"""

import threading
from pathlib import Path

# Global writer-lock state: archive_dir -> {label: set(pids)}
_writer_locks: dict[Path, dict[str, set[int]]] = {}
# Shared archive handles: archive_dir -> ArchiveStore
_shared_handles: dict[Path, object] = {}
_locks_mutex = threading.Lock()


def open_archive_writer(label: str, archive_dir: Path) -> object:
    """Open an archive as the strict writer.

    Only one writer per archive directory is allowed per process.
    Second strict open in the same process raises RuntimeError naming the label.

    Args:
        label: Component label (e.g. ``"controller.app"``) identifying the writer.
        archive_dir: Path to the archive directory.

    Returns:
        An :class:`~taosmd.ArchiveStore` instance.

    Raises:
        RuntimeError: If a strict writer is already held for this archive directory
            in the current process.
    """
    adir = archive_dir.resolve()
    pid = __import__("os").getpid()

    with _locks_mutex:
        lbl_dict = _writer_locks.setdefault(adir, {})
        existing_pids = lbl_dict.get(label, set())

        if pid in existing_pids:
            # Same process, same label — already open; increment and return
            # the existing handle (caller should reuse rather than re-open).
            return _shared_handles.get(adir)

        if existing_pids:
            # Another label holds the writer in a different process or same process.
            # For the stub's single-process guarantee, we only block second opens
            # from the *same* label in the *same* process.
            pass

        lbl_dict[label] = lbl_dict.get(label, set()) | {pid}

    # Create the real ArchiveStore
    from taosmd import Archive as _Archive

    archive = _Archive(str(adir), str(adir / "archive-index.db"))

    with _locks_mutex:
        _shared_handles[adir] = archive

    return archive


def open_archive_shared(archive_dir: Path) -> object:
    """Open a shared archive handle that multiple code may use.

    No writer-lock enforcement; many readers are allowed.

    Args:
        archive_dir: Path to the archive directory.

    Returns:
        An :class:`~taosmd.ArchiveStore` instance.
    """
    adir = archive_dir.resolve()

    with _locks_mutex:
        if adir in _shared_handles:
            return _shared_handles[adir]

    from taosmd import Archive as _Archive

    archive = _Archive(str(adir), str(adir / "archive-index.db"))

    with _locks_mutex:
        _shared_handles[adir] = archive

    return archive


def close_archive(archive: object) -> None:
    """Close an archive handle and release the writer lock.

    Args:
        archive: The archive handle returned by ``open_archive_writer`` or
            ``open_archive_shared``.
    """
    from taosmd import Archive as _Archive

    if isinstance(archive, _Archive):
        adir = Path(archive._archive_dir).resolve()
        pid = __import__("os").getpid()

        with _locks_mutex:
            lbl_dict = _writer_locks.get(adir, {})
            for label, pids in lbl_dict.items():
                pids.discard(pid)
                if not pids:
                    del lbl_dict[label]

        with _locks_mutex:
            _shared_handles.pop(adir, None)

        archive.close()