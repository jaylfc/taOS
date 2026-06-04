"""Pre-switch backup of the data dir.

Copies data/ to data-backups/pre-switch-<ts>/ before a branch switch, so a
switch to an incompatible branch can be recovered. Excludes large/regenerable
trees (models, workspace) and never recurses into data-backups itself.
"""
from __future__ import annotations

import logging
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
    """
    if not data_dir.exists():
        return None
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    dest = data_dir / "data-backups" / f"pre-switch-{ts}"
    dest.mkdir(parents=True, exist_ok=True)
    for entry in data_dir.iterdir():
        if entry.name in _EXCLUDE:
            continue
        try:
            if entry.is_dir():
                shutil.copytree(entry, dest / entry.name, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, dest / entry.name)
        except OSError as exc:
            logger.warning("snapshot_data_dir: failed to copy %s: %s", entry.name, exc)
    logger.info("snapshot_data_dir: backed up data/ to %s", dest)
    return dest
