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


class _DeclaredSizeBudget:
    """The three caps, applied one member at a time.

    Feeding members one by one rather than judging a finished list is what lets
    a streaming caller stop at the first offending header instead of after the
    whole archive has been walked. Limits default to the module constants and
    are resolved here rather than in a signature so a test (or a caller wanting
    a tighter cap) can override them by patching the module attribute.
    """

    def __init__(
        self,
        *,
        kind: str,
        max_members: int | None,
        max_member_bytes: int | None,
        max_uncompressed_bytes: int | None,
    ) -> None:
        self.kind = kind
        self.max_members = MAX_MEMBERS if max_members is None else max_members
        self.max_member_bytes = (
            MAX_MEMBER_BYTES if max_member_bytes is None else max_member_bytes
        )
        self.max_uncompressed_bytes = (
            MAX_UNCOMPRESSED_BYTES
            if max_uncompressed_bytes is None
            else max_uncompressed_bytes
        )
        self.count = 0
        self.total_uncompressed = 0

    def add(self, name: str, size: int) -> None:
        """Account for one member; raise as soon as any cap is crossed."""
        self.count += 1
        if self.count > self.max_members:
            raise ArchiveError(
                f"{self.kind} has too many files ({self.count} > {self.max_members})"
            )
        # A negative size is never legitimate, and ``size > cap`` is False for
        # every negative value: unchecked, it would clear the per-member cap
        # and pull the running total *down*, buying headroom under the
        # cumulative cap for every member after it. CPython only rejects a
        # negative size on members whose payload it has to skip; a directory
        # or symlink header delivers one intact.
        if size < 0:
            raise ArchiveError(f"{self.kind} member size invalid: {name}")
        if size > self.max_member_bytes:
            raise ArchiveError(f"{self.kind} member too large: {name}")
        self.total_uncompressed += size
        if self.total_uncompressed > self.max_uncompressed_bytes:
            raise ArchiveError(
                f"{self.kind} uncompressed size too large "
                f"({self.total_uncompressed} bytes)"
            )


def _check_declared_sizes(
    members: list[tuple[str, int]],
    *,
    kind: str,
    max_members: int | None,
    max_member_bytes: int | None,
    max_uncompressed_bytes: int | None,
) -> None:
    """Apply the three caps to `(name, declared_uncompressed_size)` pairs."""
    budget = _DeclaredSizeBudget(
        kind=kind,
        max_members=max_members,
        max_member_bytes=max_member_bytes,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )
    for name, size in members:
        budget.add(name, size)


def check_zip_limits(
    zf: zipfile.ZipFile,
    *,
    kind: str = "package",
    max_members: int | None = None,
    max_member_bytes: int | None = None,
    max_uncompressed_bytes: int | None = None,
) -> None:
    """Reject a zip bomb from the central directory, before any member is read.

    ``infolist()`` is the zip's own index and costs nothing to walk -- it never
    touches a member's compressed payload -- so the whole list can be judged at
    once. The tar equivalent below cannot.
    """
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

    Unlike a zip, a tar has no index: the headers are interleaved with the
    payloads, so reaching header N+1 means walking past member N's data -- and
    for the ``r:gz`` uploads taOS accepts, walking past data means decompressing
    it. ``getmembers()`` would therefore inflate the whole archive before the
    first cap could fire, handing an attacker exactly the CPU the caps exist to
    deny. Stepping with ``next()`` and judging each header the moment it arrives
    means an offending member's payload is never decompressed.
    """
    budget = _DeclaredSizeBudget(
        kind=kind,
        max_members=max_members,
        max_member_bytes=max_member_bytes,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )
    while (member := tar.next()) is not None:
        budget.add(member.name, member.size)


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
        tar.extractall(dest, filter=tarfile.data_filter)
    except _FILTER_ERRORS as exc:
        raise ArchiveError(f"{kind} contains an unsafe member: {exc}") from exc
