> **RENAMED 2026-07-26: "Council" is retired as a product name - this is now taOS Teams.**
>
> Teams unifies what this doc describes (role-benchmarked models) with the task
> dispatcher and the agent org model into a single app: members, routing,
> benchmarks and org chart.
>
> **"Council" is retired in user-facing text only.** It remains in the codebase
> as the package name (`tinyagentos/council`), the route prefix
> (`/api/council/*`) and the name of the benchmark methodology. Renaming those
> is a separate change and is not implied by this notice, so do not treat a
> `council` identifier in the runtime as stale.
>
> Current statement: jaylfc/taOS#2142.

# taOS Council: a role-benchmarked background model team (founding design)

Status: founding design, no code yet. PR slices at the end.
Audience: maintainers and implementation agents.
Scope: taOS controller + desktop + a small taos.my service surface.

## Why this exists

OpenRouter's fusion router showed that a pool of heterogeneous models can be
worth more than any single one. taOS Council takes that idea somewhere a
router cannot go: instead of picking a model per request, the Council is a
BACKGROUND LIVE TEAM. The user adds models to the Council, each model gets its
own taOS session and registry identity, and the team quietly monitors what the
user is doing (project board, a2a channel, files) and works together on it.

The unlock is role benchmarking done once, globally. The user's smartest
model (the primary) auditions each Council candidate for specific roles, one
time per model per role. Results are published to a public gauge registry on
taos.my, signed by the submitting account, so only one user anywhere ever has
to benchmark a given model for a given role. Everyone after them adopts the
scores instantly.

Not every task is a hard coding task, and many taOS users never write code at
all. A creative project needs a content writer, an editor, a researcher, a
summarizer. Free-tier models handle those roles well. A Council built from
free models is an instant team of specialists at zero marginal cost, and the
gauge registry tells you honestly which free model is good at which job.

The Council app is smart about assignment: the primary agent looks at the
project and the available or selected models, proposes a set of useful roles,
the user agrees or adjusts, and members are put to work as standard team
members on the task board and the a2a channel. Nothing here invents a new
collaboration surface; the Council rides the surfaces taOS already has.

## Current state, verified in code

Everything the Council needs to exist already has a load-bearing precedent in
the tree. Citations are to the current `dev` checkout unless a branch is named.

### Identity and consent minting

- Canonical agent identities are minted once, immutably, as
  `{slug}-{YYYYMMDD}-{HHMMSS}` (`tinyagentos/agent_registry_store.py:301-305`,
  uniqueness at `tinyagentos/agent_registry_store.py:38`). The org model
  already carries `title` and `reports_to` columns
  (`tinyagentos/agent_registry_store.py:124-140`).
- The consent flow mints identity plus scoped grants in one approval:
  `POST /api/agents/auth-requests` submits a claim
  (`tinyagentos/routes/agent_auth_requests.py:145`), the closed scope
  vocabulary is `VALID_SCOPES`
  (`tinyagentos/routes/agent_auth_requests.py:44-57`), approval registers the
  agent, activates it, mints an Ed25519 registry JWT bound to an optional
  `project_id`, writes per-scope grants, and adds project membership plus the
  a2a channel sync (`tinyagentos/routes/agent_auth_requests.py:327-393`).
  Granting `project_tasks` requires the human to pick the project explicitly
  (`tinyagentos/routes/agent_auth_requests.py:308-312`).

### Board access for agent tokens

- `check_agent_scope_for_project` verifies the registry JWT, the active
  grant, AND that both the token claim and the underlying grant are bound to
  the requested project (`tinyagentos/agent_token_auth.py:151-190`).
- Task routes accept either an owner session or a project-bound agent token
  via `_authorize_task_actor` (`tinyagentos/routes/projects.py:431-475`), and
  an agent may act only as itself: lifecycle bodies naming another actor are
  rejected (`tinyagentos/routes/projects.py:477-495`). Claim, release, close,
  reopen, and comments are all live agent-reachable endpoints
  (`tinyagentos/routes/projects.py:657,699,725,765,897`).

