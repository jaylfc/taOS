# External Coding-Agent Onboarding (self-join identity + project-scoped memory)

**Status:** Draft, direction approved (Jay 2026-06-10). Cross-repo contract v1 locked with @taOSmd (integration thread, taosmd side enforces). Tracking issue: #744. Extends the Trust & Comms consent loop (`docs/superpowers/specs/2026-06-09-trust-comms-layer-design.md`, shipped via #719/#731/#733).
**Owners:** taOS leads registry/auth/approval-UI; taOSmd owns the bus + memory enforcement (GrantsVerifier).
**Implementing code (today):** `tinyagentos/routes/agent_auth_requests.py`, `tinyagentos/agent_registry_store.py`, `tinyagentos/agent_grants_store.py`, `tinyagentos/routes/agent_registry.py`, `desktop/src/components/ConsentNotification.tsx`.

## Why

When @taOS is rate-limited, work continues on another coding agent (Cursor, Codex, a fresh Claude Code session, etc.). Those agents need scoped access to the project's memory and the A2A bus, granted the same way framework agents are: a user-mediated consent request, not blind self-registration. This reuses the consent loop already in production; it adds project/shelf binding and a stable identity so an agent's memory accrues across sessions.

## Desired flow (Jay)

1. The agent reads `docs/AGENT_HANDOFF.md`.
2. The agent requests access to the taOS server. It auto-derives `project_id` from the git remote (the taosmd #141-144 git-remote fingerprint).
3. Jay gets the in-taOS consent notification with options.
4. Jay verifies the project/shelf matches, then approves, with the option to **rename** the shelf or **reattach** to an existing one.
5. The agent now has scoped project-memory + A2A access under a minted, stable `canonical_id`.

## Decision: identity is STABLE per (tool, project)

A coding agent's identity is **not** per-session. It is stable per (tool, project) so its memory accrues over time. The minted `canonical_id` is recorded back so a re-spawned agent reuses it instead of re-requesting. Mechanism:
- On first approval, mint `canonical_id` and return it on the status poll (already the case).
- The agent records its `canonical_id` (and the issued token, short-lived; re-requestable) locally / in the handoff bootstrap, keyed by project. A re-spawn presents the existing token; if expired, it re-requests and Jay sees "re-auth of <known agent>", not a new identity.
- **Reattach** is the human override that keeps identity stable while changing what shelf it points at (e.g. the repo's remote changed, or two clones should share one shelf).

## Contract v1 (agreed with @taOSmd)

Token + grants feed:
- `project_id` (git-remote fingerprint, taosmd #141-144 format) is a **top-level claim** in the minted EdDSA JWT.
- Grants-feed rows carry it: `GET /api/agents/registry/grants` returns `{canonical_id, project_id, scope, expires_at}`.

Enforcement (taOSmd side):
- GrantsVerifier matches `(canonical_id, project_id)`, not just `canonical_id`.
- `/search` + `/ingest` from a registry-authed agent bind `project=` from the **verified claim** and ignore any body value (same anti-spoof rule as the #710 user_id decision).
- A2A v1 gates `/a2a/send` on any active grant; per-channel grants are a later iteration.

Approval + rename/reattach (append-only):
- The **granted** `project_id` is the admin-confirmed value at approval, not blindly the requested one. The request carries the git-derived `project_id` as the default; the rename/reattach UI lets the admin override; the minted grant uses the confirmed value (mirrors granted-subset-of-requested).
- Reattach never mutates an existing grant. It **supersedes**: revoke/expire the old grant row + mint a new `(canonical_id, project_id, scope)` row. GrantsVerifier's `(canonical_id, project_id)` match makes the old row stop matching automatically. The audit trail stays append-only.

## Compatibility constraint: numeric-epoch `expires_at`

taOSmd's `has_grant` (post-merge fix `fcc0fd7`) **fails closed on a non-numeric `expires_at`**. `agent_grants_store.py` currently stores `expires_at` as TEXT, null-only in Phase 1 (null = no expiry = active, which is fine). When real expiry lands, the grants feed must emit **numeric epoch seconds** (or null), never an ISO string, or taOSmd will reject the grant. Also fix the local `WHERE expires_at > ?` comparison (`agent_grants_store.py:113`) to compare numerically.

## taOS-side task breakdown

1. `mint_registry_token` (`agent_registry_store.py`): add `project_id` as a top-level claim.
2. `agent_grants_store.py`: `project_id` column already exists; switch `expires_at` to numeric epoch (or keep null), fix the `> ?` comparison, ensure `add_grant` records the confirmed `project_id`.
3. `GET /api/agents/registry/grants` (`agent_registry.py`): include `project_id` in feed rows (verify it already does; the docstring lists it).
4. `ApproveBody` (`agent_auth_requests.py`): add optional `project_id` override (default = the request's). `_do_approve` uses the confirmed value; **reattach = supersede** (revoke old grant for that canonical_id + mint a new row), never an in-place update.
5. `ConsentNotification.tsx`: resolve `project_id` to a readable shelf name, show "matches existing shelf X" vs "new shelf", add a rename/reattach control. Use the `frontend-design` (impeccable) skill, offline-friendly.
6. Agent-side request helper: a documented one-liner / small CLI that derives `project_id` from `git config --get remote.origin.url` and POSTs the auth-request; add it as a step in the handoff bootstrap, and have it record the returned `canonical_id` for reuse.
7. Flip `TAOSMD_REGISTRY_URL` on (activates dormant enforcement) once 1-6 land and are verified end-to-end against the live serve.

## Security notes

- Self-join agents are born `pending`; approval is the only gate (unchanged).
- The granted `project_id` and scopes are both admin-confirmed at approval; an agent cannot widen scope (#733) nor silently claim a shelf (reattach is admin-only).
- Anti-spoof: the enforced `project_id`/`user_id` come from the verified token claim, never the request body.
- Reattach is append-only, so the governance audit log (#730) shows every shelf change.

## Phase 1 shipped: bus read with a registry JWT

The first enforcement slice is live on the controller. An agent reads the
read-only A2A bus proxy with its OWN registry identity, never the owner
password:

- Endpoints: `GET /api/agents/registry/grants` + `/revoked` (scope
  `registry_feeds_read`) and `GET /api/a2a/bus/channels` + `/api/a2a/bus/messages`
  (scope `a2a_receive`). These are the only paths that accept a registry JWT in
  place of the admin session; the Bearer allowlist is exact (no skeleton key).
- The agent presents `Authorization: Bearer <registry-jwt>`. The route verifies
  the Ed25519 signature against the registry public key, requires the agent be
  `active`, and requires an active (non-expired) grant for the scope. Malformed
  or wrong-key tokens get 401; valid-but-unauthorized get 403. Fail closed.
- Token storage: the operator stores the minted JWT on the agent host in a
  gitignored per-host file (for example `~/.config/taos/agent-token`); the agent
  reads it from there. Tokens are never written into the repo.
- Minting the four internal driver agents (@taOS-dev, @taOS-website-dev,
  @taOSmd-dev, @Hermes), idempotently by handle:
  `taosctl agents seed-internal` (or `taosctl agents mint --handle @X --slug x
  --scopes a2a_send,a2a_receive`). Re-running reuses the existing canonical id
  and re-asserts the grants.

## Open questions

- Default scope set for a coding agent: `memory_read` + `memory_write` + `a2a_send` + `a2a_receive`; `files_*` / `tools_execute` gated tighter (probably off by default).
- Token lifetime for a stable identity: short-lived token, re-requested on expiry (re-auth of a known canonical_id), vs a longer-lived token with revocation-feed coverage. Lean short-lived + re-auth.
- Per-channel A2A grants (v2): gate `taos-progress` vs `general` separately, or keep the v1 "any active grant" gate.
