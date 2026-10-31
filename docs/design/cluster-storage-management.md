# Cluster storage management

**Status:** Proposal, for owner review. Docs only.

## Problem

A taOS cluster is a pile of consumer boxes: a Pi with an SD card and a USB
SSD, a mini PC with an NVMe drive, a gaming PC with a big HDD. Today taOS
sees one number per node (root filesystem size and free space) and puts
everything under one data directory and one models tree on the controller.
Users cannot say "keep models on the NVMe in the gaming PC, backups on the
USB HDD on the Pi, scratch on tmpfs", or "give the agent LXCs the media share
on the home NAS". They cannot see a drive they just plugged in, cannot attach
a network share, and cannot move a role from one disk to another.

The goal is cluster storage management built from three parts: a per-node
inventory of attachable storage (local USB/SATA/NVMe block devices and
NFS/SMB network shares), an attach action that mounts one on a chosen node,
and roles that say what taOS keeps there. taOS then places data by role
across the cluster.

## Current state

- **Detection:** `tinyagentos/hardware.py` `_detect_disk()` reports root
  `total_gb` / `free_gb` and labels the first real disk (nvme, emmc, sd, ssd,
  hdd) from `lsblk -dno NAME,ROTA,TRAN`. No device list, partitions,
  filesystems, UUIDs, mountpoints or removable flag.
- **Workers:** heartbeats carry `detect_hardware()` plus `storage_cap_bytes`,
  `storage_used_bytes` and dedupe totals from the btrfs Incus pool
  (`tinyagentos/cluster/worker_capacity.py`). `scripts/install-worker.sh`
  creates a loopback btrfs pool `taos-worker-pool`;
  `scripts/install-server.sh` takes `TAOS_COW_POOL=auto|btrfs|zfs|dir`.
- **Per-agent quotas:** `tinyagentos/disk_quota.py` and
  `tinyagentos/routes/disk_quota.py` scan and set Incus root sizes.
- **Where data lives:** the data dir (`TAOS_DATA_DIR`, about 50 SQLite files,
  `agent-workspaces/`, `agent-memory/`, `trace/`); models under
  `models_root()` (`TAOS_MODELS_ROOT`, `tinyagentos/installers/model_paths.py`)
  plus a legacy `data/models`; archived models under `TAOS_ARCHIVE_ROOT`
  (`tinyagentos/cluster/model_archive.py`); agent archives at
  `archive.target` (`pool:` or `path:`, `tinyagentos/config.py`).
- **Backups:** `POST /api/backup` in `tinyagentos/routes/settings.py` tars
  config, installed apps and hardware only, not the data dir.
  `tinyagentos/data_snapshot.py` copies the data dir before a branch switch.
- **External drives:** none. The only removable-media awareness is
  `tinyagentos/atomic_io.py` tolerating a FAT/exFAT data dir.
- **UI:** the Settings Storage section in `desktop/src/apps/SettingsApp.tsx`
  reads `GET /api/settings/storage`. **Bug:** the route returns
  `{"storage": [...]}` but the panel expects a bare array, so it always shows
  hard-coded placeholder sizes. Fixed separately; it does not need this design.
- **Prior decisions:** `docs/design/architecture-pivot-v2.md` chose btrfs
  pools and rejected NFS bind mounts of agent state across nodes. This design
  keeps that for agent state: the Incus pool and the data dir stay on
  node-local storage and move between nodes by copy. Attaching a network
  share for other roles (models, archives, backups, media) is a separate
  thing and is in scope (see Network shares).

## Design

### Inventory

Each node (controller and workers) reports a **device inventory** on
register and on change (udev events, with a slow poll fallback):

```
devices[]: {
  id:          stable, from filesystem UUID, else WWN/serial
  node:        node id
  path:        /dev/sda1 (display only, never used as identity)
  model, serial, transport (usb|sata|nvme|mmc|virtio|nfs|smb), rotational, removable
  size_bytes, fs_type, label
  mounted_at:  path or null
  managed:     true when taOS mounted it
  roles:       []
  health:      ok | warn | fail (SMART where available)
}
```

Collection reads `lsblk --json -b -o ...` and `/sys/block` with argument
lists, no shell. SMART uses `smartctl --json` when installed and is optional.
The controller stores the merged inventory in a new `storage_devices` table.

### Attach and detach

