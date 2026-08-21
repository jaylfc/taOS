# tinyagentos/native_agent_identity.py
"""First-boot identity for the OS-native taOS agent.

WHY THIS EXISTS.  Every other agent in taOS has an identity: a canonical_id, a
registry row, scopes it was granted, and a token that is its own.  The agent
built into the OS -- the one that operates the desktop on the user's behalf --
had none of that.  It authenticated as the OWNER, using either the caller's
browser session or ``data/.auth_local_token``, an admin-equivalent shared
credential.  So the agent's actions were indistinguishable from the human's in
every audit trail, it could not appear on the A2A bus as itself, and nothing it
did could be revoked without revoking the human.

This module mints that identity at first boot, with no admin step and no
prompt: an install that has an owner has an agent identity.

FOUR PROPERTIES, each of which is a requirement rather than a nicety:

1. PER-INSTALL.  The identity is anchored to ``<data_dir>/.install_id`` -- the
   same id the version ping uses, deliberately not a second one.  Two installs
   owned by the same account are two identities, so "this machine's agent" is a
   thing that can be named, listed and revoked.

2. OWNER-LINKED.  ``user_id`` is the install's primary user.  The registry
   treats user_id as immutable, so the mint has to happen at a moment when the
   owner is already known -- which is why this runs at owner creation and at
   startup, never at an ownerless boot.  An identity minted with no owner would
   be stuck that way for its whole life.

3. NOT SHARED.  The token is written to ``<data_dir>/.taos_agent_token`` (0600)
   on the install that minted it.  Nothing ships a credential between installs;
   an image cloned to a new machine gets a new install id and mints its own.

4. CONSERVATIVE.  Two scopes, ``a2a_send`` + ``a2a_receive``: enough to be a
   participant on the bus as itself, and nothing else.  Anything more goes
   through the normal scope-request flow, which already exists and is
   user-mediated.  A first-boot mint that quietly granted file or task access
   would be a silent privilege grant, which is the opposite of the point.

WHAT THIS DOES NOT DO.  It does not let the agent drive the desktop with its
token.  ``/api/desktop/*`` resolves the acting user from the session and the
middleware sets ``user_id = None`` for registry JWTs, so a registry token
reaches those routes as nobody.  The desktop path still uses the session/local
token exactly as before.  This slice is identity + bus; the desktop half is a
separate change and is called out here so the boundary is not mistaken for an
oversight.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.auto_update import install_id as read_install_id

logger = logging.getLogger(__name__)

# The origin marks the row as minted by the OS itself rather than deployed by a
# user or self-joined by an external agent.  register() lands any origin other
# than "external-selfjoin" as active, which is what we want: there is no consent
# round-trip to run against the owner who just created the install.
NATIVE_AGENT_ORIGIN = "taos-native"

# Bus participation only.  See property 4 above before adding to this.
NATIVE_AGENT_SCOPES = ("a2a_send", "a2a_receive")

# How much of the install id goes into the canonical_id and the handle.  The
# full id is on the row in install_id; this is for humans reading either one in
# an audit log.
_SLUG_INSTALL_CHARS = 8

# The handle carries the install discriminator for the same reason the
# canonical_id does.  A bare "@taOS-agent" looks nicer and is wrong: the registry
# holds a UNIQUE index on (handle) WHERE status='active', so the moment two
# installs' identities live in one registry -- which is the whole point of the
# account/cluster model -- the second one cannot be inserted at all.  Found by
# the clone test below, not by reasoning: it failed with
# "UNIQUE constraint failed: agent_registry.handle".
NATIVE_AGENT_HANDLE_PREFIX = "@taOS-agent"


def native_agent_handle(install: str) -> str:
    """Bus handle for the native agent of install *install*."""
    return f"{NATIVE_AGENT_HANDLE_PREFIX}-{install[:_SLUG_INSTALL_CHARS]}"


# The token file is per-install and never leaves it.
TOKEN_FILENAME = ".taos_agent_token"

# canonical_id slug prefix.  `taos-` is a RESERVED prefix: register() refuses it
# unless allow_reserved=True, which is a keyword the HTTP layer never populates
# from a request body.  This module is in-process, so it can legitimately pass
# it -- an agent named by the OS should be under the OS's own prefix, and an
# external caller still cannot claim one.
_SLUG_PREFIX = "taos-agent"


def token_path(data_dir: Path | str) -> Path:
    """Path of this install's native-agent token file."""
    return Path(data_dir) / TOKEN_FILENAME


