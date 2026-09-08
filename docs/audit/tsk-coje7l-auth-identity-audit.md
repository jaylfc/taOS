# Audit: auth-identity Security + Correctness Pass

**Generated:** 2026-09-02
**Task:** tsk-coje7l — AUDIT [auth-identity]: deep read-only security+correctness pass
**Method:** Static review of `tinyagentos/auth.py`, `tinyagentos/auth_context.py`,
`tinyagentos/auth_middleware.py`, `tinyagentos/middleware/csrf.py`,
`tinyagentos/auth_requests_store.py`, `tinyagentos/agent_tokens_store.py`,
`tinyagentos/agent_token_auth.py`, `tinyagentos/agent_registry_store.py`,
`tinyagentos/agent_grants_store.py`, `tinyagentos/native_agent_identity.py`,
`tinyagentos/device_store.py`, `tinyagentos/device_auth.py`,
`tinyagentos/device_pair_requests_store.py`, `tinyagentos/agent_model_key_store.py`,
`tinyagentos/bridge_session.py`, `tinyagentos/issued_cookies.py`,
`tinyagentos/deployer.py`, and the matching route modules and tests. Read-only:
**no source behavior was changed**. Baseline verified green: 376 tests across the
auth-identity surface pass (`tests/test_auth.py`,
`tests/test_auth_middleware.py`, `tests/test_agent_token_auth.py`,
`tests/test_auth_local_token_bindings.py`, `tests/test_auth_pin.py`,
`tests/test_auth_pin_routes.py`, `tests/test_auth_requests.py`,
`tests/test_auth_store_durability.py`, `tests/test_device_auth.py`,
`tests/test_consent_actions_scopes.py`, `tests/test_agent_internal_mint.py`,
`tests/test_agent_registry_store.py`, `tests/test_proxy_cookie_isolation.py`,
`tests/test_csrf.py`).

