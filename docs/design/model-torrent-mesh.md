# Model Torrent Mesh

**Status:** Partially superseded. taOSnet shipped as a CLOSED, authenticated swarm, which changes several choices below (open opentracker, DHT bootstrap, "Jay's home mirror", and the tinyagentos.com hostnames are all obsolete). The authoritative live server contract is `docs/taosnet.md` (built by the taos.my side). The original open-swarm design is kept below for history; the current architecture is summarised next.

Peer-to-peer model distribution for taOS. Every instance that downloads a model becomes a seeder, taking load off the central mirror and making the catalog resilient to outages.

## Current state (2026-07-08)

taOSnet is a closed, account-authenticated swarm on `taos.my`, not an open BitTorrent network.

- **Gate:** an authenticated taOS account (paid via taOSgo today). No account still works via the HTTP web-seed fallback.
- **Passkey:** each node fetches an opaque, account-bound passkey (`GET /api/taosnet/passkey`) and injects it into the private tracker announce `https://tracker.taos.my/<passkey>/announce`. BEP-27 private torrents, DHT off. Tracker returns 401 on a bad/rotated passkey (re-fetch).
- **Index + publish:** `GET /api/taosnet/index` (account-authed, keyed on info_hash), `GET /taosnet/<info_hash>.torrent` (public metadata), `PUT /api/taosnet/index/<info_hash>` (bearer `TAOSNET_PUBLISH_TOKEN`, used by the catalog-publish CLI).
- **Tracker + seedbox:** in-app tracker on `tracker.taos.my` (chihaya upgrade path). The bootstrap seedbox runs on the VPS backed by a 5TB Google Drive with a local-disk cache for the hot set; NOT a home seedbox.
- **Web seed (BEP-19):** every torrent lists the HuggingFace `resolve` URL, so "no peers" degrades to today's HTTP download. No new failure mode.
- **Eligibility:** only license-redistributable weights are seeded (`tinyagentos/taosnet/license_eligibility.py`; Apache/MIT/BSD/CC-BY/OpenRAIL/Gemma/Llama-community true, NC/research/gated false).

