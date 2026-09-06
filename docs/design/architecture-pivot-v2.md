# Architecture pivot v2 — whole-container archive, portable collab services, disk quotas

**Status:** Decision record — complete. All phases landed.

---

## Post-landing status

Phases 1 and 2.A–2.C have landed on branch `fix/agent-creation-flow`.

| Phase | Scope | Commits |
|---|---|---|
| Phase 1 | Disk quota, recycle-bin Layer 1 + 3, monitoring background task, quota prompt library | `307e496` through `f97c6ae` |
| Phase 2.A | Deployer: three bind mounts removed, single trace mount, openclaw bootstrap inside container, `root_size_gib` default 40 | `43e18c1`, `4d3b6b2`, `0578f62`, `cf0c3d9`, `2ea0917` |
| Phase 2.B | `_archive_agent_fully`, `restore_archived_agent`, `purge_archived_agent` rewritten around incus snapshot primitives; `archive.target` config knob | `ed7b3bb`, `32cd237`, `fa373b7` |
| Phase 2.C | `agent_env.py` deleted; env rewrites now via `containers.set_env` | `792cab2` |

`docs/design/framework-agnostic-runtime.md` and
`docs/runbooks/agent-archive-restore.md` have been updated to reflect the
post-pivot reality.

The sections below remain as the authoritative decision record for the choices
made in this pivot. They are not updated retroactively; read them in the context
of the implementation state described above.

---

**Supersedes (if adopted):** parts of `framework-agnostic-runtime.md`,
`agent-archive-restore.md` runbook, `_archive_agent_fully` in `tinyagentos/routes/agents.py`,
the agent-home bind-mount model in `tinyagentos/deployer.py`.

**Does not change:** openclaw bridge adapter, LXC/Docker coexistence,
LiteLLM proxy + trace callback, local-token auth, MCP skills surface,
chat hub + openclaw SSE bridge.

---

## 1. Why we're reconsidering

The current model states:

> **Containers hold code. Hosts hold state.**

That rule was written to make framework swaps, container upgrades, and backup
cheap. It has achieved those goals. The bind-mount approach works correctly
for all five state categories (workspace, memory, home, trace, secrets).

The pressure to reconsider is not correctness — it is **operational
complexity at archive time**.

`_archive_agent_fully` in `tinyagentos/routes/agents.py` (line 165) currently
coordinates six distinct side-effecting steps to produce one archive:

1. Force-stop the incus container.
2. Rename the container from `taos-agent-{slug}` to `taos-archived-{slug}-{ts}`.
3. Move three host directories (`agent-workspaces/`, `agent-memory/`,
   `agent-home/`) into a dated archive bucket under `data/archive/`.
4. Revoke the agent's LiteLLM key.
5. Export DM channel messages to `agent-home/.taos/chat-export.jsonl`.
6. Move the config entry from `config.agents` to `config.archived_agents`.

Steps 1–3 involve separate incus operations and `shutil.move` calls across
up to three directory trees. If the host filesystem is slow, the storage
pool is under pressure, or there is a partial failure mid-archive, the
result is a container renamed but directories not yet moved, or vice versa.
The runbook documents the failure modes explicitly because they are real.

The user's observation: `incus snapshot create taos-agent-{slug}` followed
by `incus export taos-agent-{slug}/{snapshot}` would produce a single
self-contained archive in one atomic operation, because the container
image already includes `/root` (agent-home), `/workspace`, and `/memory`
once those mounts are folded inside the container root.

That re-framing is the thesis of this pivot: **the archive operation should
be one incus command, not six coordinated primitives**.

The corollary is architectural: if the archive unit is the container
snapshot, then state that today lives on the host via bind-mounts must
move inside the container. That is a partial reversal of the existing rule.
This document examines whether the reversal is justified, where the new
boundary should sit, and what infrastructure is needed to keep the
remainder of the system coherent.

---

## 2. Current state — benefits + costs of the bind-mount model

### 2.1 Benefits

**Framework swap is free.** The agent's entire state survives a framework
change because nothing is in the image. Stop, change `framework:` field,
start against the same mounts. This is correct and valuable.

**Container upgrade is free.** `taos update` rebuilds images from scratch
and restarts containers. State is untouched.

**Backup is a single rsync.** `rsync -av data/ backup/` captures every
agent because every agent's state is in `data/`. No container involvement.

**Cluster dispatch without data transfer.** If `data/` is network-backed
(NFS, cluster storage), an agent container can start on worker B against
the same host mounts without copying anything. The container itself is
recreated from the image.

**Memory is embedder-agnostic.** The embedding index at
`data/agent-memory/{slug}/index.sqlite` is a host-side file. Re-embedding
with a different model is a host-side operation; containers are unaware.

### 2.2 Costs

**Archive is a multi-step distributed transaction.** The six-step sequence
in `_archive_agent_fully` has partial-failure modes documented in the
runbook. The `container_renamed` flag in the response exists precisely
because the container and directory states can diverge. A timestamp
collision (two archives within the same UTC second) requires manual
recovery.

**Restore requires re-stitching the same six steps in reverse.** The
`restore_archived_agent` path renames the container, moves three
directories back, mints a new LiteLLM key, rewrites the env file, and
updates config. Any step can fail independently.

**The archive unit is not self-contained.** An archive bucket under
`data/archive/{slug}-{ts}/` is meaningful only in the context of the
taOS config file that records `archive_dir`. Moving the archive to
another machine requires moving both the config entry and the three
subdirectories in lockstep.

**Bind-mount surface grows with features.** Today there are three mounts
(`/workspace`, `/memory`, `/root`). Future features (Gitea clone cache,
NAS-linked datasets, project-scoped overlays) would add more entries to
the `mounts` list in `deployer.py` (lines 139–143). Each new mount is
another thing that must be moved during archive and restore.

**Trade-off summary:**

| Concern | Bind-mount model | Whole-container snapshot |
|---|---|---|
| Archive atomicity | 6-step transaction, partial failures possible | 1 incus command |
| Restore atomicity | 5-step reverse transaction | `incus import` + key mint |
| Cluster dispatch (live agent) | Near-free if data/ is network-backed | Requires image transfer or shared pool |
| Framework swap | Free (data on host) | Possible but requires re-mount logic |
| Backup without incus | `rsync data/` | Needs `incus export` or pool backup |
| Portability of archive bundle | Config + 3 dirs, must be co-located | Single tarball |
| Failure modes | Directory-config divergence, timestamp collision | Snapshot-pool divergence (ZFS/btrfs specific) |

Neither model dominates. The pivot is justified specifically for the
**archive path**. Live dispatch and backup strategy are separate concerns
that must be preserved or explicitly traded.

---

## 3. Proposed model — whole-container snapshot as primary archive

### 3.1 Container lifecycle (create / run / archive / restore / purge)

**Create.** Identical to the current flow. `deploy_agent` in
`tinyagentos/deployer.py` provisions the container, installs the
framework, and mounts state directories. No change at creation time.

**Run.** Identical to today. Bind mounts remain in place during normal
operation. The per-agent trace store continues to write to
`agent-home/{slug}/.taos/trace/` on the host.

**Archive.** New flow replaces `_archive_agent_fully`:

1. Force-stop the container.
2. Take a named incus snapshot:
   `incus snapshot create taos-agent-{slug} archive-{ts}`.
