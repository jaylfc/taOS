"""Crash-safe file writes.

A plain ``Path.write_text`` truncates the target and streams the new bytes
into the page cache.  If the machine loses power before those pages reach
the disk, the file's *metadata* (size, mtime) can be durable while its
*data* is not — the file comes back the right length and full of NUL bytes.

That is not theoretical.  On 2026-08-21 an unclean power-off left the taOS
account store (``data/.auth_user.json``) as 901 NUL bytes with an intact
size and mtime, which the auth layer read as "no users exist" and answered
with the first-run onboarding screen.  The device was mounted
``data=writeback``, which widens the window, but ``data=ordered`` only
orders data *that has been submitted* — it does not make a bare write
durable either.

``atomic_write_text`` closes the window the only way that works: write a
sibling temp file, ``fsync`` it so the bytes are on the platter, then
``os.replace`` (atomic within a directory) and ``fsync`` the directory so
the rename itself is durable.  A crash at any point leaves either the
complete old file or the complete new one — never a half-written or
NUL-filled one.
"""
from __future__ import annotations

import errno
import os
import secrets
from pathlib import Path

__all__ = ["atomic_write_text", "atomic_write_bytes", "atomic_create_bytes"]


def _fsync_dir(directory: Path) -> None:
    """``fsync`` *directory* so a rename into it survives a power cut.

    Without this the rename can be lost on a crash even though the file
    contents were synced -- so a failure here means we did not deliver the
    durability the caller asked for, and saying nothing would be a lie.
    The exception is a filesystem that cannot fsync a directory at all
    (some network and union filesystems); that is a property of the mount,
    not a failed write.
    """
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EBADF):
            raise
    finally:
        os.close(dir_fd)


def atomic_write_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Durably replace *path* with *data*.

    *mode*, when given, is the permission bitmask the file ends up with; it
    is applied to the temp file before the rename so the content is never
    briefly world-readable.  When omitted the existing file's mode is kept
    (or the process umask applies for a new file).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if mode is None:
        try:
            mode = os.stat(path).st_mode & 0o777
        except FileNotFoundError:
            mode = None

    # Same directory as the target: os.replace is only atomic within a
    # filesystem, and a temp dir may well be a different one (/tmp is
    # commonly tmpfs). The random suffix keeps two concurrent writers of the
    # same target from sharing one temp inode and interleaving their bytes.
    tmp = path.with_name(f".{path.name}.tmp{secrets.token_hex(8)}")
    try:
        # O_EXCL: a name this random cannot legitimately exist already, so a
        # collision means something else is writing and we must not join it.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(tmp, flags, mode if mode is not None else 0o666)
        try:
            # os.write is allowed to write fewer bytes than it was given.
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        if mode is not None:
            # os.open honours the umask; chmod does not.
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    _fsync_dir(path.parent)


def atomic_create_bytes(path: Path, data: bytes, *, mode: int | None = None) -> bytes:
    """Durably create *path* holding *data*, but only if it is not there yet.

    Returns the bytes that are actually persisted at *path*: *data* when this
    call created the file, or the existing content when the file was already
    there -- including when another process created it in the window between
    this call's own existence check and its write.

    ``atomic_write_bytes`` is a durable *replace*, which is right for a state
    file any writer may legitimately overwrite and wrong for the one-time
    creation of persistent key material.  Two processes sharing a data dir can
    both observe an absent key file and both generate; each write is atomic, so
    the file is never corrupt, but the last one wins and the *loser* carries on
    using key material that is not on disk.  Everything it encrypted (or
    signed) is unreadable after a restart.

    The name is therefore claimed with ``os.link``, which fails ``EEXIST``
    rather than replacing: the race is decided by the kernel, exactly one
    writer's bytes are ever persisted, and every other writer is handed those
    same bytes back to use instead of its own.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        return path.read_bytes()
    except FileNotFoundError:
        pass

    tmp = path.with_name(f".{path.name}.tmp{secrets.token_hex(8)}")
    linked = False
    try:
        # O_EXCL: a name this random cannot legitimately exist already, so a
        # collision means something else is writing and we must not join it.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                     mode if mode is not None else 0o666)
        try:
            # os.write is allowed to write fewer bytes than it was given.
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        if mode is not None:
            # os.open honours the umask; chmod does not.
            os.chmod(tmp, mode)
        try:
            os.link(tmp, path)
            linked = True
        except FileExistsError:
            pass
        except OSError:
            # A filesystem without hard links (exFAT/FAT on a removable data
            # dir).  Claim the target name exclusively instead: the atomic
            # create-or-fail guarantee this function exists for is preserved,
            # at the cost of a crash window in which the new file can be
            # partial -- which a replace would not have closed either.
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             mode if mode is not None else 0o666)
            except FileExistsError:
                pass
            else:
                try:
                    view = memoryview(data)
                    while view:
                        view = view[os.write(fd, view):]
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if mode is not None:
                    os.chmod(path, mode)
                linked = True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    if not linked:
        # Someone else got there first; their bytes are the ones that survive a
        # restart, so they are the ones the caller must use.
        return path.read_bytes()

    _fsync_dir(path.parent)
    return data


def atomic_write_text(
    path: Path, text: str, *, mode: int | None = None, encoding: str = "utf-8"
) -> None:
    """``atomic_write_bytes`` for text.  See that function for the rationale."""
    atomic_write_bytes(path, text.encode(encoding), mode=mode)