def _has_token(data_dir: Path | str) -> bool:
    """True only if a NON-EMPTY token file exists.

    Existence alone is not enough. A zero-byte file is what a crash or a failed
    write between O_EXCL and the write leaves behind, and treating it as "already
    minted" pins the agent to an empty credential forever while every boot looks
    successful. Belt and braces with the unlink in _write_token, because that
    unlink cannot help a file left by an earlier version or a hard kill.
    """
    path = token_path(data_dir)
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _write_token(data_dir: Path | str, token: str) -> tuple[Optional[Path], bool]:
    """Write *token* 0600, creating it only if absent.

    Returns ``(path, created_by_this_call)``. The flag is not decoration: the
    caller cannot work it out. Snapshotting ``_has_token`` before the call
    cannot distinguish "I wrote it" from "another worker wrote it during my
    call", so the loser of a startup race logged "token written" having
    written nothing. Only this function knows which of its three exits it
    took, so it is the only place the fact can come from.

    Created with O_EXCL rather than a plain write: two workers racing at startup
    must not have one truncate the file the other is reading.  An existing file
    is left ALONE -- the agent may already be running with that token, and
    rewriting it under a live process is how you get an agent holding a
    credential nobody recognises.
    """
    path = token_path(data_dir)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Someone got there first -- but "there is a file" is not "there is a
        # token". A zero-byte file is the leftover of a crash or a failed write,
        # and honouring it here would defeat _has_token: the caller would decide
        # to mint, and this would refuse to write, so the agent stays credentialless
        # on every boot forever. An empty file is nobody's token, so replace it.
        if _has_token(data_dir):
            return path, False
        try:
            os.unlink(path)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Lost a genuine race in the gap; the winner's token stands.
            return path, False
        except OSError as exc:
            logger.error("could not replace the empty token file at %s: %s", path, exc)
            return None, False
    except OSError as exc:
        logger.error("native agent token could not be written to %s: %s", path, exc)
        return None, False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token)
    except OSError as exc:
        # REMOVE the file we just created.  O_EXCL created it and fdopen
        # truncated it, so a failed write (ENOSPC, a full quota, a disk error)
        # leaves a ZERO-BYTE file behind -- and an existing file is exactly what
        # tells the next boot the token is already minted.  Left in place, one
        # transient write error permanently pins the agent to an empty
        # credential, and every subsequent boot reports success. A broken state
        # that reads as a finished one.
        logger.error("native agent token write failed at %s: %s", path, exc)
        try:
            os.unlink(path)
        except OSError:
            logger.error("could not remove the empty token file at %s", path)
        return None, False
    return path, True


async def ensure_native_agent_identity(
    *,
    registry: Any,
    grants: Any,
    data_dir: Path | str,
    signing_key_pem: bytes,
    user_id: str,
) -> Optional[dict]:
    """Mint (or re-assert) this install's native agent identity. Idempotent.

    Returns the registry record, or ``None`` when the identity could not be
    minted -- in which case the caller carries on: an install without a native
    agent identity is degraded, not broken, and refusing to boot over it would
    turn a missing convenience into an outage.

    Safe to call on every startup and at owner creation.  Lookup is by
    install_id, not by handle: the handle is a display string that a user or a
    migration could change, while the install id is what the identity actually
    belongs to.
    """
    install = read_install_id(Path(data_dir))
    if not install:
        # install_id() swallows its own errors and returns "" -- fine for a
        # telemetry ping, not fine here.  An identity minted with a blank anchor
        # could never be listed or revoked as part of this install, and would be
        # indistinguishable from the pre-v6 rows that legitimately have none.
        logger.error(
            "native agent identity NOT minted: no install id could be read from %s. "
            "The agent will keep using the owner's credential.",
            data_dir,
        )
        return None

    if not user_id:
        # Not an error: an install with no owner yet simply is not ready. The
        # setup route calls us again the moment the owner exists.
        logger.info("native agent identity deferred: install has no owner yet")
        return None

    existing = await registry.list_for_install(install, status="active")
    record = next(
        (r for r in existing if r.get("origin") == NATIVE_AGENT_ORIGIN), None
    )

    if record is None:
        short = install[:_SLUG_INSTALL_CHARS]
        record = await registry.register(
            framework=NATIVE_AGENT_ORIGIN,
            display_name=f"{_SLUG_PREFIX}-{short}",
            user_id=user_id,
            origin=NATIVE_AGENT_ORIGIN,
            handle=native_agent_handle(install),
            capabilities=[],
            allow_reserved=True,
            install_id=install,
        )
        logger.info(
            "native agent identity minted: %s (install %s, owner %s)",
            record["canonical_id"], short, user_id,
        )

    # add_grant is idempotent, so this re-asserts the baseline on every boot.
    # That is deliberate: a scope removed by hand comes back, because these two
    # are what the OS agent needs to function at all. Anything a user ADDED is
    # untouched -- this only ever adds.  Default tier, matching the internal
    # mint path; 'once' is the only tier this store writes today.
    for scope in NATIVE_AGENT_SCOPES:
        await grants.add_grant(record["canonical_id"], scope)

    if not _has_token(data_dir):
        token = mint_registry_token(
            record["canonical_id"],
            signing_key_pem,
            user_id=record.get("user_id", ""),
            framework=record.get("framework", NATIVE_AGENT_ORIGIN),
        )
        written, created = _write_token(data_dir, token)
        if created:
            logger.info("native agent token written to %s", written)
        elif written is not None:
            # Startup race: another worker won and this process wrote nothing.
            # Saying "written" here would be a log that lies about who did what,
            # and the fact comes from _write_token because a pre-write
            # _has_token snapshot is False in exactly this case too.
            logger.info("native agent token already present at %s", written)

    return record
