# External-Agent Invite Flow — Phase 1 Implementation Plan

> **For the fleet builders:** each slice below is an independent, PR-able unit. Read the full design at `docs/design/external-agent-project-invite.md` (its "Approved-build addendum" governs Phase 1 where it conflicts with the draft). Implement TDD: write the failing test first, make it pass, commit. Run tests synchronously in the foreground with `/Volumes/NVMe/Users/jay/Development/tinyagentos/.venv/bin/python -m pytest <files> -q`. Then push + open a PR to `dev`.

**Goal:** Let an admin mint a single-use, PIN-protected invite from a project's Members panel that a remote coding agent (Claude Code, grok, kilo, ...) redeems to join the project with a scoped registry identity — and receive an onboarding kit that makes it a self-driving project member.

**Architecture:** A new `project_invites` store + routes sit IN FRONT OF the existing consent machinery. Redemption creates a real auth-request and drives it through the existing `_do_approve` (extracted into a reusable helper), so registry minting, grants, membership, and the a2a channel are all unchanged and auditable. Phase 1 is LAN/tailnet only (controller-direct URL); the taos.my relay is a later phase.

**Tech stack:** FastAPI + aiosqlite (backend), React/TS (Members UI), `secrets`/`hmac`/`sha256` (PIN), the existing registry EdDSA JWT + grants + a2a bus.

## Global Constraints (verbatim)
- Git identity `jaylfc <jaylfc25@gmail.com>`. NEVER add Co-Authored-By / "Generated with" / any AI attribution to commits or PRs. NEVER use an em dash in commits, PRs, comments, or code strings.
- `project_tasks` bound to the invite's `project_id` is ALWAYS in the granted scopes (force-included at redeem), so a successful redeem always yields project membership via `_do_approve`.
- PIN never stored raw: `sha256(pin)` only, compared with `hmac.compare_digest`. Invite TTL 15 min; 5 wrong-PIN attempts invalidate; single-use (atomic pending->redeemed, concurrent loser 409); per-IP rate limit on redeem (20 / 10s); 10 pending invites/project cap.
- The redeem endpoint and `GET /i/{invite_id}` are auth-EXEMPT (the PIN is proof of possession); every other new route is admin/session or agent-JWT gated. No skeleton key.
- Bundle carries NO secrets (the token arrives via the status poll).
- Reference implementation to mirror for the store + PIN + rate-limit: `tinyagentos/cluster/pairing_store.py` + `tinyagentos/routes/cluster.py`.

---

## File structure

- Create `tinyagentos/projects/invite_store.py` — the `project_invites` store (S1).
- Create `tinyagentos/routes/project_invites.py` — mint/list/revoke/redeem/`/i/{id}` routes + bundle + onboarding-kit builder (S1, S2).
- Modify `tinyagentos/routes/agent_auth_requests.py` — extract `_do_approve` body into `approve_request_record(...)` helper (S2).
- Modify `tinyagentos/auth_middleware.py` — add redeem + `/i/{id}` to `_is_exempt`; add the a2a stream path to the read allowlist (S2, S3).
- Modify `tinyagentos/routes/a2a_bus.py` — add `GET /api/a2a/bus/stream` proxy + `since` cursor on the messages proxy (S3).
- Modify `tinyagentos/app.py` — instantiate + `init()` the invite store; register the routers.
- Modify `desktop/src/apps/ProjectsApp/ProjectMembers.tsx` (+ a new `InviteAgentDialog.tsx`) — the Invite UI (S4).
- Tests: `tests/projects/test_invite_store.py`, `tests/test_routes_project_invites.py`, `tests/test_routes_a2a_bus_stream.py`, `desktop/src/apps/ProjectsApp/__tests__/InviteAgentDialog.test.tsx`.

---

## Slice S1 — invite store + mint/list/revoke routes

**Files:** create `tinyagentos/projects/invite_store.py`, `tinyagentos/routes/project_invites.py`; modify `tinyagentos/app.py`; test `tests/projects/test_invite_store.py`, `tests/test_routes_project_invites.py`.

**Interfaces produced (later slices rely on these exact names):**
- `class ProjectInviteStore(BaseStore)` with:
  - `async def mint(self, *, project_id, scopes: list[str], approval_mode: str, check_interval_secs: int, created_by: str) -> dict` — generates a 6-digit `invite_id` + 4-digit PIN, stores `sha256(pin)`, `expires_ts=now+900`, `status="pending"`; force-includes `project_tasks` in `scopes`; enforces the 10-pending cap (raise `InvitePendingCapError`); RETURNS the record plus the raw `pin` (only time it exists) as `{"record": {...}, "pin": "4821"}`.
  - `async def get(self, invite_id) -> dict | None` (sweeps expired->status on read).
  - `async def list_for_project(self, project_id) -> list[dict]` (never returns pin_hash).
  - `async def revoke(self, invite_id) -> bool` (status=revoked).
  - `async def redeem(self, invite_id, pin) -> dict` — checks TTL/attempts/status, `hmac.compare_digest(sha256(pin), pin_hash)`; on wrong pin increments `redeem_attempts` (>=5 -> invalidate) and raises `InvitePinError`; on success ATOMICALLY flips pending->redeemed in one UPDATE guarded by `status='pending'` (0 rows affected -> raise `InviteAlreadyRedeemedError`), returns the record. (Used by S2.)
