# Cross-User Collaboration - Design and Spec

**Status:** Draft for owner review
**Epic:** Cross-User Collaboration (flagship)
**Pilot:** hogne (real second taOS user, own instance, own network)
**Baseline:** origin/dev `4adb976d`

This spec lets a taOS user add another taOS user as a contact, invite them as a
human collaborator on a project, receive their delegated agents into that
project (A2A + task claims), and chat with them human-to-human in taOS instead
of an external messenger. It is the foundation for shared projects between taOS
users and for a future dev-swarm where users donate agents to a shared project.

---

## 1. North-star walkthrough (the hogne scenario, end to end)

**Day 0 - Contact.** Jay opens **Contacts** (evolved ContactsApp). A new "taOS
Network" section shows his hub identity (`jaylfc@taos.my`). He searches `hogne`,
hits **Add Contact**, which fires the existing `POST /api/hub/friends/request`
through the account proxy. hogne sees the request in his own Contacts app
(surfaced via **Notifications** plus a **Decisions** card) and accepts. Both
instances now hold a signed `friend` edge and each other's Ed25519/X25519 public
keys. A **peer link** handshake runs automatically on accept: each instance mints
the other a scoped peer token (section 2), establishing the instance-to-instance
control channel.

**Day 1 - Collaborator invite.** In the **Projects app**, on the `taOS` project,
Jay opens Members, then **Invite Collaborator**, and picks hogne from his
contacts. This mints a `collab`-kind invite (invite_store, section 4) and sends
it as a signed envelope over the peer control channel. hogne gets a Decisions
card on his instance: "jaylfc invites you to collaborate on project 'taOS'" with
the project name, Jay's identity, and what membership means. He accepts; Jay gets
a confirmation notification; `project_members` on Jay's instance gains a row
`(member_id="hub:hogne", member_kind="human")`. hogne's instance records the
remote membership in its own **Shared Projects** list (Projects app, new "Shared
with me" section).

**Day 2 - Agent delegation.** In hogne's Projects app, the shared project card has
**Delegate Agent**. He picks his agent `grok-taos`. His instance sends a
delegation-request envelope; Jay's instance (per project policy: manual-approve
for v1) raises a Decisions card: "hogne wants to delegate agent 'grok-taos' to
taOS (scopes: a2a, tasks, canvas-read)". Jay approves, and Jay's instance mints a
standard `external-selfjoin` registry identity **tagged
`sponsor_contact_id="hub:hogne"`**, mints the scoped JWT, and returns the
connection bundle over the peer channel. hogne's instance hands the bundle to his
agent's existing cron-poll harness. From that moment
`grok-taos-20260718-...` appears in Jay's Agents app (with a "delegated by hogne"
badge), speaks on the project A2A channel, and claims board tasks - identical
machinery to any external-selfjoin agent today.

