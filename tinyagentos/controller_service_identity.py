# tinyagentos/controller_service_identity.py
"""First-boot service identity for the taOS controller.

WHY THIS EXISTS.  The memory service is where history records live, and a
record has to name a real writer.  The controller had no identity of its own,
so it could not write records as itself -- its writes were indistinguishable
from the owner's and could not be audited or revoked separately.  This module
mints a service principal for the controller at first boot, following the same
pattern as ``ensure_native_agent_identity`` in native_agent_identity.py.

FOUR PROPERTIES, each of which is a requirement rather than a nicety:

1. PER-INSTALL.  The identity is anchored to ``<data_dir>/.install_id`` -- the
   same id the version ping uses.  Two installs are two identities, so "this
   machine's controller" is a thing that can be named, listed and revoked.

2. OWNER-LINKED.  ``user_id`` is the install's primary user.  The registry
   treats user_id as immutable, so the mint has to happen at a moment when the
   owner is already known -- which is why this runs at startup, never at an
   ownerless boot.  An identity minted with no owner would be stuck that way
   for its whole life.  Not an error: an install with no owner yet is simply
   deferred, and the next boot retries.

3. NOT SHARED.  The token is written to ``<data_dir>/.secrets/taos_controller_token``
   (0600) on the install that minted it.  Nothing ships a credential between
   installs, and it is never read from or written to an environment variable.

4. CONSERVATIVE.  One scope, ``records:write``: enough for the controller to
   name itself as the writer of history records, and nothing else.  Anything
   more goes through the normal scope-request flow.

WHAT THIS DOES NOT DO.  It does not grant the controller any agent or human
privilege: a service-principal token is not accepted anywhere an agent or
human token is required (see ``_verify_agent_scope`` in agent_token_auth.py).
It is identity for record authorship, not a backdoor into agent-required
endpoints.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.auto_update import install_id as read_install_id

logger = logging.getLogger(__name__)

# The origin marks the row as minted by the OS itself for the controller.
# register() lands any origin other than "external-selfjoin" as active, which
# is what we want: there is no consent round-trip to run against the owner.
CONTROLLER_ORIGIN = "taos-controller"

# The one scope the controller needs: name itself as the writer of history
# records.  Deliberately nothing else.
CONTROLLER_SCOPES = ("records:write",)

# Bus/registry handle for the controller.  Each install has its own registry
# database, so a stable handle is fine within an install; the canonical_id
# (slug + timestamp) is what is actually unique across installs.
CONTROLLER_HANDLE = "service:controller"

# canonical_id slug prefix.  `taos-` is RESERVED: register() refuses it unless
# allow_reserved=True, which the HTTP layer never populates.  This module is
# in-process, so it can pass it -- a service named by the OS is under the OS's
# own prefix.  `controller` is not itself reserved, so allow_reserved is not
# needed; the prefix is kept for clarity and future-proofing.
_SLUG_PREFIX = "taos-controller"

# How much of the install id goes into the canonical_id.  The full id is on
# the row in install_id.
_SLUG_INSTALL_CHARS = 8

# The token file lives in the taOS secrets area under the data directory and
# is never placed in an environment variable.
TOKEN_DIR = ".secrets"
TOKEN_FILENAME = "taos_controller_token"


def controller_token_path(data_dir: Path | str) -> Path:
    """Path of this install's controller service token file."""
    return Path(data_dir) / TOKEN_DIR / TOKEN_FILENAME


def _has_token(data_dir: Path | str) -> bool:
    """True only if a NON-EMPTY token file exists."""
    path = controller_token_path(data_dir)
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _write_token(data_dir: Path | str, token: str) -> tuple[Optional[Path], bool]:
    """Write *token* 0600, creating it only if absent.

    Returns ``(path, created_by_this_call)``.  An existing non-empty file is
    left alone -- the controller may already be running with that token, and
    rewriting it under a live process strands it on a credential nobody
    recognises.  An empty file (leftover of a crash or failed write) is
    replaced so one transient error cannot pin the controller to an empty
    credential forever.
    """
    path = controller_token_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if _has_token(data_dir):
            return path, False
        try:
            os.unlink(path)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Lost a genuine race in the gap; the winner's token stands.
            return path, False
        except OSError as exc:
            logger.error("could not replace the empty controller token file at %s: %s", path, exc)
            return None, False
    except OSError as exc:
        logger.error("controller token could not be written to %s: %s", path, exc)
        return None, False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token)
    except OSError as exc:
        logger.error("controller token write failed at %s: %s", path, exc)
        try:
            os.unlink(path)
        except OSError:
            logger.error("could not remove the empty controller token file at %s", path)
        return None, False
    return path, True


