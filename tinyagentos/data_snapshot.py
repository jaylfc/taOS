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
    logger.info("snapshot_data_dir: backed up data/ to %s", dest)
    return dest