### Account keypair signing

- The hub identity keystore (merged hub slice 1) mints an Ed25519 signing key
  plus an X25519 encryption key per node, persisted 0600, private keys never
  leaving the node (`tinyagentos/hub/identity.py:140-162`). `sign()` signs raw
  bytes (`tinyagentos/hub/identity.py:185-189`), the canonical author id is
  the SHA-256 fingerprint of the signing public key
  (`tinyagentos/hub/identity.py:165-168,207-209`), and `verify_signature` is
  the fail-closed check (`tinyagentos/hub/identity.py:212-226`). This is the
  keypair that signs benchmark submissions.

### The taos.my proxy pattern

- `tinyagentos/routes/account_proxy.py` forwards same-origin `/api/account/*`
  to `https://taos.my` with cookie pass-through, a closed action allowlist
  (`tinyagentos/routes/account_proxy.py:41-69`), rid-style input validation,
  and the base URL server-side only
  (`tinyagentos/routes/account_proxy.py:77,128-182`). The hub identity
  directory actions (`tinyagentos/routes/account_proxy.py:259-275`) are the
  exact shape the Council's community DB client copies.

### The Decisions app (propose-first surface)

- Backend: `POST /api/decisions` with types single_select, multi_select,
  approve_deny, free_text, options carrying `recommended` and `rationale`,
  priority, project binding, and free-form `metadata`
  (`tinyagentos/routes/decisions.py:83-96,122`). Answers route back to the
  asking agent over the a2a bus (`tinyagentos/routes/decisions.py:43-61`).
  Kind-specific handlers already exist for metadata-driven side effects, for
  example `{kind: "execution_gate"}` (`tinyagentos/routes/decisions.py:264`).
- Store schema in `tinyagentos/decisions/decision_store.py:21-45`; desktop
  app and card types in `desktop/src/apps/DecisionsApp.tsx:17-68`, registered
  at `desktop/src/registry/app-registry.ts:65`; notification click-through
  mapping in `desktop/src/lib/server-notifications.ts:38-63`.

### Notifications

- `NotificationStore.add(title, message, level, source, data)` persists and
  fans out (`tinyagentos/notifications.py:128`, schema at
  `tinyagentos/notifications.py:14-24`); acting on a notification archives it
  (`tinyagentos/notifications.py:215`). There is no model-detected
  notification source today; the Council adds one.

### Lead/tool org model and element assignment

- The lead-identity founding design (branch `docs/lead-agent-identity-design`,
  `docs/design/lead-agent-identity-and-canvas-access.md`) locks two rules the
  Council inherits: capability is fully orthogonal to role (per-agent
  capability checkboxes govern what an agent may touch; role says who is
  accountable), and Lead is an exclusive, server-enforced designation that
  grants nothing by itself (its D3 and D7).
- The nested-elements design (`docs/design/projects-nested-elements.md`,
  decisions 2 and 4) gives projects typed elements with whole-element
  assignment to a member (`assignee_id` on the element record), and an
  optional additive element claim for agent tokens. Council members slot into
  both without modification.

### Kilo model listing (external)

- The kilocode provider is OpenAI-compatible with the gateway base
  `https://api.kilo.ai/api/gateway` (`tinyagentos/routes/providers.py:25`),
  probed via `GET /models` like other cloud providers
  (`tinyagentos/routes/providers.py:584`); LiteLLM entries must set
  `api_base` explicitly (`tinyagentos/litellm_config.py:285-286`). On the
  gateway side, free-tier models carry ids ending in `:free`. In-repo the
  default alias is `kilo-auto/free` (`tinyagentos/routes/providers.py:37`),
  which is the router alias, not a free-tier listing; detection keys on the
  gateway's `:free` suffix.

### Audition precedent (ran live this week)