**Day 2, ongoing - Human chat.** Jay opens **Messages**, sees hogne under
Contacts, opens a DM. Messages travel instance-to-instance over the peer channel
(store-and-forward outbox when hogne's box is asleep). The external messenger is
retired. Project A2A traffic from hogne's delegated agents also renders in the
project chat channel, so the humans see their agents talking.

**Surfaces touched:** Contacts app, Notifications, Decisions app, Projects app
(Members, Shared-with-me, Delegate Agent), Agents app (delegated badge,
kill-switch), Messages app (remote DM), Settings (Collaboration section: peer
links, revocations).

---

## 2. Identity model (the crux)

**Decision: hub identity is the canonical anchor; locally a new `contacts` store
row; remote humans NEVER hit the local API directly in v1.** All human-side
interaction flows instance-to-instance: hogne uses his own taOS; his instance is
the API client of Jay's instance.

**Why not `origin="external-human"` in agent_registry:** rejected. The registry's
whole lifecycle is agent-shaped - heartbeats, LiteLLM virtual keys, deploy state,
`VALID_SCOPES` vocabulary, pending-to-active on selfjoin. A human row would be a
lie the rest of the system acts on: Observatory would try to observe it,
model-sync would try to key it, the board would treat it as claimable labor. The
closed enum (`agent_registry_store.py`, default `taos-deployed`) is closed on
purpose; widening it to smuggle humans in trades one migration for permanent
semantic ambiguity in every consumer.

**Why not a full `remote_users` mirror of agent_registry:** rejected as scoped for
v1. A registry-shaped store implies remote humans authenticate directly against
the local API with their own JWTs, which drags in session semantics, CSRF,
per-human rate limiting, and a second login system, for a capability (remote
human browsing Jay's box) we explicitly do not want in v1. The local multi-user
work is about OS-grade users on one box; conflating it with remote collaborators
would poison both designs. If direct remote-human API access is ever wanted, it
becomes a v2 on top of the contact row, not a prerequisite.

### Schema

**New `contacts_store.py`** (SQLite, same conventions as sibling stores):

```sql
CREATE TABLE contacts (
    contact_id        TEXT PRIMARY KEY,       -- "hub:{username}" e.g. "hub:hogne"
    hub_username      TEXT NOT NULL UNIQUE,
    display_name      TEXT NOT NULL,
    ed25519_pub       TEXT NOT NULL,          -- pinned at friend-accept
    x25519_pub        TEXT NOT NULL,
    status            TEXT NOT NULL,          -- pending|active|blocked|revoked
    local_crm_id      TEXT,                   -- optional link to existing CRM row
    created_at        REAL NOT NULL,
    revoked_at        REAL
);

CREATE TABLE peer_links (
    contact_id        TEXT PRIMARY KEY REFERENCES contacts(contact_id),
    inbound_token_hash  TEXT NOT NULL,        -- token WE minted for their instance
    outbound_token      TEXT NOT NULL,        -- token THEY minted for us (stored in plaintext; encryption deferred to post-MVP)
    endpoints         TEXT NOT NULL DEFAULT '[]',  -- their advertised endpoints (LAN/mesh/relay)
    established_at    REAL NOT NULL,
    last_seen_at      REAL,
    revoked_at        REAL
);
```

### Auth story for the peer channel

- **Handshake (contact-accept, collab-invite, delegation-request):**
  Ed25519-signed envelopes. Each envelope MUST bind the sender, recipient, and
  message kind into the signature domain via canonical serialization
  (`canonicalize({from_hub_username, to, kind, body, ts, nonce})` before signing)
  so a captured envelope cannot be replayed against a different recipient or
  reinterpreted as a different kind. The verifier MUST reject envelopes whose
  `to` field does not match the local identity. A freshness window on `ts`
  (default 300 s) plus a persistent replay cache keyed by
  `(contact_id, kind, nonce)` records accepted nonces before triggering side
  effects; stale, reused, or misaddressed envelopes are rejected.
  Signatures are verified against the pinned contact pubkey (trust-on-first-use
  at friend-accept; hub lookup only for initial discovery). Signed envelopes are
  valid even when relayed by the hub - the hub cannot forge them, but the replay
  cache and audience check prevent misrouting.
- **Steady state:** bearer **peer token** (opaque, hashed at rest, same pattern as
  registry tokens), presented by the remote instance to a new, narrow route family
  `POST /api/peer/{inbox,chat,ack}`. `sub` concept: `contact:{hub_username}`. Peer
  tokens grant only the peer route family, never the general API, never registry
  scopes. This keeps the JWT/scope world (agents) and the peer world (instances)
  disjoint, so nothing in `VALID_SCOPES` needs to change for humans and the
  equality-tested `_ALLOWED_SCOPES` sync stays untouched.

**Peer token vs. project-level authorization:** the peer token authenticates the
  contact identity and unlocks the route family. However, operations that carry a
  `project_id` payload (delegation requests, chat channel events, activity digests)
  MUST additionally verify that the contact holds an active
  `member_kind=\"human\"` row in the target project before accepting the data.
  Contact identity and the UI-selected project or channel value are inputs, not
  authorization; the receiver enforces project membership, target-project policy,
  and channel membership independently on every peer operation.

**Consequence accepted:** because remote humans do not hit the local API, "what a
human member can do" in v1 is bounded by what the peer channel carries (section
4). That is a feature: it makes the security review tractable and the pilot small.

---

## 3. Contacts

**Reuse hub friend-requests as-is.** The hub layer (request/accept/decline/block/
mute, signed edges, presence) is working and already the exact trust primitive:
**contact = accepted `friend` edge**. We do not wrap or re-implement; the contacts
store subscribes to friend-edge changes (accept creates a contact row plus kicks
off the peer-link handshake; block/revoke cascades per section 8). `EDGE_KINDS =
(REL_FRIEND,)` in `hub/relationships.py` stays the authority on who can become a
contact.

**ContactsApp.tsx: evolve, do not replace.** The offline CRM is real user value
and half the model we need (people with names/notes). Plan:

- Add nullable `linked_hub_username` to CRM contact rows; unified list with a
  "taOS" badge on linked contacts.
- New panel: incoming/outgoing friend requests (backed by existing
  `/api/hub/friends/*` via account proxy), presence dots (existing
  `/api/hub/presence`), and per-contact collaboration status (shared projects,
  delegated agents, peer-link health).
- "Add contact" search hits hub lookup (already in the account-proxy allowlist:
  `hub/identity/lookup`).

Rejected: a separate "Network" app - two people-lists is a UX bug.

---

## 4. Human collaborator membership

**Decision: `member_kind="human"` in the existing `project_members` table**
(widen the validation at `project_store.py:263` to `("native", "clone", "human")`;
`member_id = contact_id`). Rejected: a separate `project_human_members` table -
the members list, activity feed, and lead logic all iterate one table today; a
second table forks every consumer for zero modeling gain. The row shape fits
(`role`, `is_lead`, `added_at` all meaningful; `memory_seed='none'`,
`source_agent_id=NULL`).

**What a human member can DO in v1 - minimal:**

| Capability | v1 | Notes |
|---|---|---|
| Appear in members list, activity attribution | yes | badge "remote" |
| Sponsor delegated agents (section 5) | yes | the payoff |
| Project chat participation | yes | via section 7 transport, attributed to `hub:hogne` |
| Receive activity digest (task moves, completions) | yes | pushed as peer-channel events; rendered in their Shared-with-me card |
| Direct board/canvas manipulation, file access | no | v2; their agents do board work under scoped JWTs |

This is honest about the architecture (section 2): the human is a **trust
container plus chat participant**; labor flows through delegated agents. It also
matches the strategic frame - dev-swarm donors do not need board write access,
their agents do.

**Invite flow:** add a `kind` column to `project_invites` (`agent`, today's
default, or `collab`). `collab` invites reuse mint/TTL/attempt-cap/claimed-state
machinery (#2002) unchanged. Delivery: signed envelope over the peer channel (or
hub relay if offline). **PIN: keep it**, delivered out of band (for the pilot:
literally the last thing Jay ever sends hogne on the external messenger).
Rationale: the envelope signature already authenticates the sender, but the PIN
defends against a compromised peer instance auto-accepting; it is cheap and
UX-consistent with existing invites. Mark it a per-invite toggle (`pin_required`,
default on) so future auto-mediated flows (section 5) can waive it under policy.

**Consent on BOTH sides:** the inviter consents by minting; the invitee consents
via a Decisions card on their own instance (accept/decline), mirroring the
agent_auth_requests pattern. No silent membership ever.

---

## 5. Agent delegation (the payoff)

**Decision: auto-mediated invite handshake, NOT federation.** Evaluated head to
head:

| | Mediated handshake (recommended) | True federation (bus/registry sync) |
|---|---|---|
| Agent auth | normal external-selfjoin JWT from Jay's registry | cross-instance trust of hogne's tokens |
| Code delta | new envelope kinds + 1 registry column | federate two unauthenticated buses (7900 is LAN-raw), registry replication, conflict resolution |
| Blast radius | Jay's existing scope enforcement, unchanged | new distributed-trust surface |
| Agent-side work | zero - agents already speak this dialect | new client protocol |

Federating the A2A bus is disqualifying on its own: the taOSmd bus is
127.0.0.1-bound, LAN-unauthenticated, write-gated only by the controller proxy.
Federation would mean either exposing it or building an authenticated bus bridge,
a whole epic. The mediated handshake makes cross-user agents literally
indistinguishable from today's external agents at every enforcement point.

**Flow:** hogne selects agent plus project, his instance sends a
`delegation-request` envelope `{agent_slug, display_name, requested_scopes,
project_id}`, Jay's policy gate (v1: manual Decisions card; policy knob
`auto_approve_delegation` per project for the dev-swarm future), on approve Jay's
instance mints a `collab`-sponsored **project invite** (`kind="agent"`,
`pin_required=false` - the envelope chain substitutes) and returns
`{invite_id, connection_bundle}` over the peer channel, hogne's instance drives
the standard claim flow, and Jay's registry mints identity
`origin="external-selfjoin"` with:

