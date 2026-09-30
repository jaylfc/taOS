# Skill-collaboration layer: a user's own agents share what they learn

Status: design spike (2026-09-27), rev. 2026-09-30, doc only — no implementation
in this change. The revision settles two corrections from the #3222 lead review:
the isolation unit is the **user** (or a project), not the taOS instance, and S2
is blocked on the A2A bus redesign (§3.2, §8).
Design scoped for slices S1–S3; section 7 lists the questions that need a
decision before S2. Tracking issue: #900. Related: #898 (the artifact this
layer distributes), #896 (the surface it reviews through), #900's pointer to the
canonical-skills idea (#595) and upstream-tracking catalog (#596).

## 1. What it is — and the naming collision that has to be settled first

A user runs several agents. One of them discovers something real about this
user's hardware, models, or workflow ("FLUX Q3 fits a 12GB 3060; Q4 OOMs"). Today
that knowledge is trapped in one agent's context. This layer is the mechanism
that lets a *user's own agents* publish that lesson, and lets the others adopt
it only if it is relevant and only after a human has seen it.

**`skill` already means something else in this repo, and this doc does not
reuse it.** `tinyagentos/skills.py` (`SkillStore`) is the executable capability
registry — tool schemas, `frameworks` (native/adapter/unsupported), per-agent
`agent_skills` assignments shown in `/api/skills` and the agent Skills tab (see
`docs/design/skills-plugins.md`). Nothing here changes that surface.

#900 and #898 use "skill" for *knowledge documents* layered over the read-only
canonical guides. To keep the two apart:

- **guide** — the knowledge artifact. The canonical guides (`docs/agent-manual/`,
  read-only, compiled by `scripts/build-agent-manual.py` into `docs/taos-agent-manual.md`,
  which `routes/taos_agent.py` loads at import time as the taOS agent's system prompt) are
  guides. #898's agent-authored guides are guides.
- **supplement** — one agent's additive note over one guide. This layer stores,
  shares and governs supplements. Call it a *supplement*, never a "skill".
- **channel** — the bus thread supplements travel on. Recommend `learning`
  (the issue offers "skills (or learning)") so bus channel names never collide
  with the capability registry either.

## 2. Where it sits — every piece exists except the transport, which is under a hold

| Piece | State | Used here as |
|---|---|---|
| taOSmd coordination bus (`routes/a2a_bus.py`, `TAOS_A2A_BUS_URL`, default `http://127.0.0.1:7900`) | Separate service; **under a maintainer hold since 2026-08-24**, pending a redesign as a real connector | The intended transport — but S2 cannot start on today's proxy (§3.1, §8) |
| Bus identity | The taOS send proxy mints `from` from the caller's registry JWT (`agent_token_auth.check_agent_scope`, scope `a2a_send`); the bus verifies `token sub == from` | Free provenance — a supplement's author cannot be spoofed |
| Read side | `a2a_receive` scope or admin session gates reads | Who may subscribe |
| Internal per-project a2a (`projects/a2a.py`) | One `kind="a2a"` group channel per project, @mention routing | Out of scope. That is project chat, not learning |
| Read-only canonical guides (`docs/agent-manual/`) | Compiled by `scripts/build-agent-manual.py` to `docs/taos-agent-manual.md`, which `routes/taos_agent.py` loads at import time as the taOS agent's system prompt (`SYSTEM_PROMPT: str = _load_manual()`, `:104`/`:117`, handed to the adapter at `:491` when no persona is set). `build_manual()` in `agent_manual.py` is a pure function of session constants and does not read this file; `.claude/skills/taos-agent/SKILL.md` is a hand-authored companion skill consolidating the same content, not the compiled artifact | The layer supplements sit over, never into |
| Per-agent memory (taosmd/QMD, `memory_mode`) | Existing | Overlaps by design — see §7, build with @taOSmd |
| Decisions app (`routes/decisions.py`) | Agents create decisions with a registry token; a human answers | The review gate surface in S3 |
| #896 control plane (`trace_store`, `otel/*`, `scheduler/history_store.py`, Activity UI) | Spec'd/partly built | The evidence and audit surface the review gate links to |

Two constraints fall out of the table and shape the design:

- **Bus rows are flat** — `{id, ts, from, body, thread, reply_to}`. There is no
  attachment slot, so the supplement envelope travels *inside* `body` as JSON.
  Large prose is referenced by a content handle, not inlined (§7 Q2).
- **The cost rule from `docs/design/taos-native-collaboration.md` applies
  here verbatim:** checking is mechanical, waking is gated. A poll that finds
  nothing must cost zero model turns, and adoption happens at guide-render time,
  not by spending a turn to decide whether to adopt.

## 3. Channel and subscription model

### 3.1 Channel

One bus thread, `learning`. It is a normal bus channel: any agent that can
`a2a_send` posts to it, any agent that can `a2a_receive` reads it. The envelope
(§4) carries everything else.

This is deliberately more than a chat channel and deliberately less than a new
service: identity, ordering, cursors, replay and the offline-degradation path
are already solved by the bus proxy, and the Messages app gets a readable view
for free.

**The transport is under a hold, so this doc must not read as bus-ready.** The
A2A bus has been on hold since 2026-08-24 pending a redesign as a real connector
— the direction of #2150 (agent comms move into taOStalk + Projects) and #2147
(the bus stays a control plane; bytes move through project Files). Nothing here
assumes the current read-only proxy survives that redesign: the proxy is what
*shapes* the envelope (§4) and the poller (§3.5), not what S2 is guaranteed to
ship on. S2 is blocked on the redesign landing, in addition to the isolation
question in §3.2 — see §8 and open question 7.

### 3.2 User (and project) isolation is a precondition, not later hardening

The bus proxy authorizes reads on the `a2a_receive` grant alone and forwards the
channel unfiltered, and the default bus URL is a **shared local service**. More
than one population boundary would therefore see each other's supplements, and
the unit this design has to isolate is the **user** (or, where a project is the
fleet boundary, the project), **not the instance**:

- **user to user on one instance.** taOS is multi-user (`tinyagentos/auth.py`, a
  multi-user auth manager; owned resources are keyed by `user_id` and gated by
  `require_owner_or_admin` in `tinyagentos/auth_context.py`), and an agent
  belongs to the user who registered it (`agent_registry.user_id`,
  `require_agent_owner_or_admin` in `routes/agents.py`). One box therefore hosts
  several *unrelated* fleets. "The same taOS instance" is not "the user's own
  agents": an instance-scoped subscription default would put user A's lessons in
  front of user B's agents. §3.3 is scoped accordingly.
- **instance to instance on a shared bus** — the case that made this a
  precondition in the first place.
- **`author.instance` and `author.handle` are self-declared metadata and must
  not be used as an isolation control** — an author that can lie about its handle
  is not prevented from lying about its instance.

S2 must therefore require all of:

- a **per-instance bus** (the configured `TAOS_A2A_BUS_URL` points at a bus that
  serves exactly this instance) *and* an **enforced user (or project) namespace
  / ACL applied in the bus path** for both send and receive — not a client-side
  filter over a shared stream, and not an instance namespace standing in for the
  user boundary;
- a **local store keyed by owning user** (or project), so a subscriber can only
  read its own fleet's supplements even if a foreign row reaches the proxy.

Until all of that is in place, S1–S2 stay inside one user's fleet on one
instance; the cross-user and cross-instance paths stay unbuilt (§9). Which
mechanism to standardise on is open question 6.

**Per-channel scoping is a real dependency, not a nicety.** Today the bus gates
*send* on any active grant with `a2a_send` and *read* on any active grant with
`a2a_receive` — `external-agent-onboarding.md` names per-channel grants as the
v2 gap. S1 keeps the blast radius small by making **publication an explicit
allowlist** (§3.3); S2 adopts per-agent local filtering (§3.5) regardless of what
the bus does. Wiring stricter bus-side ACLs is a separate card.

### 3.3 Who publishes, who subscribes

- **Publishers (S1):** an explicit allowlist in config. Publishing is the
  dangerous direction — it is what can put a bad lesson in front of other agents —
  so it starts opt-in per agent, not grant-default.
- **Subscribers (S1):** default *on* within the same **user's** fleet on this
  instance — the agents that user owns (`agent_registry.user_id`), which is the
  whole point: "a user's OWN agents". Opt-out per agent. It is deliberately
  **not instance-wide**: a multi-user instance hosts several users, so an
  instance-scoped default would subscribe one user's agents to another user's
  lessons (§3.2). Where a project is the fleet boundary — a shared project with
  several members — the project is the unit instead.
- **Cross-user / community (not in S1–S3):** #900's "shareable like apps/themes/
  packages" path needs the hub distribution model plus signing
  (`tinyagentos/hub/identity.py`, the signed-submission pattern in
  `docs/design/taos-council.md`). S1–S3 stay inside one user's fleet (or one
  project) on purpose — the governance gate has to be proven before anything
  crosses a user or an instance boundary. This is the explicit boundary between
  "my agents help each other" and "the internet can write my guides".

### 3.4 What a subscription means

An agent's local subscription is a **filter**, not a bus membership:

```
scope = {
  guide:        "10-image-prompting",   # canonical guide id
  capability:   "image_generation",     # from the capability registry
  hardware:     "x86-cuda-12gb",        # the app-manifest hardware_tiers vocabulary
  framework:    null,                   # optional tighter match
}
```

An incoming `fleet` supplement is adopted only if its scope intersects the
agent's declared capabilities/tier. An agent with no image-generation capability
never sees image-generation lessons. Unscoped supplements ("general") are adopted
by everyone but are held to the same review gate.

### 3.5 Pull mechanics (no turn for emptiness)

Per subscribing agent, a small scheduled process — not an LLM turn — holds a
`since` cursor (the bus returns a finite float message-ts; unknown query params
are a 400 by design, so a broken cursor fails loudly rather than silently
re-reading the window) and asks the local proxy for new `learning` rows. It:

1. parses the envelope, drops malformed ones,
2. **applies authorized tombstones first** (§6.3). A tombstone is a distinct
   event kind, not a supplement carrying `status: "retracted"`, so it is never
   subject to the status filter below and is never "adopted" — it removes. It is
   applied only if its authority checks out (§6.3). Applying tombstones before
   anything else means a retraction and a re-publish travelling in the same batch
   cannot leave the withdrawn version live,
3. drops anything out of scope, belonging to another user or project, or from a
   foreign instance namespace (§3.2),
4. drops anything whose `status != "fleet"` — `draft` and `review` are seen at
   most — or that duplicates a known supplement id,
5. **verifies the promotion record** (§6.2): `promotion.by` must resolve to a
   reviewer this instance trusts, and `promotion.decision_id` must match an
   approval this instance holds. A sender-declared `status: "fleet"` with no
   verifiable promotion is stored as *seen*, never adopted. The bus attests
   *who sent it* (the send proxy's `token sub == from` check); it does not attest
   *that anyone reviewed it*, and the two must not be conflated,
6. writes surviving supplements into the local store,
7. only then may raise a signal — batched on a short settle window — and only
   for a supplement that is new *and* relevant *and* verifiably promoted.

## 4. The supplement: data shape

One JSON envelope in the bus `body`, and one row in a local store:

```json
{
  "kind": "guide.supplement",
  "schema": 1,
  "id": "gs-<sha256 of the immutable core: author+targets+scope+claim+body_md+evidence>",
  "author": {"handle": "@taos-dev", "canonical_id": "...", "instance": "pi-01"},
  "targets": {"guide": "10-image-prompting", "guide_version": "<pinned>"},
  "scope": {"capability": "image_generation", "hardware": "x86-cuda-12gb", "framework": null},
  "claim": "FLUX Q3 fits a 12GB 3060; Q4 OOMs at 1024px.",
  "body_md": "Short, actionable, additive. Points at the canonical guide, never restates it.",
  "evidence": [{"run_id": "...", "trace": "<#896 trace id>", "observed": "OOM at step 2, Q4, 1024px"}],
  "provenance": {"created_ts": 0, "source_bus_msg": "...", "prev": null},
  "promotion": {"by": "@<reviewer handle>", "canonical_id": "...", "decision_id": "dec-...", "ts": 0},
  "status": "draft|review|fleet",
  "supersedes": null
}
```

Rules that make the shape safe:

- **`id` is content-addressed over the immutable core only** — `author`,
  `targets`, `scope`, `claim`, `body_md`, `evidence`. Mutable lifecycle fields
  (`status`, `promotion`, `supersedes`, generation) are deliberately **excluded**
  so the same identity survives `review → fleet → retracted`; a lifecycle change
  is an *event* about that id, not a new supplement. Two agents learning the same
  thing converge on one id; a re-publish is a no-op. (Identity here is computed,
  not delegated: the project Files store in `tinyagentos/routes/project_files.py`
  is a plain tree with whole-tree project grants and no content addressing —
  unlike `tinyagentos/hub/store.py`, which is content-addressed for hub posts —
  so it cannot supply a stable handle for this.)
- **`claim` is required and small.** It is what a human reviews and what a
  conflict is detected on; `body_md` is the detail.
- **`evidence` must point at a real artifact** (a #896 trace id, a run id). A
  supplement with no evidence is rejected at publish, not at review — the gate
  should not spend a human on an unfalsifiable claim.
- **`status` is monotone-ish**: `draft → review → fleet`. Promotion is the only
  transition that requires the gate (§6); withdrawal is not a status change but a
  `guide.tombstone` event (§6.3), which is why a tombstone can never be swallowed
  by the status filter.

## 5. Merge and reconcile with the canonical guides

The canonical guides are read-only (#898). A supplement therefore **never
edits, overrides or shadows canonical text** — it is a separate layer rendered
next to it.

- **Rendering.** At guide-render time (the import-time `SYSTEM_PROMPT = _load_manual()`
  assembly in `routes/taos_agent.py` and any future guide loader), canonical text is emitted byte-identical; adopted supplements are
  appended under a clearly marked block, e.g. `### Local notes (agent-supplied,
  reviewed)`, each stamped with author + id. An agent reading its manual can
  always tell which sentence came from the platform and which came from a peer.
- **No negation.** A supplement whose text contradicts a canonical directive is
  dropped at render, not merged. Supplements add nuance and local facts; they do
  not argue with the manual.
- **Precedence and determinism.** For a given scope key, most-specific wins
  (hardware+tier beats general), then newest promoted version, then `id` as the
  tiebreak — so every subscriber renders the same manual for the same inputs.
- **Conflicts are surfaced, never silently resolved.** Two promoted supplements
  with the same scope and contradictory claims both survive; both are rendered,
  ranked, and flagged in the review surface. #900's "conflicting lessons can be
  ranked" is the goal; auto-picking a winner is not.
- **Version drift is explicit.** A supplement pins `targets.guide_version`. When
  the canonical guide's version changes, supplements pinned to the old version
  are marked `stale` and excluded from rendering until re-verified — advice
  written against text that has since changed must not be applied silently.
- **Idempotence.** Adoption is keyed on `id`; re-reading the bus is a no-op.
  A `supersedes` chain resolves to exactly one live supplement per (scope, guide),
  with the chain retained for audit.

## 6. The review / governance gate (#896)

#900's own words: review *before* spread, not after. So `fleet` — the only
status other agents adopt — is unreachable without passing the gate.

### 6.1 States

```
draft       author-local; rendered only for the author; never on the bus
review      submitted; on the bus as status=review; NOT adopted by anyone
fleet       promoted by the gate; adopted by in-scope subscribers
(withdrawn) a guide.tombstone event drops a `fleet` supplement fleet-wide on the
            next sync; the id is never resurrected
```

A **personal** supplement (usable on the author alone) is a local `draft`+adopt
state and never leaves the box — that is the boundary that keeps the gate
meaningful: nothing unreviewed is ever *shared*.

### 6.2 The gate

Submission creates a **Decisions card** (`routes/decisions.py` already lets an
agent create a decision with a registry token and a human answer). The card
carries: the claim, the body, the author/provenance block, and a deep link into
the #896 trace for the evidence. Approval transitions the supplement to `fleet`
and republishes it on the `learning` channel; rejection retracts it locally and
explicitly (no silent drop — the author is told).

- **S3 gate = human only.** One reviewer: the user.
- **Later:** trusted-agent review with human appeal, and N-confirm for
  cross-instance promotion. Not S1–S3.
- **Answers are recorded, not just applied**: who promoted what, when, on what
  evidence — the audit trail #896 wants, produced by the same act as the
  promotion.
- **Promotion is an attested record, not a status flag.** Approving writes the
  `promotion` block (`by`, `canonical_id`, `decision_id`, `ts`) from the
  *reviewer's* identity — the republish is sent as the reviewer, not the author —
  and subscribers check it before adopting (§3.5 step 5). A sender-supplied
  `status: "fleet"` proves nothing.

### 6.3 Rollback

A bad lesson must be removable, and the mechanism has to work with the grant
model as it actually is: `agent_grants_store` exposes `add_grant` / `list_grants`
/ `list_active_grants`, plus `revoke_grant` / `revoke_all_for_project` — but a
revoke removes a subscriber's *access*, not knowledge already stored there, so
it cannot recall a published lesson. Rollback is therefore a **tombstone event on the bus** (kind
`guide.tombstone`):

- promotion writes a generation number into the local store; retraction
  increments it and publishes a minimal tombstone event referencing the `id`
  (kind `guide.tombstone`, not a supplement with `status: "retracted"`, so the
  status filter in §3.5 step 4 can never silently drop it);
- **a tombstone only counts from an authority that could have promoted the
  supplement**: the reviewer identity that promoted it, or the original author
  withdrawing their own. Anyone else's tombstone is recorded as seen and ignored
  — otherwise any agent with `a2a_send` could erase another agent's lesson;
- **the two paths are checked differently, because they are different claims.**
  A reviewer retraction must present the promotion attestation for that
  `decision_id` (§6.2); an author withdrawal must present the author's own
  authenticated identity, which the bus already proves (`token sub == from`) and
  which must match the supplement's `author.canonical_id`. Requiring the
  promotion attestation on the author path would reject a legitimate withdrawal
  whenever the author was not also the reviewer, and requiring only authorship on
  the reviewer path would let the author resurrect-then-re-retract around a
  reviewer's decision. The poller accepts either, per the path the tombstone
  declares, and enforces it at §3.5 step 2;
- subscribers apply tombstones before new supplements in the same batch and drop
  the supplement *and* everything it superseded;
- the tombstone is append-only — the history of "this was believed, then
  retracted, because X" is itself the audit artifact.

### 6.4 Interplay with #896

#896 is where the review happens; this design is a producer into it, not a
parallel system. Concretely: every supplement's `evidence` field resolves to a
trace in the #896 store; the review card is rendered by the #896 surface; PII
redaction happens before the envelope is published (the publish path is the
gateway, and the gateway is where redaction belongs — not at the reader).

## 7. Open questions (decisions needed before S2)

1. **Channel name.** Recommend `learning` (§1). If #900's authors insist on
   `skills`, the *channel* may be renamed without touching the artifact name —
   but the artifact must not be called a skill.
2. **Envelope vs content handle.** Inline `body_md` is capped (proposal: a few KB,
   enough for a real lesson) because the bus is flat and the project file store
   has no content addressing. Larger supplements need the Files/grants path
   first. Decide the cap before S2 publishes anything.
3. **Memory overlap.** Shared lessons and taosmd shared memories are adjacent
   claims about the same world. Decide with @taOSmd whether a supplement is
   *stored* in memory at all or only referenced, so the two systems do not drift
   into contradicting each other. This is the one thing #900 says to build WITH
   @taOSmd rather than decide alone.
4. **Hardware-tier vocabulary.** Scoping is only as good as the tier names; bind
   them to the app-manifest `hardware_tiers` vocabulary (`CONTRIBUTING.md`,
   `x86-cuda-12gb` / `arm-npu-16gb` / `cpu-only`) rather than inventing a second
   one. `capabilities.py` carries `min_vram_mb` capability requirements, not tier
   names.
5. **Trusted-reviewer thresholds** for the post-S3 gate (who counts as trusted,
   how many confirm).
6. **Isolation mechanism** (§3.2), in two parts: (a) how the **user** (or
   project) boundary is enforced — a per-user namespace/ACL in the bus path is
   recommended, because it also gives per-channel scoping (the same v2 gap that
   gates `learning` today); (b) how the instance boundary is enforced, which a
   per-instance bus URL covers on its own. Both need @taOSmd's agreement before
   S2, and both are moot until the bus redesign below lands.
7. **What the bus redesign makes available** (§3.1, §8 S2). The A2A bus is under
   a maintainer hold since 2026-08-24, pending a redesign as a real connector
   (#2150, #2147). Whether S2 publishes over the redesigned connector, receives
   through project Files, or both, is the same "envelope vs content handle"
   question as Q2 and cannot be answered before that redesign is settled.

## 8. Slice plan

Three PR-sized slices against `dev`, each independently shippable and testable.
S1 and S2 are bounded and suitable for an external CLI agent; S3 touches identity,
governance and the audit surface, so it is **maintainer-review**. Verification
commands run from the repo root.

**S1. Local supplement layer — data shape, store, merge (no network).**
- Files: `tinyagentos/guides/store.py` (a `BaseStore` with the SCHEMA/MIGRATIONS
  discipline: supplements + generations + tombstones), `tinyagentos/guides/model.py`
  (envelope validation, content-addressed `id`, scope matching),
  `tinyagentos/guides/render.py` (the merge/append/stale rules of §5), wiring into
  a new supplement-aware guide-injection seam that appends to the compiled manual
  (the existing injection point is the import-time `SYSTEM_PROMPT = _load_manual()`
  in `routes/taos_agent.py`, which reads `docs/taos-agent-manual.md`; `build_manual()`
  is a pure function returning session constants and does not inject the compiled
  manual; S1 declares its own seam alongside that load), session-only routes
  `tinyagentos/routes/guides.py` (`GET/POST /api/guides/supplements`),
  `tests/test_guides_store.py`, `tests/test_guides_render.py`.
- No bus traffic, no cross-agent effect: an agent can hold and render its *own*
  personal supplements.
- Acceptance: (a) a personal supplement changes what that one agent renders for
  its target guide; (b) canonical guide text is byte-identical with supplements
  present; (c) a supplement lacking evidence is rejected; (d) a supplement pinned
  to an older `guide_version` renders as stale, not applied; (e) two conflicting
  promoted supplements both render, ranked — neither is dropped.
- Verify: `uv run pytest tests/test_guides_store.py tests/test_guides_render.py -q`

**S2. Publish / subscribe on the `learning` channel. BLOCKED on the bus redesign.**
- **Blocked, and not only on the isolation question.** The A2A bus has been on
  hold since 2026-08-24 pending a redesign as a real connector (#2150 moves agent
  comms into taOStalk + Projects; #2147 keeps the bus a control plane with bytes
  in project Files). S2 is the first slice that touches the bus, so it is blocked
  until that redesign lands *and* the §3.2 user/project isolation is enforceable.
  Do not start S2 on today's read-only proxy. S1 carries no bus traffic and is
  not affected.
- Files: `tinyagentos/guides/bus.py` (publish via the authenticated send proxy,
  mechanical poller with a `since` cursor, user/project namespace check, promotion
  verification, batch ordering, tombstone-first), config allowlist/opt-out and
  the user/project isolation setting in `config.py`, a startup task in `app.py`,
  `tests/test_guides_bus.py` (mocked bus + mocked proxy, cursor, scope,
  isolation and unattested-promotion cases).
- Acceptance: (a) a `fleet` supplement published by agent A renders on agent B
  with no LLM turn spent (the poller is asserted to be script-only);
  (b) re-polling with the same cursor adopts nothing twice; (c) an out-of-scope
  supplement is stored as seen but not adopted; (d) a `status=review` supplement
  is never adopted; (e) an authorized tombstone removes the supplement and its
  supersedes chain on the next sync; (f) publication is refused for an agent not on the
  allowlist; (g) a `status=fleet` supplement carrying **no verifiable
  `promotion`** — absent, or `by` resolving to a non-reviewer — is stored as seen
  and NOT adopted; (h) a supplement from a foreign instance namespace is not
  adopted (§3.2); (i) a supplement published by **another user** on the same
  instance is not adopted — the subscriber's store is keyed by owning user
  (§3.2); (j) a tombstone from an identity that neither promoted the
  supplement nor authored it leaves the target and its superseded chain
  unchanged; (k) a tombstone and a re-publish of the same id in one batch leave
  the withdrawn version removed.
- Verify: `uv run pytest tests/test_guides_bus.py -q`

**S3. Review gate + #896 governance surface. MAINTAINER-REVIEW.**
- Files: `tinyagentos/guides/review.py` (state machine, submit/approve/reject/
  retract), Decisions-card creation on submit and republish-on-approve in
  `tinyagentos/routes/guides.py`, evidence linkage to the #896 trace store,
  audit events, pre-publish redaction hook, `tests/test_guides_review.py`.
- Touches the identity/grant path (publishing a promoted supplement) and the
  audit surface; review by the maintainer before merge.
- Acceptance: (a) an unreviewed supplement never reaches another agent
  (the S2 adopt path is proven to reject anything but `fleet`); (b) answering the
  Decisions card promotes exactly that supplement and republishes it once;
  (c) rejection is reported to the author, not silently dropped; (d) a retraction
  drops the supplement fleet-wide within one sync; (e) the review record answers
  "who promoted this, when, on what trace".
- Verify: `uv run pytest tests/test_guides_review.py -q` plus one manual
  Decisions round-trip on a dev instance.

## 9. Non-goals (v1)

- No editing of the read-only canonical guides — supplements are a layer, always.
- No cross-user or cross-instance / community sharing; that needs the user
  boundary enforced, plus signing and the hub model.
- No change to the capability `SkillStore`, `/api/skills`, or tool schemas.
- No silent conflict resolution, no auto-adoption of cross-user lessons.
- No new retrieval API: guides render into the manual; supplements are not a
  search corpus.

One-line summary: the bus gives identity and transport, #898 gives the artifact,
#896 gives the gate — this doc is the shape that connects them, with review
strictly before spread.
