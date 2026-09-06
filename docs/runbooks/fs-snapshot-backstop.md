# FS Snapshot Backstop — Layer 3 Recycle-Bin

## What it does

taOS implements a three-layer safety net for deleted container data:

| Layer | Mechanism | Scope |
|-------|-----------|-------|
| 1 | In-container soft-delete (`/usr/local/bin/rm` wrapper moves files to `.taos-trash/`) | Per container |
| 2 | Agent archive snapshots taken before destructive operations (`taos-archive-<ts>`) | Per container |
| 3 | Host-level btrfs snapshots via Snapper (this runbook) | Entire storage pool |

Layer 3 covers cases where Layers 1 and 2 were bypassed — for example, a container was deleted without an archive, the agent ran `rm` directly via shell bypass, or the in-container trash was purged early.

Layer 3 is only available when the incus storage pool uses the **btrfs** driver. ZFS pools (`zfs-auto-snapshot`) are a Phase 2+ item. `dir`-backed pools have no snapshot capability.

---

## Verify snapper is running

```bash
systemctl status snapper-timeline.timer
systemctl status snapper-cleanup.timer
snapper -c taos-containers list
```

The `list` output shows numbered snapshots with type `timeline` and a timestamp. On a freshly configured host there may be zero entries until the first hourly tick.

You can also use the probe script to see a summary without changing state:

```bash
bash /opt/tinyagentos/scripts/fs-snapshot-probe.sh
```

---

## List snapshots

```bash
snapper -c taos-containers list
```

Example output:

```
 # | Type     | Pre # | Date                     | User | Cleanup  | Description | Userdata
---+----------+-------+--------------------------+------+----------+-------------+---------
 1 | timeline |       | Wed 16 Apr 2026 09:00:01 | root | timeline |             |
 2 | timeline |       | Wed 16 Apr 2026 10:00:01 | root | timeline |             |
```

---

## Restore a file from a past snapshot

Snapper stores btrfs snapshots under the pool source path. Locate it:

```bash
incus storage show default | grep source
# e.g. source: /var/lib/incus/storage-pools/default
```

Snapshots live at:

```
/var/lib/incus/storage-pools/<pool>/.snapshots/<N>/snapshot/
```

where `<N>` is the snapshot number from `snapper list`.

To recover a file:

```bash
POOL_PATH=/var/lib/incus/storage-pools/default
SNAP=5
# List containers visible in that snapshot
ls "$POOL_PATH/.snapshots/$SNAP/snapshot/containers/"

# Copy the file out
cp "$POOL_PATH/.snapshots/$SNAP/snapshot/containers/my-agent/rootfs/home/user/important.txt" \
   /tmp/recovered-important.txt
```

Mount read-only if you need to browse interactively:

```bash
mount -o ro,subvol=.snapshots/$SNAP/snapshot "$POOL_PATH" /mnt/snap-$SNAP
ls /mnt/snap-$SNAP/containers/
umount /mnt/snap-$SNAP
```

---

## Disable Layer 3

To stop new snapshots being taken and prevent cleanup runs:

```bash
systemctl disable --now snapper-timeline.timer
systemctl disable --now snapper-cleanup.timer
```

Existing snapshots are not deleted by this — they remain until you remove them manually:

```bash
snapper -c taos-containers delete 1-5   # delete snapshots 1 through 5
```

To remove the snapper config entirely:

```bash
snapper -c taos-containers delete-config
```

---

## Storage cost

Snapper uses btrfs Copy-on-Write. Each snapshot costs only the delta since the previous one. On a lightly active cluster (agents idle, no large model moves) a week of hourly snapshots typically adds a few hundred MB. Cost grows proportionally with data churn — heavy container installs, large file writes, or frequent model swaps will increase delta size. Monitor with:

```bash
btrfs filesystem du /var/lib/incus/storage-pools/default
snapper -c taos-containers list
```

Retention policy: 24 hourly + 7 daily. Older snapshots are cleaned up automatically by `snapper-cleanup.timer`.
