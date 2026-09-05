"""One guarded entry point for every archive taOS extracts from an upload.

An uploaded ``.taosapp``, ``.taostheme`` or backup tarball is attacker
controlled: a 40 KB zip can declare gigabytes of uncompressed content, and
``zipfile.read()`` inflates that declared size straight into memory. The guards
below therefore run over the archive's own index -- ``infolist()`` /
``getmembers()`` -- BEFORE a single member is read, because once a member has
been read the memory is already gone.

Every extraction site routes through here (``userspace/package.py``,
``themes/package.py``, ``routes/settings.py::restore_backup``) so a new one
cannot ship without the limits. Callers translate :class:`ArchiveError` into
whatever their own surface raises or returns.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

# Bomb defenses: cap the declared uncompressed total, the per-member size and
# the member count. Sized so a 4 GB Pi survives a hostile upload while the
# largest legitimate package (an app bundle) still installs.
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 10_000

# tarfile.FilterError only exists on Pythons carrying PEP 706. An empty tuple in
# an `except` clause never matches, so the handler stays valid either way.
_FILTER_ERRORS: tuple[type[BaseException], ...] = (
    (tarfile.FilterError,) if hasattr(tarfile, "FilterError") else ()
)


class ArchiveError(Exception):
    """Raised when an archive is unsafe to extract (bomb limits, unsafe member)."""


def _check_declared_sizes(
    members: list[tuple[str, int]],
    *,
    kind: str,
    max_members: int | None,
    max_member_bytes: int | None,
    max_uncompressed_bytes: int | None,
) -> None:
    """Apply the three caps to `(name, declared_uncompressed_size)` pairs.

    Limits default to the module constants and are resolved here rather than in
    the signature so a test (or a caller wanting a tighter cap) can override
    them by patching the module attribute.
    """
    if max_members is None:
        max_members = MAX_MEMBERS
    if max_member_bytes is None:
        max_member_bytes = MAX_MEMBER_BYTES
    if max_uncompressed_bytes is None:
        max_uncompressed_bytes = MAX_UNCOMPRESSED_BYTES

    if len(members) > max_members:
        raise ArchiveError(f"{kind} has too many files ({len(members)} > {max_members})")
    total_uncompressed = 0
    for name, size in members:
        if size > max_member_bytes:
            raise ArchiveError(f"{kind} member too large: {name}")
        total_uncompressed += size
    if total_uncompressed > max_uncompressed_bytes:
        raise ArchiveError(
            f"{kind} uncompressed size too large ({total_uncompressed} bytes)"
        )


def check_zip_limits(
    zf: zipfile.ZipFile,
    *,
    kind: str = "package",
    max_members: int | None = None,
    max_member_bytes: int | None = None,
    max_uncompressed_bytes: int | None = None,
) -> None:
    """Reject a zip bomb from the central directory, before any member is read."""
    _check_declared_sizes(
        [(zi.filename, zi.file_size) for zi in zf.infolist()],
        kind=kind,
        max_members=max_members,
        max_member_bytes=max_member_bytes,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )


def check_tar_limits(
    tar: tarfile.TarFile,
    *,
    kind: str = "archive",
    max_members: int | None = None,
    max_member_bytes: int | None = None,
    max_uncompressed_bytes: int | None = None,
) -> None:
    """Reject a tar bomb from the member headers, before anything is written.

    A tar stores each member's size in its header and never yields more bytes
    than that, so the declared total bounds what an extraction can write.
    """
    _check_declared_sizes(
        [(m.name, m.size) for m in tar.getmembers()],
        kind=kind,
        max_members=max_members,
        max_member_bytes=max_member_bytes,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )


def extract_tar_safely(
    tar: tarfile.TarFile,
    dest: Path,
    *,
    kind: str = "archive",
    max_members: int | None = None,
    max_member_bytes: int | None = None,
    max_uncompressed_bytes: int | None = None,
) -> None:
    """Size-check a tarball, then extract it under PEP 706's ``data`` filter.

    The filter blocks absolute paths, ``..`` traversal, links escaping `dest`
    and special files. A Python without it is refused outright rather than
    silently downgraded to an unsafe ``extractall`` -- same stance as
    ``desktop_rebuild.py``.
    """
    if not hasattr(tarfile, "data_filter"):
        raise ArchiveError(
            f"this Python lacks the path-safe tar filter (PEP 706); "
            f"refusing to extract {kind}"
        )
    check_tar_limits(
        tar,
        kind=kind,
        max_members=max_members,
        max_member_bytes=max_member_bytes,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )
    try:
        tar.extractall(dest, filter="data")
    except _FILTER_ERRORS as exc:
        raise ArchiveError(f"{kind} contains an unsafe member: {exc}") from exc