3. Export the snapshot to a tarball:
   `incus export taos-agent-{slug} data/archive/{slug}-{ts}.tar.gz --compression=gzip --instance-only`
   (or `--optimized-storage` if the pool is ZFS or btrfs, producing a
   driver-native blob that restores significantly faster on the same pool type).
4. Revoke the agent's LiteLLM key.
5. Destroy the live container: `incus delete --force taos-agent-{slug}`.
6. Move the config entry from `config.agents` to `config.archived_agents`,
   recording `archive_tarball` path and `archive_snapshot` name.

Steps 1–3 are still sequential but the failure boundary is cleaner: if
the export fails, the live container is untouched and no directory state
has moved. Steps 4–6 happen only after a verified export.

The host-side `data/agent-workspaces/`, `data/agent-memory/`, and
`data/agent-home/` directories are included in the snapshot because they
are bind-mounted into the container root at the time the snapshot is
taken. On a ZFS or btrfs pool, `incus snapshot create` is copy-on-write
and near-instantaneous regardless of directory size.

**Restore.** New flow:

1. `incus import data/archive/{slug}-{ts}.tar.gz [new-slug]`.
2. If the slug already exists in live agents, append a numeric suffix
   (same collision logic as today).
3. Mint a new LiteLLM key.
4. Rewrite `agent-home/{slug}/.openclaw/env` with the new key via
   `update_agent_env_file`.
5. Add the agent back to `config.agents` with `status: stopped`.

Step 1 is a single atomic incus call. The container comes back with
all bind-mount paths intact because they were included in the snapshot.

**Purge.** Delete the archive tarball from disk and remove the config
entry. The container is already gone (destroyed at archive time).
Irreversible. No change in user-visible semantics from today.

### 3.2 Snapshot storage (ZFS/btrfs pool, optional tarball export)

taOS already creates an incus storage pool at install time. The pool
type determines snapshot characteristics:

| Pool type | Snapshot cost | Export time | Notes |
|---|---|---|---|
| ZFS | Copy-on-write, near-zero | O(changed blocks) if optimized | Preferred on Orange Pi 5 Plus with NVMe |
| btrfs | Copy-on-write, near-zero | O(changed blocks) if optimized | Viable on spinning rust |
| dir | Full copy of rootfs | O(container size) | Slow; acceptable for small agents |
| lvm | Block-level snapshot | O(container size) for export | Supported but not recommended |

For the taOS standard deployment on an Orange Pi 5 Plus with an NVMe pool,
**btrfs is the chosen pool backend.** btrfs is first-class in Debian,
requires no out-of-tree kernel module, provides qgroups for per-container
quotas, supports transparent compression, and has a lighter RAM footprint
than ZFS. Crucially, btrfs is portable across the mixed-arch cluster
because it lives on each host's Linux layer — on macOS and Windows hosts
taOS runs inside a Linux VM (Orbstack / WSL2), so btrfs availability is
intrinsic to using LXC on those platforms.

A typical agent container with a Python venv and framework dependencies
runs approximately 800 MB on disk. With btrfs copy-on-write snapshots,
an archive operation that previously required moving up to three
multi-gigabyte directory trees now writes only the changed blocks since
the last snapshot.

The tarball export is the portable archive format. The snapshot itself
is the in-pool fast-restore format. Both are produced in the proposed
archive flow. Operators may prune in-pool snapshots on a schedule while
retaining the tarball for long-term storage.

### 3.3 What lives inside the container (now)

With the pivot, the following state categories move inside the container
rootfs, accessed from their existing mount paths:

| Path inside container | Current host path | Mechanism |
|---|---|---|
| `/workspace` | `data/agent-workspaces/{slug}/` | Bind mount (unchanged during run; included in snapshot) |
| `/memory` | `data/agent-memory/{slug}/` | Bind mount (unchanged during run; included in snapshot) |
| `/root` | `data/agent-home/{slug}/` | Bind mount (unchanged during run; included in snapshot) |

At archive time, these paths are captured inside the snapshot because
incus snapshots the container filesystem tree including all mounted paths
that were live when the snapshot was taken. The host directories become
the primary copy during run and the snapshot becomes the archive copy.

This is not strictly "state inside the container image" in the sense the
original rule prohibits — the directories are still host-backed during
live operation. The change is that the snapshot-at-archive-time captures
them. The rule's original intent (no state lost when container is
rebuilt from image) still holds for live agents; only the archive
semantics change.

### 3.4 What stays on the host (smaller surface)

The following remain host-side and are NOT captured in the container
snapshot:

| Item | Location | Reason stays on host |
|---|---|---|
| LiteLLM proxy + config | `data/litellm-config.yaml` | Shared across all agents; not per-agent |
| User memory index | `data/user_memory.db` | Shared; per-user not per-agent |
| Agent-to-agent messages | `data/agent_messages.db` | Shared bus |
| Secrets store | `data/secrets.db` | Never enters a container by design |
| Local auth token | `data/.auth_local_token` | Machine-bound; must not be archived |
| Config file | `data/config.yaml` | Cluster-level truth; agent sections reference archived entries |
| incus pool | `/var/lib/incus/` | Managed by incus, not taOS directly |

The QMD embedding process, LiteLLM proxy, and skill MCP server all remain
host daemons accessible to containers via injected env vars. No change.

### 3.5 What leaves the host (no more per-dir moves, no chat-export)

Under the pivot, `_archive_agent_fully` no longer calls `shutil.move` on
the three host directories. After the container is exported and destroyed,
the host paths (`data/agent-workspaces/{slug}/`, `data/agent-memory/{slug}/`,
`data/agent-home/{slug}/`) may optionally be deleted (they are now inside
the tarball) or left in place as a read-only cache pending purge. The
recommended default is to delete them on successful export to reclaim
disk space, but the delete is gated on a verified export checksum.

The chat-export step (step 5 in the current flow, writing
`agent-home/.taos/chat-export.jsonl` before archiving) is no longer
needed as a separate copy: the chat export is written first, then the
snapshot captures it in situ. The write still happens; it no longer needs
to happen before a `shutil.move`.

---

## 4. Collaboration services — containerised, cluster-portable

### 4.1 Principle: these are first-class taOS services, deployable to any worker

Agent containers are not the only containers taOS manages. The app store
already contemplates Docker-image services. This section establishes the
pattern for two collaboration services that warrant first-class treatment:
a code host (Gitea) and a virtual NAS.

The principle for both: **they are containerised services, not host
daemons.** They follow the same container lifecycle as agent containers —
create, run, stop, migrate — but with their own storage semantics
distinct from agent state. They are opt-in. A taOS installation that
does not enable them loses no existing capability.

A cluster-portable service means: the service container can be stopped on
worker A, its state volume moved (or re-pointed) to worker B, and the
container restarted on worker B with no data loss and minimal downtime.
This is achievable when the service's persistent state is stored in a
volume that can be independently migrated, snapshotted, or replicated.

### 4.2 Gitea for code + small binaries

Gitea is an open-source self-hosted Git service. Source:
https://gitea.com / https://docs.gitea.com

**Deployment model in taOS.** Gitea runs as a single LXC container
managed by taOS (or as a Docker container on platforms without incus).
The service is deployed via the standard app-store flow. Its ports are
not exposed to the host except via proxy device on the incus bridge.

**What Gitea stores.**

