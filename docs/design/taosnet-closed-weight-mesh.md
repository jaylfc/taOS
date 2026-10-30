# taOSnet: closed peer-to-peer model weight mesh

**Status:** Proposal, for owner review. Docs only. Builds on
`docs/design/model-torrent-mesh.md` (its "Current state" section is the
authoritative summary of what shipped) and
`docs/design/taosgo-mesh-join-foundation.md` (the Headscale mesh join).

## Problem

Model weights are the largest thing a taOS box downloads: tens of GB per
model, repeated on every box in a household or a group of friends. taOSnet
already exists as a closed, account-gated BitTorrent swarm with a
HuggingFace web seed, but in practice it never runs, and it only covers
weights whose licence allows public redistribution. Three gaps matter:

1. **It is not switched on.** No catalog manifest carries a `magnet` or
   `info_hash`, so `tinyagentos/download_manager.py` always takes the HTTP
   path.
2. **Your own boxes do not share with each other.** A user with three boxes
   downloads the same 40 GB three times from the internet. Peers are found
   only through the central tracker (plus LAN discovery when it happens to
   work), and a box with no taOS account cannot join at all.
3. **Gated and non-redistributable weights** (Llama base, Flux Dev) are
   HTTP-only by design, so they get no help at all, even between a user's own
   machines, where no redistribution happens.

"Closed" in this doc means: only authenticated taOS identities take part, and
transfers happen only inside a trust boundary the user chose. It is never an
open public swarm.

## Current state

- **Client:** `tinyagentos/torrent_downloader.py` runs a libtorrent session
  with DHT off and LSD on, injects the account passkey into the private
  tracker announce and the HuggingFace web seed, and verifies SHA-256 after
  download. `tinyagentos/download_manager.py` tries the torrent first, then
  HTTP with `.part` files, Range resume and SHA-256 checks.
- **taOSnet helpers:** `tinyagentos/taosnet/passkey_client.py`,
  `tinyagentos/taosnet/torrent_client.py`, and
  `tinyagentos/taosnet/license_eligibility.py` (seed only redistributable
  licences).
- **Seeding settings:** `tinyagentos/torrent_settings.py` (seed on by
  default, upload cap, active seed cap); `tinyagentos/routes/torrent.py`.
- **Mesh:** `tinyagentos/taosnet/mesh.py` runs `tailscale up` against the
  taOS Headscale; credentials in `tinyagentos/taosnet/mesh_credentials.py`.
- **Not built:** catalog-publish CLI and manifest magnets; the seedbox;
  `tinyagentos/routes/models.py` passes `magnet` but not `web_seeds` or
  `torrent_url`; per-model opt-out; seeding stats UI; any box-to-box path.
- **Unrelated but similar name:** `tinyagentos/scheduling/mesh_sync.py`
  syncs taOSmd memory between controller and workers, not weights.

## Design

### Three trust rings

| Ring | Who | What may flow | Discovery |
|---|---|---|---|
| **1. Own cluster** | the controller and its workers | any weights the user holds, including gated ones | the cluster's worker list |
| **2. Own boxes** | other taOS boxes on the same taOS account | any weights the user holds | the account's Headscale tailnet |
| **3. taOSnet** | any authenticated taOS account | redistributable weights only | private tracker |

Rings 1 and 2 are the user copying their own files between their own
machines, which licences that forbid redistribution permit (the user already
accepted the licence and downloaded once). Ring 3 is today's taOSnet. A
transfer always uses the innermost ring that has the file.

### Ring 1 and 2: owner-local sharing

- **Identity:** each box already has a mesh identity once joined
  (`mesh_up`). Ring 2 peers are only the account's own tailnet nodes; ring 1
  peers are cluster workers authenticated by the existing worker token.
- **Transport:** plain HTTPS range requests between boxes over the tailnet
  (ring 2) or the cluster link (ring 1), not BitTorrent. Two to five peers do
  not need swarm machinery, and HTTP range plus SHA-256 is what the download
  manager already verifies.
- **Index:** each box serves `GET /api/taosnet/local/index`: the models it
  holds, by catalog id, variant and file SHA-256. Only ring 1 and 2 peers can
  call it (worker token, or tailnet source address plus account-bound token).
- **Fetch:** before HTTP or torrent, the download manager asks ring 1 then
  ring 2 peers for the file by SHA-256 and pulls ranges from the fastest one,
  verifying the hash. Missing or failing peers fall through to the next ring.
- **Never leaves the rings:** gated weights are never added to a torrent and
  never served to a ring 3 peer. The local index route refuses any caller
  that is not ring 1 or 2.

### Ring 3: turn taOSnet on

- Finish what `model-torrent-mesh.md` lists as remaining: the publish CLI
  writes `info_hash`, `torrent_url` and `web_seeds` into eligible manifests;
  `routes/models.py` passes all three through.
- Content identity is the file SHA-256 already in manifests; the torrent is
  a transport, not the identity.
- **Bind to the mesh when available:** with a mesh up, listen on the
  tailnet interface only for peer traffic, so a box never opens a public
  port for seeding unless the user asks.
- Per-model seeding opt-out and a stats view (uploaded, peers, ratio) in the
  Models app.

### Integrity

Every byte from any ring is verified against the manifest SHA-256 before the
file is moved into `models_root()`. A peer that serves bad ranges is dropped
for the session. Ring 1 and 2 peers do not get to vouch for files the
manifest does not describe (a local-only model with no catalog entry is out
of scope for v1).

## API surface

| Route | Ring | Notes |
|---|---|---|
| `GET /api/taosnet/local/index` | 1, 2 | held files by SHA-256; refuses ring 3 |
| `GET /api/taosnet/local/blob/{sha256}` | 1, 2 | HTTP range; refuses ring 3 and unknown hashes |
| `GET /api/taosnet/status` | local UI | rings reachable, seeding stats |
| existing `routes/torrent.py` settings | local UI | gains per-model opt-out |

## Phased plan

1. **Wire the existing client:** `routes/models.py` passes `web_seeds` and
   `torrent_url`; add tests for the torrent-first path with a stub session.
2. **Ring 1:** cluster-local fetch by SHA-256 from workers (and controller to
   worker), the biggest win for a multi-node household. Hardware test: two
   nodes, one model.
3. **Ring 2:** the local index and blob routes over the tailnet, with the
   account-bound check. Hardware test: two boxes on one account.
4. **Ring 3 publish:** the catalog-publish CLI and the first manifests with
   `info_hash` (needs the seedbox; taos.my side).
5. **Mesh-only binding, opt-out, stats UI.**

## Open questions

1. Ring 2 auth: is tailnet membership enough, or should each box also
   present an account-bound token to the other? Proposal: both.
2. Should trusted friends (the hub social friend edge) become a ring between
   2 and 3, for redistributable weights only? Proposal: later, it adds no
   new licence rights and mostly duplicates ring 3.
3. Default seeding for ring 3: keep "on" (current default) or ask at first
   download? Proposal: ask once, remember.
4. Should local-only models (no catalog entry) be shareable in rings 1 and 2
   by hash alone? Proposal: yes in phase 5, with the user confirming.