```sql
ALTER TABLE agent_registry ADD COLUMN sponsor_contact_id TEXT;  -- NULL for all existing rows
```

**Sponsorship semantics:**

- Delegated rows carry `sponsor_contact_id`; the Agents app shows "delegated by
  {contact}".
- **Cascade:** revoking the contact (or their project membership) revokes tokens
  of all identities where `sponsor_contact_id` matches (scoped to that project for
  membership-revoke; all projects for contact-revoke), unassigns their in-flight
  board tasks back to `ready`, and posts an A2A system line. Token revocation is
  a bulk operation; additionally, **every delegated request** (task claims, A2A
  calls, canvas reads, feed fetches) MUST perform a fail-closed runtime check:
  verify current sponsor-contact status (`active`) and project membership
  (`member_kind=\"human\"`, not removed) at call time, not just at token issuance.
  A bearer JWT from a now-revoked sponsor is rejected even if the JWT itself has
  not expired. Queued outbox deliveries for a revoked contact are purged, and
  in-flight task assignments for their sponsored identities are unclaimed back to
  `ready` atomically with the revocation transaction.
- **Trust tiers:** default grant set for delegated agents is `{a2a_send,
  a2a_receive, project_tasks, canvas_read, registry_feeds_read}`. The
  project-scoped grants (`project_tasks`, `canvas_read`) MUST carry a
  non-null, owner-validated `project_id` claim in the minted JWT that is
  immutable for the grant lifetime; reject null or mismatched `project_id`
  at every project-scoped route, following the existing scope contract in
  `agent_auth_requests.py`. Anything more
  (`files_read`, `canvas_write`, `memory_*`, `tools_execute`) requires an explicit,
  per-scope Decisions approval and is displayed permanently in the Agents app.
  `files_write` and `decisions_write` are **denied to sponsored identities in v1**
  (enforced in grant minting, not just UX).
