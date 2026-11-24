"""One writer per archive directory inside the controller process.

The memory library's ``Archive`` object appends to the day's JSONL file through
its own file handle, keeps its own per-file line counter and writes the SQLite
index.  Two such objects on one directory interleave appends and hand out
colliding line numbers, so the controller must hold exactly one.

The rules, all enforced here and nowhere else:

* ``open_archive_writer`` is the only way the controller builds an ``Archive``.
  The registry is keyed by the resolved real path of the directory (symlinks
  and relative spellings resolved), never by a label.  A second open of the
  same directory raises ``ArchiveWriterHeldError`` (a ``RuntimeError``) naming
  the holder, whatever label the second caller passes.  The ownership check
  and the publish of the new handle happen inside one locked step.
* ``get_archive_writer`` is the accessor every other in-process user goes
  through to reach the same object.  It never opens anything.
* ``release_archive_writer`` closes the writer and frees the directory, so a
  later start in the same process can take it again.
* Readers that only read archive files by path (``SessionCatalog``,
  ``CatalogPipeline``) never construct an ``Archive``; they are read-only
  users and need no handle.

This works with the memory library pinned today (0.4.0), which has no lock of
its own.  The lock here is in-process only; when the library ships its own
per-directory lock this module is the single place to adopt it.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from taosmd import Archive

_lock = threading.Lock()
# real path of the archive directory -> (holder label, writer)
_writers: dict[str, tuple[str, Archive]] = {}


class ArchiveWriterHeldError(RuntimeError):
    """The archive directory already has its one writer in this process."""


def _key(archive_dir: str | os.PathLike) -> str:
    return os.path.realpath(os.fspath(archive_dir))


def open_archive_writer(
    label: str,
    archive_dir: str | os.PathLike,
    index_path: str | os.PathLike | None = None,
) -> Archive:
    """Build the one writer for ``archive_dir`` and register it.

    The returned object is not yet initialised: the caller awaits
    ``init()`` on it (the lifespan does), and must call
    ``release_archive_writer`` when done.  ``index_path`` defaults to the
    library's own default inside ``archive_dir``; the controller passes
    ``<data>/archive-index.db``.

    Raises ``ArchiveWriterHeldError`` naming the current holder if the
    directory already has a writer in this process.
    """
    key = _key(archive_dir)
    with _lock:
        held = _writers.get(key)
        if held is not None:
            raise ArchiveWriterHeldError(
                f"archive directory {key} is already open for writing by "
                f"{held[0]!r}; {label!r} must use get_archive_writer() to "
                f"reach that writer instead of opening a second one"
            )
        if index_path is None:
            index_path = Path(key) / "archive-index.db"
        writer = Archive(archive_dir=key, index_path=index_path)
        _writers[key] = (label, writer)
    return writer


def get_archive_writer(archive_dir: str | os.PathLike) -> Archive | None:
    """The one writer for ``archive_dir``, or ``None`` when none is open."""
    with _lock:
        held = _writers.get(_key(archive_dir))
    return held[1] if held else None


def held_archive_writers() -> dict[str, str]:
    """Real directory path -> holder label, for every writer in this process."""
    with _lock:
        return {key: label for key, (label, _w) in _writers.items()}


async def release_archive_writer(writer: Archive) -> bool:
    """Close ``writer`` and free its directory.  Idempotent.

    Only the registered object releases: a caller holding a stale or foreign
    handle cannot free somebody else's directory.  Returns True when this
    call released it.
    """
    with _lock:
        for key, (_label, held) in list(_writers.items()):
            if held is writer:
                del _writers[key]
                break
        else:
            return False
    await writer.close()
    return True