The gauge harness is not speculative. During the week of 2026-07-06 the
primary agent ran live auditions of four kilo free-tier candidates using the
studio-audit card pattern: each candidate got an isolated worktree and tmux
session and a small bounded repo task with tests. Outcomes: hy3 and stepflash
passed; laguna failed on surgical-changes discipline (touched code outside
the task); nemotron-ultra failed on zero output. nemotron-ultra was then
re-auditioned in a read-only reviewer format (review a merged PR the primary
agent had already reviewed, graded against that ground truth), which is the
reviewer gauge below. These sessions are the operational template the harness
codifies; they predate this document rather than follow from it.

## Locked decisions (do not re-litigate)

1. **Big taxonomy v1.** Ten plus roles from day one (table below), each with
   a real-task gauge suite. Roles without a credible suite yet are marked
   provisional and never block anything.
2. **Benchmark trust is signature plus confirmation.** Submissions are signed
   with the submitter's taos.my account keypair (the hub identity keystore).
   A score becomes community-trusted after N independent accounts confirm
   within tolerance; until then it displays as provisional.
3. **Autonomy is propose-first.** The Council watches the board, the a2a
   channel, and project files, and PROPOSES: suggested tasks, draft reviews,
   role changes, delivered as Decisions-app cards. Auto-execution happens
   only where the user has set a per-role autonomy dial to auto-ok.
4. **Detect automatically, gauge on approval.** The model watcher polls the
   kilo list for new `:free` entries minus a manually managed exceptions
   list and notifies the user; gauging starts on one tap. When the community
   DB already holds trusted scores for the model, adoption can be automatic
   with no local gauge, which is the common case at scale.

## Role taxonomy v1

Role ids are stable ASCII slugs; display names are presentation only.

| Role slug      | Display       | Gauge suite v1                          | Status      |
|----------------|---------------|------------------------------------------|-------------|
| `coder`        | Coder         | Bounded repo tasks with tests            | Proven      |
| `reviewer`     | Reviewer      | Re-review merged PR vs ground truth      | Proven      |
| `writer`       | Writer        | Brief to draft, rubric graded            | Designed    |
| `editor`       | Editor        | Flawed draft to edit, diff graded        | Designed    |
| `summarizer`   | Summarizer    | Long doc to summary, fact-recall graded  | Designed    |
| `translator`   | Translator    | Round-trip plus reference comparison     | Designed    |
| `researcher`   | Researcher    | Question set with verifiable answers     | Provisional |
| `planner`      | Planner       | Goal to task breakdown, rubric graded    | Provisional |
| `critic`       | Critic        | Artifact critique vs known-flaw list     | Provisional |
| `data_analyst` | Data analyst  | CSV question set with exact answers      | Provisional |

- **Proven** suites ran live this week (see audition precedent above).
- **Designed** suites have an honest grading path today: the primary model
  grades against a rubric, artifacts are retained, and the user spot-checks.
  Example writer gauge: three briefs of increasing constraint (tone, length,
  audience); the candidate drafts; the primary grades each draft on a
  published rubric (brief adherence, structure, factual restraint); the user
  spot-checks one draft before the score is submitted.
- **Provisional** suites lack a credible ground truth yet (research needs
  live-web verification, planning quality is only visible downstream). They
  ship as roles you can assign manually; their scores display as "ungauged"
  and never enter the community DB until the suite graduates.

The taxonomy is extensible: a role is a registry row, not an enum.

## Council data model

All Council state lives in a new `data/council.db` (SQLite, aiosqlite,
BaseStore pattern) owned by a new `tinyagentos/council/` package. Members are
NOT a new identity system: a member row points at an existing registry
`canonical_id`.

### Role registry

```sql
CREATE TABLE IF NOT EXISTS council_roles (
    slug            TEXT PRIMARY KEY,      -- 'coder', 'writer', ...
    display_name    TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    gauge_status    TEXT NOT NULL,         -- 'proven' | 'designed' | 'provisional'
    suite_version   TEXT,                  -- NULL when provisional
    builtin         INTEGER NOT NULL DEFAULT 1
);
```