- **Kill-switch:** three levels - per-agent revoke (existing registry revoke),
  per-contact "pause collaboration" (suspends peer link plus all sponsored tokens,
  reversible), per-instance panic (Settings, Collaboration, Disable all, kills the
  peer route family at the router).

---

## 6. Transport

The unsolved problem is the friend-to-friend leg (tailnets are per-account; the
hub is rendezvous-only). Split control plane from data plane:

- **Control plane** (invites, consent, delegation, chat, digests): low-volume
  signed envelopes. Delivered direct when reachable; else **hub store-and-forward
  relay** - the hub queues sealed (X25519, section 7) envelopes up to 32KB, TTL 7
  days, recipient polls via the account proxy. This respects the hub charter: it
  never sees plaintext and never becomes the system of record (instances keep the
  authoritative copies).
- **Data plane** (delegated agent to Jay's controller: JWT poll, task claim, A2A
  post): needs a real network path; relaying agent traffic through taos.my is a
  cost/abuse non-starter.

**Data-plane options weighed:**

1. **Guest node on the collaborator's headscale namespace plus ACL pinning** -
   hogne's instance (one node) joins Jay's namespace via a preauth key minted at
   delegation-accept, with headscale ACLs restricting that node to `controller:6969`
   only. Reuses landed mesh slices (#1770/#1772 credential persistence plus
   headless join). Risk: headscale ACL policy must actually be enforced per
   node-tag - this is a hard gate, not a nice-to-have; an un-ACL'd guest sees the
   whole tailnet.