async def ensure_controller_service_identity(
    *,
    registry: Any,
    grants: Any,
    data_dir: Path | str,
    signing_key_pem: bytes,
    user_id: str,
) -> Optional[dict]:
    """Mint (or re-assert) this install's controller service identity. Idempotent.

    Returns the registry record, or ``None`` when the identity could not be
    minted -- in which case the caller carries on: an install without a
    controller service identity is degraded, not broken, and refusing to boot
    over it would turn a missing convenience into an outage.

    Safe to call on every startup.  Lookup is by install_id.  The service
    principal holds exactly one scope, ``records:write``, and nothing else.
    """
    install = read_install_id(Path(data_dir))
    if not install:
        logger.error(
            "controller service identity NOT minted: no install id could be read from %s. "
            "The controller will keep writing records under the owner's identity.",
            data_dir,
        )
        return None

    if not user_id:
        # Not an error: an install with no owner yet simply is not ready. The
        # next boot retries the moment an owner exists.
        logger.info("controller service identity deferred: install has no owner yet")
        return None

    existing = await registry.list_for_install(install, status="active")
    record = next(
        (r for r in existing if r.get("origin") == CONTROLLER_ORIGIN), None
    )

    if record is None:
        short = install[:_SLUG_INSTALL_CHARS]
        record = await registry.register(
            framework=CONTROLLER_ORIGIN,
            display_name=f"{_SLUG_PREFIX}-{short}",
            user_id=user_id,
            origin=CONTROLLER_ORIGIN,
            handle=CONTROLLER_HANDLE,
            capabilities=[],
            install_id=install,
            allow_reserved=True,
        )
        logger.info(
            "controller service identity minted: %s (install %s, owner %s)",
            record["canonical_id"], short, user_id,
        )

    # add_grant is idempotent, so this re-asserts the baseline on every boot.
    # That is deliberate: a scope removed by hand comes back, because records:write
    # is what the controller needs to function at all. Anything a user ADDED is
    # untouched -- this only ever adds.  Default tier, matching the internal
    # mint path; 'once' is the only tier this store writes today.
    for scope in CONTROLLER_SCOPES:
        await grants.add_grant(record["canonical_id"], scope)

    if not _has_token(data_dir):
        token = mint_registry_token(
            record["canonical_id"],
            signing_key_pem,
            user_id=record.get("user_id", ""),
            framework=record.get("framework", CONTROLLER_ORIGIN),
            principal_type="service",
        )
        written, created = _write_token(data_dir, token)
        if created:
            logger.info("controller service token written to %s", written)
        elif written is not None:
            logger.info("controller service token already present at %s", written)

    return record


async def rotate_controller_service_token(
    *,
    registry: Any,
    data_dir: Path | str,
    signing_key_pem: bytes,
) -> Optional[str]:
    """Rotate the controller's service token by bumping token_min_iat and minting a new one.

    Returns the new token, or None if the controller service identity does not
    exist.

    The cutoff is ``max(now + 1, current_cutoff + 1)`` and the replacement is
    minted AT that cutoff, so every rotation supersedes every token issued
    before it even within the same second.
    """
    import time

    install = read_install_id(Path(data_dir))
    if not install:
        return None

    existing = await registry.list_for_install(install, status="active")
    record = next(
        (r for r in existing if r.get("origin") == CONTROLLER_ORIGIN), None
    )
    if record is None:
        return None

    before_iat = record.get("token_min_iat") or 0
    target = max(int(time.time()) + 1, before_iat + 1)

    def _mint(at: int) -> str:
        return mint_registry_token(
            record["canonical_id"],
            signing_key_pem,
            user_id=record.get("user_id", ""),
            framework=record.get("framework", CONTROLLER_ORIGIN),
            principal_type="service",
            iat=at,
        )

    token = _mint(target)

    updated = await registry.bump_token_min_iat(record["canonical_id"], target)
    cutoff = int((updated or record).get("token_min_iat") or 0)
    if updated is not None and cutoff != target:
        # A concurrent rotation advanced the cutoff past our target; re-mint at
        # the cutoff that actually landed so the credential we write clears it.
        token = _mint(cutoff)

    # Write the new token, replacing the old one.
    path = controller_token_path(data_dir)
    try:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        logger.error("controller token could not be written to %s: %s", path, exc)
        return None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token)
    except OSError as exc:
        logger.error("controller token write failed at %s: %s", path, exc)
        try:
            os.unlink(path)
        except OSError:
            pass
        return None

    logger.info("controller service token rotated at %s", path)
    return token