Built: Phase-1 hybrid-download client (merged); license classifier (PR #1723); client passkey / web-seed / torrent_url wiring + DHT-off in `torrent_downloader.py` and `tinyagentos/taosnet/torrent_client.py` (PR #1728, Pi-verified against libtorrent 2.0.13); the taos.my server contract (live, `docs/taosnet.md`).

Remaining: DownloadManager passkey fetch via the account-session proxy + clean HTTP fallback for unlinked controllers; the catalog-publish CLI (runs where the weights live, i.e. the seedbox) writing magnets/info_hashes back into the manifests; the seedbox build itself.

## Goals

- **Offload the mirror** — Jay's home server is one machine. A torrent swarm
  scales with users, not with Jay's upstream bandwidth.
- **Resilience** — if the mirror goes dark, peers keep serving each other.
- **Integrity** — every byte is content-addressed and verified; bad peers can't
  poison a file.
- **Opt-in seeding** — users who only leech still work; users who want to help
  flip one switch.
- **HTTP fallback** — if the swarm is dead (new model, firewall, etc.), the
  download falls back to HuggingFace or Jay's mirror transparently. The user
  never sees a failure mode they can't recover from.
- **Licensing-aware** — only redistribute models with licences that allow it
  (Apache 2.0, MIT, OpenRAIL, Llama Community redistribution clause). Gated
  models (Flux Dev, Llama base) stay HTTP-only from HuggingFace.

## Non-goals

- Replacing HuggingFace as the source of truth. HF stays authoritative; we
  mirror, we don't fork.
- Anonymous file sharing. Peers see each other's IPs — users who care run over
  Tailscale/WireGuard.
- General-purpose file sharing. This is narrowly scoped to model weights and
  nothing else.

## Architecture

```
┌─────────────────┐   publishes   ┌──────────────────────┐
│  Jay's mirror   │ ────────────> │ catalog.yaml entry   │
│  (seedbox)      │               │  torrent/magnet URL  │
└──────┬──────────┘               └──────────┬───────────┘
       │ seeds 24/7                          │ carried inside
       │                                     │ the manifest
       │                                     ▼
       │                           ┌─────────────────────┐
       │                           │ TinyAgentOS instance│
       │                           │ (libtorrent client) │
       │                           └──────┬──────────────┘
       │                                  │ swarm
       ├──────────────────────────────────┤
       │          peer / peer / peer      │
       │                                  │
       ▼                                  ▼
       opentracker (optional)     DHT bootstrap nodes
```

### Mirror side (Jay's server)

- **opentracker** — single binary, C, handles millions of peers per core. Or
  skip it and rely on DHT + PEX only.
- **Seedbox daemon** — one libtorrent process with `data/models/` mounted,
  seeding every file. Auto-picks up new files via inotify.
- **Torrent generator** — on mirror ingest, `mktorrent --web-seed=<hf-url>` so
  every torrent also lists HuggingFace as a web seed (BEP-19). New peers can
  pull from HTTP fallbacks without tracker/DHT.
- **Catalog publisher** — a CI job (or manual tool) that takes a new model,
  generates the torrent, posts the magnet link to the manifest, commits.

### Client side (every TinyAgentOS instance)

- **libtorrent-rasterbar** via `python-libtorrent`. Rock-solid, BSD-licensed,
  used by qBittorrent and Deluge.
- **Hybrid download manager** — extends the existing `DownloadManager`:
  1. If the manifest variant has a `magnet` or `torrent` field, kick off a
     libtorrent session and start swarming.
  2. If no peers within N seconds (default 30), fall back to `download_url`.
  3. If torrent completes: verify SHA256 against manifest, then done.
  4. If HTTP completes first (web seed from inside libtorrent, or direct
     fallback): still verify SHA256.
- **Seeding** — after a successful download, the torrent is kept in the
  libtorrent session. Seeding runs in the background with user-configurable
  upload limits.
- **Kill-switch** — Settings toggle: `seed_downloaded_models` (default: off on
  mobile, on on desktop/server).

## Manifest schema additions

Each variant gains optional P2P fields alongside the existing `download_url`:

```yaml
variants:
  - id: q4-gguf
    size_mb: 2400
    download_url: https://huggingface.co/...      # unchanged — HTTP source of truth
    sha256: 1a2b3c...                             # unchanged — integrity check
    # new — all optional, falls back to HTTP if absent
    magnet: "magnet:?xt=urn:btih:ABC...&dn=..."   # preferred: peers only, no tracker needed
    torrent_url: https://mirror.tinyagentos.com/torrents/q4-gguf.torrent
    info_hash: "abc123..."                         # for libtorrent resume / dedup
    web_seeds:                                     # BEP-19, bootstraps swarm
      - https://huggingface.co/.../resolve/main/file.gguf
    license_allows_redistribution: true            # set by catalog publisher
```

When `license_allows_redistribution` is false or missing, the client ignores
the torrent fields and goes HTTP-only. Conservative by default.

## Data flow: a user downloads a model

1. User clicks **Download** in the Model Browser.
2. Client calls `POST /api/models/download` with `{app_id, variant_id}`.
3. Server reads the manifest and starts a `DownloadManager` task.
4. Manager checks `magnet`/`torrent_url`:
   - **Present + redistribution allowed + torrent enabled**: libtorrent
     starts swarming, publishes progress via the existing WebSocket/polling
     hook.
   - **Absent, disabled, or no peers after grace period**: HTTP stream from
     `download_url`, exactly like today.
5. On completion, SHA256 is verified against the manifest.
6. If seeding is enabled and redistribution is allowed, the torrent is kept
   active and the file is advertised to the swarm.

The UI shows one progress bar and never exposes `p2p vs http` unless the user
asks for details (a "via 17 peers" subtitle, like Steam's download UI).

## Bootstrapping a new model

When Jay (or any catalog maintainer) adds a new model:

1. Download the file from HF to the mirror.
2. Run the `tinyagentos catalog publish` CLI, which:
   - Generates a torrent with `web-seed` pointing to HF and to the mirror.
   - Computes SHA256 (mandatory) and info hash.
   - Adds `magnet`, `torrent_url`, `info_hash`, `web_seeds` to the manifest.
   - Commits the manifest change.
3. Mirror's seedbox daemon auto-picks up the new file via inotify.
4. Clients that pull the updated catalog get the new magnet link.

## Privacy, licensing, and networking concerns

- **Peer IP leakage** — inherent to BitTorrent. Mitigations: Tailscale-only
  mode (configure libtorrent to bind to the tailscale interface), VPN detection
  warning on first use, clear "Seeding exposes your IP to peers" UI copy.
- **ISP throttling** — some home ISPs throttle BitTorrent. HTTP fallback
  handles this automatically; users may never notice.
- **NAT traversal** — libtorrent handles UPnP, NAT-PMP, and uTP hole punching.
  For unreachable peers, the mirror's public seedbox is always available.
- **Licensing** — strict allowlist. Gated or proprietary models never get
  torrent fields. See `tinyagentos/licensing.py` for the allowlist check.
- **Abuse** — a malicious client that corrupts files is caught by the SHA256
  check. libtorrent's piece-level hashing catches corruption mid-download
  and bans bad peers.
- **Disk exhaustion** — seeding uses existing `data/models/` files — zero
  extra disk. Cap on simultaneous seeds (default 10) to limit memory.

## Performance budget

Target: a first-time user downloading a 4 GB model should see ~30 MB/s sustained
(100× faster than one-to-one HTTP from a congested home server) once the swarm
is warmed. Worst case (no peers) drops to HTTP speed with no user-visible
failure. Memory overhead: libtorrent itself is ~25 MB plus ~16 MB per active
torrent; a 10-torrent seed session fits in ~200 MB.

## Phased rollout

### Phase 1 — client-side hybrid download (MVP)
- Add `python-libtorrent` dependency (optional — falls back to HTTP if missing).
- Extend `DownloadManager` with a `_download_torrent` path.
- Add `magnet`, `torrent_url`, `info_hash`, `web_seeds`,
  `license_allows_redistribution` to the manifest schema.
- Publish ONE test model with a torrent (Qwen3-4B-Q4) from Jay's seedbox.
- UI: Model Browser shows a peer count when swarming.
- Settings toggle: `Share downloaded models with other users`.

### Phase 2 — mirror + catalog tooling
- **opentracker on tinyagentos.com**, co-located with the landing page and
  seedbox. UDP on `tracker.tinyagentos.com:6969` plus HTTP
  `tracker.tinyagentos.com/announce`. Single static binary, ~2 MB RAM, no
  database — nginx fronts the HTTP announce, opentracker owns the UDP port
  directly. DHT + LSD stay the primary peer-discovery path, so the tracker is
  a bootstrap accelerator, not a single point of failure: if it goes down,
  swarms keep working.
- **Default announce list** baked into every magnet the catalog publisher
  emits:
  1. `udp://tracker.tinyagentos.com:6969/announce` (ours)
  2. 1–2 well-known public backups (opentrackr, etc.) so we're not the only
     announce URL a client has to reach.
- Seedbox daemon — systemd unit wrapping libtorrent, auto-seeds the mirror's
  `models/` directory. Lives on the same box as the tracker and the website.
- `tinyagentos catalog publish <model>` CLI that generates torrents and
  updates manifests in one command. Injects the default announce list +
  web-seed URL automatically.
- Batch-torrent the existing 97 manifests — anything with a permissive licence
  gets a magnet; the rest stay HTTP-only.
- **Abuse-report plumbing** — a `tracker-abuse@tinyagentos.com` alias and a
  small runbook for removing a torrent from the tracker + seedbox if a
  licence review later flips. Only variants with
  `license_allows_redistribution: true` are ever announced, so this should be
  rare, but it needs to exist before the tracker goes public.

### Phase 3 — seeding polish
- Seeding stats in the dashboard (`Sharing: 3 models, 12 peers, 47 MB
  uploaded`).
- Per-model opt-out (stop seeding individual files).
- Tailscale-only mode.
- Seeding pause on battery / metered network (mobile).

### Phase 4 — advanced
- DHT-only publication (no tracker needed for niche/private catalogs).
- Delta updates — when a model gets a new quant, share only the diff.
- BEP-46 mutable torrents for rolling catalog indexes.

## Open questions

- **Tracker vs DHT-only?** Tracker is faster to warm swarms for rarely-shared
  models; DHT is simpler to operate. Recommend: both — DHT by default, tracker
  as a hint in the magnet.
- **python-libtorrent availability on ARM64 / Pi?** Available via apt on
  Debian/Ubuntu; needs testing on Orange Pi Armbian. Fallback to HTTP if
  import fails.
- **Should we ship a tracker as part of TinyAgentOS?** Decided: yes, one
  central opentracker at `tracker.tinyagentos.com`, co-located with the
  website and seedbox in Phase 2. Not shipped inside the OS itself — running
  a tracker is an ops commitment that belongs on our infra, not every user's
  box.
- **Web seed vs magnet as primary?** Magnet with web-seed inside is the
  cleanest — one field, both transports, no branching logic.

## References

- BEP-3 — BitTorrent core protocol
- BEP-5 — DHT
- BEP-9 — magnet links
- BEP-19 — web seeds (HTTP/FTP)
- BEP-46 — updatable torrents
- libtorrent Python bindings: https://www.libtorrent.org/python_binding.html
- opentracker: https://erdgeist.org/arts/software/opentracker/