Seeded with the v1 taxonomy on init, idempotently (the migration pattern used
throughout `agent_registry_store.py`). Custom roles insert with `builtin=0`.

### Member store

```sql
CREATE TABLE IF NOT EXISTS council_members (
    id              TEXT PRIMARY KEY,
    canonical_id    TEXT NOT NULL UNIQUE,  -- agent_registry row (identity)
    model_id        TEXT NOT NULL,         -- gateway model id, e.g. '...:free'
    provider        TEXT NOT NULL,         -- 'kilocode' in v1
    roles           TEXT NOT NULL,         -- JSON: [{role, score, source}]
    autonomy        TEXT NOT NULL,         -- JSON: {role: 'off'|'propose'|'auto'}
    status          TEXT NOT NULL,         -- 'active' | 'benched' | 'retired'
    added_at        TEXT NOT NULL
);
```

Adding a model to the Council mints its identity through the SAME consent
path external agents use: the Council app submits an auth request with the
model's display name as the identity claim and framework `council`, and the
user approves it from the standard consent card
(`tinyagentos/routes/agent_auth_requests.py:145,237`). One approval, one
canonical id, one registry JWT held by the Council runtime on the member's
behalf. No parallel minting path; the registry stays the single source of
identity truth.

Autonomy dials are per (member, role): `off` (member holds the role but the
watcher never acts on it), `propose` (default; everything goes through a
Decisions card), `auto` (the watcher may execute the role's action class
without a card, still logged to the project audit feed).

### Gauge suite registry

```sql
CREATE TABLE IF NOT EXISTS gauge_suites (
    role            TEXT NOT NULL,
    version         TEXT NOT NULL,         -- 'coder@1.0.0' style, semver
    task_manifest   TEXT NOT NULL,         -- JSON: task list + grading spec
    created_at      TEXT NOT NULL,
    PRIMARY KEY (role, version)
);
```

Suites are content-addressed in spirit: a version is immutable once any score
references it. Changing a task means a new version, so scores are always
comparable within a version and never across versions.

### Benchmark record store (local history)

Every local gauge run persists, whether or not it is submitted upstream:

```sql
CREATE TABLE IF NOT EXISTS gauge_records (
    id                  TEXT PRIMARY KEY,
    model_id            TEXT NOT NULL,
    role                TEXT NOT NULL,
    gauge_suite_version TEXT NOT NULL,
    score               INTEGER NOT NULL,  -- 0..100
    artifacts_hash      TEXT NOT NULL,     -- SHA-256 over the artifact bundle
    signature           TEXT,              -- hex Ed25519, NULL until submitted
    submitter           TEXT,              -- account key fingerprint, NULL local
    timestamp           TEXT NOT NULL,
    submitted           INTEGER NOT NULL DEFAULT 0
);
```

This is also the wire schema. The signed payload is the canonical JSON of
`{model_id, role, gauge_suite_version, score, artifacts_hash, timestamp}`
(sorted keys, no whitespace), signed via `tinyagentos/hub/identity.py:185-189`
with `submitter` set to the signing-key fingerprint
(`tinyagentos/hub/identity.py:165-168`). The artifact bundle (task inputs,
candidate outputs, grading transcript) is retained locally under
`data/council/artifacts/{record_id}/` so a score is always auditable; only
its hash travels.

## The gauge harness

The harness is how the primary model runs auditions. It is the codification
of what ran live this week, per role family:

**Coder roles: worktree plus tmux per candidate.** For each candidate the
harness creates an isolated git worktree and a tmux session, hands the
candidate a small bounded repo task with tests (the studio-audit card
pattern), and grades on: tests pass, diff stays inside the task boundary
(laguna's failure mode), and output exists at all (nemotron-ultra's failure
mode). Grading is mostly mechanical (test exit codes, diff paths); the
primary model grades only the surgical-changes judgment call.

