# tinyagentos/controller_service_identity.py
"""First-boot service identity for the taOS controller.

WHY THIS EXISTS.  The memory service is where history records live, and a
record has to name a real writer.  The controller had no identity of its own,
so it could not write records as itself -- its writes were indistinguishable
from the owner's and could not be audited or revoked separately.  This module
mints a service principal for the controller at first boot, following the same
pattern as ``ensure_native_agent_identity`` in native_agent_identity.py.

FIVE PROPERTIES, each of which is a requirement rather than a nicety:

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

4. CONSERVATIVE.  One grant, ``records:write``: enough for the controller to
   name itself as the writer of history records, and nothing else.  Scopes are
   GRANTS in the registry grants feed (GET /api/agents/registry/grants), one
   record per scope, exact-match; the token carries no scope claim.  The grant
   is created once, with the identity, and never re-asserted afterwards, so
   revoking it sticks.  Token claims (the memory service's contract): ``iss``
   "taos-registry", ``sub`` the canonical_id, integer ``iat`` and ``exp``
   (lifetime at most 24 hours), ``kind`` "service", ``jti``; no ``aud``.
   taOS's own verifiers read the older ``principal_type`` claim, which is
   emitted too.

5. RENEWED BY THE CONTROLLER.  The token lives ``TOKEN_TTL_SECONDS``.  Start-up
   and a background loop renew it once it is missing, malformed, not the
   expected shape, or two thirds through its life (``RENEW_BEFORE_SECONDS``
   left), so it is never presented near ``exp``.  A renewal
   writes the new file atomically BEFORE the old token is superseded, so a
   failed renewal leaves the old token in place and working until it expires.
   Nothing here blocks start-up.

WHAT THIS DOES NOT DO.  It does not grant the controller any agent or human
privilege: a service-principal token is not accepted anywhere an agent or
human token is required (see agent_token_auth.py and auth_middleware.py), and
no route mints or rotates it for anyone else.  It is identity for record
authorship, not a backdoor into agent-required endpoints.  The token is never
logged.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

from tinyagentos.agent_registry_store import (
    CONTROLLER_ORIGIN,
    mint_registry_token,
    verify_registry_token,
)
from tinyagentos.atomic_io import atomic_write_bytes
from tinyagentos.auto_update import install_id as read_install_id

logger = logging.getLogger(__name__)

# The grants the controller holds: name itself as the writer of history
# records.  Deliberately nothing else (not a2a_send: nothing requires the
# controller to send on the bus; records:read and a2a_read are reserved).
CONTROLLER_GRANTS = ("records:write",)

# Bus/registry handle for the controller.  Each install has its own registry
# database, so a stable handle is fine within an install; the canonical_id
# (slug + timestamp) is what is actually unique across installs.
CONTROLLER_HANDLE = "service:controller"

# canonical_id slug prefix.  `taos-` is RESERVED: register() refuses it unless
# allow_reserved=True, which the HTTP layer never populates.  This module is
# in-process, so it can pass it.
_SLUG_PREFIX = "taos-controller"

# How much of the install id goes into the canonical_id.  The full id is on
# the row in install_id.
_SLUG_INSTALL_CHARS = 8

# The token file lives in the taOS secrets area under the data directory and
# is never placed in an environment variable.
TOKEN_DIR = ".secrets"
TOKEN_FILENAME = "taos_controller_token"

# Lifetime and renewal.  The memory service allows at most 24 hours and gives
# no leeway on the bus path, so a token lives 24 hours and is renewed once a
# third of its life is left (two thirds elapsed); the loop looks every hour.
TOKEN_TTL_SECONDS = 24 * 3600
RENEW_BEFORE_SECONDS = TOKEN_TTL_SECONDS // 3
RENEWAL_INTERVAL_SECONDS = 3600.0


def controller_token_path(data_dir: Path | str) -> Path:
    """Path of this install's controller service token file."""
    return Path(data_dir) / TOKEN_DIR / TOKEN_FILENAME


ON_BEHALF_OF_KINDS = ("person", "agent")


