"""The gateway's ONE auth and model-permission seam: scoped gateway keys.

Every gateway route takes ``caller: GatewayCaller = Depends(gateway_caller)``
and asks ``caller.may_use(model)`` (for the requested model AND the model it
resolves to); nothing else in the package looks at credentials. The auth
middleware exempts exactly ``GET /api/llm/v1/models`` and
``POST /api/llm/v1/chat/completions`` so a bearer key reaches this function.

Accepted, in order:

- a signed-in SESSION (``request.state.via == "session"``) -> kind "session",
  every model;
- the host's shared LOCAL TOKEN (``<data_dir>/.auth_local_token``, either as
  the middleware already resolved it or as a bearer on the exempt paths) ->
  kind "local_token", every model. A deployer-minted per-agent local token is
  REFUSED: it is the agent's controller identity, and granting it every model
  would sidestep the agent's model scope;
- the per-install LiteLLM master key (``<data_dir>/.litellm_master_key``) ->
  kind "admin", every model (parity with ``litellm_auth.user_api_key_auth``);
- a gateway key (``sk-taosgw-...``) minted here, bound to an agent id or a
  node (principal ``node:<id>``) -> kind "agent" / "node", its allowlist;
- a legacy per-agent LiteLLM key (``sk-taos-...``, ``agent_keys``) -> kind
  "agent", its allowlist.

Everything else -- missing, malformed, unknown, revoked or expired -- is a
401 ``GatewayError`` (OpenAI-shaped). The key is never logged. An agent over
its LLM budget gets the LiteLLM hook's 429, as the client saw it, before
anything is forwarded.

Storage: the ``gateway_keys`` table in the existing LiteLLM keystore file
(``tinyagentos.litellm_keystore``). Only ``sha256(key)`` is stored; the
plaintext is returned once by :func:`mint_gateway_key`. Lookup is by hash and
the stored hash is then compared with :func:`hmac.compare_digest`.

The allowlist rule (DELIBERATELY different from LiteLLM): an EMPTY allowlist
denies every model. LiteLLM read ``models=[]`` as allow-all. ``None`` (every
model) is reserved for the admin kinds. ``taos-default`` is usable only when
named, and naming the concrete model it currently resolves to does not grant
the alias. The router applies the alias rule: a caller granted
``taos-default`` may use whatever it currently resolves to (only the owner
sets the default), while a concrete model requested directly still needs its
own entry. :meth:`GatewayCaller.may_use` is plain set membership, nothing more.
"""
from __future__ import annotations

import hmac
import logging
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse

from tinyagentos.litellm_keystore import (
    LiteLLMKeyStore,
    default_keystore_path,
    token_hash,
)
from tinyagentos.llm_gateway.errors import GatewayError, unauthorized

logger = logging.getLogger(__name__)

GATEWAY_KEY_PREFIX = "sk-taosgw-"
# secrets.token_urlsafe(32) is always 43 characters.
_KEY_BODY_LEN = 43
GATEWAY_KEY_LEN = len(GATEWAY_KEY_PREFIX) + _KEY_BODY_LEN

_SCOPED_KINDS = frozenset({"agent", "node"})
# Kinds that carry allowed_models=None (every model), and only they may.
ADMIN_KINDS = frozenset({"admin", "session", "local_token"})
NODE_PRINCIPAL_PREFIX = "node:"
# request.state.via values set by auth_middleware.
_VIA_SESSION = "session"
_VIA_LOCAL_TOKEN = "local_token"

# A presented credential must be printable ASCII token68-ish characters. This
# rejects unicode, control bytes and inner whitespace before anything is
# compared (compare_digest raises TypeError on non-ASCII str).
_TOKEN_RE = re.compile(r"[A-Za-z0-9._~+/=-]{16,512}")

# Indirection so tests can move the clock for expiry.
_now = time.time

_default_data_dir: Path | None = None
_stores: dict[str, LiteLLMKeyStore] = {}