- Git repositories: on-disk under `GITEA_WORK_DIR/repositories/`
- Attachments, LFS, avatars: under `GITEA_WORK_DIR/data/`
- All metadata (users, teams, issues, permissions): in a relational DB
  (SQLite by default; PostgreSQL for production).

**Agent accounts and team ACL.** taOS provisions one Gitea user account
per agent via the Gitea API (`POST /api/v1/admin/users`). Agents are
grouped into Gitea organisations mapped to taOS teams. Repository access
is granted at the org-team level. An agent writing code is the owner of
its own user-namespaced repositories; read access to shared repositories
is granted by team membership. No agent has admin scope.

**Cluster-move semantics.** Gitea's state is split: repositories are on
disk; metadata is in the database. Moving the container to another worker
requires both:

1. Moving the repository data volume (LXC storage volume or bind-mount).
2. Moving the database. For SQLite this is a file copy; for PostgreSQL it
   is a `pg_dump` / `pg_restore` or streaming replication failover.

The recommended configuration for cluster-portable Gitea in taOS:

- Run Gitea's work directory as an LXC storage volume attached to the
  Gitea container, not as a host bind-mount. This volume can be exported
  via `incus storage volume export` and imported on another worker.
- Use SQLite for deployments with one or two workers (acceptable up to
  ~50 agents / ~500 repositories). The SQLite file lives inside the
  work-directory volume and travels with it.
- For larger deployments, run PostgreSQL as a second container on the
  same worker with its own storage volume. Both containers must be
  migrated together; they should be co-located by the scheduler.

**Cluster-move procedure (SQLite):**

```
# On worker A
incus stop taos-gitea
incus storage volume export default taos-gitea-data gitea-data.tar.gz

# Transfer tarball to worker B
scp gitea-data.tar.gz worker-b:/tmp/

# On worker B
incus storage volume import default /tmp/gitea-data.tar.gz taos-gitea-data
incus start taos-gitea   # container was already migrated via incus export/import
```

This is more complex than moving a stateless service because Gitea's
database and repository tree must be migrated atomically. The recommended
operational practice is to schedule Gitea migration during a low-activity
window, run `gitea admin regenerate hooks` after restore, and verify
repository access before directing agent traffic to the restored instance.

A future taOS scheduler that understands service affinity should treat
Gitea + its DB container as a co-location group, migrating both together.

**Why Forgejo.** Forgejo is the chosen code forge (decision 7). It is a
drop-in Gitea-API-compatible fork governed by the Codeberg e.V. non-profit,
with a more active release cadence in 2025–26. The REST API surface is
identical to Gitea's; all references to `POST /api/v1/admin/users` and
related endpoints apply equally to Forgejo. The two engines are swappable
without any taOS integration change. GitLab CE is excluded because its
minimum footprint (2 GB RAM) is incompatible with the Pi deployment target.

### 4.3 Virtual NAS — open-source backend comparison

#### 4.3.1 Requirements for a taOS NAS

A virtual NAS in taOS must satisfy all of the following:

1. **arm64 container image available.** The primary deployment target is
   an Orange Pi 5 Plus (arm64). The service must ship a supported arm64
   Docker or OCI image without requiring a self-build.

2. **Runs in a single container.** The service itself (not its data) must
   run as a single container process. It may use attached storage volumes
   but must not require sidecar processes or a separate control plane to
   be functional.

3. **Cluster-portable.** Moving the service container between workers must
   not require data loss. State must either be in an attached volume
   (migratable) or replicated across nodes (self-healing). A service
   that binds its data to a specific machine's local disk is not
   cluster-portable.

4. **Per-agent identity and ACL.** Each taOS agent must be able to hold
   a distinct credential (access key or user account) scoped to a subset
   of buckets or shares. An agent must not be able to read another agent's
   files without an explicit grant.

5. **Per-agent or per-bucket quota.** taOS must be able to set a storage
   cap per agent or per bucket, live, without service restart. Quota
   overrun must produce a hard rejection at upload time, not silent
   data loss.

6. **Standard API.** The service must expose either an S3-compatible HTTP
   API or a POSIX/NFS mount that works from Python (boto3), Node, Go,
   and Rust without proprietary SDK dependency.

7. **Resource footprint under 512 MB RAM.** The service must idle below
   512 MB RAM on arm64. taOS workers share memory with agent containers
   and the embedding process.

8. **Actively maintained open-source.** The project must be under an
   OSI-approved license with an active upstream and published arm64 images.

#### 4.3.2 Comparison table

| Project | API | arm64 image | Multi-node HA | Quotas | Permissions | RAM baseline | License | Primary use case |
|---|---|---|---|---|---|---|---|---|
| **Garage** | S3-compatible | Yes (dxflrs/garage, multi-arch) | Yes — native geo-distributed cluster, replication factor 1–7 | Per-bucket (max-size, max-objects); enforced as hard reject at upload | Per-key per-bucket (read/write/owner); key-scoped bucket aliases | ~50–128 MB (Rust binary; docs cite 1 GB minimum for cluster node including OS) | AGPL-3.0 | Self-hosted geo-distributed S3 for small deployments |
| **MinIO** | S3-compatible (feature-complete) | Yes — until Oct 2025. Images discontinued; repo archived Feb 2026. Community edition no longer maintained | Distributed mode requires 4+ nodes minimum | Bucket quotas (via mc admin bucket quota) | Full IAM with policies, user groups | ~256–512 MB | AGPL-3.0 (effectively dead for OSS use) | High-performance enterprise object storage |
| **SeaweedFS** | S3 gateway + POSIX filer (FUSE) | Yes (chrislusf/seaweedfs) | Yes — master + volume server + filer architecture; horizontal scale | Not natively per-bucket; workaround via prefix policies | Basic auth on volume servers; S3 IAM via filer gateway | ~64–256 MB (filer + master + volume, separate processes) | Apache-2.0 | Billions of small files; mixed S3 and POSIX access |
| **Nextcloud** | WebDAV + POSIX (via desktop sync); S3 backend optional | Yes (arm64v8/nextcloud) | High complexity; requires shared DB + shared object backend for HA | Per-user quota (admin-settable, live) | Per-user, per-folder share ACLs; group membership | ~512 MB–2 GB (PHP + web server + DB; WebDAV single request reported >300 MB RAM) | AGPL-3.0 | End-user file sync and collaboration |
| **Seafile** | WebDAV + proprietary sync protocol | Official arm64 from v9.0.1 | HA requires Seafile cluster (paid); CE is single-node only | Per-user quota (admin-settable) | Per-library ACL; group-based sharing | ~512 MB (seaf-server + ccnet + seahub) | AGPL-3.0 (CE) | Team file sharing with library-level version control |

**MinIO note.** MinIO entered maintenance mode in December 2025 and was
officially archived in February 2026. Docker images were discontinued in
October 2025. The community edition is no longer a viable dependency for
any new system. It is included in this table for completeness and to
explain why it is rejected.