- **Attach** a device: taOS mounts it at `/var/lib/taos/storage/<id>` by
  UUID with `nosuid,nodev,noexec` (except roles that need exec, see below),
  writes a systemd mount unit so it survives reboot (with `nofail` so an
  unplugged USB disk does not block boot), and marks `managed: true`.
- **Format** is a separate, explicit, typed-confirmation action (the user
  types the device label). Offered filesystems: ext4 and btrfs. Never offered
  for a device holding the root filesystem or an existing taOS role.
- **Detach** refuses while a role holds live data, unless the user first
  moves the role (below). Safe removal for USB unmounts and powers off.
- All of this runs through a small root helper with a closed verb set
  (`mount`, `unmount`, `format`, `poweroff`) and device ids validated
  against the inventory, the same pattern the lock screen power controls use
  (a root drop box with a fixed verb list), never sudo from the web process.

### Network shares

A network share is attachable storage like a USB disk, with a different
inventory source: the user adds it by hand instead of taOS discovering it.

- **Inventory:** a share is a `devices[]` entry with `transport: nfs|smb`,
  `source` (`<server>:/<export>` for NFS, `//<server>/<share>` for SMB/CIFS),
  `node` (the node that mounts it; the same share may be attached on several
  nodes as separate entries) and `credential` (the name of a secret, never a
  value). `id` is a hash of node, transport and source, since a share has
  no UUID. `health` reports reachability (last mount or stat result), not
  SMART.
- **Credentials:** never stored in config, the inventory table, the mount
  unit or an API response. They go in the existing secrets store,
  `SecretsStore` in `tinyagentos/secrets.py`: values are Fernet-encrypted
  with a key at `<data_dir>/.secrets_key` (created mode 0600 via
  `atomic_create_bytes`), and `credentials` is already one of its
  `DEFAULT_CATEGORIES`. Share secrets are never granted to agents through
  `secret_access`, so no agent can read them; agents only ever see the
  mounted path.
- **Mount:** the root helper gains a `mount-share` verb. For CIFS it reads
  the secret, writes a root-owned credentials file (mode 0600, under a
  root-only directory) and mounts with `credentials=<file>`, so the password
  never appears on a command line or in `/proc/mounts`. NFS uses `sec=sys`
  with no credentials file (Kerberos is out of scope for now). The mount
  point is the same `/var/lib/taos/storage/<id>` with `nosuid,nodev,noexec`,
  and the systemd mount unit adds `_netdev,nofail` so an offline NAS does not
  block boot. On a worker the share is mounted by the worker itself: it
  fetches the secret from the controller over its existing signed channel
  (`require_worker_hmac`, `tinyagentos/cluster/worker_auth.py`) and writes
  its own local credentials file.
- **Roles:** a share may hold `models`, `model-archive`, `backups` and
  `media`, plus `agent-share` (below). It may not hold
  `agent-state` or `scratch`.
- **Share for agent LXCs:** the share is mounted on the host node first,
  then bind-mounted into the container as an Incus disk device, the
  mechanism `create_container` in `tinyagentos/containers/__init__.py`
  already uses for its `mounts` list (`incus config device add <name>
  taos-mount-<n> disk source=<host path> path=<container path>`). Today that
  code skips bind mounts for containers on a remote worker, because the
  worker cannot see the controller's filesystem; that is why the share must
  be mounted on the node that runs the agent, and the disk device points at
  that node's local mount point. Read-only is the default; read-write is a
  per-agent opt-in.
- **Constraint, agent state stays local:** the Incus pool, agent SQLite
  databases and the data dir never live on a share. This is the earlier
  rejection in `docs/design/architecture-pivot-v2.md`: SQLite locking over
  NFS/SMB is unreliable, a btrfs Incus pool cannot sit on a network
  filesystem, and a NAS dropping off the network would freeze or corrupt
  running agents instead of just making some files unavailable.

### Roles

A role says what taOS stores on a device. Proposal (not decided, see Open
questions): a device may hold several roles, and a role may live on several
devices across the cluster.

| Role | Holds | Placement rule |
|---|---|---|
| `models` | model weights tree (`models_root()` layout) | prefer fast local disk on the node that runs the model |
| `model-archive` | archived weights | any large disk; cheap HDD is fine |
| `backups` | taOS backups (data dir snapshots, agent archives) | a different device, ideally a different node, from the data |
| `agent-state` | Incus pool for agent containers | fast disk on the node that runs the agents |
| `media` | user files, generated images, recordings | large disk, user-visible in Files |
| `scratch` | caches, temp, conversion work | anything fast; may be wiped |
| `agent-share` | a share mounted into chosen agent LXCs | network share or local disk; read-only by default |

