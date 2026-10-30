# taOS social: peer-to-peer architecture (hubsync)

**Status:** Proposal, for owner review. Builds on, and does not replace:

- `docs/design/hub-social-network-foundation.md` (the foundation: locked
  decisions, data model, slices 1 to 8),
- `docs/design/account-username-subdomain-model.md` (usernames are the one
  identity namespace),
- `docs/design/cross-user-collaboration.md` (contacts, peer links, signed
  envelopes, peer tokens).

The foundation marks its sync, cache and encryption slices (5 to 7) as
"maintainer/design-review-required: the protocol details above are a sketch
that must be hardened in review before code". This doc is that hardening. It
adds the ownership rule, exact cache and delete rules, the offline-origin read
path, feed assembly, the API surface and a phased plan whose first phase is
local only.

## 1. What already exists

Slices 1 to 4 of the foundation are built:

- **Identity:** `tinyagentos/hub/identity.py`, Ed25519 signing plus X25519
  encryption keys in `<data_dir>/hub/identity.json` (0600). The author id is
  the signing key fingerprint, never the username. Directory registration
  goes through `tinyagentos/routes/account_proxy.py`.
- **Store:** `tinyagentos/hub/store.py` (`HubStore`, SQLite): canonical JSON,
  SHA-256 content addresses, sign and verify helpers, tables for authors,
  objects, blobs, the per-author chain index and relationships.
- **Posts:** `tinyagentos/hub/posts.py`: hash-chained posts (`seq`, `prev`),
  signed tombstones, `verify_chain`, image ingest (re-encode, strip EXIF).
- **Relationships:** follow, friend, cache-grant, block and mute in
  `tinyagentos/hub/relationships.py`; friend accept runs the peer handshake
  from `tinyagentos/peer.py`.
- **Routes:** `tinyagentos/routes/hub.py`: profile, relationships, presence,
  `POST /api/hub/posts`, `GET /api/hub/timeline` (own posts only),
  `POST /api/hub/posts/{hash}/delete`.
- **Peer channel:** `tinyagentos/routes/peer.py`: bearer-token
  `/api/peer/inbox` with signed envelopes, nonce replay cache, rate limits and
  a `kind` dispatch that logs unknown kinds.

Missing: peer post sync, cache enforcement, encryption, multi-author feed, a
way to read a blob back (the Hub app shows the blob hash, not the image), and
a chain export a peer could pull.

Two defects found while mapping, fixed in phase 0 below:

1. `delete_post` drops every blob the post referenced, but blobs are
   content addressed and shared: posting the same image twice and deleting one
   post breaks the other.
2. `delete_post` does not check the target is the node's own post. Today the
   store only holds own posts, but the moment it caches friends' posts a local
   user could append a tombstone for someone else's post to their own chain.

## 2. Principles

1. **The origin owns the content.** The origin is the node that holds the
   author's private signing key. Only the origin can create, and so only the
   origin can delete. Every other copy is a cache.
2. **Caches are honest by construction.** Every object is signed and content
   addressed; a cacher can withhold but cannot forge or alter.
3. **Cache, then delete.** A delete stops the origin serving at once and
   publishes a signed tombstone. Caches drop the content when they learn of
   the tombstone and never serve it again.
4. **Pull is the invariant.** Announcements and hints only make sync faster.
5. **The central index holds pointers, never content.** Username to keys,
   presence, TTL'd hints and opt-in public head pointers. No posts, no blobs,
   no ciphertext.
6. **No new identity system.** Hub identity keys and the existing peer link
   and contact records are the only identities. Agents and the Relationships
   app keep their own planes.

## 3. Data model additions

The post, profile, follow, friend, cache-grant and tombstone objects are as in
the foundation. Two signed statements are added, both mutable state (highest
`version` wins, like profiles):

```json
{"type": "cache-set", "author": "<fp>", "version": 3,
 "cachers": ["<fp>", "<fp>"], "sig": "..."}
```

The author's current list of granted cachers. Readers use it to know where
to fall back when the origin is offline, and cachers use it to know they are
still granted. Revoking a grant is a new version without that cacher.

```json
{"type": "circle-roster", "author": "<fp>", "version": 9,
 "readers": ["<fp>", "..."], "sig": "..."}
```

The author's current friends-only readership. Until circle encryption lands
(foundation slice 7), a cacher serves `visibility: circle` objects only to
fingerprints in the latest roster it holds. After encryption, the roster still
limits who receives ciphertext and chain metadata.

