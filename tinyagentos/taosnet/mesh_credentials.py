"""Host-local persistence for the per-host taOSgo service credentials.

When this host joins an account mesh (the consent-join flow proxied by
``routes/account_proxy.py``), taos.my returns a ready payload carrying two
single-scope, host-bound bearer tokens:

- ``controller_token`` -- scope ``taosnet:passkey`` (fetch the account taOSnet
  passkey; see ``passkey_client.py``)
- ``sites_token``      -- scope ``sites:publish`` (publish a Web Studio site to
  ``<label>.<handle>.taos.my``; taos.my #167)

This module is the *writer* for the ``get_controller_token()`` seam
``passkey_client.py`` documented: it persists those tokens to a single 0600 file
under the data dir so headless callers (the download manager, the Web Studio
publish caller) can present them without a browser session. The single-use
Headscale preauth key is deliberately NOT stored here -- it is consumed once at
mesh-join time, not a durable credential.

Resolution mirrors ``create_app``: the data dir is ``TAOS_DATA_DIR`` if set, else
``<project>/data``. Env overrides (``TAOS_CONTROLLER_TOKEN`` / ``TAOS_SITES_TOKEN``)
still win, so local dev and existing tests keep working without a join.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_FILENAME = "mesh_credentials.json"

# The fields we persist from the join ready payload. Anything else (notably the
# single-use headscale_preauth_key) is intentionally dropped.
_FIELDS = (
    "account_id",
    "host_id",
    "tailnet_name",
    "controller_token",
    "sites_token",
    "joined_at",
)


def _data_dir() -> Path:
    env = os.environ.get("TAOS_DATA_DIR")
    if env:
        return Path(env)
    # tinyagentos/taosnet/mesh_credentials.py -> project root is two parents up.
    return Path(__file__).resolve().parent.parent.parent / "data"


def _path() -> Path:
    return _data_dir() / _FILENAME


def save_mesh_credentials(payload: dict) -> None:
    """Persist the join ready-payload service credentials, host-bound, mode 0600.

    Keeps only the ``_FIELDS`` allowlist (drops the single-use preauth key and
    anything else). Requires at least ``controller_token`` + ``host_id``; raises
    ``ValueError`` otherwise. Atomic (write temp + ``os.replace``) so a crash
    mid-write never leaves a partial file. Idempotent: re-saving the same host's
    payload just rewrites identical data.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    creds = {k: payload.get(k) for k in _FIELDS}
    if not creds.get("controller_token") or not creds.get("host_id"):
        raise ValueError("payload missing controller_token/host_id")

    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:  # pragma: no cover - best effort on odd filesystems
        pass

    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    data = json.dumps(creds).encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:  # pragma: no cover
        pass


def _load() -> Optional[dict]:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return None
    # A corrupt or externally-edited file could parse to a non-object (list,
    # string, number); the getters do ``(_load() or {}).get(...)`` so a non-dict
    # would raise AttributeError and break the headless passkey fetch. Treat any
    # non-object as absent (fail closed to "not joined").
    return data if isinstance(data, dict) else None


def get_controller_token() -> Optional[str]:
    """The persisted ``taosnet:passkey`` token, or ``TAOS_CONTROLLER_TOKEN`` if
    that dev override is set, or None when this host has not joined a mesh."""
    return os.getenv("TAOS_CONTROLLER_TOKEN") or (_load() or {}).get("controller_token") or None


def get_sites_token() -> Optional[str]:
    """The persisted ``sites:publish`` token (Web Studio publish caller), or the
    ``TAOS_SITES_TOKEN`` dev override, or None before join."""
    return os.getenv("TAOS_SITES_TOKEN") or (_load() or {}).get("sites_token") or None


def get_host_id() -> Optional[str]:
    """The taos.my host row id this instance joined as, or None."""
    return (_load() or {}).get("host_id") or None


def has_mesh_credentials() -> bool:
    """True once this host has joined an account mesh (a controller token exists)."""
    return get_controller_token() is not None


def clear() -> None:
    """Forget the persisted mesh credentials (on leave/unjoin). No-op if absent."""
    try:
        _path().unlink()
    except OSError:
        pass