Roles map onto existing knobs instead of new mechanisms: `models` sets the
node's `TAOS_MODELS_ROOT`; `model-archive` sets `TAOS_ARCHIVE_ROOT`;
`backups` becomes an `archive.target` of `path:` plus the backup destination;
`agent-state` creates an Incus storage pool on the device; `media` becomes a
Files location; `agent-share` adds a disk device to the chosen agents'
containers. Each node keeps one default device per role (the root disk
until the user picks another).

### Moving a role

"Move models from the SD card to the USB SSD" is a job: copy with checksum
verification, switch the role pointer, then remove the old copy only after a
health check. For `agent-state`, a move is `incus move` of each container to
the new pool (stopped, or live where supported). The cluster-level case
(a role on another node) reuses the existing paths: model placement and
`tinyagentos/cluster/model_archive.py` promotion for models, container
migration for agent state. Agent state never moves onto or through a
network filesystem. A role held on a share needs no move between nodes:
each node attaches the same share.

### Backup placement

With a `backups` device, the backup job finally covers what matters: a
consistent snapshot of the data dir (SQLite online backup API per database,
not a raw copy), agent archives and config, with retention. The UI warns when
backups and data share a device, and suggests a device on another node.

### API

| Route | Notes |
|---|---|
| `GET /api/storage/devices` | cluster inventory, filterable by node |
| `POST /api/storage/devices/{id}/attach` | admin; mount and persist |
| `POST /api/storage/shares` | admin; add an NFS/SMB share, credentials go to the secrets store |
| `POST /api/storage/devices/{id}/detach` | admin; refuses with live roles |
| `POST /api/storage/devices/{id}/format` | admin; typed confirmation token |
| `PUT /api/storage/devices/{id}/roles` | admin; set roles, starts move jobs |
| `GET /api/storage/jobs` | move and backup jobs with progress |

Workers expose the same verbs on their worker API, called only by the
controller. All routes are admin only.

### UI

A Storage view in the Cluster app (`desktop/src/apps/ClusterApp.tsx`,
registered as `cluster` in `desktop/src/registry/app-registry.ts`), which
already shows each worker's root disk from the heartbeat `hw.disk`. Nodes
as groups, each device or share as a row with model or source, size, usage
bar, health dot and role chips; an Attach button for unmanaged devices and
an Add share form; a role editor with the placement hint for each role; a
jobs strip. The same view hosts the install-location chooser
(#149 on the owner's tracker), since choosing where something installs is choosing a
device with the right role. Keyboard and screen reader access as for every
taOS panel (list semantics, labelled controls, confirm dialogs with
`role="alertdialog"`).

## Phased plan

1. **Fix the Storage panel bug** (separate small PR) and report real usage
   from `models_root()` instead of the legacy `data/models`.
2. **Inventory:** device collection on controller and workers, the
   `storage_devices` table, `GET /api/storage/devices`, read-only UI.
3. **Attach and detach** via the root helper, mount units, safe removal.
   Hardware test with a USB SSD on a Pi.
4. **Network shares:** add share, credentials in the secrets store,
   `mount-share`, bind mount into agent LXCs. Test with an SMB and an NFS
   export.
5. **Roles without moves:** assign roles to empty devices; new data lands
   there (models, archive, backups, media, scratch, agent-share).
6. **Backups that cover the data dir**, onto the `backups` role.
7. **Role moves** on one node, then across nodes for models and agent state.
8. **Format** and SMART health.

## Open questions

1. Where does storage management live? Per the owner's ruling, in the
   Cluster app (see UI), which is also the home for the install-location
   chooser (#149 on the owner's tracker). Open part: should Settings keep a read-only
   summary that links there? Proposal: yes.
2. Should taOS auto-attach a known device (seen before, same UUID) when it is
   plugged in again? Proposal: yes for devices that hold a role, never for
   new ones.
3. Offer ZFS? The server installer already supports a ZFS pool; managing
   ZFS devices from the UI is a much larger surface. Proposal: no, ext4 and
   btrfs only.
4. Encryption at rest for removable `backups` devices (LUKS) in phase 8 or
   later?
5. Should `media` be per user (each user's files on their chosen device) or
   per box?
6. Roles per mount: may one device or share hold several roles, and one
   role span several devices (many-to-many), or is it one role per mount?
   Proposal: many-to-many, as written in Roles, but the ruling left this
   open.
