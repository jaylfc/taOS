# Multi-User Auth-Context Layer — Design

**Date:** 2026-06-09
**Status:** Approved (design decisions locked via review); ready for implementation
**Branch:** `feat/multi-user-auth-context`
**Supersedes:** PR #709 (registry revocation feed) — folded in here so a single PR owns the registry route changes.

## Problem

A background security review flagged two HIGH findings on the agent registry (`tinyagentos/routes/agent_registry.py`, merged in #705):

1. **Spoofable identity in signed token** — `register_agent` reads `user_id` (and `origin`) from the request **body** and `mint_registry_token` signs that `user_id` into the EdDSA token. A caller can self-assert any `user_id`.
2. **IDOR / cross-tenant exposure** — `DELETE /api/agents/registry/{id}` revokes any agent with no ownership check; `GET /api/agents/registry` and `/{id}` return every tenant's entries.

Root cause is shared with the whole app: `AuthMiddleware` validates a session/local-token as a pass/401 gate but **does not expose the authenticated user to route handlers**, so no route can enforce per-user ownership. `AuthManager` already resolves sessions to a `user_id`, tracks `is_admin`, and exposes `is_multi_user()` — the building blocks exist; the wiring does not.

## Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| 1 | How routes get the user | **Middleware sets `request.state` + a `current_user` FastAPI dependency** |
| 2 | Authorization rule | **Owner-or-admin** (owner = `user_id` match, OR `is_admin`) |
| 3 | Scope of this spec/PR | **Auth-context layer + apply to the registry now**; other stores user-scoped incrementally in tracked follow-ups |
| 4 | Bus access to registry endpoints | **`/pubkey` public (exempt); `/revoked` behind the local/service token** |

## Design

### 1. Auth context in the middleware

`AuthMiddleware.dispatch` resolves identity it currently discards. After each successful auth path, set request state; default to anonymous otherwise.

- **Exempt paths** (SPA shell, static, `/pubkey`, etc.): `request.state.user_id = None`, `request.state.is_admin = False`. Routes that need a user use the dependency, which 401s.
- **Local token path** (`Authorization: Bearer <.auth_local_token>`): possession = same-user-on-host trust → maps to the **primary/admin user**. Set `request.state.user_id = auth_mgr.get_primary_user()["id"]`, `request.state.is_admin = True`, `request.state.via = "local_token"`.
- **Session cookie path**: `user_id = auth_mgr.validate_session(token)`; look up `is_admin` via `auth_mgr.get_user_by_id(user_id)`. Set `request.state.user_id`, `request.state.is_admin`, `request.state.via = "session"`.

State is set on `request.state` before `call_next`. No behavioural change to the gate itself (same allow/redirect/401 logic).

### 2. `current_user` dependency + authz helpers

New module `tinyagentos/auth_context.py`:

```python
from dataclasses import dataclass
from fastapi import Request, HTTPException

@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    is_admin: bool

def current_user(request: Request) -> CurrentUser:
    """FastAPI dependency. 401 if no authenticated user on request.state."""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authentication required")
    return CurrentUser(user_id=uid, is_admin=bool(getattr(request.state, "is_admin", False)))

def require_owner_or_admin(user: CurrentUser, resource_user_id: str) -> None:
    """403 unless the caller owns the resource or is an admin."""
    if user.is_admin or user.user_id == resource_user_id:
        return
    raise HTTPException(status_code=403, detail="forbidden")
```

This is the **default pattern** for every user-scoped resource going forward, not registry-specific.

### 3. Registry route changes (`routes/agent_registry.py`)

- **`register`**: drop `user_id` from `RegisterRequest`; derive it from `Depends(current_user)`. Constrain `origin` to an allowlist `{"taos-deployed", "external-selfjoin"}` (default `taos-deployed`); reject others with 422. The minted token's `user_id` is now the authenticated caller's — no longer spoofable.
- **`list_registry`**: admins see all; non-admins see only their own (`[r for r in records if r["user_id"] == user.user_id]`). Add `store.list_for_user(user_id)` to push the filter into SQL rather than filtering in Python.
- **`get_registry_entry`**: 404 if missing; `require_owner_or_admin` on the record's `user_id` (403 otherwise — but return 404 to non-owners to avoid existence disclosure; see Testing).
- **`revoke_registry_entry`**: fetch first, `require_owner_or_admin`, then revoke. 404 for unknown; 403/404 for not-owned.
- **`get_pubkey`**: add `/api/agents/registry/pubkey` to `AuthMiddleware.EXEMPT_PATHS` so the bus fetches it with no session. Update the (now-accurate) docstring.
- **`list_revoked`** (folded from #709): `GET /api/agents/registry/revoked` → `{"revoked": [{canonical_id, revoked_at}, ...]}`, declared **before** `/{canonical_id}`. **Admin-or-local-token only** (it returns the global revocation set the bus needs; members don't see it). The bus authenticates with the local token, which maps to admin. Backed by `store.list_revoked()`.

### 4. Store additions (`agent_registry_store.py`)

- `list_for_user(user_id) -> list[dict]` — `WHERE user_id = ?`.
- `list_revoked() -> list[dict]` — `[{canonical_id, revoked_at}]WHERE revoked_at IS NOT NULL` (from #709).

### Backward compatibility / standalone

- **Single-user (today):** the one user is primary → `is_admin = True` → sees and manages everything. No functional change for Jay's deployment.
- **First boot / not configured:** unchanged (onboarding redirect / 401).
- **Local token:** existing scripts/CLI using the Bearer token keep working and now act explicitly as the admin user.

## Coordination with @taOSmd

- `/pubkey` becomes public — their pubkey fetch needs no token (simpler than today). Confirmed shape unchanged: `{"public_key": "<PEM SubjectPublicKeyInfo>"}`, `iss = "taos-registry"`.
- `/revoked` requires the local/service token — they configure the bus with it (alongside `registry_url`). Feed shape unchanged: `{"revoked": [{canonical_id, revoked_at}]}`.
- Their `feat/registry-bus-auth` branch already targets these contracts; only the `/revoked` auth requirement is new — relay it.

## Testing

- **Middleware:** state set correctly for session / local-token / exempt / anonymous paths.
- **Dependency:** `current_user` 401s with no user; returns correct `is_admin`.
- **Registry authz:** owner can read/revoke own; non-owner gets 404 (existence-hiding) on read and 403/404 on revoke; admin can do both across users; `register` ignores body `user_id` and signs the session user; `origin` allowlist enforced; `list` scoped for member vs full for admin.
- **Pubkey:** reachable unauthenticated (exempt).
- **Revoked feed:** admin/local-token only (member 403); correct shape; route-ordering regression (`/revoked` not matched as a canonical_id).
- Existing 32 registry tests stay green (adjust the ones that posted body `user_id`).

## Rollout

1. This PR: auth-context layer + registry. Closes/supersedes #709.
2. Follow-up (tracked, task #11 family): apply `current_user` + owner scoping to the other user-keyed stores (memory, projects, knowledge, …) incrementally — one store per PR, each with tests.
3. After merge + @taOSmd's bus-auth: coordinate the shared-Pi deploy (config `registry_url` + service token + restart).

## Out of scope

- Full RBAC / capability-based authz (the existing caps system stays for shortcuts; revisit if finer granularity is needed).
- Retrofitting every store in one epic (explicitly deferred to incremental follow-ups).
- Token expiry/refresh (revocation-list is the chosen model; documented limitation stands).
