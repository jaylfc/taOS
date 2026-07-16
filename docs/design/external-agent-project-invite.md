# Inviting an external coding agent to a taOS project (link + PIN)

Status: APPROVED 2026-07-16 (Jay). Building Phase 1.
Builds on: the consent loop (`docs/design/external-agent-onboarding.md`, #744),
the `project_tasks` scope (#1774), the member-add-on-approve commit on
`feat/consent-project-picker` (bec4186b), the cluster pairing code
(`tinyagentos/cluster/pairing_store.py`), and the taOSgo mesh-join foundation
(`docs/design/taosgo-mesh-join-foundation.md`).

## Approved-build addendum (2026-07-16)

Jay approved this design with a real driver: a Claude Code agent building a
game should register, create a game project, and join as lead dev. Two
decisions and two additions to the draft below:

- **Phase 1 = LAN / tailnet only.** Mint the controller-local invite and show
  the controller-direct URL (section 3, Approach C, without the taos.my leg).
  The taos.my relay resolver is deferred to a later phase; slices 1-3 are
  fully useful on LAN/tailnet, which is where the driving agent runs.
- **Lead role in the mint dialog.** The scope checkboxes include the exclusive
  project Lead toggle (the epic's `lead_member_id`); ticking it makes the
  redeemed agent the project Lead in one step.

- **ADDITION 1 - canvas endpoints in the bundle.** The `apis` block (section 4)
  currently lists only task + A2A routes. When the invite grants
  `canvas_read` and/or `canvas_write`, the bundle MUST also carry the canvas
  routes so a lead-dev agent can drive the board:
  `canvas_elements: GET/POST /api/projects/{pid}/canvas/elements`,
  `canvas_element: PATCH/DELETE /api/projects/{pid}/canvas/elements/{eid}`,
  `canvas_snapshot: GET /api/projects/{pid}/canvas/snapshot.png`. Include a
  route only when its scope was granted (read routes need `canvas_read`, write
  routes need `canvas_write`), mirroring the middleware allowlist.

- **ADDITION 2 - the onboarding kit (capability guide).** Beyond machine
  endpoints, the redeem response and the `GET /i/{invite_id}` page carry an
  `onboarding` block AND serve a short markdown guide so the agent is aware of
  everything it can do. Contents:
  - **Links:** the taOS repo (`https://github.com/jaylfc/taOS`) and the
    in-repo agent guides (`docs/agent-manual/`, e.g. `04-apps.md`,
    `09-os-control.md`), plus the Projects + Canvas capability docs.
  - **Capabilities in plain language:** the Projects app (tasks, the kanban
    board, claim/comment/close a task) and the Canvas (read/write elements -
    notes, links, text, mermaid, flowchart - and the snapshot), scoped to what
    the invite granted.
  - **A2A contract:** use the AUTHENTICATED proxy (`/api/a2a/bus/*`), never the
    raw :7900 bus; `from` is forced to the agent's own handle; the
    `thread`/`body` message shape; how to reach Jay and other agents.
  - **Memory + timed-check instructions (explicit, imperative):** write your
    `canonical_id`, project id/slug, token file location, and the bus contract
    into your OWN persistent memory now; then poll every `check_interval_secs`
    (and/or hold the SSE stream) for your ready tasks and any mentions, act,
    and report progress on the bus. This is the loop that makes the agent an
    autonomous project member rather than a one-shot.
  The guide is generated from the granted scopes + project + derived handle at
  redeem time (no secrets: the token still arrives via the status poll), so an
  agent that only reads the guide has an accurate, personalized capability map.

The sections below are the full draft; where they conflict with this addendum
(e.g. the taos.my URL leg, the `apis` block omitting canvas), the addendum wins
for Phase 1.

### Addendum 2 (2026-07-16): agent-centric, multi-project model

Jay clarified the target: an external agent is a FIRST-CLASS registry citizen
that can belong to MANY projects, is invited from the Agents app as well as
from a project's Members panel, and is free to pick up jobs from any project it
is a member of and talk on the A2A bus. The free builder + review chain
(hy3/stepflash/nemotron) are shared to projects the same way. Decisions:

- **Project creation timing.** A project invite is bound to a project that
  already exists. "Name a new project" in the mint dialog creates the project
  AT MINT TIME and binds the invite to it; the redeeming agent JOINS it (it
  does not create the project). So by the time the URL + PIN are handed over,
  the project exists.

- **Token model = one agent-identity token, per-project access gated by
  grants (Jay's choice).** This SUPERSEDES the epic's project-bound token for
  external agents: the registry JWT identifies the AGENT (no hard project
  binding as the gate); each request to a project route is authorized by
  looking up an ACTIVE grant + membership for that agent on that project. One
  credential, assign to N projects freely by adding grant + membership rows
  (both already per-project in the data model). A2A identity/handle is global.

- **Two entry points, one identity.**
  1. Project Members -> project invite (register-or-reuse the agent + assign to
     THIS project). [current S4]
  2. Agents app -> agent invite (register the agent; optionally assign to 0+
     projects at mint) + an "Assign to project..." action to add an EXISTING
     registered agent to more projects later (grant + membership, no re-mint,
     no new token). [new S5]

- **Re-onboard is idempotent per agent.** A redeem/assign for an agent that
  already has an identity reuses its canonical_id + token and just adds the
  new project's grant + membership. Only a genuinely new handle mints a new
  identity.

Build impact (supersedes the plan's slicing where noted):
- S1 store: `project_id` is NULLABLE (an Agents-app invite may register an
  agent with no project); the project-scoped invite still sets it.
- NEW S2a (foundational, lands with/before S2): relax the agent-token project
  gate in `auth_middleware` + `check_agent_scope_for_project` so a request is
  allowed when the agent holds an active grant for the requested project, not
  only when the token is bound to it. `mint_registry_token` stops hard-binding
  the token to a single project for external agents. Keep the existence-hiding
  404 for projects the agent has NO grant on.
- S2 redeem: register-or-reuse the agent, then (if a project is bound) add the
  grant + membership via the extracted approve helper; the token is
  agent-identity, not project-locked.
- NEW S5: Agents-app "Invite external agent" + "Assign to project..." UI + the
  assign endpoint (`POST /api/projects/{pid}/members/agents/{canonical_id}` or
  similar: grant + membership for an existing agent).

### Addendum 3 (2026-07-16): delivery is TIMED-CHECK first; SSE is not realtime for agents

Reality check (Jay): the SSE stream does NOT make an agent respond in realtime.
An LLM agent is not a persistent event loop; it only acts when its harness
hands it a turn, so an open stream just buffers frames the agent reads on its
NEXT turn - functionally a poll. This is why every agent (including the ones
running now) only replies after a timed check, never on push. Decisions:

- **The timed check (`check_interval_secs`) is the reliable delivery floor for
  Phase 1.** The onboarding kit LEADS with the poll loop and describes the SSE
  stream as an optional optimization "only if your harness can hold a
  connection and wake on a message." Do not tell an agent it will get realtime
  pushes; it will not, on its own.
- **S3 (the SSE proxy) still ships** - it is the correct pipe, it makes the
  taOS UI (Messages app, a real persistent reactor) genuinely realtime, and it
  is the pipe a future wake-loop consumes. But it is UI/future value, not
  agent-realtime value.
- **True agent realtime = a wake-trigger loop**, designed SEPARATELY as
  #93 (@taOS native A2A loop: wake-trigger + reply) / #99 (in-app auto-reply):
  a small persistent per-agent process that holds the stream (or polls tightly)
  and INVOKES the agent's harness with the new message, i.e. creates a turn.
  For an external CLI agent that is an optional listener wrapper shipped in the
  onboarding kit later. Out of scope for this invite feature.

## Why

Deploying a CLI agent from inside taOS is the easy case: taOS owns the process
and can inject the token, the controller URL, and the project binding. The hard
case is a coding harness or CLI (Claude Code, grok CLI, Codex, opencode, etc.)
already running on a DIFFERENT machine, possibly on a different network. Today
that operator has to hand-assemble the auth-request POST, know the controller's
address, and shuttle the minted token across. The invite flow collapses all of
that into one thing a human can say to their agent:

> "Go to `user.taos.my/482910`, PIN 4821, and join the project."

The invite is a bootstrap artifact. It answers two questions the remote agent
cannot answer on its own: WHERE is the controller (addresses, in fallback
order), and HOW do I become someone there (the consent flow, pre-filled). It
must not become a second identity system: everything after redemption flows
through the existing registry + grants + consent machinery.

Who owns the agent's WORKING COPY also differs between the two cases; the
workspace-isolation section below draws that boundary.

## What already exists (verified in code)

- Consent backbone: `POST /api/agents/auth-requests` (unauthenticated) creates
  a pending request; the admin approves via ConsentActions; `_do_approve`
  registers the agent, mints a scoped EdDSA registry JWT (project_id as a
  top-level claim), writes grants + relationship edges, and (on
  `feat/consent-project-picker`) adds the agent as a project member and syncs
  the project a2a channel. The agent polls the opaque request_id for its token.
  (`tinyagentos/routes/agent_auth_requests.py`)
- Agent-token surface: the middleware allowlists exactly the registry feeds,
  the authenticated A2A bus proxy (`/api/a2a/bus/send`, `/channels`,
  `/messages`), and the anchored `project_tasks` kanban routes. No skeleton
  key. (`tinyagentos/auth_middleware.py`)
- Code-based pairing precedent: cluster pairing stores `sha256(code)` only,
  compares with `hmac.compare_digest`, expires pending entries after 15
  minutes, caps failed attempts at 5, enforces single-use by DELETE-on-claim,
  and rate-limits the unauthenticated claim endpoint per client IP (fixed
  window, 20 per 10s). (`tinyagentos/cluster/pairing_store.py`,
  `tinyagentos/routes/cluster.py`)
- Address knowledge: `controller_callback_host` resolves, in order, an
  operator override, the Tailscale IP (`tailscale ip -4`), then
  `app.state.controller_lan_ip`. `taosnet/mesh.py::mesh_status()` reports
  `{joined, node_ip, tailnet, hostname}`. Workers detect their LAN IP via the
  UDP `getsockname` trick. (`tinyagentos/routes/agent_deploy.py`,
  `tinyagentos/taosnet/mesh.py`, `tinyagentos/worker/agent.py`)
- Off-LAN transport: the taOSgo mesh join persists per-host service
  credentials (`taosnet/mesh_credentials.py`) and joins the account Headscale
  mesh; the taos.my relay terminates TLS and forwards to the controller over
  the tailnet (the `TAOS_TRUST_FORWARDED_PROTO` seam in
  `routes/account_proxy.py` exists for exactly this).
- Bus delivery: the raw taOSmd bus (default `127.0.0.1:7900`) exposes
  `GET /a2a/messages?thread=&limit=` and an SSE stream
  `GET /a2a/stream?thread=&since=`. The controller proxies the read and send
  paths with registry-JWT auth; there is NO stream proxy yet
  (`tinyagentos/routes/a2a_bus.py`), and the messages proxy does not pass a
  `since` cursor. Both are added by this design.

## 1. The invite artifact

An invite is a short-lived, single-use, project-bound record minted by an
admin from the project's Members panel.

Record (new `project_invites` store, SQLite, modeled on
`cluster_pairing_store`):

```
invite_id      TEXT PRIMARY KEY   -- 6 random digits, e.g. "482910" (display id, appears in URLs)
project_id     TEXT NOT NULL
pin_hash       TEXT NOT NULL      -- sha256(pin), never the raw PIN
scopes         TEXT NOT NULL      -- JSON list chosen by the admin at mint time;
                                  -- ALWAYS includes project_tasks (force-included
                                  -- for this project_id even if the admin unchecks it)
approval_mode  TEXT NOT NULL      -- "auto" | "manual" (see section 2)
check_interval_secs INTEGER       -- fallback poll interval for the invited agent
                                  -- (default 1800; admin-overridable at mint time)
created_by     TEXT NOT NULL      -- admin user_id, becomes decided_by on auto-approve
created_ts     REAL NOT NULL
expires_ts     REAL NOT NULL      -- created + TTL (default 15 minutes)
redeem_attempts INTEGER DEFAULT 0 -- wrong-PIN counter, capped at 5
status         TEXT NOT NULL      -- pending | redeemed | expired | revoked
redeemed_by    TEXT               -- identity_claim of the redeemer (audit)
redeemed_request_id TEXT          -- the auth-request the redemption created
```

Security model (deliberately the cluster-pairing model):

- Single use. Redemption atomically flips `pending -> redeemed` in one
  transaction; a concurrent second redeem loses and gets 409. A redeemed
  invite never returns the bundle again.
- TTL 15 minutes, same as `_EXPIRY_SECS` in the pairing store. The flow is
  "click invite, paste into the agent, agent acts within seconds"; a long TTL
  only widens the brute-force window. Expired invites answer 410 and the UI
  offers regenerate.
- PIN handling: 4 digits, generated with `secrets`, stored only as
  `sha256(pin)`, compared with `hmac.compare_digest`. A wrong PIN increments
  `redeem_attempts`; at 5 the invite is invalidated (404 thereafter), exactly
  like `_MAX_ATTEMPTS`.
- Entropy math: the URL carries the 6-digit invite_id (semi-public: it will
  sit in shell history and agent logs), the PIN travels out of band. With a
  5-attempt cap the chance of guessing a 4-digit PIN for a known invite_id is
  0.05 percent, and blind guessing must first hit a live invite_id inside its
  15-minute window. On top: a per-IP fixed-window rate limit on the redeem
  endpoint (reuse the `_manual_claim_rate_ok` pattern, 20 requests per 10s).
- Caps: at most 10 pending invites per project (mirrors `_PENDING_CAP`
  thinking); minting an 11th returns 429.
- Revocation: `DELETE /api/projects/{pid}/invites/{invite_id}` (admin), plus
  the Members panel revoke control. Revoking sets `status=revoked`; the store
  sweeps expired rows lazily on read, like the pairing store does.
- Redemption endpoint is auth-EXEMPT (the PIN is the proof of possession),
  added method-sensitively in `auth_middleware._is_exempt`, exactly the way
  `POST /api/cluster/pairing/claim` is.

Where the link points: see section 3 below (it is one of the two contentious
decisions).

## 2. Redemption semantics: is the PIN the approval?

The contentious core. Three options:

**Approach A: the PIN is full approval.** Redeeming returns the connection
bundle plus a freshly minted registry token in one shot. One step, fastest.
Rejected: the agent never states an identity_claim before the mint, the
consent audit trail (#730) gets a hole, scopes become implicit, and a leaked
link+PIN is a complete credential with no second gate. It also forks a second
minting path beside `_do_approve` that must be kept in lockstep forever.

**Approach B: the PIN only unlocks the bundle.** Redemption returns addresses
and instructions; the agent then submits a normal auth-request; the operator
approves it from the consent notification as today. Maximally safe, zero new
trust machinery. Rejected as the default: it makes the human act twice within
one minute (mint the invite, then approve the request the invite caused),
which is precisely the friction the feature exists to remove.

**Approach C (recommended): pre-authorized consent.** The invite dialog IS the
consent moment. The admin picks the scopes at mint time (same closed
vocabulary, same checkboxes ConsentActions renders today). Redemption then:

1. Verifies invite_id + PIN (attempts, TTL, single-use as above).
2. Reads the redeemer's DECLARED `harness` (required, e.g. `kilo`, `grok`,
   `opencode`, `claude`, `aider`) and optional `label`, derives the handle
   (section 2a), and creates a REAL auth-request with `framework` = the
   harness, `identity_claim` = the derived handle, `requested_scopes` = the
   invite's scopes, and `project_id` = the invite's project.
3. Immediately approves it through the SAME `_do_approve` code path, with
   `decided_by` = the invite's `created_by` and the request marked
   `origin="invite:<invite_id>"`.
4. Returns the connection bundle plus the `request_id` and the derived handle;
   the agent polls the normal status endpoint and receives
   `{canonical_id, token}` exactly as a manually approved agent would.

Everything downstream is untouched and auditable: registry row, grants feed
rows (taOSmd's GrantsVerifier sees a normal grant), relationship edges,
project membership + a2a channel sync, the append-only decision record. The
human consents exactly once, with explicit scopes, before any agent exists.

**Membership is a guaranteed outcome, not a side effect.** An invite is
project-scoped by construction, so a successful redeem MUST end with the agent
being a native member of that project. Two invariants enforce this:

- The invite's granted scopes ALWAYS include `project_tasks` bound to the
  invite's `project_id`. The mint dialog may add or drop other scopes, but
  `project_tasks` for this project is non-optional and is force-included by the
  redeem path even if an older invite record somehow omitted it.
- Because `_do_approve` adds the agent as a project member and syncs the a2a
  channel whenever `project_tasks` is granted with a bound project (commit
  bec4186b on `feat/consent-project-picker`), membership follows automatically.
  The invite path does not re-implement membership; it guarantees the two
  preconditions (`project_tasks` + bound `project_id`) that make `_do_approve`
  do it, and treats a redeem that did not produce a member row as a failure to
  surface, not a best-effort miss.

### 2a. Identity: harness-declared naming

The connecting agent must DECLARE its harness at redeem time; taOS does not
guess it. The redeem request carries:

- `harness` (required): the coding tool, lowercased and slugified
  (`kilo`, `grok`, `opencode`, `claude`, `aider`, ...).
- `label` (optional): a short role/scope hint (`frontend`, `docs`, `review`).

The controller derives the human-facing HANDLE as
`{project_slug}-{harness}` with the label appended when present:

- `myproject-grok`
- `myproject-kilo`
- `myproject-grok-frontend`

This handle is what `_do_approve` receives as both `display_name` and `handle`
on `registry.register(...)` (today the invite/self-join path passes
`handle=""`; the invite path fills it). The registry then slugifies the
display name and mints the immutable `canonical_id` the existing way:
`{slug}-{YYYYMMDD}-{HHMMSS}` (`mint_canonical_id` in `agent_registry_store.py`),
so the canonical id for `myproject-grok-frontend` becomes
`myproject-grok-frontend-20260710-142233`. The timestamp keeps `canonical_id`
globally unique even when two agents share a harness+label.

Deconfliction (two layers, both already have hooks):

- `canonical_id`: the registry's existing collision guard appends `-{NN:02x}`
  when the same slug lands in the same second; unchanged, we rely on it.
- `handle`: the timestamp is NOT in the handle, so two live `myproject-grok`
  agents would collide on the bus address. Before minting, the redeem path
  checks the registry for an ACTIVE agent already holding the target handle in
  this project; if found, it appends a short numeric suffix
  (`myproject-grok-2`, then `-3`, ...) until free. The label, when the operator
  supplied one, is the natural first disambiguator, so this suffix is the
  fallback for same-harness-same-label re-invites.

Stability note: identity is stable per (project, harness, label). A re-spawn
that presents a still-valid stored token reuses its `canonical_id`; only a
genuinely new handle mints a new identity. This matches the "stable per (tool,
project)" decision in `external-agent-onboarding.md`, refined to include the
harness + optional label in the human-readable handle.

Approach B survives as a per-invite toggle: the mint dialog gets a "require
manual approval" switch (approval_mode="manual"), in which case redemption
creates the auth-request but leaves it pending and the normal consent bell
fires. Default is auto (Approach C); cautious operators flip the switch.

Implementation seam: `_do_approve` currently takes an admin `CurrentUser` from
the route. Extract the mint machinery into a helper that takes
`(record, granted_scopes, effective_project, decided_by)` so both the route
and the invite redemption call it; no behavior change for the existing route.

## 3. The link itself: user.taos.my vs controller-direct

Second contentious decision. The redemption API always lives on the
controller; the question is what URL the human hands to the agent.

**Approach A: taos.my-hosted short link only** (`<handle>.taos.my/i/482910`).
Pretty, location-independent, works from any network. But it requires a
linked taOS account, taos.my-side work, and fails the fully-local,
no-cloud deployment (core stays LAN-only by default).

**Approach B: controller-direct URL only** (`http://192.168.1.20:6969/i/482910`).
Zero cloud dependency, works today. But it only works when the agent's
machine can already reach the controller, and the URL is ugly and
environment-specific.

**Approach C (recommended): controller is authoritative, taos.my is a
resolver.** The invite dialog always mints the controller-local invite and
shows the best URL for the situation:

- Not account-linked or mesh not joined: show the controller-direct URL
  (best reachable base, using the `controller_callback_host` resolution
  order plus LAN enumeration).
- Account linked + mesh joined: show `https://<handle>.taos.my/i/482910`,
  with the controller-direct URL available under a "local network link"
  disclosure.

The taos.my `/i/{invite_id}` endpoint does no auth logic of its own: it
resolves the handle to the account's always-on host and forwards (relay) the
GET and the redeem POST to the controller over the tailnet, the same
TLS-terminating reverse-proxy contract the taOSgo relay already defines. The
PIN check, single-use flip, and minting all still happen on the controller.
If the relay cannot reach the controller, the redemption fails with a clear
"host offline" answer; we do NOT park bundles or secrets on taos.my in v1
(no encrypted-bundle escrow; that is a possible later optimization and is
listed in open questions).

The taos.my slice is deferred to last (section 9); slices 1 to 3 are fully
useful on LAN and tailnet with the controller-direct URL.

## 4. The connection JSON bundle

Returned by a successful redeem (and re-fetchable while the minted token is
valid via an authenticated `GET /api/agents/self/bundle`, so a re-spawned
agent with a stored token can re-discover addresses without a new invite).

```json
{
  "version": 1,
  "invite_id": "482910",
  "project": {
    "id": "prj_9f2c...",
    "name": "taOS website",
    "slug": "taos-website"
  },
  "controller": {
    "endpoints": [
      {"kind": "lan",   "url": "http://192.168.1.20:6969",  "priority": 1},
      {"kind": "lan",   "url": "http://10.0.40.5:6969",     "priority": 2},
      {"kind": "mesh",  "url": "http://100.64.0.7:6969",    "priority": 3},
      {"kind": "relay", "url": "https://jay.taos.my",        "priority": 4}
    ],
    "health_path": "/api/health",
    "registry_pubkey_path": "/api/agents/registry/pubkey"
  },
  "auth": {
    "flow": "auth_request",
    "request_id": "6f0e2c1a-...",
    "poll_path": "/api/agents/auth-requests/{request_id}",
    "granted_scopes": ["project_tasks", "a2a_send", "a2a_receive"],
    "agent_handle": "myproject-grok-frontend",
    "token_header": "Authorization: Bearer <token>"
  },
  "apis": {
    "tasks_list": "/api/projects/{project_id}/tasks",
    "tasks_ready": "/api/projects/{project_id}/tasks/ready",
    "task_lifecycle": "/api/projects/{project_id}/tasks/{task_id}/(claim|release|close|reopen)",
    "task_comments": "/api/projects/{project_id}/tasks/{task_id}/comments",
    "a2a_bus_send": "/api/a2a/bus/send",
    "a2a_bus_messages": "/api/a2a/bus/messages",
    "a2a_bus_channels": "/api/a2a/bus/channels"
  },
  "delivery": {
    "stream_path": "/api/a2a/bus/stream?channel={channel}&since={cursor}",
    "poll_path": "/api/a2a/bus/messages?channel={channel}&since={cursor}",
    "check_interval_secs": 1800,
    "cursor": "ts",
    "filter": "mentions+project"
  },
  "notes": [
    "Try controller endpoints in priority order; first /api/health 200 wins.",
    "All paths are relative to the working endpoint base URL."
  ]
}
```

Field sourcing:

- `lan` endpoints: enumerate non-loopback IPv4 addresses on the controller
  host (the UDP `getsockname` trick from `worker/agent.py` for the primary,
  plus interface enumeration for multi-homed hosts), each with the
  controller's bound port. `TAOS_CONTROLLER_CALLBACK_HOST` override, when
  set, becomes priority 1.
- `mesh` endpoint: `mesh_status().node_ip` when joined. Included because the
  remote machine may already be on the same tailnet (Jay's laptop is); a
  non-mesh machine simply fails the probe and moves on.
- `relay` endpoint: present only when account-linked + mesh joined. The
  public relay hostname is not currently persisted by
  `mesh_credentials.py` (it stores account_id/host_id/tailnet_name); the
  join ready payload or an account call must supply it (open question 6).
- `apis`: exactly the agent-JWT-reachable surface from
  `auth_middleware._AGENT_TASK_ROUTES` and `_A2A_BUS_*_PATHS`. Note the raw
  bus (:7900) is deliberately NOT in the bundle: it is unauthenticated and
  trusts `from`; external agents must use the authenticated proxy, which
  forces `from` to their own registry handle.
- `delivery`: the realtime stream + timed-check fallback contract, section 8.
  `check_interval_secs` echoes the value the admin set at mint time.
- `auth.agent_handle`: the handle the controller derived from the declared
  harness + optional label (section 2a), echoed so the agent knows the name it
  will appear under in Members and on the bus before its token poll returns.

The bundle contains no secrets (the token arrives via the status poll), so a
logged bundle is an information leak about addresses at worst, and the PIN
gates even that.

## 5. Remote-agent connect sequence

1. Operator clicks "Invite external agent" in Members, gets URL + PIN, pastes
   the one-line instruction into their agent.
2. Agent GETs the URL. `GET /i/{invite_id}` is content-negotiated: browsers
   get a small human page (what this is, what will happen), agents
   (`Accept: application/json`) get machine instructions:
   `{"redeem": {"method": "POST", "path": "/api/projects/invites/redeem", "fields": {"invite_id": "required", "pin": "required", "harness": "required (your tool: kilo|grok|opencode|claude|aider|...)", "label": "optional (short role hint, e.g. frontend)"}}}`.
   Note there is no `identity_claim` field: the controller DERIVES the handle
   from `harness` + `label` (section 2a); the agent only declares what it is.
3. Agent POSTs the redeem with the PIN and its harness (plus optional label).
   On success: the bundle (including `auth.agent_handle`, the derived name) +
   `request_id` (auto mode: already approved; manual mode: pending, the human
   sees the normal consent bell, pre-scoped, showing the derived handle).
4. Agent polls `poll_path` on the same origin it redeemed from until
   `{status: "accepted", canonical_id, token}`. It stores
   `{canonical_id, token, bundle}` in its per-host config
   (`~/.config/taos/agent-token` convention from the onboarding doc), keyed
   by project, so a re-spawn reuses the identity (stable per tool+project).
5. Transport selection: for every subsequent call the agent probes
   `endpoints` in priority order, `GET {base}/api/health` with a 2 to 3 s
   timeout, first 200 wins, remembered as the working base and re-probed only
   on failure. LAN first (fastest, free), then mesh (encrypted tailnet), then
   relay (works from anywhere, Pro-gated).
6. Work loop: `GET tasks/ready`, `claim`, comment progress, `close`; post to
   the coordination bus via `/api/a2a/bus/send` as itself; receive messages
   per the delivery contract (section 8). Project membership (added at
   approval) means it appears in Members and in the project a2a channel
   member list.

Failure modes: wrong PIN (403, attempts increment), expired (410), already
redeemed (409), rate-limited (429), all with actionable bodies. The agent-side
instruction text shown in the dialog covers "if the URL is unreachable, ask
the operator for the local network link".

## 6. UX

Members panel (`desktop/src/apps/ProjectsApp/ProjectMembers.tsx`):

- An "Invite external agent" action beside the existing add-member control.
- Mint dialog: scope checkboxes (default checked: `project_tasks`,
  `a2a_send`, `a2a_receive`; unchecked: `memory_read`, `memory_write`;
  `files_*` and `tools_execute` not offered in v1). `project_tasks` is shown
  checked and DISABLED with a "required for project invites" hint, so the
  operator cannot mint a project invite that would not confer membership. The
  harness name is NOT set here: it is declared by the connecting agent at
  redeem time (section 2a), so the dialog does not ask for it. The dialog also
  has a "require manual approval" toggle (default off), a "check-in interval"
  field for the
  fallback poll cadence (default 30 minutes; presets 5m / 15m / 30m / 1h /
  6h, free entry allowed, stored as `check_interval_secs`), and the mint
  button.
- Result view: the URL (best form per section 3) and the PIN rendered large,
  a copy button for each, and a copyable single-line agent instruction:
  "Fetch <URL> and redeem with PIN <PIN>; follow the returned JSON
  instructions to join the taOS project." The PIN is shown in this dialog
  only and is not retrievable afterwards (hash-only at rest).
- Pending invites list inside Members: invite_id, scopes, countdown to
  expiry, state chip (pending / redeemed by <claim> / expired / revoked), and
  a revoke button.
- Member display: the redeemed agent shows in Members as its derived handle,
  `project-harness` or `project-harness-label` (e.g. `myproject-grok`,
  `myproject-kilo-frontend`), the same string used on the bus, so the operator
  sees at a glance which tool joined and in what role.
- On redemption (auto mode): a notification "<agent_handle> joined <project>
  via invite 482910" and the member row appears (the member-add + a2a sync
  already ship with the consent-project-picker work). Manual mode: the standard
  consent bell + ConsentActions, pre-scoped, showing the derived handle and the
  invite id as provenance.

## 7. Transport fallback logic (probe contract)

Documented in the bundle and in the agent handoff docs so any harness can
implement it in a few lines:

1. Order candidates by `priority`.
2. `GET {base}{health_path}` with timeout 2.5 s, no retries per candidate.
3. First 200 is the working base; cache it for the session.
4. On any subsequent request failing at transport level, re-run the probe
   from the top (LAN may have come back; do not stick to the relay).
5. If all candidates fail: surface the error and the list of tried endpoints
   to the operator; do not retry-loop unattended.

The controller does not need new code for probing (the agent does it), but
`/api/health` must remain auth-EXEMPT (it already is) and must work through
the relay path unchanged (it does: plain reverse proxy).

## 8. Message delivery: realtime stream + timed-check fallback

An invited agent is useless if it only speaks when spoken to by its operator.
Other agents (and Jay) will address it on the bus; it must hear them. Two
delivery modes, both carried in the bundle's `delivery` block, with the
timed check as the guaranteed floor.

### Realtime: SSE stream through the authenticated proxy

The raw bus already exposes `GET {bus}/a2a/stream?thread=&since=` (SSE). The
controller gains a matching authenticated proxy:

- `GET /api/a2a/bus/stream?channel={c}&since={cursor}`, added to
  `_A2A_BUS_READ_PATHS` in the middleware allowlist, gated exactly like
  `/api/a2a/bus/messages` (admin session, local token, or registry JWT with
  an active `a2a_receive` grant, fail closed). The proxy holds one upstream
  SSE connection to the bus per client and relays events verbatim, injecting
  a comment heartbeat (`: ping`) every 25 s so idle streams are
  distinguishable from dead ones and intermediaries do not reap them.
- Works through every transport tier unchanged: on LAN and mesh it is a
  direct long-lived HTTP response; through the taOSgo relay it is the same
  reverse-proxied response, and the heartbeat keeps the relay's idle timeout
  from severing it. The relay must be configured to not buffer SSE
  (`X-Accel-Buffering: no` semantics); flag this in the relay slice.
- Reconnect contract (documented in the bundle notes + handoff docs): on
  disconnect, reconnect with jittered exponential backoff (1 s doubling to a
  60 s cap), passing `since=<last processed cursor>` so the gap replays from
  the bus history. A reconnect storm therefore never loses messages, only
  delays them.

### Fallback: the timed check

Not every harness can hold a socket open. A CLI that only wakes on a cron, a
sleeping laptop, a flaky hotel link: these get the same messages by polling
`GET /api/a2a/bus/messages?channel={c}&since={cursor}` on a fixed interval.

- Default interval: 30 minutes (`check_interval_secs: 1800`).
- Overridable at invite time: the mint dialog's check-in interval field
  writes `check_interval_secs` onto the invite record, which flows verbatim
  into the bundle's `delivery` block. The operator inviting a
  runs-every-5-minutes cron agent sets 300; an overnight batch agent gets
  21600. The value is advisory to the agent but authoritative as the
  operator's expectation: the Members row can later surface "last checked in"
  against it (follow-up, not v1).
- The `since` cursor convention: `cursor` = the highest message `ts` the
  agent has fully processed. Poll with `since=<cursor>` (exclusive on the bus
  side), process results oldest-first, dedupe by message `id` (the bus
  replays boundary messages on equal timestamps), then persist the new
  cursor in the same per-host config file as the token. Persisting AFTER
  processing means a crash re-delivers rather than drops: at-least-once, no
  gaps between checks.
- The existing messages proxy needs one addition: pass the `since` query
  param through to the bus (it currently forwards only `thread` + `limit`).

An agent SHOULD run the stream when it can and drop to the timed check when
it cannot; the cursor convention is shared, so switching modes is seamless.

### Delivery filtering: what does the agent actually receive?

The bus is channel-oriented and does not filter by recipient today; a reader
of a channel sees everything on it. Options:

- **Everything on subscribed channels.** Simple, matches the bus model, but a
  chatty `build` channel drowns a narrowly-scoped agent and burns its
  context.
- **Mentions only.** `@handle` mentions and direct threads only. Quiet, but
  the agent misses channel-wide coordination ("all agents: hold merges").
- **Mentions + its channels (recommended default, `filter:
  "mentions+project"`).** The agent subscribes to its project's channel(s)
  and any DM/decisions thread bearing its handle; within those, the harness
  is TOLD (bundle note + handoff docs) to treat `@handle` mentions and
  replies to its own messages as must-act items and the rest as ambient
  context it may skim or ignore. v1 implements this client-side (the bundle
  names the channels; the agent filters); a server-side `mentions_only=1`
  query flag on the proxy is a cheap follow-up once real traffic shows the
  need.

## Agent workspace isolation (taOS owns the isolation layer)

Invite and create differ in who owns the workspace the agent works in.

**Invited agents (this doc's subject): isolation is not ours to own.** An
invited agent already runs on ITS OWN machine, with its own repo clone and
cwd. taOS cannot own that filesystem. Enforcement lives at the INTEGRATION
boundary only: a per-agent revocable push identity, branch-and-PR-only,
nothing auto-merges, CI plus human review as the gate (join kit #95,
coordination discipline #96). Worst case is a bad PR that review rejects.

**Created agents: taOS provisions the workspace, so isolation is
enforceable.** When taOS SPAWNS a coding CLI (grok, kilo, opencode) on the Pi
or a cluster node for a project (executor lane #94, spawn-from-Projects
#101), taOS owns the workspace it hands the process. A shared cwd is a real
hazard here: an agent in the project root, the orchestrator's cwd, or worse
the live `/opt/taos` install means concurrent edits racing, one agent's dirty
tree bleeding into another's, commits landing on the wrong branch, and a weak
model editing or deleting the running OS. Today the grok CLI already runs in
a separate git worktree on its own branch while the orchestrator stays in the
main checkout, but that isolation is hand-built; taOS does not own it yet.

The proposal, layered and model-independent:

- **Container tier (strongest, default for created agents):** spawn the CLI
  inside an LXC/incus container (Apple Containerization on macOS) whose only
  mount is a fresh repo clone plus a scoped push token. `cd ..` gets it
  nowhere; blast radius is its own branch. Reuses the existing Hermes/LXC
  agent-deploy machinery.
- **Worktree tier (lighter, trusted/local):** taOS creates an
  `agent/{agent-id}/{task-id}` branch plus a dedicated git worktree and
  spawns the agent with cwd pinned there.
- **The key move: the agent is NOT the git operator.** taOS owns
  branch/commit/push/PR; the agent only edits files inside its sandbox. taOS
  diffs the sandbox on task-close, creates the branch, and opens the PR. "A
  dumb model does not follow the branch rule" becomes impossible, because
  there is no branch for it to get wrong. An onboarding "stay on your
  worktree" instruction is then a courtesy, not the safety mechanism.

The unifying principle: the working copy is a resource taOS hands out per
task, not a folder agents cooperate in, and git operations belong to the OS,
not the model. Two enforcement boundaries fall out: the filesystem boundary
(container or worktree; ours only for created agents) and the integration
boundary (scoped revocable push identity, branch-and-PR-only, review gate),
which applies to BOTH kinds of agent and is the sole backstop for invited
ones. None of this changes the invite slices below; the created-agent side
lands with the spawn work (#101).

## 9. Slice plan

Each slice independently shippable and testable; foundation first.

- **Slice 1: invite store + mint/redeem.** `project_invites` store (schema
  above, pairing-store discipline: hash-only PIN, TTL, attempt cap,
  flip-on-claim semantics, lazy sweep), `POST
  /api/projects/{pid}/invites` (admin), `POST /api/projects/invites/redeem` +
  `GET /i/{invite_id}` (EXEMPT, per-IP rate limit), the `_do_approve`
  extraction, auto + manual approval modes wired through the existing consent
  path, `check_interval_secs` on the record. Harness-declared identity: the
  redeem body requires `harness` + optional `label`, the path derives the
  `{project_slug}-{harness}[-label]` handle, checks the registry for an active
  same-handle agent in this project and appends a numeric suffix on collision,
  and passes the handle as `display_name` + `handle` into register. Membership
  invariant: `project_tasks` bound to the invite's project is force-included in
  the granted scopes, and a redeem that does not yield a member row is a hard
  failure, not best-effort. Bundle in this slice carries a single endpoint
  (`controller_callback_host` result). Tests: mint/redeem round trip,
  single-use race, wrong-PIN cap, TTL expiry, revocation, scope subset
  enforcement, project_tasks force-inclusion, handle derivation +
  same-handle collision suffix, canonical_id timestamp uniqueness, middleware
  exemption is method-exact, grants feed + member add + a2a channel sync all
  fire (member row is asserted, not assumed).
- **Slice 2: full connection bundle + address discovery.** LAN interface
  enumeration, mesh endpoint from `mesh_status`, `apis` map, the
  authenticated `GET /api/agents/self/bundle` re-fetch, the probe contract in
  the docs + `docs/AGENT_HANDOFF.md` bootstrap snippet. Tests: endpoint
  ordering, multi-homed host, mesh-not-joined omits mesh entry.
- **Slice 3: delivery (realtime + timed check).** The `/api/a2a/bus/stream`
  SSE proxy (allowlist entry, `a2a_receive` gate, heartbeat), the `since`
  passthrough on the messages proxy, the `delivery` block in the bundle, the
  cursor + reconnect contract in the handoff docs. Tests: stream auth
  matrix (admin / JWT / no grant), heartbeat cadence, since-resume replays
  the gap, poll dedupe on equal-ts boundary, interval override flows from
  mint dialog to bundle.
- **Slice 4: Members-panel UX.** Dialog (scope picker, manual-approval
  toggle, check-in interval field), pending list + revoke, redemption
  notification. Playwright + unit tests; screenshot verification against the
  real app.
- **Slice 5: taOSgo fallback.** Relay endpoint in the bundle (requires the
  relay hostname to be known controller-side), taos.my `/i/{id}` resolver +
  relay forwarding of the redeem POST, SSE-through-relay verification (no
  buffering, heartbeat survives idle timeout), `TAOS_TRUST_FORWARDED_PROTO`
  posture verified. This is the only slice with taos.my-side work.
- **Slice 6: agent-side helper.** A documented curl-only sequence plus a tiny
  `taos-join` script (bash + powershell, per the worker-script convention)
  that takes URL + PIN, does redeem + poll + token storage, prints the
  working endpoint, and includes a reference implementation of the stream +
  timed-check loop with cursor persistence. Also the canonical-skills
  injection so in-taOS harnesses can explain the flow to their operators.

Dependency note: slices 1 to 4 assume the member-add-on-approve commit
(`feat/consent-project-picker`, bec4186b) has merged; otherwise redeemed
agents get grants but no Members row.

## 10. Open questions for Jay

1. **Approval default:** auto (pre-authorized consent, one human step,
   recommended) vs manual as the default with auto opt-in. The toggle exists
   either way; this is only about the default.
2. **PIN width:** keep the 4-digit PIN (fine with the 5-attempt cap + rate
   limit) or go 6-digit for margin since agents, not humans, type it anyway?
3. **TTL:** 15 minutes (pairing parity, recommended) or longer (60 min) for
   the "email the invite to a collaborator" case? Longer TTL mostly serves
   human-to-human invites, which may be a different feature.
4. **taos.my escrow:** is resolver+relay forwarding enough (recommended, no
   secrets on taos.my), or should the controller push an encrypted bundle
   copy to taos.my at mint time so redemption works while the host is
   offline? (More moving parts, real secret custody questions.)
5. **Project chat access:** external agents can comment on tasks and post on
   the coordination bus, but have no write path to the per-project a2a CHAT
   channel (the agent-JWT allowlist does not cover channel posting). Is bus +
   task comments enough for v1, or should a `project_channel_send` scope be
   designed as a follow-up?
6. **Relay hostname source:** `mesh_credentials.py` does not persist the
   public relay FQDN. Extend the join ready payload (taos.my change) or
   derive it from an authenticated account call at bundle-build time?
7. **Multi-agent invites:** strictly single-use (recommended; minting is one
   click) or an N-use invite for onboarding several agents at once?
8. **Token lifetime** (inherited from the onboarding doc's open question):
   short-lived token + re-request on expiry, or long-lived + revocation-feed
   coverage. The invite flow makes re-requesting cheaper, which strengthens
   the short-lived case.
9. **Delivery filtering:** confirm the `mentions+project` client-side default
   (section 8), and whether a server-side `mentions_only` flag is worth
   pulling into v1 rather than waiting for real traffic.
10. **Missed check-ins:** should the Members row surface "last checked in"
    against `check_interval_secs` and warn when an agent goes quiet for N
    intervals (observability tie-in), or is that Observatory's job?
11. **Default isolation tier on low-RAM hosts:** for a CREATED agent on a
    4GB Pi, is the container tier (stronger, but per-agent LXC overhead) or
    the worktree tier (near-free, weaker walls) the right default? taOS also
    runs on 4GB hosts, so the strongest tier cannot be the unconditional
    default.

## Non-goals (v1)

Human collaborator invites (this is agent onboarding; humans have accounts),
N-use or standing invite links, `files_*` / `tools_execute` scope offering in
the invite dialog, taos.my bundle escrow, per-channel A2A grants, server-side
mention filtering, missed-check-in alerting, and any change to how
in-taOS-deployed agents are provisioned.