# ---------------------------------------------------------------------------
# Caller
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GatewayCaller:
    caller_id: str
    kind: str  # "agent" | "node" | "session" | "local_token" | "admin"
    allowed_models: frozenset[str] | None  # None = every model (admin kinds only)
    key_id: str | None = None

    def __post_init__(self) -> None:
        if self.allowed_models is None:
            if self.kind not in ADMIN_KINDS:
                raise ValueError(
                    f"only {sorted(ADMIN_KINDS)} callers may carry allowed_models=None"
                )
        elif not isinstance(self.allowed_models, frozenset):
            raise ValueError("allowed_models must be a frozenset (or None for admin)")
        elif self.kind in ADMIN_KINDS:
            raise ValueError("an admin-kind caller carries allowed_models=None")

    def may_use(self, model: str) -> bool:
        """True iff this caller may call ``model``.

        Admin kinds: any model. Scoped: exact membership; an EMPTY set denies all.
        """
        if not isinstance(model, str) or not model:
            return False
        if self.allowed_models is None:
            return True
        return model in self.allowed_models


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BudgetExceeded(GatewayError):
    """The LiteLLM hook's budget hard stop, exactly as the client saw it.

    The hook raises ``HTTPException(429, "agent '<a>' has exceeded its LLM
    budget")`` and LiteLLM's auth error handler renders that as
    ``ProxyException(type="auth_error", param="None", code="429")`` ->
    ``{"error": {"message", "type", "param": "None", "code": "429"}}``. This
    reproduces that response (string ``param`` and ``code`` included), so a
    client that handled the LiteLLM refusal handles this one.
    """

    def __init__(self, agent: str):
        super().__init__(
            429, f"agent '{agent}' has exceeded its LLM budget",
            type="auth_error", code="429",
        )

    def response(self) -> JSONResponse:
        return JSONResponse(
            {"error": {"message": self.message, "type": self.type,
                       "param": "None", "code": self.code}},
            status_code=self.status,
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def configure_gateway_keystore(data_dir: str | Path | None) -> None:
    """Set the data dir used when a caller does not pass one (create_app)."""
    global _default_data_dir
    _default_data_dir = Path(data_dir) if data_dir is not None else None


def _store(data_dir: str | Path | None = None) -> LiteLLMKeyStore:
    base = Path(data_dir) if data_dir is not None else _default_data_dir
    if base is None:
        raise RuntimeError("LLM gateway keystore is not configured")
    path = str(default_keystore_path(base))
    store = _stores.get(path)
    if store is None:
        store = LiteLLMKeyStore(path)
        _stores[path] = store
    return store


def _read_secret_file(path: Path) -> str | None:
    try:
        value = Path(path).read_text().strip()
    except OSError:
        return None
    return value or None


# ---------------------------------------------------------------------------
# Mint / revoke
# ---------------------------------------------------------------------------


def node_principal(node_id: str) -> str:
    if not isinstance(node_id, str) or not node_id:
        raise ValueError("node_id must be a non-empty string")
    return NODE_PRINCIPAL_PREFIX + node_id


def _validate_models(allowed_models: Iterable[str]) -> list[str]:
    if isinstance(allowed_models, (str, bytes)):
        # A bare string would iterate into single characters.
        raise TypeError("allowed_models must be an iterable of model names, not a string")
    models = list(allowed_models)
    for m in models:
        if not isinstance(m, str) or not m:
            raise ValueError("every allowed model must be a non-empty string")
    return sorted(set(models))


def mint_gateway_key(
    *,
    bound_to: str,
    kind: str,
    allowed_models: Iterable[str],
    ttl_seconds: float | None = None,
    data_dir: str | Path | None = None,
) -> str:
    """Mint a key bound to ``bound_to``; return the plaintext ONCE.

    ``kind`` is "agent" (``bound_to`` = agent id) or "node" (``bound_to`` =
    ``node:<id>``; prefer :func:`mint_for_node`). An empty ``allowed_models``
    mints a key that authenticates but may use no model.
    """
    if kind not in _SCOPED_KINDS:
        raise ValueError(f"kind must be one of {sorted(_SCOPED_KINDS)}")
    if not isinstance(bound_to, str) or not bound_to:
        raise ValueError("bound_to must be a non-empty string")
    is_node = bound_to.startswith(NODE_PRINCIPAL_PREFIX)
    if (kind == "node") != is_node:
        raise ValueError("node keys are bound to 'node:<id>'; agent keys never are")
    if is_node and len(bound_to) == len(NODE_PRINCIPAL_PREFIX):
        raise ValueError("node principal has an empty id")
    models = _validate_models(allowed_models)
    expires_ts = None
    if ttl_seconds is not None:
        if not ttl_seconds > 0:
            raise ValueError("ttl_seconds must be positive")
        expires_ts = _now() + float(ttl_seconds)

    key = GATEWAY_KEY_PREFIX + secrets.token_urlsafe(32)
    key_id = "gk_" + secrets.token_hex(8)
    _store(data_dir).insert_gateway_key(
        key_id=key_id,
        key_hash=token_hash(key),
        bound_to=bound_to,
        kind=kind,
        allowed_models=models,
        expires_ts=expires_ts,
    )
    logger.info("llm gateway: minted key %s for %s (%s)", key_id, bound_to, kind)
    return key


def revoke_keys_for(bound_to: str, *, data_dir: str | Path | None = None) -> int:
    """Revoke every key bound to ``bound_to``; returns how many were live.

    Also deletes legacy per-agent keys stored under the same name.
    """
    if not isinstance(bound_to, str) or not bound_to:
        return 0
    n = _store(data_dir).revoke_bound(bound_to)
    if n:
        logger.info("llm gateway: revoked %d key(s) for %s", n, bound_to)
    return n


def mint_for_node(
    node_id: str,
    allowed_models: Iterable[str],
    *,
    ttl_seconds: float | None = None,
    data_dir: str | Path | None = None,
) -> str:
    return mint_gateway_key(
        bound_to=node_principal(node_id),
        kind="node",
        allowed_models=allowed_models,
        ttl_seconds=ttl_seconds,
        data_dir=data_dir,
    )


def revoke_for_node(node_id: str, *, data_dir: str | Path | None = None) -> int:
    return revoke_keys_for(node_principal(node_id), data_dir=data_dir)


# ---------------------------------------------------------------------------
# The dependency
# ---------------------------------------------------------------------------


def _parse_bearer(header) -> str | None:  # noqa: ANN001
    if not isinstance(header, str):
        return None
    scheme, sep, rest = header.partition(" ")
    if not sep or scheme.lower() != "bearer":
        return None
    candidate = rest.strip(" \t")
    if _TOKEN_RE.fullmatch(candidate) is None:
        return None
    return candidate


def _ct_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("ascii"), b.encode("ascii"))