def controller_record(record: dict, *, on_behalf_of: Optional[dict] = None) -> dict:
    """Return a copy of a record envelope the controller is about to write,
    naming who it is written for.

    ``on_behalf_of`` is ``{"kind": "person" | "agent", "id": "<canonical_id>"}``
    (same identifier namespace as the token's ``sub``) and travels in the
    envelope, never in the token.  The receiver records the token's ``sub`` as
    the actor.  Any other kind, an empty or non-string id, or extra keys raise
    ``ValueError``; ``None`` means the controller writes for nobody and the
    field is absent.  The input is not mutated.
    """
    out = dict(record)
    if on_behalf_of is None:
        return out
    if not isinstance(on_behalf_of, dict) or set(on_behalf_of) != {"kind", "id"}:
        raise ValueError('on_behalf_of must be {"kind": ..., "id": ...}')
    kind, ident = on_behalf_of["kind"], on_behalf_of["id"]
    if kind not in ON_BEHALF_OF_KINDS:
        raise ValueError(f"on_behalf_of kind must be one of {ON_BEHALF_OF_KINDS}")
    if not isinstance(ident, str) or not ident.strip():
        raise ValueError("on_behalf_of id must be a non-empty string")
    out["on_behalf_of"] = {"kind": kind, "id": ident}
    return out