Sources:
- Garage: https://garagehq.deuxfleurs.fr / https://hub.docker.com/r/dxflrs/garage
- MinIO: https://github.com/minio/minio / https://www.min.io/blog/from-open-source-to-free-and-open-source-minio-is-now-fully-licensed-under-gnu-agplv3
- MinIO end-of-life: https://blog.vonng.com/en/db/minio-is-dead/ / https://github.com/minio/minio
- SeaweedFS: https://github.com/seaweedfs/seaweedfs
- Nextcloud: https://hub.docker.com/r/arm64v8/nextcloud / https://docs.nextcloud.com/server/stable/admin_manual/installation/system_requirements.html
- Seafile CE arm64: https://forum.seafile.com/t/seafile-community-edition-9-0-1-is-ready-arm64-is-supported-now/15480

#### 4.3.3 Recommendation: Garage — because of footprint, quota model, and active maintenance

**Primary recommendation: Garage.**

Garage satisfies all eight requirements:

1. Publishes a multi-architecture OCI image at `dxflrs/garage` (Docker Hub)
   that includes arm64. The image is approximately 19 MB compressed.

2. Runs as a single binary. The entire service — S3 API, cluster RPC,
   metadata DB — is one statically linked Rust binary with no runtime
   dependencies. It runs comfortably in a single container.

3. Native cluster support with replication factor 1 (single-node) to 7+.
   A single-node deploy is production-viable with replication factor 1;
   adding a second node increases durability without service restart.
   Cluster membership is managed via `garage layout` and `garage apply`.

4. Per-key per-bucket ACL: `garage bucket allow --key <id> --read --write
   <bucket>`. Key-scoped local aliases allow each agent key to address the
   same bucket under a different name. An agent key with read-only access
   cannot write even if it knows the bucket name.

5. Per-bucket quota: `garage bucket set-quotas <bucket> --max-size 40G
   --max-objects 500000`. Quotas are enforced as a hard reject at upload
   time (HTTP 403 or 507 depending on version). They can be set and
   updated live via the admin API without service restart.

6. Full S3-compatible API. boto3, the AWS SDK for Go, aws-sdk-rust, and
   the Node AWS SDK all work against Garage without modification beyond
   the endpoint URL.

7. The Garage process itself idles at roughly 50–100 MB RSS on arm64 in a
   single-node configuration. The official documentation cites a 1 GB RAM
   minimum for a full cluster node; that figure is inclusive of the OS and
   leaves headroom — not the process itself. Empirical reports from
   Raspberry Pi and similar arm64 SBC deployments consistently show the
   process under 200 MB under light load.

8. AGPLv3, actively maintained as of April 2026. Written in Rust by the
   Deuxfleurs cooperative. The main development repository is at
   https://git.deuxfleurs.fr/Deuxfleurs/garage with a GitHub mirror at
   https://github.com/deuxfleurs-org/garage.

**Why not MinIO.** MinIO is archived and no longer publishes Docker images.
It is not a viable dependency.

**Why not SeaweedFS.** SeaweedFS has a larger operational surface — master
server, volume servers, and filer are separate processes with separate
configuration. It has no native per-bucket quota primitive. Its S3
permission model through the filer gateway is less granular than Garage's
per-key per-bucket ACL. SeaweedFS is a better choice for workloads in the
billions of objects range; taOS does not need that scale on a Pi cluster.
See §4.3.4.

**Why not Nextcloud or Seafile.** Both are end-user file-sync products.
Their resource footprint (PHP runtime, web server, separate DB process)
far exceeds the 512 MB constraint. Neither provides an S3-compatible API
as the primary interface — WebDAV requires a different client library
path in agent code. Nextcloud's WebDAV handler has documented RAM spikes
above 300 MB per request. Neither is appropriate as an agent-facing API
service.

#### 4.3.4 Alternative: SeaweedFS — when POSIX semantics are required or scale justifies complexity

**Alternative recommendation: SeaweedFS.**

Choose SeaweedFS over Garage when:

- An agent requires POSIX filesystem semantics (e.g. a framework that
  opens files by path rather than by S3 object key, or a tool that calls
  `os.listdir()`). SeaweedFS's filer provides a FUSE mount
  (`weed mount`) that presents the object store as a local directory.
  Garage has no FUSE component.

- The deployment will store millions of small files and the operator
  wants the SeaweedFS needle-map memory optimisation (16 bytes per file
  in memory rather than a full B-tree entry).

- The cluster will grow to 5+ nodes and the operator wants SeaweedFS's
  more granular volume-level replication controls.

For a single-node or two-node taOS deployment on Orange Pi hardware with
agent workloads typical of the current system, Garage is the better fit.

#### 4.3.5 Container deployability + cluster-move semantics

**Deploying Garage in taOS.**

Garage is deployed as an LXC container managed by taOS. A minimal
`taos-nas-garage` container runs the `garage server` process and has
one attached storage volume for the data directory:

```
/etc/garage.toml       ← config (bind-mounted from host config dir)
/var/lib/garage/meta/  ← metadata DB (sled; sqlite fallback) — storage volume
/var/lib/garage/data/  ← object data blocks — storage volume
```

The two volumes are separate so metadata (small, random I/O) and data
(large, sequential I/O) can be placed on different physical devices if
available.

**Cluster-move scenario.**

Worker-A hosts `taos-nas-garage`. Worker-A crashes or is taken offline
for maintenance. taOS detects the container as unavailable.

Recovery steps:

1. Export the Garage container snapshot:
   `incus export taos-nas-garage garage-{ts}.tar.gz` from worker-A (if
   accessible) or from the last periodic snapshot taken by the taOS
   backup schedule.

2. Export the two storage volumes:
   `incus storage volume export default taos-garage-meta garage-meta-{ts}.tar.gz`
   `incus storage volume export default taos-garage-data garage-data-{ts}.tar.gz`

3. On worker-B: import container and volumes.
   `incus import garage-{ts}.tar.gz taos-nas-garage`
   `incus storage volume import default garage-meta-{ts}.tar.gz taos-garage-meta`
   `incus storage volume import default garage-data-{ts}.tar.gz taos-garage-data`

4. Attach volumes to container and start:
   `incus config device add taos-nas-garage meta disk ... `
   `incus start taos-nas-garage`

5. Agents reconnect. Because Garage's endpoint URL is injected into
   agent containers as `TAOS_NAS_URL` at deploy time, updating this env
   var on all live agent containers and restarting them is the only agent-
   side action required.

**Limitation.** If worker-A is permanently unavailable and no exported
tarballs exist, the data in the Garage volumes on worker-A is lost.
This is equivalent to losing any unmirrored storage volume. The
mitigation is: (a) configure Garage with replication factor 2+ across
workers so data is already on worker-B, or (b) run periodic
`incus storage volume export` jobs as part of the taOS backup schedule.

With replication factor 2, Garage handles the worker-A failure
natively: the data is already on worker-B, the container just needs to
be restarted there. The admin API layout re-assignment (`garage layout
assign`, `garage layout apply`) redistributes data after the failed node
is removed from the cluster.

#### 4.3.6 Agent API ergonomics (S3 vs POSIX mount)

**S3 via boto3 (recommended for LLM agents).**

```python
import boto3

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["TAOS_NAS_URL"],       # e.g. http://127.0.0.1:3900
    aws_access_key_id=os.environ["TAOS_NAS_KEY"],
    aws_secret_access_key=os.environ["TAOS_NAS_SECRET"],
    region_name="garage",
)

# Upload a file the agent produced
s3.upload_file("/workspace/report.pdf", "agent-research", "reports/2026-04-16.pdf")

# Download a shared dataset
s3.download_file("shared-datasets", "corpus/wiki-2024.jsonl", "/workspace/wiki.jsonl")

# List files in the agent's bucket
for obj in s3.list_objects_v2(Bucket="agent-research")["Contents"]:
    print(obj["Key"])
```