2. Per-project shared subnet - rejected: headscale has no first-class cross-tailnet
   sharing; inventing subnet slicing is more work than ACLs for less isolation.
3. Hub data relay - rejected for data plane (volume, cost, hub charter).

**Recommendation - phased, pilot-first:**

- **Phase T0 (PILOT, works early):** hogne's instance gets a guest preauth key onto
  Jay's namespace, ACL-pinned to port 6969. Everything (control plus data) flows
  over that WireGuard path. Pilot needs: headscale ACL support verified plus one
  "mint guest preauth" flow plus build_connection_bundle already advertising the
  mesh endpoint. No hub relay required for the pilot at all - hogne is online
  enough that direct delivery plus a sender-side outbox suffices.
- **Phase T1 (general):** hub sealed-envelope relay for the control plane (offline
  contacts, NAT-hostile cases), guest-node join productized as the standard data
  plane with automated ACL management.
- **Phase T2 (later, if guest-join friction bites):** pairwise WireGuard
  negotiated over the control plane, removing the shared-namespace requirement
  entirely. Explicitly deferred.

---

## 7. Human-to-human chat (replace the external messenger)

**Decision: extend the chat store; transport over the peer channel; the hub stays
out of it.** The "hub is not a second messenger" non-goal survives intact: the hub
carries sealed envelopes it cannot read only when peers are offline (T1) -
rendezvous and dead-drop, never a message store of record. Project DMs and contact
DMs are Messages-app territory; each instance holds its own full mirror (consistent
with data-on-your-node).

**Schema (surgical):**

- `chat_channels`: new `type="dm-remote"`; `members` JSON gains entries like
  `{"kind":"contact","id":"hub:hogne"}`.
- `chat_messages`: `author_type` stays `{user, agent}`; remote humans are
  `author_type="user"` with namespaced `author_id="hub:hogne"` (local user keeps
  the bare id - no ambiguity, no enum change). Add `remote_msg_id TEXT`
  (sender-assigned UUID) for idempotent delivery, `delivered_at REAL NULL`.
- New `peer_outbox` table: `{id, contact_id, envelope, attempts, next_retry_at,
  created_at}` - store-and-forward with exponential backoff; drained on peer-link
  `last_seen` refresh.

**Delivery semantics v1:** at-least-once with `remote_msg_id` dedupe; the
`chat_messages` table MUST enforce an atomic uniqueness constraint on
`(contact_id, channel_id, remote_msg_id)` — insert and commit the message before
sending the delivery acknowledgment, and treat uniqueness-constraint conflicts
as successful duplicate deliveries (no second row, no error surface). Delivery
acks (double-tick equivalent) via `POST /api/peer/ack`; **read receipts deferred**
(optional v1.1, off by default). Ordering: per-channel sender timestamps,
render-side sort - good enough for two humans.

**Encryption:** on the T0 pilot path, transport is WireGuard - mandating E2E now
buys little and costs key-management UX. **Decision: not-E2E in v1 for
direct/mesh delivery, BUT any envelope that transits the hub relay MUST be
X25519-sealed from day one** (the primitive exists in `hub/identity.py`; sealing
is a function call, not an epic). This means the sealing code ships in v1 anyway,
and flipping "seal everything" on becomes a v1.1 toggle rather than a redesign.
Honest framing: v1 trust model is "trust your own two instances plus WireGuard,"
not "trust nobody."