def _over_budget(agent: str, data_dir: Path) -> bool:
    """The hook's hard stop, carried over. Fails open when budgets were
    never configured (no store file), as the hook does."""
    from tinyagentos.agent_budget_store import AgentBudgetStore, default_budget_path

    path = default_budget_path(data_dir)
    if not path.exists():
        return False
    try:
        return AgentBudgetStore(path).is_over_budget(agent)
    except Exception:
        logger.exception("llm gateway: budget store unreadable; not enforcing")
        return False


def _resolve_scoped(store: LiteLLMKeyStore, presented: str) -> GatewayCaller | None:
    presented_hash = token_hash(presented)
    if presented.startswith(GATEWAY_KEY_PREFIX):
        if len(presented) != GATEWAY_KEY_LEN:
            return None
        rec = store.gateway_key_by_hash(presented_hash)
        if rec is None or not _ct_equal(rec["key_hash"], presented_hash):
            return None
        if rec["revoked_ts"] is not None:
            logger.info("llm gateway: rejected revoked key %s", rec["key_id"])
            return None
        if rec["expires_ts"] is not None and rec["expires_ts"] <= _now():
            logger.info("llm gateway: rejected expired key %s", rec["key_id"])
            return None
        if rec["kind"] not in _SCOPED_KINDS:
            return None
        return GatewayCaller(
            caller_id=rec["bound_to"],
            kind=rec["kind"],
            allowed_models=frozenset(rec["allowed_models"]),
            key_id=rec["key_id"],
        )
    rec = store.agent_key_by_hash(presented_hash)
    if rec is None or not _ct_equal(rec["token_hash"], presented_hash):
        return None
    return GatewayCaller(
        caller_id=rec["agent"],
        kind="agent",
        allowed_models=frozenset(rec["allowed_models"]),
        key_id=None,
    )