The three env vars (`TAOS_NAS_URL`, `TAOS_NAS_KEY`, `TAOS_NAS_SECRET`)
are injected at deploy time by `deployer.py`, the same pattern as
`OPENAI_API_KEY` and `TAOS_SKILLS_URL`. The agent runtime code is
framework-agnostic: boto3, the AWS Go SDK, and aws-sdk-rust all speak the
same S3 wire protocol against the same endpoint.

**POSIX mount via SeaweedFS (fallback path).**

```python
# With SeaweedFS filer FUSE mount at /mnt/nas
import os

with open("/mnt/nas/agent-research/reports/2026-04-16.txt", "w") as f:
    f.write(report_text)

files = os.listdir("/mnt/nas/shared-datasets/")
```

The POSIX path requires the FUSE mount to be set up inside the container
at deploy time. It is simpler to write for agents that use standard file
I/O, but adds a FUSE dependency to the container image and makes the
underlying storage less portable.

**Which is better for LLM agents.** The S3 API is preferred for
LLM-authored code: it is explicit (every operation names the bucket and
key), it has consistent error semantics across all client libraries,
and it does not require a privileged FUSE mount inside the container.
An LLM writing `s3.upload_file(...)` is less likely to produce a
path-escape bug than one writing to an arbitrary POSIX path.

#### 4.3.7 Permission model — mapping taOS concepts to Garage primitives

taOS concept: **agent X in team Y with read-only access to dataset Z**.

Garage primitives: access keys + bucket permissions.

Mapping:

| taOS concept | Garage primitive |
|---|---|
| Agent identity | One Garage access key per agent, provisioned at agent deploy time |
| Agent's private storage | One bucket per agent (e.g. `agent-{slug}`); key has read+write |
| Team shared storage | One bucket per team (e.g. `team-{slug}`); all team member keys have read; team-owner key has write |
| Read-only dataset Z | Bucket `dataset-{name}`; agent key granted `--read` only via `garage bucket allow` |
| No access | Key not added to bucket; Garage returns HTTP 403 on any attempt |

taOS provisions access keys via the Garage admin API at agent creation
time and revokes them via `garage key delete` at agent archive time
(analogous to the existing LiteLLM key lifecycle). Permission changes
take effect immediately — no service restart, no propagation delay.

The Garage admin API is not the S3 API. taOS calls the admin API from
the host (or from the taOS API server process) to manage keys and bucket
policies. Agents only see the S3 endpoint.

**Key rotation.** At restore time, a new Garage key is provisioned and
the old one (revoked at archive time) is not re-used. The new key is
written to the agent's env file at `agent-home/{slug}/.openclaw/env`
alongside the new LiteLLM key. Same lifecycle pattern.

#### 4.3.8 Quota model

Garage bucket quotas:

```
garage bucket set-quotas agent-{slug} --max-size 40G --max-objects 1000000
```

**Defaults in taOS.** The proposed default per-agent bucket quota is
40 GB max-size. The quota is set at bucket creation time by the deployer.
It can be changed live via the admin API without any container restart.

**Enforcement.** Garage enforces quotas as a hard reject at upload time.
An agent attempting to upload a file that would push the bucket over the
quota limit receives an error response from the S3 API (HTTP 507
Insufficient Storage or HTTP 403 depending on Garage version). The agent
process sees a boto3 `ClientError`; the taOS trace callback records the
failure. The bucket continues to accept read and delete operations after
the quota is reached — only new writes are rejected.

**Soft warning.** Garage does not natively emit a soft warning at a
percentage threshold. The taOS monitoring layer (§5.2) handles this: a
background process periodically queries bucket usage via
`garage bucket info <name>` and raises a threshold alert at 35 GB (87.5%
of the 40 GB default). The user sees the two-button UX (§5.3) before the
hard limit is reached.

**Quota changes without downtime.** The `garage bucket set-quotas`
command hits the admin API, which updates the quota record in the
metadata DB in the next RPC round. No reload, no restart, no impact on
in-flight S3 requests. Changes take effect for the next upload attempt
after the update propagates (typically under one second).

### 4.4 Why both are opt-in

Neither Gitea nor Garage is deployed by default. A taOS installation
without code-hosting or shared file storage requirements should not have
these services consuming RAM and disk.

Both are opt-in services deployable from the taOS app store using the
existing Docker/LXC service flow. The deployer handles provisioning
(create container, attach volumes, set quotas, provision agent keys) and
the teardown handler handles cleanup (revoke keys, archive volumes).

Agent containers do not receive `TAOS_NAS_*` or `TAOS_GITEA_*` env vars
unless the corresponding service is enabled and the agent has been
granted access. No code change is required in the agent runtime to ignore
a missing service; absent env vars simply mean the feature is unavailable.

---

## 5. Disk quota + agent-authored audit flow

### 5.1 Quota enforcement (LXC + Docker specifics, storage-pool requirements)

**LXC (incus) containers.** Disk quotas for the container rootfs are
enforced via the storage pool. On a ZFS pool, per-container disk quotas
are set via:

```
incus config set taos-agent-{slug} limits.disk <size>
```

This sets a ZFS dataset quota on the container's root dataset. The
container cannot write past this limit; writes return ENOSPC to the
process inside the container. The same mechanism applies to attached
storage volumes: set `size.quota` on the volume.

On a btrfs pool, per-container quotas use btrfs subvolume qgroups. The
behaviour is equivalent: writes past the quota return ENOSPC.

On a `dir` pool (no CoW filesystem), incus does not enforce disk quotas
natively at the pool level. Operators must rely on the per-agent bucket
quota in Garage (§4.3.8) as the enforcement boundary for NAS storage.

**Docker containers.** If the host uses Docker with devicemapper or
overlay2 on XFS with `pquota` mount option, Docker supports
`--storage-opt size=<n>` at container creation. Without a quota-capable
FS, Docker does not enforce per-container disk limits at the kernel level.
For Docker deployments, Garage bucket quotas serve as the primary
per-agent storage limit.

**Recommended storage requirements.** A taOS host intended to enforce
per-agent disk quotas at the OS level should use ZFS or btrfs as the
incus pool backend. The install script should detect the pool type and
warn if it is `dir` and per-container quotas have been configured.

**Default quota.** 40 GB per agent container (rootfs + attached volumes
combined). This is the default applied at deploy time. Operators can
override per-agent at creation time or change it live.

### 5.2 Monitoring + thresholds

A background monitoring task in the taOS API server polls per-agent
storage usage at a configurable interval (default: every 15 minutes).

For LXC (ZFS/btrfs pool): `incus info taos-agent-{slug}` returns disk
usage in the storage section. The monitor parses this and computes
utilisation as a percentage of the configured `limits.disk`.

For Garage buckets (if NAS enabled): the monitor calls
`garage bucket info agent-{slug}` via the Garage admin API and reads
`bytes` and `objects` from the response.

Two thresholds:

| Threshold | Default | Action |
|---|---|---|
| Warn | 35 GB (87.5% of 40 GB default) | Emit UI notification; surface two-button dialog |
| Hard | 40 GB | Enforced by pool/Garage; writes rejected at kernel or S3 API level |