**Reviewer roles: read-only report tasks.** The candidate reviews a merged PR
the primary agent already reviewed, with no write access at all, and its
findings are graded against the primary's ground-truth review: findings
matched, findings missed, findings hallucinated (the nemotron-ultra audition
pattern). Cheap, safe, and honest because the answer key exists before the
candidate starts.

**Creative roles: rubric-graded artifacts.** Brief in, artifact out, graded
by the primary model against the suite's published rubric, with a mandatory
user spot-check on at least one artifact before the score can be submitted
upstream. The spot-check keeps a lazy or self-agreeing grader honest and is
the human anchor the community trust model assumes.

Harness mechanics: one candidate at a time by default (free-tier rate limits
make parallel auditions self-defeating), every run is a `gauge_records` row
plus an artifact bundle, and a crashed or empty run scores what it earned (a
zero is a result, as nemotron-ultra demonstrated). The harness never runs
unattended on first contact with a model: gauging starts from an explicit
user tap (locked decision 4).

## Community gauge registry (taos.my)

### Controller side

A new action family in the account proxy, exactly the hub-identity shape
(`tinyagentos/routes/account_proxy.py:41-69,259-275`):

- `POST /api/account/council/gauges` submits a signed record (body: the wire
  schema above plus `signing_pubkey`).
- `GET /api/account/council/gauges?model_id=...` fetches all records and the
  trust state for a model.
- `GET /api/account/council/gauges/summary?role=...` fetches the ranked
  trusted board for a role.

Reads for adoption are unauthenticated-friendly on the taos.my side (public
data); submission requires the account session cookie, which the proxy
already relays.

### Server side (taos-website work, listed separately below)

Tables: `gauge_submissions` (the wire schema plus `signing_pubkey`, verified
on ingest with the same Ed25519 check as `verify_signature`,
`tinyagentos/hub/identity.py:212-226`) and `gauge_consensus`
(`model_id, role, suite_version, trusted_score, state, confirmations`).

### The N-confirm state machine

Per `(model_id, role, gauge_suite_version)`:

```
(no records)         -> UNKNOWN
first valid record   -> PROVISIONAL (score = that record)
N distinct accounts,
all within tolerance -> TRUSTED (score = median of confirming records)
records disagree
beyond tolerance     -> DISPUTED (displayed with the spread, never a single number)
new suite version    -> state resets for that version; old versions stay readable
```

- "Distinct accounts" means distinct signing-key fingerprints, one counted
  submission per fingerprint per (model, role, version).
- Recommended N = 3 and tolerance = plus or minus 10 points on the 0..100
  scale (open question 1 below).
- DISPUTED is a first-class display state, not an error: it tells the next
  user their local gauge is genuinely needed and their submission is the
  tiebreaker.
- Fraud posture, stated honestly: signatures prove WHO said it, not that the
  run was honest. N-confirm makes a lie expensive (it needs N colluding
  accounts) but not impossible. The artifacts hash enables spot audits
  (taos.my can request an artifact bundle for a suspicious record), and a
  fingerprint caught submitting fabricated records is delisted. v1 ships
  with exactly this posture and does not pretend to more.

Display rule everywhere: TRUSTED scores render plainly; PROVISIONAL renders
with a badge and the submitter count; DISPUTED renders as a range; UNKNOWN
renders as "not yet gauged" with the gauge-now action.

## Council app UI

A new desktop app `council` (`desktop/src/apps/CouncilApp.tsx`, registered in
`desktop/src/registry/app-registry.ts` like `decisions` at line 65).

**Roster view.** One card per member: model name, provider, identity
canonical id, role badges each carrying the score and its trust state
(trusted / provisional / local-only / ungauged), the per-role autonomy dial,
and status (active / benched / retired). An "Add model" flow lists gateway
models, shows community trust state per role inline, and ends in the standard
consent card, never a bespoke approval.