**taOStalk:** untouched - it is the operator's own CLI sessions in chat
(#1952-57). Coordinate only on chat-schema migrations landing in the same window.

---

## 8. Security model

**Trust chain (each link independently revocable, each mints strictly less
power):**

| Link | Established by | Grants | Revocation effect |
|---|---|---|---|
| 1. Contact accept | mutual human consent (hub friend edge) | peer link: signed-envelope exchange plus peer routes ONLY | kills peer link; cascades 2-4 |
| 2. Collab invite accept | inviter mints plus invitee Decisions consent (plus PIN) | `member_kind="human"` row: chat, digests, right to request delegation | removes membership; cascades 3-4 for that project |
| 3. Agent delegation approve | sponsor requests plus owner Decisions consent | one external-selfjoin identity, `sponsor_contact_id` set | revokes that identity's token, unassigns tasks |
| 4. Scope grant | owner per-scope Decisions consent | additions beyond default tier | narrows JWT at next mint; immediate via grant-store check |

**What a malicious contact/instance CAN do (v1):** spam chat/invite/delegation
envelopes (rate limits below); have an approved agent post A2A noise or
claim-and-stall board tasks (visible in Observatory, task-reassign heals,
kill-switch); read project A2A plus task metadata within granted scopes; probe
port 6969 if ACL-pinned onto the mesh.

**CANNOT do:** touch any non-peer API route (peer tokens are route-family-bound);
read files/memory or write decisions (scopes denied to sponsored identities);
impersonate another contact (Ed25519 pinned at accept - hub compromise cannot
forge envelopes); escalate scopes without a Decisions card; reach other mesh
nodes (ACL gate - this is why headscale ACL enforcement is a **launch blocker**
for T0, verified by a test that a guest node cannot reach a second node); persist
after contact-revoke (cascade is transactional: contact row, peer link,
memberships, sponsored identities, outbox purge).

**Rate limits:** peer routes 60 req/min/contact, chat 600 msgs/day/contact,
pending delegation requests at most 3/contact (mirrors invite pending-cap 10
spirit), envelope size at most 32KB. Hub relay: at most 200 queued
envelopes/recipient.

**Composition with existing auth:** three disjoint credential planes - browser
sessions plus CSRF (local human, unchanged), registry JWTs plus scopes (agents
including delegated, unchanged), peer tokens (new, narrowest). No plane can mint
credentials in another except through a human Decisions approval. Peer routes are
CSRF-exempt (bearer-only, no cookies), same posture as agent routes.

---

## 9. Phased plan

**Lane key:** LEAD = maintainer (foundations/security-critical), HOGNEK = infra
lane (real build/test CI, fork-approval rules apply), FLEET = free-model builders
(bounded test/UI cards - explicit goal: many claimable cards).

### Milestone A - Contact plus peer link (pilot-critical)

- A1 LEAD: `contacts_store` plus `peer_links` plus envelope sign/verify plus peer
  route family plus rate limits.
- A2 LEAD: friend-accept to contact-row plus handshake wiring (hub subscription).
- A3 HOGNEK: hub sealed-envelope relay endpoints on taos.my (T1, not pilot-blocking,
  can trail).
- A4 FLEET (cards): ContactsApp taOS section UI; presence dots; request
  accept/decline panel; envelope-crypto unit tests; peer-route rate-limit tests;
  contact-revoke cascade tests.

### Milestone B - Collaborator membership (pilot-critical)

- B1 LEAD: `member_kind="human"`; invite_store `kind` column; collab invite
  mint/deliver/consent (Decisions card both sides).
- B2 FLEET: Members UI (remote badge, invite picker), Shared-with-me section,
  activity-digest renderer, invite state-machine tests, membership-revoke cascade
  tests.

### Milestone C - Transport T0 (pilot-critical, the risk item)

- C1 LEAD: headscale ACL spike - verify per-node pinning actually isolates a guest
  node. **Go/no-go gate for the pilot date.**
- C2 HOGNEK: guest-preauth mint flow plus ACL automation plus `mesh status`
  surfacing of guest links. (hogne building the leg his own instance will use, and
  he can test both ends.)
- C3 FLEET: ACL isolation test harness; connection-bundle endpoint-selection tests.

### Milestone D - Agent delegation (pilot payoff)

- D1 LEAD: delegation envelopes plus policy gate plus `sponsor_contact_id` plus
  sponsored-scope denylist plus cascades.
- D2 FLEET: Delegate-Agent UI (hogne side), delegated badge plus kill-switch UI
  (Jay side), Settings-Collaboration panel, delegation state-machine tests,
  scope-denial tests, task-unassign-on-revoke tests.

### Milestone E - Chat

- E1 LEAD: schema migration (dm-remote, remote_msg_id, delivered_at) plus
  peer_outbox plus delivery/ack path. (Migrations tested against an EXISTING
  pre-change DB per the standing rule.)
- E2 FLEET: Messages UI for remote DMs, delivery-tick states, outbox retry tests,
  dedupe tests, offline-queue drain tests.

**Minimal PILOT DEMO path:** A1, A2, A4 (UI subset), B1, C1, C2, D1, E1 - Jay and
hogne chatting while grok-taos claims a real board task. Everything else (hub
relay, read receipts, seal-everything, auto-approve policy) is general-product.

**Dependencies/adjacencies:** #1893 `user_shares_store` - do not resurrect here;
it is per-resource (memory) sharing for taOSnet. It should later take `contact_id`
as its `shared_with_user_id`, so land A1 first and note that on #1893. #1892
(Memory app switch): orthogonal. taOStalk slices: orthogonal, coordinate chat
migrations. Simple-agent-collaboration spec (#1968): the 30-min cron loop is
exactly what delegated agents run - no changes needed, which is the point. This
epic **subsumes** the hub design doc's open "friend-to-friend transport" question
(answers: guest-node T0 / sealed relay T1) and **leaves alone** the bus (7900),
multi-user local isolation, and taOSgo.

---

## 10. Decision points for the owner

1. **Remote humans never hit the local API in v1** - all interaction
   instance-to-instance via the peer channel. Recommended: YES. (Alternative:
   remote login sessions - big auth surface, delays pilot by weeks.)
2. **Human member capability floor** - v1 human members are trust-container plus
   chat plus digests; no direct board/canvas writes (their agents do the work).
   Recommended: minimal v1. (Alternative: read-only board mirror on their
   instance - nice, but it is a sync engine; defer.)
3. **Pilot transport = guest node on your headscale namespace, ACL-pinned to port
   6969**, gated on the ACL isolation spike passing. Recommended: YES, with C1 as
   hard go/no-go. (Alternative: wait for hub relay for everything - pilot slips and
   agent traffic through taos.my costs you.)
4. **Delegation approval mode** - v1 manual (Decisions card per agent),
   `auto_approve_delegation` knob exists but ships OFF. Recommended: manual for
   pilot, revisit for dev-swarm.
5. **Chat encryption** - v1 transport-encrypted (WireGuard/TLS) not E2E;
   hub-relayed envelopes X25519-sealed from day one; full E2E a v1.1 toggle.
   Recommended: accept. (Alternative: E2E now - key-rotation UX before we have
   shipped one message.)
6. **PIN on collab invites** delivered out of band (one last external message),
   waivable per-invite later. Recommended: keep PIN.
7. **Sponsored-agent hard denylist** - `files_write` plus `decisions_write`
   unmintable for delegated agents in v1, even with approval. Recommended: YES.
   (Alternative: everything approvable - one misclick from a foreign agent writing
   your files.)

---

## 11. Milestone F - Collaborator Community View (belonging surface)

**Added 2026-07-18 (owner request).** When you are a collaborator on a remote
project, your own Projects app should not just list it - it should open a rich,
read-mostly **Community View** of that project so you see your data involvement
and feel part of the team: a mini social-GitHub for the shared project. This
refines Decision 2: a human member is still a trust-container plus chat, but now
also gets a **rich read-only projection** of the project. Writes still flow only
through their delegated agents.

### Surfaces (the "Shared with me" card expands into)

- **Overview stats:** task throughput (claimed/closed over time), open /
  in-progress / done counts, active contributors, and *your* involvement (tasks
  your agents have claimed/closed, your streak).
- **Contributions + Leaderboard:** per-contributor claim/close counts, ranked -
  the "feel part of the team" surface. Derived from the board audit log (task
  claim/close events already carry the claimant); a human contributor's line
  aggregates all their sponsored agents.
- **Public live kanban:** a read-only projection of the board - available-to-claim,
  in-progress (with claimant badge), done - updating live. Read-only for the human;
  their agents claim through the normal scoped board API (Milestone D).
- **Community chat / social area:** the project A2A channel plus a human community
  channel, rendered in the shared view (humans post via the peer channel, section
  7). Announcements, task chatter, presence.

### The one new architectural piece: a scoped read-projection sync

Decision 1 says remote humans never hit the owner's local API. So the community
view is **not** direct API access - it is a **read-only project projection** the
owner's instance serves to member instances over the peer channel:

- **Projection contents (allowlist, non-sensitive only):** project metadata,
  board tasks (id, title, status, claimant display, timestamps, labels),
  contribution rollups, community-channel messages. **Never** files, memory,
  secrets, canvas payloads, or non-community chat.
- **Delivery:** pull-on-open (`peer` request `project-snapshot`) plus a live event
  push (task moves, new community messages) over the existing peer channel; the
  member instance caches and renders. This reuses the activity-digest transport
  from Milestone B, widened from a digest to a full scoped projection with a live
  feed. It is the read counterpart to Milestone D's write delegation.
- **Authorization:** the projection is minted only for active `member_kind="human"`
  members of that project; membership-revoke stops the feed and the member
  instance drops its cached projection (cascade, section 8).
- **Scope of "public":** v1 the community view is **member-scoped** (collaborators
  only). A truly public/anonymous view (adding a `projects.visibility` column defaulting to `'private'`, with `'public'`
  enabling an unauthenticated read projection served via the hub) is a clean later
  toggle on the same projection machinery — noted as a future schema change, not
  built in v1.

### Reconciliation with earlier decisions

- Decision 2 (minimal human member) is **refined, not overturned**: the human gets
  a rich read surface but zero board/canvas/file write. The projection is
  strictly read; all mutation still requires a delegated agent under a scoped JWT.
- No new credential plane: the projection rides the peer token (section 2), same
  route family, same rate limits.
- The leaderboard/streak data is a rollup over the board audit log
  (`board_audit_store`), which already records claim/close with actor - no new
  event source needed.

### Slices (F, folds into the phased plan)

- F1 LEAD: read-projection builder on the owner side (scoped snapshot + allowlist
  filter) plus the `project-snapshot` peer request plus the live-event push; the
  member-side projection cache.
- F2 LEAD: contribution/leaderboard rollup over the board audit log (per-contributor
  and per-sponsored-agent aggregation).
- F3 FLEET (cards): Community View UI (overview stats panel, leaderboard, read-only
  live kanban render, community chat pane); projection-cache render tests;
  membership-revoke drops-projection test; leaderboard-rollup unit tests.

This milestone depends on B (membership) and the peer channel (A); it is
**pilot-desirable but not pilot-blocking** - the minimal pilot can ship with the
plain "Shared with me" list and gain the rich view immediately after.
