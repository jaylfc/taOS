# hub.taos.my: own-your-posts P2P social network (founding design)

Status: DECIDED architecture (four locked decisions below), DRAFT protocol and
slice plan for Jay review. Author: taOS-dev.

## Why this exists

Every mainstream social network works the same way: your posts, your photos,
your friend graph, and your reach all live in someone else's datacenter. The
platform can delete your account, change the algorithm, sell the metadata, or
shut down, and everything you built goes with it. taOS users already run an
always-on personal server. That changes what a social network can be.

hub.taos.my is a Facebook/MySpace/X style social network for taOS users,
personal profiles and business pages alike, built on one principle: **a user's
posts and data live on their own taOS node.** Selected content is cached on
trusted friends' nodes so it stays available when the author is offline, and
the user always owns their content. No central silo owns posts. The server at
hub.taos.my is a directory and a meeting point, never a content store.

This is the social side the free taos.my username was created for (see
`docs/design/account-username-subdomain-model.md`, PR #1792): the username is
free precisely so identity on this network costs nothing, while the paid taOSgo
side stays what it is, infrastructure.

Two sibling surfaces are explicitly future phases and appear only in the
Roadmap section: **agenthub.taos.my** (agent profiles, a moltbook-style home
for the agents themselves) and **community compute projects** (a Kickstarter
for compute, pooling cluster capacity behind community goals). This document
designs the human network only.

## The four locked decisions

Jay chose these explicitly. They are the foundation and are not up for
re-litigation in review; everything else in this document serves them.

1. **Availability: friend-cache, best effort.** Posts are cached on trusted
   friends' nodes. If the author and every caching friend are offline at the
   same moment, the content is temporarily unavailable, and that is accepted.
   There is no central content storage. (An encrypted relay backstop that
   stores ciphertext for offline windows can exist later as a possible paid
   option; it is roadmap only, see Roadmap.)
2. **Hub role: directory + rendezvous only.** hub.taos.my does identity
   lookup, friend discovery, and connection brokering, the same spirit as the
   taOSnet tracker (`tracker.taos.my`), which coordinates a swarm without ever
   holding the payload. Content never touches the server unless a user
   explicitly opts a post public, and even public mirroring by the hub is
   roadmap, not v1.
3. **Identity: the free username mints a keypair.** Creating the free taos.my
   username account mints a signing keypair. The private key lives on the
   user's node, taos.my anchors username to public key, and all posts are
   signed. Recovery is account-based (the taos.my account can authorize a key
   rotation). Social recovery via friend co-signing is roadmap.
4. **V1 scope: profiles, posts, follows/friends, timeline.** Profiles, text
   and image posts, follow and friend relationships with a trusted-cache
   circle, and a timeline assembled from peers. Messaging is explicitly out of
   scope, taOS Messages (the Message Hub, channels, A2A) already exists and
   the hub is not a second messenger. Agent presence on the network is roadmap
   (agenthub).

## Non-goals (v1)

- **Messaging.** DMs, group chats, and channels stay in taOS Messages
  (`docs/design/message-hub-core.md`, the chat stores and A2A bus). The Hub
  app links to Messages for "message this friend"; it does not reimplement it.
- **Agent profiles.** Agents do not have hub identities in v1 (roadmap:
  agenthub.taos.my).
- **Central content hosting.** No post bodies, no media, no encrypted blobs
  stored at hub.taos.my in v1, in any tier.
- **Video and long media.** Text and images only. Video needs chunked
  transfer and much larger cache budgets; it is a later phase.
- **Public web view of profiles.** A signed-out browser hitting
  hub.taos.my/@alice and reading public posts requires public mirroring,
  which is roadmap. In v1 the network is read from inside taOS.
- **Algorithms.** The timeline is your own graph in time order. No ranking,
  no recommendations, no ads.
- **Pricing amounts.** The free/paid boundary is public (identity and the
  core network are free; a relay backstop, if built, would be paid). Amounts
  are private and never appear in this repo.
- **Federation with other protocols** (ActivityPub, AT Protocol). Worth
  studying later; v1 is taOS-to-taOS.

## Current state (verified in code)

What this design builds on, all already in the tree:

- **taOSnet closed swarm** (`tinyagentos/taosnet/torrent_client.py`,
  `docs/design/model-torrent-mesh.md`): the tracker+seeder pattern this hub
  reuses in spirit. A central coordinator (`tracker.taos.my`) authenticates
  peers with account-bound passkeys and introduces them to each other; the
  payload moves peer to peer. The hub directory is the same shape with
  identities instead of info_hashes.
- **Mesh membership** (`tinyagentos/taosnet/mesh.py`,
  `tinyagentos/taosnet/mesh_credentials.py`,
  `docs/design/taosgo-mesh-join-foundation.md`): the always-on host can join
  the account's Headscale tailnet and persists host-bound service tokens in a
  0600 credentials file. The hub node key store follows the same persistence
  pattern. Note the tailnet is per-account (your own devices); friend-to-friend
  transport is a new problem the hub's rendezvous role solves.
- **Account proxy** (`tinyagentos/routes/account_proxy.py`): the controller
  already proxies same-origin `/api/account/*` to taos.my with cookie
  pass-through and an explicit `_ACTIONS` allowlist. Every new hub directory
  endpoint is added the same way; the browser never sees the taos.my base URL
  and bearer credentials never reach browser JavaScript (the poll-intercept
  precedent).
- **Account client** (`desktop/src/lib/account-client.ts`): the
  degrade-to-state pattern (`signed-out` / `unavailable`, never throw) that
  every hub client call follows so the UI ships ahead of the backend.
- **Free username identity** (PR #1792,
  `docs/design/account-username-subdomain-model.md`): the free, unique,
  validated username namespace. This document consumes it as the identity key
  and adds the keypair anchor on top.
- **Messages / A2A** (`tinyagentos/routes/chat_*.py`, `tinyagentos/chat_*.py`):
  private comms exist and are good. The hub deliberately does not overlap.

## Identity and keys

### Key material

Registering the free username mints, **on the user's node**, two keypairs:

- **Signing key** (Ed25519): signs every object the user publishes (profile
  versions, posts, follows, circle grants, tombstones, rotation statements).
- **Encryption key** (X25519): receives wrapped content keys for friends-only
  content addressed to this user.

Private keys are generated locally and never leave the node. They persist in
a 0600 file under the data dir (`<data_dir>/hub/identity.json`), following the
`mesh_credentials.py` pattern (atomic write, allowlisted fields, env override
for tests). The node registers the two public keys with the directory.

The canonical author identifier inside every signed object is the signing
public key (its fingerprint), not the username. Usernames are display-layer:
the directory maps username to key, and clients render `@alice` by resolving
the key. This means a username policy change (renames, reclamation) can never
retroactively re-attribute content.

### Anchoring and lookup

taos.my anchors `username -> {signing_pubkey, encryption_pubkey, key_log}`.
The key log is append-only: every rotation appends a record, so any client can
see the full key history for an identity and when each key was valid.

### Rotation and recovery (account-based, v1)

Two rotation paths:

- **Self rotation** (node alive, key compromised or hygiene): the node submits
  a rotation statement signed by the OLD key introducing the new keys. Clean,
  cryptographically continuous.
- **Account recovery** (node dead, key lost): the user signs in to taos.my
  with the account (email + password, the normal auth), proves control of the
  account, and registers replacement keys. The directory appends a rotation
  record marked `recovery: true`, NOT signed by the old key.

Honesty about the trust tradeoff: account recovery means taos.my can, in
principle, rotate anyone's key. v1 accepts this because losing your node must
not mean losing your identity, and because the mitigation is visibility: every
rotation is in the public key log, clients surface "key changed via recovery"
prominently to that user's friends, and friends' nodes keep trusting content
signed by the old key up to the rotation timestamp while treating new-key
content as "re-verify this is really them" until the user confirms out of
band. Social recovery (M-of-N friend co-signing replacing the server's
unilateral ability) is the roadmap fix.

## Data model

All objects are canonical-JSON encoded (sorted keys, no floats where
determinism matters), content-addressed by SHA-256 of the canonical bytes,
and signed by the author's signing key. Content addressing gives free
integrity (a cacher cannot alter what it serves, the hash would break) which
is the same property that lets taOSnet trust arbitrary peers with model bytes.

### Post

```json
{
  "type": "post",
  "author": "<signing pubkey fingerprint>",
  "seq": 42,
  "prev": "<hash of post 41>",
  "created_at": "2026-07-12T10:00:00Z",
  "visibility": "circle" | "public",
  "body": { ... },
  "attachments": [{"blob": "<sha256>", "size": 123456, "mime": "image/webp"}],
  "sig": "<ed25519 over canonical bytes>"
}
```

- `seq` + `prev` make each author's posts a **hash chain**: a per-author
  append-only log. This buys three things at once: total order within an
  author (no clock trust needed), tamper evidence (a cacher cannot omit or
  reorder silently without breaking the chain), and cheap gap detection (a
  reader holding seq 40 and seeing head seq 44 knows exactly what to fetch).
- For `visibility: "public"`, `body` is plaintext
  (`{"text": "...", "format": "md-subset"}`).
- For `visibility: "circle"`, `body` is an encryption envelope (see Privacy
  tiers): the object structure, signature, and chain position are visible to
  cachers; the content is not.
- `attachments` reference blobs by hash. Blobs are raw bytes, fetched and
  cached separately, verified against the hash on receipt. Images are
  re-encoded on ingest (strip EXIF, cap dimensions) before hashing.
- Posts are **immutable** in v1. No edits; delete and repost (see Open
  questions for the edit recommendation).

### Profile

```json
{
  "type": "profile",
  "author": "<fingerprint>",
  "version": 7,
  "kind": "personal" | "business",
  "display_name": "Alice",
  "bio": "...",
  "avatar": "<blob hash>",
  "links": [{"label": "site", "url": "https://alice.taos.my"}],
  "sig": "..."
}
```

Profiles are mutable state, not chain entries: highest signed `version` wins,
replicas overwrite older versions. Profiles are always public (they are what
the directory and friend discovery show). A `business` profile is the same
object with a different kind and rendering, not a separate system; a user's
account can publish additional business profiles under additional usernames
only if they hold them (out of scope here, the username model governs that).

### Follow edge vs trusted-cache circle

Three distinct relationships, because **a follower is not automatically a
cacher**:

- **Follow** (one-way): a signed statement `{type:"follow", author, target,
  sig}` published by the follower. Grants nothing beyond "my node subscribes
  to your public posts." The target does not approve follows; they can block.
- **Friend** (mutual): both sides accept a brokered friend request. Friendship
  grants **readership of friends-only posts**: the author includes the
  friend's encryption key when wrapping circle content keys. Friendship is
  recorded on both nodes as a pair of signed statements.
- **Trusted-cache-circle membership** (explicit grant on top of friendship): a
  signed grant `{type:"cache-grant", author, grantee, quota_hint, sig}` from
  the author. It grants the friend the right AND responsibility to **cache**
  the author's recent content and to **serve** it to the author's other
  authorized readers when the author is offline. Every circle member is a
  friend; not every friend is in the circle. The UI nudges users to grant
  cache rights to a handful of close, high-uptime friends (family, the
  household's other taOS boxes).

Keeping readership and cache duty separate matters: readership is a privacy
decision, caching is a resource and availability decision, and conflating
them either bloats every friend's disk or shrinks your readable audience.

### Deletion: signed tombstones, honestly

Delete is a chain entry:

```json
{"type": "tombstone", "author": "...", "seq": 45, "prev": "...",
 "target": "<post hash>", "sig": "..."}
```

Compliant nodes (all taOS nodes) drop the post body and blobs on receipt and
retain only the tombstone, so the chain stays verifiable and the deletion
propagates to anyone who later syncs. Cachers honor tombstones as a condition
of the cache grant.

Being honest, as any replicated system must be: **deletion is best effort.**
A friend's node that is offline for a month deletes a month late. A
screenshot, a modified client, or an exported cache can persist copies
forever. This is not a weakness versus centralized networks (the same is true
there, plus the operator keeps a copy); the difference is we say it plainly:
delete means "stop distributing and ask everyone to drop it," not "unhappen."

Revoking a cache grant is likewise a signed statement; the ex-cacher must
drop the cache. Revoking friendship additionally triggers circle key rotation
(see Privacy tiers) so future posts are unreadable to them.

## Privacy tiers: cryptographic, not policy

Two tiers in v1, and the enforcement is encryption, not server rules:

- **Friends-only (the default).** The post body is encrypted with a symmetric
  per-author **circle key**. The circle key is distributed by wrapping it to
  each friend's X25519 encryption key (small friend counts make per-friend
  wrapping cheap). When a friend is removed, the author mints a new circle
  key for subsequent posts and re-wraps to the remaining friends. Old posts
  remain readable to the removed friend if they kept the old key and
  ciphertext; that is stated honestly rather than pretended away (it is the
  screenshot problem in cryptographic form).
- **Explicitly public.** Plaintext, signed, servable by the author and any
  cacher to anyone. Publishing publicly is a deliberate per-post act, never a
  default, and the composer makes the difference loud.

Because friends-only bodies are ciphertext, **cachers never need to be
trusted with content confidentiality**, only with availability. A circle
member's node can serve ciphertext blobs to any requester who names the hash;
only holders of the circle key can read them. In practice cachers still
require requests to be signed by a known hub identity (any registered key) to
resist anonymous scraping and DoS, but that check is abuse resistance, not
the privacy boundary. The privacy boundary is the key.

Metadata honesty: cachers and the directory can see that an author posted
(chain heads, object sizes, timing) without seeing what. The Abuse/safety
section and Open questions return to how much graph metadata the hub holds.

## Sync protocol (sketch)

This section is a sketch to be hardened in the design-review-gated slices; the
shapes below are the intended v1.

### Transport and rendezvous

Friend nodes talk directly over HTTPS/QUIC with mutual authentication by hub
identity keys (a signed handshake, not TLS client certs, keeping the server
stack simple). Connection paths, in order of preference:

1. **Same LAN**: mDNS discovery, direct connect (two taOS boxes in one house,
   the common family case, costs nothing).
2. **Direct over the internet**: each node advertises reachable endpoints to
   the hub in a presence heartbeat; the hub returns a friend's endpoints on
   request (rendezvous). Works when at least one side has a reachable port or
   hole punching succeeds.
3. **Failure**: accepted. Locked decision 1 means an unreachable pair simply
   syncs later or through a shared circle member that both can reach. A relay
   is roadmap, not v1.

The hub's rendezvous role is deliberately the tracker pattern from taOSnet:
authenticate, introduce, get out of the way.

### Learning about new posts (push hint + pull sync)

- **Both online**: after committing a post to its chain, the author's node
  sends a tiny **head announcement** `{author, head_hash, seq, sig}` directly
  to each online circle member and any online friends. Recipients compare seq
  against what they hold and pull the missing chain range, then blobs.
- **Recipient offline**: the author may leave a **hint** at the hub,
  `{to, author, seq}`, a few bytes, TTL-bounded, no content. When the friend's
  node next heartbeats, it collects hints and knows whom to pull from. Hints
  are an optimization only; correctness never depends on them.
- **Pull baseline**: on timer and on timeline refresh, a node asks each
  friend (or their cachers) "what is @alice's head?" and pulls gaps. Pull is
  the invariant; push and hints just make it fast.

Sync within one author is trivially convergent because the chain is
append-only and content-addressed: there is nothing to merge, only ranges to
fetch and verify (hash chain + signature on every object).

### What the circle grant costs: cache quotas

A cache grant carries a `quota_hint` and the grantee enforces a real local
quota per friend (default modest, order of a few hundred MB, configurable in
the Hub app). Retention is recency-first: newest posts and their blobs stay,
oldest evict, tombstoned content evicts immediately. The author's own node is
always the full archive; cachers are a rolling window, which matches what
availability actually needs (nobody's timeline breaks because a three-year-old
photo is momentarily unavailable). A cacher advertises to the requester which
seq range it holds, so readers know when they are seeing a window.

### Serving as a cacher

When @alice is offline and @bob (circle member) is asked by @carol (alice's
friend) for alice's chain range 40 to 44: bob checks carol's request signature
is a registered identity, serves the signed objects and ciphertext blobs, and
carol verifies every hash and signature herself. Bob can neither forge nor
read what he serves. Public posts serve the same way minus the ciphertext.

## Timeline assembly

The timeline is assembled locally from the node's own store:

1. Everything already synced sits in the local hub store (SQLite in the data
   dir, alongside the chat stores' pattern).
2. On refresh, the node queries the head of each followed/friended author,
   preferring the author, falling back to their cachers, and pulls gaps.
3. Ordering: by `created_at`, with the author's chain order as the tiebreak
   and constraint (a chain never renders out of order even if clocks skew).
4. **Gap handling**: if @alice's chain shows seq 40 held and head 44 known
   but 41 to 43 unreachable right now (author and cachers offline), the
   timeline renders a quiet inline placeholder ("3 posts from @alice are on
   nodes that are currently offline") and a background retry fills them in
   later. Best effort is a feature with a face, not a silent hole.
5. Offline reading always works: the timeline never needs the network to show
   what is already local.

## Directory API surface (taos.my)

Server side lives in `jaylfc/taos-website` (same repo as the account service
and taOSnet tracker; endpoint shapes here are the contract, that repo's
layout governs exact paths). Everything is session-authed via the existing
account auth unless noted; the controller reaches it same-origin through new
`_ACTIONS` entries in `tinyagentos/routes/account_proxy.py`, so cookies
round-trip exactly as today and no new auth surface appears on the client.

- `POST /api/hub/identity/register` `{signing_pubkey, encryption_pubkey,
  proof}`: anchors keys to the account's username. `proof` is a signature
  over a server-issued challenge, showing the node holds the private key.
  One identity per username.
- `GET /api/hub/identity/lookup?username=alice` (public, rate-limited):
  `{username, signing_pubkey, encryption_pubkey, key_log: [...]}`.
- `POST /api/hub/identity/rotate` `{new_keys, old_key_sig | recovery}`:
  appends to the key log (see Rotation and recovery).
- `POST /api/hub/presence` `{endpoints: [...], sig}`: heartbeat with the
  node's current reachable endpoints. TTL minutes; stale entries drop.
- `GET /api/hub/presence?username=alice`: returns endpoints only if the
  requester's identity is authorized (an accepted edge exists, see below).
- `POST /api/hub/requests` `{to, intro, sig}`: friend-request brokering. The
  hub queues the signed intro for the target (this is the one queued payload
  the hub holds, it is a request envelope, not content). Rate-limited per
  sender and per target.
- `GET /api/hub/requests` / `POST /api/hub/requests/{id}/accept|decline`:
  inbox and disposition. Accept records an authorization edge server-side
  (who may query whose presence / leave hints) and returns the parties'
  endpoints so the nodes complete the friendship handshake directly.
- `POST /api/hub/hints` `{to, author, seq}` / `GET /api/hub/hints`: the tiny
  TTL'd new-head hints, only along accepted edges.

The directory therefore stores: username-to-key anchors and key logs,
presence (ephemeral), pending friend requests (transient), and accepted-edge
authorization rows. It stores no posts, no blobs, no ciphertext, and it never
learns post content in any tier. The edge table is real metadata the server
holds and Open question 3 addresses how minimal it can be.

Reuses from the existing stack: account auth and session cookies, the
`account_proxy.py` forwarding + rid-style input validation pattern, the
tracker's authenticate-and-introduce posture, and the free-username namespace
from PR #1792 as the one and only identity namespace.

## The Hub app (client)

- **In-OS app first**: `desktop/src/apps/HubApp/` following the standard app
  patterns (window shell, panels, tests alongside). Views: Timeline, My
  Profile, Friends (requests, circle management with per-friend cache toggles
  and quota display), and a Composer (text + images, a loud
  friends-only/public switch defaulting to friends-only).
- **Controller side**: `tinyagentos/hub/` for the node engine (identity
  store, object store, chain logic, sync workers, peer server) and
  `tinyagentos/routes/hub.py` for the local API the app consumes, mirroring
  how chat routes wrap the chat stores. Directory calls go through
  `account_proxy.py` additions, peer traffic through the new peer endpoint.
- **Client resilience**: every call follows the `account-client.ts`
  degrade-to-state pattern; the app renders signed-out, no-identity,
  directory-unavailable, and offline states explicitly, and the timeline
  works offline from the local store.
- **Standalone PWA later**: per the universal app architecture and the
  Messages dual-PWA precedent, the Hub app later ships as an installable PWA
  against the same local APIs. Nothing in v1 may assume the desktop shell in
  its data layer; that is the only PWA-readiness constraint imposed now.

## Abuse and safety

What a directory-only server can and cannot do, stated plainly:

- **Hub CAN moderate**: the username namespace (already governed by the
  reserved list and account terms), directory listing (an abusive identity
  can be delisted, its presence lookups and request brokering and hints
  refused), friend-request spam (rate limits, per-target caps), and identity
  registration (one per username, account-gated).
- **Hub CANNOT moderate**: content it never sees. Friends-only posts are
  ciphertext between consenting friends; that is the design, the same
  position as any E2E system. A network-level takedown means delisting from
  the directory and revoking rendezvous, which removes reach; bytes already
  on friends' nodes are those users' own storage.
- **Block and mute are local + circle operations, and they are strong**:
  blocking someone unfollows, removes them from friendship and circle,
  rotates the circle key (future posts unreadable), refuses their connections
  at the peer endpoint by key, and asks the hub to sever the edge (no more
  presence visibility or hints in either direction). Mute is the local-only
  subset (stop rendering, keep the edge). Blocklists are private to the node,
  not published.
- **Friend-request spam resistance**: requests require a registered identity
  (free but account-backed, so throwaways cost signup friction), hub-side
  rate limits per sender, per-target caps, and a user setting for who may
  request (anyone / friends-of-friends / nobody). Declines are silent.
- **Impersonation**: usernames are unique and key-anchored; display names are
  not unique. The client always shows the @username with the display name,
  and key-log recovery rotations are surfaced (see Identity).

## Roadmap (explicitly not v1)

In rough order:

1. **Public mirroring at hub.taos.my**: opt-in per post; the hub (or a CDN in
   front of it) mirrors explicitly-public posts so profiles get a public web
   presence and posts survive author offline. Changes the hub's storage
   posture, so it is its own design pass.
2. **Encrypted relay backstop** (possible paid option): a store-and-forward
   relay holding ciphertext for offline windows, closing the "author and all
   cachers offline" gap for people who want it. Pairs naturally with the
   taOSgo infrastructure side of the account model.
3. **Social recovery co-signing**: M-of-N friends co-sign key rotations,
   removing the server's unilateral recovery power from decision 3.
4. **agenthub.taos.my**: agent profiles and agent presence, the moltbook-like
   surface where taOS agents have a public face, built on the same identity
   log with agent identities namespaced under their owner.
5. **Community compute projects**: Kickstarter-for-compute; community goals
   funded in pooled cluster capacity rather than money, building on the
   cluster/mesh work.
6. **Richer media** (video via chunked blobs), **post edits** (superseding
   versions), **reactions/replies** (threaded signed objects referencing a
   target post; small design, deliberately deferred so v1 ships).
7. **Standalone Hub PWA** per the universal app architecture.

## Slice plan

Numbered, PR-sized, ordered. Slices 1 to 4 are bounded and specified tightly
enough for external CLI coding agents to implement from this text. Slices 5
to 7 are architecture-heavy (sync protocol, caching, crypto) and are
**maintainer/design-review-required**: the protocol details above are a
sketch that must be hardened in review before code.

1. **Identity: keypair + directory registration.**
   Server (taos-website): `hub_identities` table
   `{account_id, username, signing_pubkey, encryption_pubkey}` +
   `hub_key_log`, the register/lookup/rotate endpoints with challenge proof,
   one identity per username. Controller: `tinyagentos/hub/identity.py`
   keystore (0600, `mesh_credentials.py` pattern), keygen on first use, and
   `account_proxy.py` `_ACTIONS` additions for register/lookup/rotate.
   Tests: keystore round-trip + perms, proxy forwarding + 503, challenge
   proof rejects a wrong key, lookup returns the key log.
2. **Profile object + local hub store.** `tinyagentos/hub/store.py` (SQLite:
   objects, blobs, authors tables), canonical-JSON encode/hash/sign/verify
   helpers, profile create/update (version bump) and render-own-profile in a
   minimal `HubApp` shell with the standard degrade states. No networking
   beyond slice 1's directory calls. Tests: canonicalization vectors,
   sign/verify, version-wins, store round-trip.
3. **Follow / friend / circle model + request brokering.** Server: requests
   inbox endpoints, accepted-edge rows, presence heartbeat/lookup along
   edges. Controller/app: send/accept/decline flows in the Friends view,
   signed follow statements, cache-grant statements (stored, not yet acted
   on), block/mute local operations. Tests: edge authorization (presence
   denied without an accepted edge), rate-limit behavior, block severs the
   edge.
4. **Post objects + composer + own-timeline.** Chain logic (`seq`/`prev`,
   append, verify, tombstones), image ingest (re-encode, strip EXIF, blob
   store), composer with the visibility switch, own-posts timeline from the
   local store. Still no peer sync; this slice makes the data model real.
   Tests: chain append/verify, tamper detection, tombstone drops content and
   keeps the chain verifiable, EXIF stripped.
5. **Peer sync v1** (maintainer/design-review-required). The peer endpoint
   (mutual key handshake), head announcements, range pull, blob fetch with
   hash verification, mDNS LAN path, presence-based rendezvous, hint
   endpoints. Live-verify with two real nodes (Pi + Fedora box).
6. **Friend cache + serve** (maintainer/design-review-required). Cache-grant
   enforcement, per-friend quotas + recency eviction, serving authorized
   readers while the author is offline, tombstone honoring, cacher range
   advertisement. Live-verify the offline-author case end to end.
7. **Friends-only encryption tier** (maintainer/design-review-required, the
   crypto slice). Circle key mint/wrap/rotate, encrypted envelopes, removal
   rotation, block integration. Until this slice lands, "friends-only" posts
   are enforced by serve-authorization only and the composer labels them
   accordingly, an explicitly temporary state.
8. **Timeline assembly + gaps.** Multi-author merge, chain-order rendering,
   gap placeholders + background retry, public-post rendering for followed
   non-friends. This completes v1.

Ordering: 1 to 4 strictly sequential (each consumes the previous), 5 to 7
sequential after 4, 8 after 5 (usable after 5, complete after 7). Server
slices land in taos-website ahead of their controller consumers, with the
client degrade states covering the lag, same as the account panel today.

## Open questions for Jay (each with a recommendation)

1. **Post edits in v1?** Recommendation: no. Immutable posts plus
   delete-and-repost keeps the chain and cache semantics simple; superseding
   versions are roadmap item 6 and slot in cleanly later as a new object type
   referencing the original.
2. **Circle key rotation on friend removal: immediate or lazy?**
   Recommendation: immediate for all subsequent posts (mint on removal),
   accepting that previously shared posts stay readable to the removed
   friend. Lazy rotation saves negligible work at our circle sizes and
   extends exposure.
3. **How much social graph does the hub hold?** The accepted-edge table is
   the largest piece of server-held metadata (it authorizes presence lookups
   and hints). Recommendation: keep it, but store edges as pairs of identity
   fingerprints with no timestamps beyond what abuse handling needs, document
   it in the privacy copy, and revisit under roadmap item 3 whether edges can
   become client-held capabilities instead.
4. **Default cache quota per friend and default circle size nudge?**
   Recommendation: a few hundred MB per granted friend, images capped per
   post, and UI copy nudging 2 to 5 cachers. Exact numbers are a product
   tuning call, not architecture; they should ship as config with sane
   defaults.
5. **Multi-node accounts.** A user with two taOS hosts: which one is the hub
   node? Recommendation: v1 designates a single primary hub node per identity
   (the box that holds the private key); the user's other nodes act as
   ordinary circle cachers of their own content, which the model already
   supports and which neatly covers the household-availability case.
6. **Follower visibility of friends-only posts.** This design says followers
   get public posts only and friends get the encrypted tier. Confirm there is
   no middle "followers" tier in v1. Recommendation: confirm two tiers;
   every added tier is another key-distribution regime, and two covers the
   personal/business split (friends-only for people, public for pages).
7. **Directory lookup exposure.** Should `lookup?username=` be fully public
   or signed-in only? Recommendation: signed-in only at launch (still free),
   softening the enumeration and scraping surface while the network is small,
   with rate limits either way; revisit when public mirroring (roadmap 1)
   gives profiles an intentional public face.
8. **Name the protocol.** The signed-chain + friend-cache protocol will
   outlive the first app and agenthub reuses it. Recommendation: give it a
   short internal name now (for example "taOS hubsync") so docs and code
   reference one thing; naming it later means renaming code.
