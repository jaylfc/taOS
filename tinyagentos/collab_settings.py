"""The collaboration kill switch: one instance-wide flag plus a paused list.

Stored in ``<data_dir>/collaboration.json`` as
``{"enabled": bool, "paused_contacts": ["hub:<hex>", ...]}`` and flipped from
Settings by an admin.  Read on every request (no cache) so a flip takes effect
on the very next peer call.

* ``enabled`` false: every peer route answers 503 before any bearer lookup or
  signature work, and outbound collaboration (collab invite mint, peer
  delivery) refuses.
* a contact in ``paused_contacts``: its authenticated peer calls answer 403
  and nothing is sent to it.  Pausing deletes nothing; un-pausing restores
  service.

No file yet reads as the default (enabled, nothing paused).  A file that exists
but cannot be read or parsed reads as DISABLED: the switch exists to stop
traffic, so a damaged switch must not silently turn traffic back on.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from tinyagentos.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)

COLLAB_SETTINGS_FILE = "collaboration.json"

_CONTACT_ID_RE = re.compile(r"^hub:[0-9a-f]+$")


@dataclass(frozen=True)
class CollabSettings:
    enabled: bool = True
    paused_contacts: list[str] = field(default_factory=list)

    def is_paused(self, contact_id: str) -> bool:
        return contact_id.strip().lower() in self.paused_contacts

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "paused_contacts": list(self.paused_contacts)}


def _path(data_dir: Path | str) -> Path:
    return Path(data_dir) / COLLAB_SETTINGS_FILE


def normalise_contact_id(value: str) -> str:
    """Strip and lowercase *value*; ValueError unless it looks like ``hub:<hex>``."""
    cid = value.strip().lower()
    if not _CONTACT_ID_RE.match(cid):
        raise ValueError(f"{value!r} is not a hub:<hex> contact id")
    return cid


def _disabled() -> CollabSettings:
    return CollabSettings(enabled=False, paused_contacts=[])


def read_collab_settings(data_dir: Path | str | None) -> CollabSettings:
    """The current switch state.  Missing file: enabled.  Damaged file: disabled."""
    if data_dir is None:
        return CollabSettings()
    path = _path(data_dir)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return CollabSettings()
    except OSError as exc:
        logger.warning("collaboration switch %s unreadable (%s); treating as disabled", path, exc)
        return _disabled()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("collaboration switch %s is not valid JSON; treating as disabled", path)
        return _disabled()
    if not isinstance(data, dict) or not isinstance(data.get("enabled", True), bool):
        logger.warning("collaboration switch %s has no boolean 'enabled'; treating as disabled", path)
        return _disabled()
    paused_raw = data.get("paused_contacts", [])
    if not isinstance(paused_raw, list) or not all(isinstance(p, str) for p in paused_raw):
        logger.warning("collaboration switch %s has a malformed paused list; treating as disabled", path)
        return _disabled()
    paused: list[str] = []
    for p in paused_raw:
        try:
            cid = normalise_contact_id(p)
        except ValueError:
            logger.warning("collaboration switch %s: ignoring bad paused id %r", path, p)
            continue
        if cid not in paused:
            paused.append(cid)
    return CollabSettings(enabled=data.get("enabled", True), paused_contacts=paused)


def write_collab_settings(data_dir: Path | str, settings: CollabSettings) -> None:
    """Persist the switch.  Atomic, so a crash mid-write cannot leave a torn file."""
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(settings.to_dict()) + "\n", mode=0o600)


def outbound_refusal(data_dir: Path | str | None, contact_id: str | None = None) -> str | None:
    """Why outbound collaboration to *contact_id* must not happen, or None.

    The one check every outbound path (collab invite mint, peer delivery)
    calls before sending anything.
    """
    settings = read_collab_settings(data_dir)
    if not settings.enabled:
        return "collaboration disabled"
    if contact_id is not None and settings.is_paused(contact_id):
        return "contact paused"
    return None