def _matches_file(presented: str, path: Path) -> bool:
    stored = _read_secret_file(path)
    if stored is None or not stored.isascii():
        return False
    return _ct_equal(presented, stored)


def _local_token_caller(request, user_id: str | None = None) -> GatewayCaller:  # noqa: ANN001
    if user_id is None:
        try:
            primary = request.app.state.auth.get_primary_user()
        except Exception:  # noqa: BLE001 - no auth manager: anonymous local caller
            primary = None
        user_id = primary.get("id") if primary else None
    caller_id = f"user:{user_id}" if user_id else "local"
    return GatewayCaller(caller_id=caller_id, kind="local_token", allowed_models=None)


def gateway_caller(request: Request) -> GatewayCaller:
    """FastAPI dependency: resolve the caller or raise an OpenAI-shaped 401.

    Sync on purpose: nothing here awaits, and the seam stays directly callable.
    """
    state = getattr(request, "state", None)
    via = getattr(state, "via", None)
    user_id = getattr(state, "user_id", None)
    if via == _VIA_SESSION and user_id:
        return GatewayCaller(caller_id=f"user:{user_id}", kind="session", allowed_models=None)
    if via == _VIA_LOCAL_TOKEN:
        # The middleware resolved a local token. A per-agent one (bound to
        # request.state.agent_name) is the agent's identity, not a model
        # credential: refused.
        if getattr(state, "agent_name", None):
            logger.info("llm gateway: rejected a per-agent local token")
            raise unauthorized()
        return _local_token_caller(request, user_id)

    headers = getattr(request, "headers", None)
    presented = _parse_bearer(headers.get("authorization") if headers is not None else None)
    if presented is None:
        logger.info("llm gateway: rejected missing or malformed credential")
        raise unauthorized()

    app_state = getattr(getattr(request, "app", None), "state", None)
    data_dir = getattr(app_state, "data_dir", None) or _default_data_dir
    if data_dir is None:
        logger.error("llm gateway: no data dir configured; rejecting credential")
        raise unauthorized()
    data_dir = Path(data_dir)

    # Both admin files are always compared, so timing does not say which one
    # exists or matched. A per-agent local token matches neither and falls
    # through to the key store, where it is unknown: 401.
    is_local = _matches_file(presented, data_dir / ".auth_local_token")
    is_master = _matches_file(presented, data_dir / ".litellm_master_key")
    if is_local:
        return _local_token_caller(request)
    if is_master:
        return GatewayCaller(caller_id="litellm-master", kind="admin", allowed_models=None)

    try:
        store = _store(data_dir)
    except Exception:
        logger.error("llm gateway: keystore unavailable; rejecting credential")
        raise unauthorized() from None

    caller = _resolve_scoped(store, presented)
    if caller is None:
        logger.info("llm gateway: rejected unknown or dead credential")
        raise unauthorized()

    # Agent budgets only: a node has no agent budget.
    if caller.kind == "agent" and _over_budget(caller.caller_id, data_dir):
        logger.info("llm gateway: refused over-budget agent %s", caller.caller_id)
        raise BudgetExceeded(caller.caller_id)
    return caller


__all__ = [
    "ADMIN_KINDS",
    "BudgetExceeded",
    "GATEWAY_KEY_LEN",
    "GATEWAY_KEY_PREFIX",
    "GatewayCaller",
    "configure_gateway_keystore",
    "gateway_caller",
    "mint_for_node",
    "mint_gateway_key",
    "node_principal",
    "revoke_for_node",
    "revoke_keys_for",
]