- Exceptions: `InvitePinError`, `InviteExpiredError`, `InviteAlreadyRedeemedError`, `InvitePendingCapError`, `InviteRevokedError`.
- SCHEMA: the `project_invites` table exactly per the design doc's section 1 record. Put any index that references a migration-added column in `_post_init`, NEVER in SCHEMA (see `feedback_existing_db_upgrade_test` — this bug bricked boot 3x).

**Routes (`project_invites.py`, admin/session-gated via `current_user` + `require_owner_or_admin` on the project):**
- `POST /api/projects/{project_id}/invites` body `{scopes: list[str], approval_mode?: "auto"|"manual", check_interval_secs?: int}` -> `{invite_id, pin, expires_ts, scopes, approval_mode, check_interval_secs}` (pin returned ONCE).
- `GET /api/projects/{project_id}/invites` -> `[{invite_id, scopes, status, expires_ts, redeemed_by}]` (no pin_hash).
- `DELETE /api/projects/{project_id}/invites/{invite_id}` -> 204.

**Acceptance / tests:** mint returns a 6-digit id + 4-digit pin; `project_tasks` always present even if omitted; 11th pending invite -> 429; redeem with wrong pin increments attempts and 5th invalidates; correct pin single-uses (second redeem -> `InviteAlreadyRedeemedError`); expired invite (monkeypatch time or seed `expires_ts` in past) -> `InviteExpiredError`; revoke flips status; list omits pin_hash. Boot-migration test: seed a project_invites table WITHOUT a later-added column and assert `init()` boots (guard against the SCHEMA-before-migration brick). Run `pytest tests/projects/test_invite_store.py tests/test_routes_project_invites.py -q`.

---

## Slice S2 — redeem + `_do_approve` extraction + bundle + onboarding kit

**Files:** modify `tinyagentos/routes/agent_auth_requests.py`, `tinyagentos/auth_middleware.py`, `tinyagentos/routes/project_invites.py`; test `tests/test_routes_project_invites.py` (extend).

**Step A — extract the approval helper (no behavior change to the existing route):**
In `agent_auth_requests.py`, move the body of `_do_approve` into
`async def approve_request_record(request, *, record, granted_scopes, effective_project, decided_by, origin="consent") -> dict` that registers the agent, mints the token, writes grants + relationships + membership + a2a sync, and records the decision. The existing `_do_approve` route handler calls it with `decided_by=user.user_id`. Existing tests in `tests/test_routes_agent_auth_requests.py` must still pass unchanged.

**Step B — redeem route (auth-EXEMPT):**
`POST /api/projects/invites/redeem` body `{invite_id, pin, harness, label?}`:
1. `store.redeem(invite_id, pin)` (S1) — pin/ttl/single-use.
2. Derive handle `{project_slug}-{harness}[-{label}]`, slugified; dedup against ACTIVE registry agents holding that handle in this project by appending `-2`, `-3`, ... (reuse the registry active-handle check).
3. Build a `CreateAuthRequest`-shaped record with `framework=harness`, `identity_claim=<handle>`, `requested_scopes=<invite scopes>`, `project_id=<invite project>`, `origin="invite:<invite_id>"`.
4. If `approval_mode=="auto"`: call `approve_request_record(..., decided_by=invite.created_by, origin="invite:<id>")`. If `"manual"`: create the pending auth-request (existing `create_auth_request` path) so the consent bell fires.
5. Return `build_connection_bundle(...)` + `{request_id, agent_handle, poll_path}`.
Add `POST /api/projects/invites/redeem` and `GET /i/{invite_id}` to `auth_middleware._is_exempt` (method-sensitive, exactly like `/api/cluster/pairing/claim`). Add the per-IP rate limit (reuse the pairing `_manual_claim_rate_ok` pattern).

**Step C — `GET /i/{invite_id}` content-negotiated:** `Accept: application/json` -> `{redeem: {method, path, fields:{invite_id,pin,harness required, label optional}}, project, onboarding}`; browser Accept -> a minimal HTML page explaining what it is. No pin check here (it only advertises the redeem contract).

**Step D — `build_connection_bundle(record, granted_scopes, project, agent_handle, check_interval_secs)`** returns the JSON of design section 4 PLUS the addendum additions:
- `controller.endpoints`: enumerate the controller's non-loopback IPv4 (the `getsockname` trick + interface enumeration) as `lan` priorities, `mesh` from `mesh_status().node_ip` when joined. (No relay in Phase 1.)
- `apis`: task routes + a2a routes ALWAYS; **canvas routes ONLY when granted** — `canvas_read` adds `canvas_elements` GET + `canvas_snapshot`; `canvas_write` adds `canvas_elements` POST + `canvas_element` PATCH/DELETE. Mirror `auth_middleware` canvas allowlist exactly.
- `delivery`: `{stream_path, poll_path, check_interval_secs, cursor:"ts", filter:"mentions+project"}`.
- `onboarding` block + a `guide_markdown` string built from scopes+project+handle: links to `https://github.com/jaylfc/taOS` and `docs/agent-manual/` (04-apps.md, 09-os-control.md), a plain-language Projects+Canvas capability summary scoped to the grants, the A2A authenticated-proxy contract (`/api/a2a/bus/*`, `from` forced to the handle, `thread`/`body`), and explicit imperative instructions to (a) write canonical_id + project + token-file path + bus contract into the agent's OWN memory, and (b) poll every `check_interval_secs` (or hold the SSE stream) for ready tasks + mentions.