**Project-fit proposal flow.** From a project, "Propose a council" has the
primary model read the project (name, description, elements, open tasks) and
the available members, and emit a proposal: roles that would help, the best
scored member per role, and what each would pick up first. Rendered as a
multi_select Decisions card (options carry `recommended` and `rationale`,
`tinyagentos/routes/decisions.py:65-72`); accepting adds the chosen members
to the project through the normal membership path.

**Autonomy dials.** Direct manipulation on the roster card, per role,
mirroring the per-agent capability checkboxes of the lead-identity design:
capability and permission surfaces stay orthogonal to role there, and the
dial is deliberately the same visual grammar.

**New-model notifications.** The model watcher emits
`NotificationStore.add(..., source="council")` with the model id in `data`;
`desktop/src/lib/server-notifications.ts:38-63` gains a `council` mapping so
clicking opens the Council app on the gauge-consent view. One tap approves
gauging (or adoption, when trusted community scores already exist).

## Team integration: members are normal team members

The Council adds no new collaboration machinery. A member with a project role
is a project member like any other:

- **Board.** Claim, release, close, reopen, comment through the existing
  agent-token task routes (`tinyagentos/routes/projects.py:657,699,725,765,897`),
  authorized by `check_agent_scope_for_project`
  (`tinyagentos/agent_token_auth.py:151-190`), acting only as itself
  (`tinyagentos/routes/projects.py:477-495`).
- **a2a.** Members hold `a2a_send`/`a2a_receive` grants and speak on the
  project channel (`tinyagentos/routes/a2a_bus.py:66,98,178`), which project
  membership already syncs (`tinyagentos/projects/a2a.py:134`).
