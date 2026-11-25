# Storage engine

Adds a new disk to a taOS machine as a **storage area**, and makes an area a
person's **primary home**. Nobody prepares a disk by hand.

Face: `taosctl storage ...` and `/api/storage/*` (owner or admin only). The UI is
a later card; it presents this same engine.

## Safety class

`taosctl storage disks` lists each whole disk with a class and the reasons.

A disk is **available** only when all of these hold:

- it is a real disk (SATA/SAS, virtio, NVMe, MMC), not loop, md, device-mapper, zvol or network;
- it has no partition table, or a table whose partitions carry no signature of any kind and are plain Linux data partitions;
- nothing is mounted from it, it is not swap, not read-only, not part of a RAID/LVM/ZFS/bcache set, nothing is stacked on it;
- it is not the disk that holds `/` or `/boot` (resolved through `findmnt` and the device tree; if the root source cannot be matched to a disk, **every** disk is protected);
- its model and serial are known;
- it is not a disk taOS formatted before (VeraCrypt volumes carry no signature, so the helper keeps its own record and also protects any `taos-*` partition);
- the first and last MiB (or each partition's first MiB) read as zeros, which catches encrypted data with no signature;
- every probe succeeded and the probes agree with each other.

Anything else is **protected**, and so is any probe that failed, timed out or
produced output the classifier did not understand. The classifier never fails
open. It is `tinyagentos/storage_classifier.py`: standard library only, a pure
`classify()` over a snapshot that `collect()` gathers.

## Adding a disk

```
taosctl storage disks
taosctl storage add --disk /dev/disk/by-id/<id> --name photos --confirm "<model> <serial>"
taosctl storage add --disk ... --name games --kind shared --confirm "<model> <serial>"
```

- `private` (default): GPT partition, LUKS2, btrfs. The key file is created
  `O_CREAT|O_EXCL` mode 0400 under `/etc/taos/keys` (0700), so the disk unlocks
  with the system via `/etc/crypttab` (`nofail`).
- `shared` ("Shared with Windows"): VeraCrypt-encrypted NTFS, opened by Linux
  through `cryptsetup --type tcrypt --veracrypt` and by the Windows companion.
  Needs the `veracrypt` command line tool. If it is not installed the add is
  refused with that reason; `taos-storage-helper install-veracrypt
  --accept-licence` fetches a pinned release, but **this build ships no pinned
  release manifest**, so the shared option is unavailable until one is added
  (`/usr/local/lib/taos-storage/veracrypt.json`: `url` over https and `sha256`).
  Handing the key to the Windows companion is not part of this engine yet.
- Mounted at `/srv/taos/storage/<name>` with an fstab entry using `nofail` and a
  device timeout, so a missing disk never blocks boot.

The helper stops at the first failing command, changes nothing further, and
reports the completed steps and what state the disk is in. It never wipes to
"undo".

## The root helper

`/usr/local/sbin/taos-storage-helper` is the only code that formats a disk. It
has fixed subcommands (`list`, `status`, `add`, `prepare-home`,
`install-veracrypt`), takes a whole-disk `/dev/disk/by-id/` path, resolves it,
checks it is a block device and not in use, **re-runs the classifier itself**,
and compares the confirm string to the model and serial it read itself. Every
command is an argument list; there is no shell. It refuses to run unless it and
the classifier are root-owned and not writable by others.

`scripts/install-storage-helper.sh --user <service account>` installs it, the
classifier, and `/etc/sudoers.d/taos-storage`, which lets only that account run
only those subcommands of only that file. The entry is checked with
`visudo -c -f` before anything is installed. `install-server.sh` calls it.

## Primary home

`taosctl storage set-primary --person <id> --area <name>` creates
`<mount>/people/<id>` owned by the service account, mode 0700, and records the
assignment. Existing files are **not** moved. `taosctl storage primary --person
<id>` (or `GET /api/storage/primary`) returns the area, path, `total_bytes` and
`free_bytes`; a phone shows these. An unmounted area reports `null`, never zero.

## Agents

An agent cannot add a disk or set a home directly (403). It files a proposal
(`taosctl storage propose add ...`); a person approves it with
`taosctl storage approve <id> --confirm "<model> <serial>"`. Filing a proposal
changes nothing. An unbound host token cannot be told apart from a person, so
this guards agents that hold their own bound token.

## Testing

Unit tests use real `blkid -p` and `wipefs -n -J` output captured by
`scripts/capture-storage-fixtures.py` into `tests/fixtures/storage/` (the LUKS
and LVM images are hand-built headers read by the real tools, marked
`synthetic_image`; the lsblk JSON is built with lsblk's real key set). The
helper is tested with an injected runner and a device name no machine has.
`tests/test_storage_loop_integration.py` runs the real add on a **loop device
only**, and is skipped unless root, `TAOS_STORAGE_LOOP_TEST=1` and the format
tools are present. No test touches a real block device.