A **signed head** is the author's announcement `{type: "head", author, seq,
hash, at, sig}`. Anything that serves a chain range also returns the newest
signed head it holds, so a reader knows how stale a cache is.

## 4. Replication and cache rules

### What a cacher stores

- Only for authors whose current `cache-set` names it, within its local
  per-author quota (default from `DEFAULT_CACHE_QUOTA_BYTES`, set per author
  in the Hub app).
- Every object is verified before storage: signature against the author's
  pinned key, `hash == object_hash(obj)`, `prev` links to the held
  predecessor (gaps are allowed and recorded, never papered over).
- Recency first: newest chain entries and their blobs stay; oldest evict.
- **Tombstones are never evicted** (they are tiny), so a later attempt to
  re-serve a deleted post, from anyone, is recognised and refused.

### Delete propagation (cache then delete)

1. Origin appends the tombstone, drops the body and any blob no longer
   referenced by a live own post, and stops serving the target at once.
2. Origin announces the new signed head to every online cacher and friend,
   and leaves a hint `{to, author, seq}` at the directory for offline ones.
3. A cacher that sees a head newer than what it holds pulls the range before
   serving anything else for that author when the origin is reachable. The
   range response always includes every tombstone after the cacher's held
   seq, even when the range itself is truncated by a limit, so deletes travel
   first.
4. On receiving a tombstone the cacher drops the target body and its
   unreferenced blobs and keeps the tombstone.
5. A reader applies the same rule to its own local store.

The limit is stated plainly in the UI: delete is best effort for copies on
nodes that are offline, modified, or screenshotted.

### Revocation

A new `cache-set` without a cacher obliges it to drop its whole cache for
that author. A new `circle-roster` without a reader stops cachers serving that
reader circle objects; encryption-era key rotation follows the foundation.

## 5. Offline-origin availability

A reader resolving author A's posts tries, in order:

1. **A's origin**, located via LAN discovery or directory presence.
2. **A's cachers**, from the latest `cache-set` the reader holds, each
   located the same way. A cacher answers with the objects it holds, its held
   seq range, and the newest signed head it has.
3. **Its own local store**, which always renders offline.

The reader verifies everything itself. If the newest signed head it has seen
is beyond what any reachable node holds, the feed shows the foundation's gap
placeholder ("3 posts from @alice are on nodes that are currently offline").

## 6. Feed assembly

- The feed is built from the local store only; the network fills the store.
- A refresh walks followed and friended authors with a small concurrency
  limit (proposal: 4) and a short per-peer timeout (proposal: 3 s), pulling
  only ranges past each author's held head. Peers that are offline are
  skipped and retried on the next refresh or on a hint.
- Order: `created_at` across authors, chain order within an author (a chain
  never renders out of order even if a clock skews).
- Followers who are not friends receive `public` posts only.
- Blocked and muted authors are filtered at assembly; blocked authors are
  never fetched.

## 7. Identity and authentication

- **Friends** (accepted edge, peer link established): requests carry the
  existing peer bearer token; the contact maps to a hub fingerprint.
- **Public followers** (no peer link): a request signature header over
  `{method, path, fp, ts, nonce}` with the follower's hub signing key, a 300 s
  freshness window and the existing nonce cache, and the key resolved through
  directory lookup and pinned on first use. Public posts only.
- **Multi-user boxes:** today one box has one hub identity and any signed-in
  user can post as it. See open question 1.

## 8. API surface

### Local (session auth, CSRF, the box's users)

| Route | Phase | Purpose |
|---|---|---|
| `GET /api/hub/chain?after_seq&limit` | 0 | own chain entries with bodies, plus head; the exact payload peers will pull |
| `GET /api/hub/blobs/{hash}` | 0 | blob bytes with stored MIME (so the Hub app can render images) |
| `POST /api/hub/posts/{hash}/delete` | 0 fix | own posts only; keeps blobs still referenced |
| `GET /api/hub/feed` | 4 | multi-author feed from the local store |
| `PUT /api/hub/cache/{author}` | 2 | local quota for an author this box caches |

### Peer-facing (under `/api/peer/hubsync/`, mounted like the peer routers)

| Route | Phase | Auth |
|---|---|---|
| `GET head?author=` | 1 | peer token or signed request |
| `GET range?author&after_seq&limit` | 1 | same; circle objects only to roster members |
| `GET blob/{hash}` | 1 | same; only blobs referenced by an object the caller may read |
| inbox kind `hub_head` | 1 | existing `/api/peer/inbox` envelope |

Every peer route is read only except the inbox kind, responses are size
capped, and per-contact rate limits apply as on the existing peer routes.

### Directory (taos-website, via `account_proxy.py` `_ACTIONS`)

`hints` (the foundation's TTL'd `{to, author, seq}`) and, opt-in, `public-head`
`{author, seq, hash, sig}` so strangers can discover that a public profile has
new posts. Pointers only.

## 9. Phased plan

0. **Local only (safe without owner protocol review):** chain export and
   blob read routes; delete restricted to own posts with blob reference
   counting; `tinyagentos/hub/ingest.py`, a pure function set that verifies
   and stores a foreign chain range under the cache rules above (signature,
   hash, `prev` links, gap recording, tombstone honouring, tombstones never
   evicted), tested with two in-process stores. No sockets, no new peer
   routes. Split in two PRs: (a) routes and delete fixes, (b) ingest.
1. **Peer read routes and head announcements** between friends, LAN first.
   Gated on owner review of this doc. Live test on two boxes.
2. **Cache grants enforced:** `cache-set`, quotas, recency eviction, serving
   to roster members while the origin is offline.
3. **Public followers:** signed requests, directory hints and public heads
   (server side in taos-website first).
4. **Feed assembly** across authors with gap placeholders in the Hub app.
5. **Circle encryption** (foundation slice 7), reusing `cache-set` and
   `circle-roster`.

## 10. Open questions

Moderation and legal liability are parked by owner direction and are not
designed here; they are listed so they are not forgotten.

1. **Per-user identities on a shared box.** Should each taOS user get their
   own hub identity (keystore per user), or is the box the social identity?
   Today any signed-in user posts as the box.
2. **Moderation (parked).** Reporting, blocking at the index, abuse of
   public heads.
3. **Legal liability of caching (parked).** A cacher serves content it
   cannot read (after encryption); what notice-and-takedown duty applies.
4. Should the range response's "all tombstones after seq" be bounded for
   authors who deleted thousands of posts, or is a tombstone-only range call
   better?
5. Public-head pointers make posting cadence visible to anyone. Opt-in per
   user, or off entirely until moderation is designed?
6. Default refresh concurrency and timeout (proposed 4 and 3 s) on a Pi.
7. Should a cacher pull from other cachers (gossip), or only from the origin?
   This doc says origin only for writes into a cache, reducing amplification.
