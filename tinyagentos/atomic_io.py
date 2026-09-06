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
import logging
import os
import secrets
import time
from pathlib import Path

__all__ = ["atomic_write_text", "atomic_write_bytes", "atomic_create_bytes"]

logger = logging.getLogger(__name__)

# The no-hard-link fallback's sidecar claim: how long a loser polls for the
# winner's claim to disappear before treating it as abandoned, and how many
# times. 20 * 100ms = 2s -- generous for a few KB of key material, short
# enough not to wedge a boot.
_CLAIM_POLL_ATTEMPTS = 20
_CLAIM_POLL_INTERVAL = 0.1


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


def _create_via_claim(path: Path, data: bytes, mode: int | None) -> bytes:
    """``atomic_create_bytes`` on a filesystem with no hard links.

    ``os.link`` is what makes the normal path race-safe: it is the kernel
    that decides who wins, and a loser can only ever see the winner's
    complete bytes because the link cannot exist before the winner's
    ``fsync`` returned. exFAT/FAT (a removable data dir on a Pi) have no hard
    links, so exclusivity has to come from a *sidecar* claim file instead:

    - Creating ``<path>.claim`` with ``O_EXCL`` is itself race-safe the same
      way ``os.link`` is. The winner then writes *path* through
      ``atomic_write_bytes`` -- tmp file, fsync, ``os.replace``, fsync the
      directory -- so ``path`` itself never exists in a partial state; it
      transitions atomically from absent to complete. Only once that has
      landed is the claim removed.
    - A loser polls for the claim to disappear, then reads *path*. Because
      the claim outlives the write, "the claim is gone" means "path is
      complete", never "path is present but partial".
    - If the claim outlives the poll window without *path* ever becoming
      non-empty, the winner crashed before writing anything durable: the
      claim is reclaimed and the whole fallback is retried once. A second
      failure raises rather than ever handing back non-durable bytes.
    """
    claim = path.with_name(path.name + ".claim")
    for _ in range(2):  # the initial attempt, plus one retry after a stale claim
        try:
            fd = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
            logger.warning(
                "%s: filesystem has no hard links; falling back to a sidecar "
                "claim file for exclusive creation -- an operator should "
                "check whether this mount is expected to lack them",
                path,
            )
            try:
                # The claim only decides who is allowed to *write*; it does
                # not decide who wins. `atomic_write_bytes` replaces `path`
                # rather than creating it exclusively, so if some other
                # route landed `path` durably in the window between this
                # call's initial existence check and the claim being
                # acquired here, writing now would clobber it. Recheck and
                # hand back whatever is already there instead.
                try:
                    existing = path.read_bytes()
                except FileNotFoundError:
                    existing = b""
                if existing:
                    return existing
                atomic_write_bytes(path, data, mode=mode)
            finally:
                try:
                    os.unlink(claim)
                except OSError:
                    pass
            return data

        for _ in range(_CLAIM_POLL_ATTEMPTS):
            if not claim.exists():
                break
            time.sleep(_CLAIM_POLL_INTERVAL)
        else:
            # Poll bound exceeded and the claim is still there.
            if not path.exists() or not path.read_bytes():
                # The winner crashed before ever producing durable bytes.
                # Reclaim the name and retry the whole fallback once.
                try:
                    os.unlink(claim)
                except OSError:
                    pass
                continue
            # else: path is already complete even though the winner has not
            # unlinked its claim yet -- fall through to read it below.

        return path.read_bytes()

    raise OSError(
        f"could not persist {path}: filesystem without hard links and a stale claim"
    )


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
            # dir): os.link cannot give us the race decision, so fall back to
            # a sidecar claim file that gives the same "exactly one writer"
            # guarantee without ever exposing a partial or absent target to a
            # reader (see ``_create_via_claim``).
            return _create_via_claim(path, data, mode)
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
