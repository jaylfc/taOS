"""The runtime demo-mode switch for the console lock screen.

Two separate questions, answered in two separate places:

* WHAT demo content exists is still decided by the ``TAOS_LOCK_DEMO_*``
  environment flags (a systemd drop-in on the handset). They carry the content
  itself -- the agent list, the decision text -- so they stay where they are.
* WHETHER that content is SHOWN is this switch, stored in
  ``<data_dir>/demo_mode.json`` and flipped from Settings by an admin, so a
  demo device can be put back into its real state without anyone editing a
  unit file over ssh.

:func:`demo_env` is the only way the lock-screen routes read a demo flag. It
returns the flag's value only while the switch is on, so switching demo mode off
is exactly equivalent to unsetting every flag -- every surface takes the path it
already takes with the flag absent, including its 404s.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path

from tinyagentos.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)

DEMO_MODE_FILE = "demo_mode.json"
DEMO_FLAG_PREFIX = "TAOS_LOCK_DEMO_"


def demo_flags_configured(environ: Mapping[str, str] | None = None) -> bool:
    """True when any ``TAOS_LOCK_DEMO_*`` flag is set to something non-blank."""
    env = os.environ if environ is None else environ
    return any(
        key.startswith(DEMO_FLAG_PREFIX) and str(value).strip()
        for key, value in env.items()
    )


def _path(data_dir: Path | str) -> Path:
    return Path(data_dir) / DEMO_MODE_FILE


def read_demo_mode(data_dir: Path | str | None) -> bool:
    """Whether the switch is on.

    No file yet: ON exactly when a demo flag is configured, so a handset that
    was already running its demo keeps doing so until someone flips it.

    An unreadable or malformed file, or no data dir at all, reads as OFF. This
    screen is pre-sign-in, and the failure worth avoiding is invented content
    shown on a device that believes it is in its real state -- never the
    opposite.
    """
    if data_dir is None:
        return False
    path = _path(data_dir)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return demo_flags_configured()
    except OSError as exc:
        logger.warning("demo mode switch %s unreadable (%s); treating as off", path, exc)
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("demo mode switch %s is not valid JSON; treating as off", path)
        return False
    if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
        logger.warning("demo mode switch %s has no boolean 'enabled'; treating as off", path)
        return False
    return data["enabled"]


def write_demo_mode(data_dir: Path | str, enabled: bool) -> None:
    """Persist the switch. Atomic, so a crash mid-write cannot leave a torn file."""
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({"enabled": bool(enabled)}) + "\n", mode=0o600)


def demo_env(name: str, data_dir: Path | str | None) -> str:
    """The stripped value of demo flag *name*, or "" when it must not apply.

    "" when the flag is unset OR the switch is off -- callers cannot tell the two
    apart, which is the point: switch-off must take the flag-unset path.
    """
    if not name.startswith(DEMO_FLAG_PREFIX):
        raise ValueError(f"{name!r} is not a {DEMO_FLAG_PREFIX}* flag")
    value = os.environ.get(name, "").strip()
    if not value:
        return ""
    return value if read_demo_mode(data_dir) else ""