**Acceptance / tests:** existing auth-request tests still green (helper extraction). Auto-mode redeem end-to-end: mint -> redeem `{harness:"claude"}` -> poll returns `{status:"accepted", canonical_id, token}`, a member row exists with the derived handle, and (if canvas granted) the bundle `apis` contains the canvas routes; if not granted, it does NOT. Manual-mode redeem leaves the request pending (poll shows pending; consent bell record created). Wrong pin -> 403; redeemed invite second redeem -> 409. Bundle carries no token/secret. `guide_markdown` contains the repo link + the memory-write + timed-check instructions. Run `pytest tests/test_routes_project_invites.py tests/test_routes_agent_auth_requests.py -q`.

---

## Slice S3 — A2A stream proxy + since-cursor

**Files:** modify `tinyagentos/routes/a2a_bus.py`, `tinyagentos/auth_middleware.py`; test `tests/test_routes_a2a_bus_stream.py`.

**Interfaces:** `GET /api/a2a/bus/stream?channel={c}&since={cursor}` — authenticated (agent-JWT or session) SSE proxy to the raw bus `GET {bus}/a2a/stream?thread={c}&since={cursor}`; add the path to `_A2A_BUS_READ_PATHS` in the middleware allowlist, gated exactly like the existing `/api/a2a/bus/messages`. Also add a `since` query passthrough to the existing `/api/a2a/bus/messages` proxy (currently drops it).

**Acceptance / tests:** an agent-JWT bound to a project can open the stream and gets forwarded SSE frames (mock the raw bus); the messages proxy forwards `since`; an unauthenticated request is 401; the raw :7900 bus is never exposed directly. Run `pytest tests/test_routes_a2a_bus_stream.py -q`.

---

## Slice S4 — ProjectMembers "Invite external agent" UI

**Files:** create `desktop/src/apps/ProjectsApp/InviteAgentDialog.tsx`; modify `desktop/src/apps/ProjectsApp/ProjectMembers.tsx`; test `desktop/src/apps/ProjectsApp/__tests__/InviteAgentDialog.test.tsx` (vitest). Typecheck via `tsc --noEmit` (symlink desktop/node_modules if needed). CI spa-build is the integration gate.

**UI:**
- "Invite external agent" button beside the add-member control.
- Mint dialog: scope checkboxes — `project_tasks` shown checked + DISABLED with a "required for project invites" hint; `canvas_read`, `canvas_write` (default checked); an exclusive **Lead** toggle (sets the epic's `lead_member_id` on approve); a "check-in interval" field (presets 5m/15m/30m/1h/6h + free entry, stored as `check_interval_secs`, default 1800); a "require manual approval" toggle (default off). POSTs to `POST /api/projects/{pid}/invites`.
- Result view: the controller-direct URL + the 4-digit PIN rendered large, a copy button for each, and a copyable one-line instruction: "Fetch <URL> and redeem with PIN <PIN>; follow the returned JSON instructions to join the taOS project." PIN shown here only.
- Pending-invites list inside Members: invite_id, scopes, countdown to expiry, state chip, revoke button (`DELETE`). A redeemed agent shows in Members under its derived handle.

**Acceptance / tests:** vitest: minting posts the chosen scopes (project_tasks always present; lead toggled adds the lead grant); result view shows the URL + PIN and the copy instruction; the manual-approval + interval controls submit their values; revoke calls DELETE. `tsc --noEmit` clean. Run `npm --prefix desktop test -- InviteAgentDialog` (or the repo's vitest invocation).

---

## Self-review
- **Spec coverage:** S1 = design §1 (invite artifact) + mint UX backend. S2 = §2 (redemption/Approach C), §2a (naming), §4 (bundle) + addendum canvas + onboarding kit. S3 = §8 (delivery stream). S4 = §6 (UX). taos.my relay (§3 relay leg, §9) is explicitly OUT of Phase 1. Workspace-isolation (design's later section) is OUT of Phase 1 (the invited agent owns its own working copy; taOS-created agents are a separate concern). Covered.
- **Consistency:** `approve_request_record(record, granted_scopes, effective_project, decided_by, origin)` is the single name used by both the route and redeem. `build_connection_bundle(...)` and `ProjectInviteStore.redeem(...)` names are stable across S1/S2. Handle derivation matches design §2a.
- **No placeholders:** every slice names exact files, interfaces, and acceptance tests. Deep field-level detail lives in the design doc, referenced per slice (the builders read it).
