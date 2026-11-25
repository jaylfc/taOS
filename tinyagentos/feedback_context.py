"""Install/version context for feedback submissions (#777, Layer 1).

When a user files a bug or feature request through the in-product tracker,
the submission auto-attaches a snapshot of the LOCAL install: the app
version and running commit, the install's identity, and the recorded
update history (the pre-update commit the last update left, from the
rollback record). That is what lets a report be traced to a version and
to whether it was upgrade-related.

Everything here is local: no server, no phone-home, no registration.
Layer 2 of #777 (an opt-in central registration DB) is a separate design
with a privacy decision behind it and is deliberately NOT built here.

Every source is best-effort. A feedback submission must never fail
because an identity or history source is unavailable (fresh install with
no update yet, a data dir that cannot be read, a checkout git cannot
describe), so each field degrades to an empty value and this module never
raises.
"""
from __future__ import annotations

import logging
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import tinyagentos
from tinyagentos.auto_update import install_id
from tinyagentos.rollback import read_rollback_target

logger = logging.getLogger(__name__)


def _current_commit(project_dir: Path) -> str:
    """HEAD of the install's git checkout, or "" if it cannot be read."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        logger.debug("feedback context: git rev-parse failed", exc_info=True)
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def collect_feedback_context(
    data_dir: Optional[Path],
    project_dir: Optional[Path],
) -> dict[str, Any]:
    """Snapshot the local install context for one feedback submission.

    ``install_id`` is the same per-install random id the version ping and
    the controller service identity anchor to (``auto_update.install_id``):
    one install, one identity, whichever surface names it. The update
    history is the rollback record the updater writes before applying an
    update (``rollback.record_pre_update``): the branch + commit the last
    update left, and when. No record means the install has never been
    updated in place, which is itself the answer to "was it
    upgrade-related".
    """
    context: dict[str, Any] = {
        "version": getattr(tinyagentos, "__version__", "unknown"),
        "commit": "",
        "install_id": "",
        "platform": f"{sys.platform}-{platform.machine()}",
        "update_history": {},
    }
    if data_dir is not None:
        try:
            context["install_id"] = install_id(Path(data_dir))
        except Exception:
            logger.debug("feedback context: install_id failed", exc_info=True)
    if project_dir is not None:
        project_dir = Path(project_dir)
        context["commit"] = _current_commit(project_dir)
        try:
            target = read_rollback_target(project_dir)
        except Exception:
            logger.debug("feedback context: rollback record failed", exc_info=True)
            target = None
        if target:
            context["update_history"] = {
                "previous_commit": target["sha"],
                "previous_branch": target["branch"],
                "previous_ts": target["ts"],
            }
    return context