def _public_pem(signing_key_pem: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(signing_key_pem, password=None)
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _token_state(
    data_dir: Path | str, signing_key_pem: bytes, canonical_id: str, now: float
) -> tuple[bool, bool]:
    """Return ``(needs_renewal, has_valid_old_token)`` for the token file.

    ``has_valid_old_token`` is true when the file holds a token this
    controller signed, so a replacement should supersede it.  A missing,
    empty, truncated, unsigned or foreign file needs renewal but has nothing
    to supersede.  A token that is right but expired also counts as a valid
    old one: it is superseded so it cannot come back.
    """
    path = controller_token_path(data_dir)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return True, False
    if not raw:
        return True, False
    try:
        claims = verify_registry_token(raw, _public_pem(signing_key_pem), check_exp=False)
    except ValueError:
        return True, False
    exp, iat = claims.get("exp"), claims.get("iat")
    if (
        claims.get("sub") != canonical_id
        or claims.get("principal_type") != "service"
        or claims.get("kind") != "service"
        or "aud" in claims
        or any(isinstance(v, bool) or not isinstance(v, int) for v in (exp, iat))
        or exp - iat > TOKEN_TTL_SECONDS
    ):
        # Right signature, wrong shape (an earlier draft of the claims, say):
        # replace it and retire the old one.
        return True, True
    return (exp - now) < RENEW_BEFORE_SECONDS, True


def _mint(record: dict, signing_key_pem: bytes, iat: int) -> str:
    return mint_registry_token(
        record["canonical_id"],
        signing_key_pem,
        principal_type="service",
        iat=iat,
        exp=iat + TOKEN_TTL_SECONDS,
    )


async def _mint_not_future(record: dict, signing_key_pem: bytes, iat: int) -> str:
    """Mint at *iat*, first waiting out the (sub-second to few-second) gap if a
    rotation cutoff sits just ahead of the clock: the memory service must
    never see an ``iat`` in the future."""
    wait = iat - time.time()
    if wait > 0:
        await asyncio.sleep(wait)
    return _mint(record, signing_key_pem, iat)


def _store_token(data_dir: Path | str, token: str) -> bool:
    """Durably replace the token file (temp file in the same directory, fsync,
    rename, mode 0600).  A crash leaves the old file or the new one, never a
    prefix.  Returns False, and logs without the token, when it cannot."""
    path = controller_token_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write_bytes(path, token.encode("utf-8"), mode=0o600)
    except OSError as exc:
        logger.error(
            "controller service token could not be written to %s (%s)",
            path, type(exc).__name__,
        )
        return False
    return True


async def _issue_token(
    registry: Any,
    record: dict,
    data_dir: Path | str,
    signing_key_pem: bytes,
    *,
    supersede: bool,
) -> Optional[str]:
    """Mint a token, write it, and only then retire the old ones.

    Order matters: the new file is on disk before the cutoff moves, so a write
    that fails leaves the old token in the file AND still honoured.  When
    *supersede* is false there is no earlier token to retire.
    """
    cid = record["canonical_id"]
    now = int(time.time())
    target = now
    if supersede:
        target = max(now, int(record.get("token_min_iat") or 0) + 1)
    token = await _mint_not_future(record, signing_key_pem, target)
    if not _store_token(data_dir, token):
        return None
    if not supersede:
        logger.info("controller service token written for %s", cid)
        return token
    try:
        updated = await registry.bump_token_min_iat(cid, target)
        cutoff = int((updated or record).get("token_min_iat") or 0)
        if updated is not None and cutoff != target:
            # A concurrent rotation moved the cutoff past ours; re-mint at the
            # cutoff that landed so the credential on disk clears it.
            token = await _mint_not_future(record, signing_key_pem, cutoff)
            if not _store_token(data_dir, token):
                return None
    except Exception as exc:
        # The new token is already in place and valid.  The old one could not
        # be retired and stays honoured until it expires.
        logger.error(
            "controller service token renewed but the old one could not be "
            "superseded (%s); it stays valid until it expires",
            type(exc).__name__,
        )
    logger.info("controller service token renewed for %s", cid)
    return token


async def _find_controller_row(registry: Any, install: str) -> Optional[dict]:
    """The controller's row for this install, in ANY status.  A suspended or
    revoked row is still THE controller row: looking only at active rows made
    start-up register a second one."""
    rows = await registry.list_for_install(install)
    return next((r for r in rows if r.get("origin") == CONTROLLER_ORIGIN), None)


async def ensure_controller_service_identity(
    *,
    registry: Any,
    grants: Any,
    data_dir: Path | str,
    signing_key_pem: bytes,
    user_id: str,
) -> Optional[dict]:
    """Mint (or re-assert) this install's controller service identity and keep
    its token fresh. Idempotent; safe to call on every start and on a timer.

    Returns the registry record, or ``None`` when there is nothing to do or it
    could not be done -- in which case the caller carries on: an install
    without a controller service identity is degraded, not broken, and
    refusing to boot over it would turn a missing convenience into an outage.
    A suspended or revoked controller row is left exactly as it is: no second
    row, no new token.
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

    record = await _find_controller_row(registry, install)
    if record is not None and record.get("status") != "active":
        logger.warning(
            "controller service identity %s is %s; leaving it alone "
            "(no new row, no new token)",
            record["canonical_id"], record.get("status"),
        )
        return None

    created = record is None
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

    # The grant records are created with the identity, or if provisioning never
    # got as far as a first token (a crash between the two).  They are NOT
    # re-asserted on later starts or renewal passes: an owner who revokes
    # ``records:write`` must find it still revoked.
    if created or not controller_token_path(data_dir).exists():
        for scope in CONTROLLER_GRANTS:
            await grants.add_grant(record["canonical_id"], scope)

    needs, has_old = _token_state(
        data_dir, signing_key_pem, record["canonical_id"], time.time()
    )
    if needs:
        await _issue_token(
            registry, record, data_dir, signing_key_pem, supersede=has_old
        )
    return record


async def rotate_controller_service_token(
    *,
    registry: Any,
    data_dir: Path | str,
    signing_key_pem: bytes,
) -> Optional[str]:
    """Replace the controller's token now, whatever its age.

    Returns the new token, or None when the identity does not exist, is not
    active, or the new token could not be written (the old token is then
    untouched and still works).  Only the controller calls this; no route does.
    """
    install = read_install_id(Path(data_dir))
    if not install:
        return None
    record = await _find_controller_row(registry, install)
    if record is None or record.get("status") != "active":
        return None
    return await _issue_token(
        registry, record, data_dir, signing_key_pem, supersede=True
    )


async def run_controller_token_renewal(
    *,
    registry: Any,
    grants: Any,
    data_dir: Path | str,
    signing_key_pem: bytes,
    owner_id: Callable[[], str],
) -> None:
    """Background loop: look at the identity every ``RENEWAL_INTERVAL_SECONDS``
    and renew the token before it expires.  A failed pass is logged (error type
    only) and retried next tick; the old token keeps working meanwhile."""
    while True:
        await asyncio.sleep(RENEWAL_INTERVAL_SECONDS)
        try:
            await ensure_controller_service_identity(
                registry=registry,
                grants=grants,
                data_dir=data_dir,
                signing_key_pem=signing_key_pem,
                user_id=owner_id(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "controller service token renewal pass failed (%s); retrying",
                type(exc).__name__,
            )