The monitoring task persists the last-known usage figure per agent in the
config or a lightweight side-channel store. It does not spam
notifications: once a warn notification has been raised for an agent, a
repeat notification fires only if usage drops below 30 GB and rises again
above 35 GB.

### 5.3 Notification UX — two buttons

When an agent's storage crosses the warn threshold, the taOS frontend
surfaces a dismissible notification card in the agent's detail view and
in the notification tray. The card reads:

> **{Agent display name}** is using {N} GB of its {quota} GB limit
> ({pct}% full).
>
> [**Add 10 GB**]   [**Audit with agent**]

**Add 10 GB.** Calls `PATCH /api/agents/{slug}/quota` with
`{"disk_quota_gb": current + 10}`. The API calls the underlying
`incus config set limits.disk` and `garage bucket set-quotas` with the
new value. No container restart. The notification is dismissed.

**Audit with agent.** Opens the agent's DM chat thread with a prefilled
prompt from the audit prompt library (§5.4). The notification stays
visible until the user dismisses it or the storage drops.

### 5.4 Audit prompt library + example prompt text

The prompt library is a set of short, high-signal instructions that ask
the agent to investigate its own storage and produce a deletion plan. The
prompts are stored in `tinyagentos/assets/quota_prompts/` as plain-text
files loaded at startup. The UX inserts the selected prompt into the
message input when the user clicks "Audit with agent".

**Example prompt — workspace audit:**

```
Your workspace is {pct}% full ({used_gb} GB of {quota_gb} GB).

Review the contents of /workspace and list the 10 largest files or
directories. For each one, tell me: what it is, when it was last
modified, whether it is safe to delete, and why.

Then propose a deletion plan that would free at least {free_target_gb} GB.
Do not delete anything yet — just propose.
```

**Example prompt — deep clean:**

```
Your workspace has reached {pct}% capacity. Perform a thorough audit:

1. List all files over 100 MB and classify each as: build artefact,
   downloaded dataset, model weights, log file, or user data.
2. Identify duplicate files by size and name.
3. Identify files not accessed in the last 30 days.
4. Propose a safe deletion order, largest first, stopping when the
   projected free space reaches 20 GB.

Use trash instead of rm for every file in your plan.
```

The prompts explicitly instruct the agent to use trash (§6) rather than
direct deletion, and to propose before acting. A "confirm and execute"
follow-up prompt is a separate library entry.

### 5.5 Recycle-bin integration

The audit prompt library instructs agents to use `trash-put` rather than
`rm`. §6 describes the three-layer enforcement model that makes
`trash-put` the default behaviour even when an agent writes `rm`.

The recycle bin path inside the container is
`/root/.local/share/Trash/files/` (XDG standard). The bin is
bind-mounted from the host at `data/agent-home/{slug}/.local/share/Trash/`
so trash contents are preserved across container rebuilds and are included
in container snapshots.

The monitoring task includes the Trash directory size in the reported
disk usage. A separate "empty trash" action in the UI calls
`DELETE /api/agents/{slug}/trash` which runs `trash-empty` inside the
container.

---

## 6. Recycle-bin enforcement — three layers