> This is not a green-field review. The system already incorporates fixes for the
> incidents catalogued in its own code comments (#2081 stale-cookie CSRF lockout,
> the 2026-08-21 truncated-store account-takeover, the clone-time handle
> UNIQUE collision, the multi-project #1862 claim-pin removal, the #2202
> agent-as-model `stream` coercion, the #1977 raw-`fetch`-without-CSRF class).
> Most of the items below are therefore defense-in-depth or latent risks, not
> live exploitable holes against the current default configuration.

---

## 1. Trust model (as built)

| Principal | Credential | Where it is checked | Lifespan |
|---|---|---|---|
| Human user | argon2id password (legacy SHA-256 upgraded in place) | `AuthManager.check_password` → `/auth/login`, `/auth/complete`, `/auth/setup` | Persistent; sessions minted are the bearer |
| Human user | 4–12 digit PIN | `AuthManager.check_pin`, gated by `is_console_origin` | Persistent; long-lived session minted |
| Human session | `taos_session` cookie = `secrets.token_urlsafe(32)` (256 bit) | `AuthMiddleware` (+ UA hash binding) | 7 d / 30 d (`long_lived`) |
| Script / CLI | `.auth_local_token` (256 bit, 0600) | `AuthMiddleware` Bearer check | Persistent; never auto-rotated |
| Per-agent local token | SHA-256(token) → agent_name in `.auth_local_token_bindings.json` (0600) | `AuthMiddleware` Bearer check | Persistent; never auto-rotated |
| Agent (registry JWT) | Ed25519-signed compact JWT (`{header}.{payload}.{sig}`), `sub`=canonical_id, `iat` | `agent_token_auth._verify_agent_scope` + per-route grant check | No `exp`; rotation via `token_min_iat` |
| Agent-as-a-model caller | consent key `sk-taosagent-...` = SHA-256(token) in DB | `agent_model_key_store.resolve` | Optional expiry |
| Paired device | `taosdev_` + 256-bit scoped token | `device_auth.require_device` | Persistent; revoked/blocked via `device_store` |

Authorization is grant-gated, not claim-gated: a registry JWT authenticates
**who** the caller is; **what** it may do derives from an active row in
`agent_grants` (`canonical_id, scope, project_id, expires_at`). The JWT's own
`project_id` claim is advisory only (taOS #1862) and is intentionally NOT used
for authorization.

---

## 2. Executive risk summary

| Area | Rating | Notes |
|---|---|---|
| Password / PIN storage | Strong | argon2id; SHA-256 legacy upgrade is atomic under `_hash_upgrade_lock`; PIN console-only with forwarding-header denylist. |
| Session tokens | Strong | 256-bit; UA-bound; constant-time compare; 0600 sessions file; expired entries pruned on read/write. |
| Agent registry JWT | Strong | Ed25519, install-pinned pubkey, `token_min_iat` rotation, no alg negotiation (direct verify). |
| Middleware allowlist | Strong | Closed `(method, regex)` allowlist; method-sensitive; route-layer re-checks grant + project binding. |
| CSRF | Strong | Double-submit + credential-path + stale-cookie exemptions; `test_csrf_login_lockout.py` holds the direction. |
| Cookie isolation | Strong | AST drift guard (`test_proxy_cookie_isolation.py`) asserts the shared deny-list covers every `set_cookie` call. |
| Credentials at rest | Medium | Several bearer tokens persisted in cleartext SQLite/JSON. See Finding 1. |
| Token granularity | Low | No per-token revocation for registry JWTs (rotation is identity-wide). See Finding 2. |
| Dead code | Low | An orphaned credential store still compiles. See Finding 4. |

---

## 3. Findings

### Finding 1 — Agent JWTs and device tokens persisted in cleartext at rest
**Severity:** Medium. **Surface:** registry JWT, device bearer token.
**Locations:** `tinyagentos/auth_requests_store.py:37` (`token TEXT`),
`tinyagentos/routes/agent_auth_requests.py:328,551,694` (writes the issued JWT
into `auth_requests.token`), `tinyagentos/device_store.py:33`
(`scoped_token TEXT`), `tinyagentos/auth.py:1113,1162` (native-agent + local
token files are 0600).

The long-lived registry JWT issued on consent approval is stored **in
cleartext** in `auth_requests.token` so the polling external agent can retrieve
it once. A host compromise (or exfiltration of the `data/` SQLite store)
recovers live, long-lived agent credentials that have **no per-token expiry**
and are only invalidated by deactivating the identity or bumping
`token_min_iat` (identity-wide). The device `scoped_token` is the same class of
bearer credential in cleartext in the devices DB.

Compare with the deliberate contrast the authors drew for the
Agent-as-a-Model consent keys (`agent_model_key_store.py:11-15`): "the bearer
token IS the consent grant... only its SHA-256 hash is stored, so a database
leak never exposes live keys. This is deliberately stricter than the internal
LiteLLMKeyStore." The registry JWT path does not apply that same safeguard.

**Nuance:** cleartext persistence is *required* by the poll-delivery model —
the agent has only the opaque `request_id` and must receive the plaintext token
once. So this is a threat-model gap rather than an accident: the design trades
storage secrecy for retrieval simplicity.

**Recommendation:** deliver the token through a single-use capability that
returns it and then **zeros** it from the row (the `auth_requests` table does
not need the token afterwards for normal operation — `token_claimed` is the
analogue already used for device pair tokens), or persist the JWT encrypted at
rest using the existing Fernet surface (`tinyagentos/secrets.py`). Either
collapses the blast radius of a DB leak without changing the wire contract.

---

### Finding 2 — No per-token revocation; rotation is identity-wide
**Severity:** Low. **Locations:** `agent_registry_store.py:325-372`
(`mint_registry_token`), `agent_registry_store.py:980-997`
(`bump_token_min_iat`), `agent_token_auth.py:115-119`.

A registry JWT carries a `jti` but `jti` is never checked for replay or
membership in any revocation set. The only revocation lever is
`bump_token_min_iat`, which sets a per-identity floor such that **every** token
minted before that timestamp is rejected. There is no way to revoke a single
compromised token without invalidating all of an identity's tokens, and the
floor uses `MAX(token_min_iat, ts)` so it can only move forward (fail-closed by
construction — good).

This is an accepted design (documented in `native_agent_identity.py` property 4:
"Per-identity token rotation is achieved by bumping `token_min_iat`"). It simply
widens the blast radius of a leaked agent token to "all tokens for that
identity, until an admin rotates."

**Recommendation:** no code change required to ship, but the threat-model doc
should state the per-token window explicitly, and a follow-up should consider a
short `exp` on minted JWTs plus a refresh path for long-lived agents — or a
small token-id revocation table for incident response.

---

### Finding 3 — `verify_registry_token` does not validate the `iss` claim
**Severity:** Low. **Location:** `tinyagentos/agent_registry_store.py:375-399`.

The verifier checks only signature + non-empty `sub`. `iss` is minted as
`"taos-registry"` (`agent_registry_store.py:359`) but is never asserted on
verification. `sub` is integrity-protected by the signature, so it cannot be
forged, but `iss` provides a free binding that costs nothing to check and would
harden isolation if the Ed25519 keypair were ever shared with another issuer or
scoped to a tenant.

**Not currently exploitable** because the public key is install-pinned and never
transmitted for verification of a *different* issuer — but the absence is exactly
the kind of drift a future multi-tenant or key-rotation change would turn into a
confusion bug. Add `payload.get("iss") == "taos-registry"` to the verify path.

---

### Finding 4 — Orphaned credential store module (`AgentTokensStore`)
**Severity:** Low / maintainability. **Location:** `tinyagentos/agent_tokens_store.py`
(whole file).

`AgentTokensStore` is defined, has a partial-unique-index one-active-token-per-agent
schema, and is covered by `tests/test_agent_tokens_store.py` — but it is
**never imported by any route or wired into `app.state`** (confirmed: no
reference outside its own module and its test). It is a leftover from an earlier
per-agent token design, superceded by the registry JWT + consent-key +
local-token-binding model.

Risk: a maintainer reading this file could believe per-agent tokens are persisted
here (they are not; the live path is `agent_registry_store` +
`auth.py:bind_local_token_agent`/`mint_agent_local_token`), or could wire it up
by mistake. Dead code also means its tests assert behavior of an unreachable
path. The store-wiring gate only catches *new* stores added by a PR; this one
predates the guard.

**Recommendation:** remove the module and its test, or document `DEPRECATED`
at the top in bold if removal is deferred.

---

### Finding 5 — Legacy `.auth_password` SHA-256→argon2 upgrade is non-atomic and unlocked
**Severity:** Low. **Location:** `tinyagentos/auth.py:925-934`.

In `check_password`, the legacy single-file fallback upgrades the stored hash on
a successful SHA-256 verification with a bare, unlocked
`self._password_file.write_text(new_hash)` — whereas `set_password`
(`auth.py:747`) uses `atomic_write_text(..., mode=0o600)`. The read-modify-write
is also outside `_users_lock` (which guards the *user* store, not the legacy
file, so that is consistent). Two concurrent logins by the legacy path that both
verify the same old hash can interleave the write and truncate the file,
leaving the install locked out — and because the legacy path is reached only when
`is_configured()` is based on `.auth_password` (very old installs), this is a
narrow blast radius.

**Recommendation:** route the legacy upgrade through `atomic_write_text` and
guard it with a dedicated file lock (mirror the `_hash_upgrade_lock` pattern).

---

### Finding 6 — Redundant broad exception in `validate_session`
**Severity:** Informational. **Location:** `tinyagentos/auth.py:1040-1043`.

```python
try:
    del self._sessions[token]
except (KeyError, Exception):
    pass
```

`Exception` already subsumes `KeyError`, so the tuple is redundant and signals
confusion about intent. The `try` exists to swallow a concurrent deletion
(legitimate; the entry can be evicted between the `__getitem__` and the `del`),
and it does so — but readers may misread it as two distinct recovery paths.

**Recommendation:** `except KeyError: pass` (a concurrent `del` is the only
plausible failure here).

---

### Finding 7 — Latent: grant `expires_at` feed filter relies on lexicographic string comparison
**Severity:** Low (latent). **Locations:** `agent_grants_store.py:197-212`
(`list_active_grants`, polled by @taOSmd), `agent_token_auth.py:48-66`
(`_grant_unexpired`).

`list_active_grants` filters live grants with SQL
`expires_at IS NULL OR expires_at > ?` against
`datetime.now(timezone.utc).isoformat()`. That is sound
**only** if every stored `expires_at` is canonical UTC ISO-8601 with offset
`+00:00`. `add_grant` (agent_grants_store.py:133) stores the caller-supplied
`expires_at` **as-is**, with no normalization, while the Agent-as-a-model store
explicitly canonicalizes via `_normalize_ts`. The per-request authz path
(`_grant_unexpired`) parses back to a datetime, so it is robust; the **feed**
path is not.

Phase 1 always stores `expires_at IS NULL` (the `once` tier has no expiry — see
`agent_grants_store.py:7` and every call site in
`agent_auth_requests.py:639`), so the window is currently inert: the filter
returns all grants and nothing is mis-ranked. The defect is real the moment a
Phase 2 expiry in a non-canonical form (a `Z` suffix, a local offset, or a naive
timestamp) is written, and the feed is security-relevant because @taOSmd
consumes it for enforcement.

**Recommendation:** canonicalize `expires_at` on write in `add_grant` (reuse the
`_normalize_ts` pattern), making the string comparison sound by construction and
removing the parse-vs-string-format split between the two code paths.

---

### Finding 8 — Device "silent re-pair" control is defense-in-depth only
**Severity:** Informational. **Location:** `tinyagentos/device_store.py:156-173`
(`find_blocked_by_push_token`), `tinyagentos/routes/devices.py`.

A blocked device can be silently re-paired if its blocker re-registers with a
*different* push token: `find_blocked_by_push_token` only matches the same
`push_token`, and `push_token` is client-supplied at `register`. The route still
requires the owner's auth, so this is not a standalone bypass — but it is a
second-factor dependence: the blocked state is only as strong as "the owner's
auth gate never weakens." The code documents this honestly (`device_store.py:160-166`).

**Recommendation:** none required; this is correctly labeled defense-in-depth.
Noting it so the gate remains intentional if pairing ever gains a token-only
path.

---

## 4. Correctness notes (no security impact, verified correct)

- **Closed over-matching on sessions:** `_PersistentSessions` reads-prunes-writes
  on every mutation under `_lock`; `AuthManager` mutators are serialized by
  `_users_lock` (re-entrant via `_serialized`), so the rename-while-inviting
  lost-update race described in its docstring is closed.
- **`check_password` username-vs-email lookup does not leak which field failed**:
  both branches return the identical `(False, None)`, and the invite-code path
  uses `secrets.compare_digest` (auth.py:922).
- **`set_decision` / `device_pair_requests.set_decision` use a conditional
  `UPDATE ... WHERE status='pending'`** so concurrent approve/deny cannot both
  commit; `cur.rowcount == 0` is the lost-update signal, returned as `None` for
  the caller to map to a 409.
- **Handle impersonation is fail-closed at consent approve**: a slug-normalized
  lookup (`get_by_handle_normalised`) catches the `@taOSmd-dev` vs `taosmd-dev`
  spelling split, and any normalised match whose `origin` is not
  `external-selfjoin` is rejected with 409 before minting — so an external
  requester cannot claim an internal driver agent's handle.
- **Consent scope narrowing**: granted scopes must be a subset of requested and
  of `VALID_SCOPES` (agent_auth_requests.py:413-423); project-scoped grants
  require an explicit `project_id` from the operator/invite, never the
  agent-supplied `effective_project` fallback (agent_auth_requests.py:441-447,
  535). `set(_ALLOWED_SCOPES) == set(VALID_SCOPES)` is asserted by
  `tests/test_agent_internal_mint.py:24`.
- **`bump_token_min_iat` is monotonic** via `MAX(token_min_iat, ?)` (agent_registry_store.py:993),
  so rotation can only raise the floor — a downgrade attempt is a no-op.
- **`is_console_origin` fails closed** by the *presence* of any forwarding
  header, never by parsing them (auth.py:255-279, with the fail-closed branch at
  line 274); paired with the PIN limiter keyed by user id
  (not the shared loopback address), a flooded-username attack cannot reset the
  escalation tier.

## 5. Test-coverage assessment

- **Well covered:** session create/validate/revoke (`test_auth.py`,
  `test_auth_store_durability.py`), the middleware allowlist/denylist per path
  and per method (`test_auth_middleware.py` — incl. near-miss and extra-segment
  refusals), PIN origin-gating + throttling (`test_auth_pin.py`,
  `test_auth_pin_routes.py`), consent scope narrowing + project binding
  (`test_consent_actions_scopes.py`, `test_routes_agent_auth_requests.py`), the
  project-scoped JWT check (`test_agent_token_auth.py`), and the cross-proxy
  cookie deny-list drift guard (`test_proxy_cookie_isolation.py`).
- **Weakly covered / not asserted at the unit boundary:**
  1. The plaintext-token-at-rest property (Finding 1) has **no** test stating
     "the issued JWT must not be persisted in cleartext" — the contract that
     exists is "the token is retrievable by the poller," which is compatible
     with either cleartext or encrypted-at-rest delivery. A property test on
     `auth_requests` rows would lock the desired direction.
  2. `iss` non-validation (Finding 3) is untested — a minted-but-`iss`-stripped
     token still passes `verify_registry_token`. A one-line test would pin
     reject-on-wrong-issuer once the check is added.
  3. `AgentTokensStore` (Finding 4) is tested in isolation but the test suite
     does **not** assert the store is wired into the app — that is how the
     orphan went unnoticed. A store-wiring assertion (the gate only covers *new*
     stores) would catch future orphans.
  4. Grant `expires_at` non-canonical form feeding `list_active_grants`
     (Finding 7) is untested; the existing expiry test
     (`test_agent_token_auth.py::test_403_for_expired_grant`) exercises the
     parsed per-request path, not the string-comparison feed path.

## 6. Recommendations (prioritized)

1. **(Medium, Finding 1)** Stop persisting the consent-issued registry JWT in
   cleartext: either zero the token from `auth_requests` after first poll (the
   single-use `token_claimed` pattern from device pairing already exists as a
   template) or store it encrypted via `tinyagentos/secrets.py`. Apply the same
   to `devices.scoped_token`.
2. **(Low, Finding 3)** Assert `iss == "taos-registry"` in `verify_registry_token`.
3. **(Low, Finding 4)** Delete `agent_tokens_store.py` and its test, or mark it
   explicitly deprecated; add a store-wiring assertion to the gate so orphans
   cannot recur.
4. **(Low, Finding 5)** Route the legacy `.auth_password` upgrade through
   `atomic_write_text` + `_hash_upgrade_lock`.
5. **(Low, Finding 6)** Narrow the redundant `except (KeyError, Exception)`.
6. **(Latent, Finding 7)** Canonicalize `expires_at` on write in
   `AgentGrantsStore.add_grant` so the @taOSmd feed filter is sound by
   construction.
7. **(Optional, Finding 2)** Document the per-token revocation window in the agent
   auth threat model; consider a short `exp` + refresh for long-lived agents.

## 7. Verdict

The auth-identity subsystem is **mature and well-defended** against the
threats its authors explicitly modeled: account-takeover via a truncated store,
PIN brute force over a reverse proxy, registry JWT substitution/alg-confusion,
agent handle impersonation, consent scope widening, CSRF on stale sessions, and
cross-origin credential relay all have dedicated, tested controls. The findings
above are **defense-in-depth hardening** (cleartext at-rest credentials,
identity-wide token rotation, a missing `iss` check, and one orphaned module)
rather than bypassable vulnerabilities in the default configuration. Recommended
follow-ups 1–3 are the highest-leverage; none are required for the system to be
secure as currently deployed.