- **Lead vs tool.** Per the lead-identity design, Council members are TOOLS
  in the org model: task-scoped executors, never the exclusive project Lead.
  The Lead (the user's primary agent or the user) directs; members execute.
  Council roles are Council-local metadata and never write the registry
  `title`/`reports_to` org fields.
- **Element assignment.** A member can be assigned a whole project element
  (`assignee_id` in `docs/design/projects-nested-elements.md`), so "the
  website element belongs to the writer-member" works with zero new code.

## Model monitoring

- **Poll.** A background task polls the kilocode gateway `GET /models`
  (the existing probe path, `tinyagentos/routes/providers.py:584`) on the
  provider-refresh cadence, recommended hourly, and diffs ids ending `:free`
  against the known set.
- **Exceptions list.** A manually managed list (`data/council/exceptions.json`,
  editable in the Council app settings) of model ids never to surface:
  known-broken, deprecated, or deliberately ignored entries. Adding an id
  from a notification's overflow menu is the one-tap "stop telling me" path.
- **On new model.** Notify (source `council`). If the community DB has
  TRUSTED scores for it, offer adoption directly and, when the user has
  enabled auto-adopt in Council settings, adopt without a tap (locked
  decision 4: this is the common case at scale). Otherwise the notification
  offers "Gauge now", which runs the harness for the roles the user picks.
- **On removed model.** Members whose model id disappears from the gateway
  move to `benched` with a notification; nothing is deleted.

## Autonomy: propose-first, verified against the Decisions surface

Watchers (board events, a2a mentions, file changes in project shelves)
generate proposals, never actions. A proposal is a Decisions card with
`metadata: {kind: "council_proposal", member_id, role, action_class, payload}`
following the existing kind-handler pattern
(`tinyagentos/routes/decisions.py:264`); answering routes back over the bus
(`tinyagentos/routes/decisions.py:43-61`) and the kind handler executes the
accepted action. Action classes in v1: `suggest_task` (create a board task),
`draft_review` (post a review comment), `role_change` (re-rank a member's
roles after new scores). A dial at `auto` for that (member, role) skips the
card and executes directly, with the project audit feed as the record. The
dial default is `propose` for everything, always.

## Honest limitations

- **Gauge suites measure narrow competence.** A coder score on three bounded
  tasks predicts bounded-task performance, not architecture judgment. Scores
  rank candidates for a role; they do not certify the role.
- **Free-tier rate limits shape throughput.** A Council of free models is a
  slow team. Auditions serialize, proposals can lag activity by minutes, and
  burst work will queue. This is the honest price of zero cost.
- **Propose-first adds latency by design.** Every proposal waits for a human
  unless its dial says otherwise. The Council is a background team, not a
  real-time pair programmer.
- **Provisional roles are manual.** researcher/planner/critic/data_analyst
  ship without community scores; assigning them is a judgment call.
- **Trust is social, not cryptographic.** Signatures plus N-confirm raise the
  cost of lying; they do not eliminate it (see fraud posture above).

## Slice plan

Controller/desktop slices in order. Each is one PR-sized change against
`dev`. Slices S1 to S4 and S9 are bounded and suitable for external CLI
agents; S5 to S8 are flagged maintainer-review (identity, signing, and
autonomy surfaces). Verification commands run from the repo root.

**S1. Role registry + member store (backend, read-only routes).**
- Files: `tinyagentos/council/__init__.py`,
  `tinyagentos/council/role_registry.py`,
  `tinyagentos/council/member_store.py`,
  `tinyagentos/routes/council.py` (GET `/api/council/roles`,
  GET `/api/council/members`), wiring in `tinyagentos/app.py`,
  `tests/test_council_stores.py`, `tests/test_council_routes.py`.
- No writes beyond seeding; no identity interaction yet.
- Verify: `uv run pytest tests/test_council_stores.py tests/test_council_routes.py -q`

**S2. Roster UI, read-only.**
- Files: `desktop/src/apps/CouncilApp.tsx`,
  `desktop/src/apps/CouncilApp.test.tsx`,
  `desktop/src/registry/app-registry.ts` (one entry).
- Renders roles and members from S1; badges for trust states; no actions.
- Verify: `cd desktop && npm test -- CouncilApp`

**S3. Gauge record store + local bench history.**
- Files: `tinyagentos/council/gauge_store.py` (suites + records tables),
  routes GET/POST `/api/council/gauges` (local records only, no signing),
  history panel in `CouncilApp.tsx`, `tests/test_gauge_store.py`.
- Verify: `uv run pytest tests/test_gauge_store.py -q && cd desktop && npm test -- CouncilApp`

**S4. Kilo model-list poller + notification.**
- Files: `tinyagentos/council/model_watch.py` (poll, diff, exceptions list at
  `data/council/exceptions.json`), startup task in `tinyagentos/app.py`,
  `source: "council"` mapping in `desktop/src/lib/server-notifications.ts`,
  `tests/test_model_watch.py` (mocked gateway).
- Verify: `uv run pytest tests/test_model_watch.py -q && cd desktop && npm test -- server-notifications`

**S5. Member minting through the consent flow. MAINTAINER-REVIEW.**
- Files: `tinyagentos/council/membership.py` (submit auth request with
  framework `council`, poll, persist member row on approval),
  add-model flow in `CouncilApp.tsx`, autonomy dial writes
  (PATCH `/api/council/members/{id}`), tests.
- Touches identity minting and grants; review by maintainer before merge.
- Verify: `uv run pytest tests/test_council_membership.py -q` plus a manual
  consent round-trip on a dev instance.

**S6. Gauge harness orchestration. MAINTAINER-REVIEW.**
- Files: `tinyagentos/council/harness.py` (worktree/tmux coder runner,
  read-only reviewer runner, rubric grader invocation),
  `tinyagentos/council/suites/` (v1 task manifests for proven + designed
  roles), POST `/api/council/gauges/run`, run view in `CouncilApp.tsx`, tests
  with a stub candidate.
- Executes model-authored code in worktrees; sandbox posture must be
  reviewed by a maintainer against the app-runtime tiers.
- Verify: `uv run pytest tests/test_gauge_harness.py -q` plus one live
  audition of a known-good free model on a dev instance.

**S7. Propose-first engine. MAINTAINER-REVIEW.**
- Files: `tinyagentos/council/watchers.py`, Decisions kind handler
  `_apply_council_proposal` in `tinyagentos/routes/decisions.py`, autonomy
  dial enforcement, audit-feed logging, tests.
- Verify: `uv run pytest tests/test_council_watchers.py -q` and a scripted
  board event producing a pending Decisions card.

**S8. Community DB client: signed submission + adoption. MAINTAINER-REVIEW.**
- Files: council actions in `tinyagentos/routes/account_proxy.py` (submit,
  fetch, summary), signing in `tinyagentos/council/gauge_store.py` via
  `tinyagentos/hub/identity.py`, trust-state display + auto-adopt setting in
  `CouncilApp.tsx`, tests with a mocked upstream.
- Verify: `uv run pytest tests/test_council_community.py -q`

**S9. README section + docs cross-link.**
- Files: `README.md` (insert the Appendix A copy as a new `### taOS Council
  (preview)` subsection immediately after the `### Agent Management` block,
  currently ending near `README.md:318`, before `### Authentication`), and
  add `docs/design/taos-council.md` to the design-doc list near
  `README.md:664-670`.
- Verify: `grep -n "taOS Council" README.md` shows both insertions;
  markdown renders clean; no em dash characters introduced.

### taos.my server-side slices (taos-website repo, listed separately)

- **W1.** `gauge_submissions` table + POST endpoint with Ed25519 signature
  verification against the hub identity directory record.
- **W2.** N-confirm state machine (`gauge_consensus`), the four states, and
  per-fingerprint dedupe.
- **W3.** Public read API: per-model records + per-role trusted leaderboard,
  cacheable, unauthenticated.

## Open questions (with recommendations)

1. **N for community confirmation.** Recommend N = 3 with tolerance plus or
   minus 10 points. Low enough that popular models graduate in days, high
   enough that one careless run cannot mint global truth. Revisit with data.
2. **Gauge suite versioning cadence.** Recommend semver per role suite,
   immutable once referenced, bumped only when the task set changes, with a
   quarterly review of proven/designed suites. Scores never compare across
   versions, so cadence is a freshness/comparability trade, not correctness.
3. **One kilo API key or per-member keys.** Recommend one shared provider key
   with per-member LiteLLM virtual keys layered on it, consistent with the
   existing synced model management model. Per-member gateway keys buy
   nothing until rate limits are per-key, and complicate onboarding.
4. **Role name i18n.** Recommend shipping the stable slugs plus English
   display names now, with display names joining the desktop i18n catalog
   later. Slugs are the identity everywhere (records, wire schema, DB), so
   i18n is purely presentational and never blocking.

## Appendix A: README copy (draft)

The exact user-facing markdown S9 inserts, kept honest and short:

```markdown
### taOS Council (preview)

Your models, working as a team in the background. Add models to the Council
and each one becomes a real taOS team member with its own identity: it can
claim tasks on your project board, talk on the project channel, and pick up
the work that fits its role.

Roles are earned, not assumed. Your primary model auditions each Council
member for specific jobs (coder, reviewer, writer, editor, researcher, and
more) with real tasks, and the scores are shared on a community gauge
registry, so a model only ever has to be benchmarked once, by anyone,
anywhere. Everyone else adopts the results instantly.

Built for free-tier models: most everyday work (writing, editing,
summarizing, review passes) does not need a frontier model, and a Council of
free models gives you a standing team of specialists at no cost.

The Council proposes before it acts. Suggested tasks, draft reviews, and role
changes arrive as approval cards in your Decisions inbox; nothing runs on its
own unless you set that role's autonomy dial to auto.

Council is in beta: gauge suites measure narrow, practical competence, and
free-tier rate limits mean the team works at a background pace by design.
```

Cross-link for the README design-doc list:
`[docs/design/taos-council.md](docs/design/taos-council.md). taOS Council,
role-benchmarked background model team`.