The goal is to make permanent deletion of files from within the container
require explicit intent. An agent (or an agent's tool) that runs `rm`
without thinking should land files in the recycle bin, not lose them.

Three layers are proposed, each adding defence depth:

### 6.1 Layer 1 — /usr/local/bin/rm wrapper + trash-cli

A shell script at `/usr/local/bin/rm` inside every agent container
intercepts the standard `rm` command and redirects to `trash-put`:

```sh
#!/bin/sh
# /usr/local/bin/rm — taOS safe-delete wrapper
exec /usr/bin/trash-put -- "$@"
```

`/usr/local/bin` takes precedence over `/usr/bin` in the default `PATH`.
Any process that locates `rm` via `PATH` (shell scripts, `subprocess.run`
in Python, agent framework tool calls) will invoke the wrapper.

The wrapper script is installed by the container base image build step.
`trash-cli` is installed in the base image as a package dependency.

**Limitation.** Processes that call `rm` via an absolute path
(`/bin/rm`, `/usr/bin/rm`) bypass the wrapper. Layer 3 provides the
backstop; Layer 2 (libtrash LD_PRELOAD) is deferred to a later phase.

### 6.2 Layer 2 — LD_PRELOAD libtrash (deferred, not in Phase 1)

`libtrash` is a shared library that intercepts GNU libc `unlink`,
`unlinkat`, `rename`, `rmdir`, and related calls at the dynamic linker
level. When `LD_PRELOAD=/usr/lib/libtrash.so` is set in the process
environment, any ELF binary that calls `unlink` — regardless of how it
invoked `rm` — will have its file-deletion calls redirected to the trash
directory.

Source: https://github.com/manuelarriaga/libtrash /
https://github.com/pete4abw/libtrash (maintained fork)

**Phase 1 decision: Layer 2 is skipped.** The `LD_PRELOAD` approach
causes false positives in npm and pip build flows (package install tools
make temporary unlink calls that should not be intercepted). For Phase 1,
Layer 1 (the `/usr/local/bin/rm` wrapper) plus Layer 3 (btrfs/Snapper
snapshots) provide sufficient protection. libtrash can be revisited as an
opt-in setting if Layer 1 bypass becomes a demonstrated problem in
production.

**Limitation (if enabled in a future phase).** `LD_PRELOAD` does not
affect statically linked binaries or binaries that use direct syscalls
(`SYS_unlink`) rather than libc. Layer 3 covers the backstop.

### 6.3 Layer 3 — FS snapshots as backstop

Incus (ZFS or btrfs) can take automatic container snapshots on a
schedule:

```
incus config set taos-agent-{slug} snapshots.schedule "0 */6 * * *"
incus config set taos-agent-{slug} snapshots.expiry "7d"
```

This creates a rolling window of snapshots every 6 hours, retained for 7
days. If a file is permanently deleted despite layers 1 and 2 (e.g. via
a static binary with a direct syscall), the user can restore the file by
mounting an earlier snapshot:

```
incus snapshot restore taos-agent-{slug} <snapshot-name>
```

Or by exporting the snapshot and extracting the specific file manually.

Snapshots are stored in the same incus pool as the container and consume
CoW storage (only changed blocks). On a typical agent container, a 6-hour
rotation window adds modest pool overhead.

### 6.4 Escape hatches

Legitimate use cases exist for permanent deletion:

- Removing large temporary files during a build.
- Clearing a download cache the agent knows is reproducible.
- Securely wiping a file with known sensitive content.

For these, the agent (or a user) uses `/usr/bin/rm` directly (absolute
path, bypassing the Layer 1 wrapper) with awareness that Layer 2 will
still intercept libc calls.

To bypass Layer 2 as well, the agent can unset `LD_PRELOAD` for a
specific command:

```sh
env -u LD_PRELOAD /bin/rm -f /workspace/large-temp-file
```

This is intentionally slightly inconvenient. The friction is a feature:
an agent that needs to permanently delete something has to express that
intent explicitly, either in its tool call or in the shell command. A
casual `rm` from a tool result lands in trash.

A UI-level "permanent delete" option in the file browser should call
this escape hatch form explicitly, logging the action to the trace store
with kind `tool_call` so the audit trail records what was permanently
removed and when.

---

## 7. Migration from current model

### 7.1 What breaks (and doesn't)

**Breaks (if v1 archive is removed):**

- `_archive_agent_fully` must be rewritten. The new implementation calls
  `incus snapshot create` and `incus export` rather than `shutil.move`.
- `restore_archived_agent` must be rewritten to call `incus import`.
- `purge_archived_agent` must delete the tarball file rather than a
  directory tree.
- `docs/runbooks/agent-archive-restore.md` is superseded entirely.

**Does not break:**

- Live agent operation: bind mounts, env var injection, trace store,
  LiteLLM proxy, skill MCP, openclaw bridge — all unchanged.
- `lxc-docker-coexistence.md` policy: unaffected; the coexistence fix
  operates at the iptables layer, independent of archive semantics.
- Openclaw integration: the bridge reads env vars injected at deploy time.
  Archive and restore already handle env file rewrite. No change to the
  bridge adapter logic.
- Existing archive buckets (v1 format, directory trees): these remain
  valid. Migration adds a `format: v1` field to old entries and a
  `format: v2` field to new entries. The restore handler dispatches
  on format version.

### 7.2 Phased migration plan (v1 + v2 alongside, then deprecate)

**Phase 1 (this doc).** Alignment. No code change.

**Phase 2 (disk quota + recycle bin).** Implement §5 and §6. These are
additive and do not require any archive format change. The v1 archive
path is untouched.

**Phase 3 (v2 archive, opt-in).** Add `format: v2` support to
`_archive_agent_fully` behind a feature flag or a per-install config
option (`archive_format: v2`). Both v1 and v2 archives can exist in
`config.archived_agents` simultaneously. The restore handler checks
`format:` and dispatches accordingly.

**Phase 4 (v2 default).** Set `archive_format: v2` as the default for
new archives. Existing v1 archives are still restorable.

**Phase 5 (v1 deprecation).** Add a migration utility:
`taos agent archive-migrate <id>` that restores a v1 archive to a
running container and immediately re-archives it as v2. Document this
in the runbook. After a release cycle, remove the v1 restore path.

### 7.3 Re-archiving existing agents

Existing live agents are not affected by the archive format change.
Their bind mounts continue to work. The only impact is at next archive
time: if `archive_format: v2`, the new `_archive_agent_fully` is used.

Existing archived agents (v1 format, directory trees) can be restored
using the v1 restore path until Phase 5 deprecation. There is no
requirement to re-archive them proactively.

---

## 8. Evolved thesis — framework-agnostic-runtime.md successor

The original thesis:

> **Containers hold code. Hosts hold state.**

remains correct for the live-agent case. No revision needed for day-to-day
operation, cluster dispatch, container upgrade, or framework swap. Those
benefits are preserved.

The pivot adds a corollary:

> **Archives hold everything. A snapshot is the canonical archive unit.**

During live operation, state is on the host (bind-mounts). At archive
time, the container snapshot captures the container plus its bind-mounted
state as a single portable bundle. The host-side directories become
ephemeral after a successful archive export; they are the runtime cache,
not the ground truth.

This extends the original rule rather than contradicting it. The rule
application checklist in `framework-agnostic-runtime.md` gains a sixth
question:

> 6. If this agent is archived, is the archive a single portable unit
>    (incus tarball) or does it require coordinated multi-step moves of
>    directory trees and config entries? If the latter, re-examine whether
>    the state can be inside the container at archive time.

The collaboration services (§4) are a further extension: they are also
containers holding their own state, following the same snapshot/export
model. The cluster-portable principle applies uniformly.

A successor to `framework-agnostic-runtime.md` would restate the thesis
as:

> **Containers hold code during operation. The cluster holds state during
> rest. The cluster is made of containers.**

State does not escape to raw host filesystem paths beyond the operational
boundary. During operation, bind-mounts provide host-speed access.
At rest (archived, migrated, backed up), the container snapshot is the
complete and portable representation.

---

## 9. Delivery phases

### 9.1 Phase 0 — this doc (alignment, complete)

All 10 decisions in §10 are now resolved. The storage pool backend is
btrfs; the code forge is Forgejo; the default per-agent quota is 40 GiB.
See §10 for the full list. This phase is complete.

Deliverables: this document reviewed and accepted.

### 9.2 Phase 1 — disk quota + recycle bin (low-risk, ships first)

Scope:

- Install `trash-cli` and the `/usr/local/bin/rm` wrapper in the
  container base image. (Layer 1 only; libtrash LD_PRELOAD deferred —
  see §6.2.)
- Configure Snapper automatic snapshots on all agent containers at deploy
  time (btrfs pools; detect and skip for `dir` pool). Layer 3 backstop.
- Add per-agent disk quota (`limits.disk`) to the deployer, defaulting
  to 40 GiB.
- Implement the monitoring background task (15-minute poll, 35 GB warn
  threshold).
- Implement the two-button notification card in the frontend.
- Add `PATCH /api/agents/{slug}/quota` endpoint.
- Ship initial quota prompt library (two prompts minimum).

Risk level: low. These changes are additive; they do not alter the
archive or restore paths.

### 9.3 Phase 2 — whole-container archive (opt-in alongside current)

Scope:

- Implement v2 `_archive_agent_fully` using `incus snapshot create` +
  `incus export`.
- Implement v2 `restore_archived_agent` using `incus import`.
- Implement v2 `purge_archived_agent` (delete tarball).
- Add `format: v1` / `format: v2` discriminator to archive config entries.
- Implement dispatch in restore and purge handlers.
- Add `archive_format: v2` config option; default remains `v1` in this
  phase.
- Update the runbook with v2 procedures alongside v1.
- Add integration test: archive → export tarball exists → restore →
  agent comes up with state intact → purge → tarball gone.

Risk level: medium. The v1 path remains untouched; v2 is behind a flag.

### 9.4 Phase 3 — Forgejo integration

Scope:

- Add Forgejo as an app-store service with an LXC container template.
  (Forgejo is the chosen forge — decision 7. The Gitea-compatible REST
  API is used throughout; endpoints are identical.)
- Implement provisioning: create Forgejo container, attach work-directory
  volume, configure initial admin account.
- Implement agent account provisioning at deploy time: `POST
  /api/v1/admin/users` for each new agent.
- Implement team/org provisioning at team creation time.
- Inject `TAOS_GITEA_URL` and `TAOS_GITEA_TOKEN` into agent containers
  that have Forgejo access enabled.
- Document cluster-move procedure for Forgejo (§4.2).

Risk level: medium. Forgejo is a net-new service; no existing code paths
are modified.

### 9.5 Phase 4 — Virtual NAS (Garage)

Scope:

- Add Garage as an app-store service with an LXC container template.
- Implement provisioning: create Garage container, attach meta and data
  volumes, write `garage.toml`, initialise cluster layout.
- Implement agent key provisioning at deploy time: `garage key import`
  (or API equivalent), `garage bucket create agent-{slug}`,
  `garage bucket allow`, `garage bucket set-quotas`.
- Implement key revocation at archive time.
- Inject `TAOS_NAS_URL`, `TAOS_NAS_KEY`, `TAOS_NAS_SECRET` into agent
  containers with NAS access enabled.
- Extend the §5.2 monitoring task to poll Garage bucket usage.
- Document cluster-move procedure (§4.3.5).

Risk level: medium. Net-new service. The Garage admin API is a separate
HTTP endpoint from the S3 API; taOS needs to manage both.

### 9.6 Phase 5 — deprecate v1 archive

Scope:

- Add `taos agent archive-migrate <id>` CLI/API command.
- Announce deprecation of v1 restore in release notes.
- Remove v1 restore path one major version after announcement.
- Rewrite `docs/runbooks/agent-archive-restore.md` as v2-only.

Risk level: low (all v1 archives should have been migrated before removal).

---

## 10. Resolved decisions

All 10 architecture decisions have been made. These answers are normative
for all implementation work from Phase 1 onward.

1. **Storage pool backend: btrfs.** btrfs is first-class in Debian,
   requires no out-of-tree kernel module, qgroups cover per-container
   quotas, transparent compression is available, and RAM footprint is
   lighter than ZFS. Portable across the mixed-arch cluster because btrfs
   lives on each host's Linux layer — on macOS/Windows hosts taOS runs
   inside a Linux VM (Orbstack / WSL2), making btrfs availability intrinsic
   to using LXC on those platforms. `install.sh` recommends btrfs but does
   not force it; dir pool remains supported with reduced quota enforcement.

2. **Archive target: configurable, default pool.** The config field
   `archive.target` accepts `pool:` (default — same storage pool, zero-copy
   via btrfs reflink/snapshot), `path:/abs/path`, or `s3://bucket`. v2
   tarballs land in `data/archive/` by default (same location as v1), using
   the same pool backend. Operators may override per-install.

3. **Chat history: both tarball + global DB.** Already implemented in
   commits 438b067 and bd909af. The per-agent `chat-export.jsonl` is
   written before the snapshot so the tarball is self-contained for
   cross-cluster moves. The shared DB remains the live store.

4. **Archive time budget: snapshot preferred, rsync fallback.** btrfs
   snapshot is the default archive mechanism (near-instantaneous, CoW).
   rsync fallback applies for dir-backed pools and Docker runtimes without
   snapshot support. `install.sh` recommends btrfs but does not require it.

5. **POSIX access: Garage primary + optional FUSE per agent.** Garage is
   the primary NAS backend, accessed via S3 API (boto3 / SDK). An optional
   per-agent `rclone mount` at `/nas/` exposes the same bucket as POSIX for
   agents that need it. One toggle per agent; no architecture lock-in. No
   dependency on SeaweedFS.

6. **Recycle-bin enforcement: Layer 1 + Layer 3 only (Phase 1).** The
   `/usr/local/bin/rm` shadow wrapper (Layer 1) and btrfs/Snapper automatic
   snapshots (Layer 3) ship in Phase 1. libtrash LD_PRELOAD (Layer 2) is
   deferred — it causes false positives in npm/pip build flows. Layer 2 may
   be revisited as an opt-in setting if Layer 1 bypass becomes a
   demonstrated issue in production.

7. **Code forge: Forgejo.** Drop-in Gitea-API-compatible. Governed by
   Codeberg e.V. non-profit; more active release cadence in 2025–26. All
   `POST /api/v1/admin/users` and related endpoints are identical. The two
   engines are swappable without any taOS integration change.

8. **Snapshot naming: prefix scheme.** taOS archive snapshots are always
   named `taos-archive-<ts>`. Automatic Snapper snapshots are named
   `auto-<ts>`. The archive code filters by the `taos-archive-` prefix.
   Auto-snapshots remain enabled as the Layer 3 recycle-bin backstop and
   are not touched by the archive flow.

9. **Garage metadata backend: sled.** Cross-arch portable (arm64 Pi +
   x86_64 Fedora). Decent production performance; tested in Garage
   upstream. Fallback to the sqlite backend if sled bugs appear. The LMDB
   portability concern from the open-question phase does not apply because
   sled uses its own cross-platform format.

10. **Default per-agent quota: 40 GiB.** Applied at deploy time via
    `limits.disk` (LXC) and `garage bucket set-quotas` (NAS). Overridable
    per-agent at deploy time with `--disk=XGiB` or via the admin UI. The
    35 GiB warn threshold (87.5%) is unchanged. Re-evaluate after
    observing real agent workspace usage in production.

---

## 11. Appendix — alternatives considered + rejected

### 11.1 Keep v1 archive indefinitely, no pivot

**Argument:** the v1 system works. The six-step archive has known failure
modes, all documented. It has been deployed and tested. Rewriting it
introduces new risk for uncertain gain.

**Rejected because:** the failure modes are real (timestamp collision,
directory-config divergence), and they scale in frequency with agent
count. At 3 agents, the probability of hitting a collision is negligible.
At 50 agents over months of operation, it is not. The refactor is
proportionate.

### 11.2 Bind-mount to NFS, keep bind-mount model for archive

**Argument:** put `data/` on NFS so cluster dispatch is free. Archive is
still `shutil.move` but NFS handles the transfer transparently.

**Rejected because:** NFS adds a network dependency to every file
operation the agent performs (every trace write, every workspace write).
It also requires NFS server infrastructure. The whole-container snapshot
model achieves cluster portability without a continuously-mounted network
filesystem. NFS remains valid as a _volume backend_ for the Garage and
Gitea storage volumes (§4), not as the primary agent state store.

### 11.3 MinIO as the virtual NAS

**Argument:** MinIO is the dominant S3-compatible self-hosted store.
Extensive documentation, many tutorials, well-known API.

**Rejected because:** MinIO Community Edition was stripped of its admin
UI in May 2025, entered maintenance mode in December 2025, and the
repository was officially archived in February 2026. No Docker images
have been published since October 2025. MinIO is not a viable dependency
for any new system.

### 11.4 Nextcloud as the virtual NAS

**Argument:** Nextcloud is mature, widely deployed, has per-user quotas
and WebDAV, and runs on arm64.

**Rejected because:** Nextcloud's resource footprint (PHP, web server,
relational DB as a separate container) exceeds the 512 MB RAM constraint.
WebDAV is not S3-compatible; agents would require a separate client
library path. Nextcloud's WebDAV handler has documented single-request
RAM spikes above 300 MB. It is designed for human file-sync workflows,
not agent API access. Its operational complexity (DB migrations,
app updates, maintenance mode) is disproportionate for a storage backend.

### 11.5 Seafile as the virtual NAS

**Argument:** Seafile has library-level ACLs, per-user quotas, arm64
support since v9.0.1, and a lightweight CE.

**Rejected because:** Seafile CE does not provide an S3-compatible API.
Its primary API is the Seafile REST API (proprietary), with WebDAV as an
optional secondary interface. No standard S3 client library works against
Seafile without a custom adapter. The library-level ACL model is
well-suited for human collaboration but maps awkwardly to the
bucket-per-agent model taOS needs. Resource footprint (seaf-server,
ccnet, seahub) sits at the edge of the 512 MB constraint.

### 11.6 GitLab CE as the code host

**Argument:** GitLab CE has more features than Gitea and is widely used.

**Rejected because:** GitLab CE requires a minimum of 2 GB RAM for a
single-node install, rising to 4 GB under load. The primary taOS
deployment target is an Orange Pi 5 Plus sharing RAM with 3+ agent
containers and the embedding process. GitLab CE is incompatible with
this constraint. Forgejo provides the agent-relevant features
(API-driven repository and account management) at under 256 MB RAM idle.
