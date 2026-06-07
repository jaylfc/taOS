"""Pre-switch backup of the data dir.

Copies data/ to data-backups/pre-switch-<ts>/ before a branch switch, so a
switch to an incompatible branch can be recovered. Excludes large/regenerable
trees (models, workspace) and never recurses into data-backups itself.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_EXCLUDE = {"models", "workspace", "data-backups"}


def _harden_perms(root: Path) -> None:
    """Recursively restrict *root* to owner-only: dirs 0o700, files 0o600.

    ``copytree``/``copy2`` preserve the source modes, so without this a secret
    copied into the backup could remain group/world-readable even though the
    top-level backup dir is 0o700. Best-effort: per-entry failures are ignored
    (non-POSIX / no perms). Symlinks are skipped — chmod would alter the link
    target's permissions, not the link.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                continue
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass
        for name in filenames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                continue
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass


def snapshot_data_dir(data_dir: Path) -> Optional[Path]:
    """Copy data_dir into data-backups/pre-switch-<ts>/, skipping _EXCLUDE.

    Returns the backup path, or None if data_dir doesn't exist. Best-effort:
    per-entry copy failures are logged, not raised.

    Security: the backup holds sensitive state (auth tokens, keys, secrets DB),
    so it is created owner-only (0o700). Symlinks are preserved as links and
    never dereferenced, so a symlink under data/ cannot pull arbitrary files
    outside data/ into the backup.
    """
    if not data_dir.exists():
        return None
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    backups_root = data_dir / "data-backups"
    backups_root.mkdir(parents=True, exist_ok=True)
    dest = backups_root / f"pre-switch-{ts}"
    dest.mkdir(parents=True, exist_ok=True)
    for d in (backups_root, dest):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass  # non-POSIX / no perms — best effort
    for entry in data_dir.iterdir():
        if entry.name in _EXCLUDE:
            continue
        try:
            if entry.is_symlink():
                # Preserve as a link; never follow it (no arbitrary-file read).
                os.symlink(os.readlink(entry), dest / entry.name)
            elif entry.is_dir():
                shutil.copytree(entry, dest / entry.name, dirs_exist_ok=True, symlinks=True)
            else:
                shutil.copy2(entry, dest / entry.name, follow_symlinks=False)
        except OSError as exc:
            logger.warning("snapshot_data_dir: failed to copy %s: %s", entry.name, exc)
    # Tighten copied contents — copytree/copy2 preserved their source modes.
    _harden_perms(dest)
    logger.info("snapshot_data_dir: backed up data/ to %s", dest)
    return dest
