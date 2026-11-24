# taOS Storage Engine

The taOS Storage Engine provides safe, automated management of physical disks as encrypted storage areas, with support for assigning storage areas as primary homes for persons.

## Overview

The storage engine solves the problem of adding new disks to a taOS system safely:

- **Safety-first**: Disks are classified as `available` or `protected` with detailed reasons
- **Encrypted by default**: LUKS2 encryption with system-unlock (random key stored in `/etc/taos/keys/`)
- **No manual preparation**: The engine handles partitioning, formatting, encryption, and mounting
- **Boot-safe**: systemd mount units with `nofail` so missing disks never block boot
- **Primary homes**: Persons can have a primary home directory on a storage area, with capacity exposed to devices

## CLI Commands

### List Disks

```bash
taosctl storage disks [--json]
```

Lists all physical block devices with metadata and safety classification.

**Output fields:**
- `path`: Device path (e.g., `/dev/sdb`)
- `by_id_path`: Stable `/dev/disk/by-id/` path
- `make`, `model`, `serial`: Hardware identification
- `size_bytes`, `size_human`: Disk capacity
- `transport`: Bus type (sata, nvme, usb, etc.)
- `rotational`: `true` for HDD, `false` for SSD/NVMe
- `read_only`: Hardware write-protect status
- `partitions`: Array of partition info (fstype, mountpoint, etc.)
- `mountpoints`: Current mountpoints on the whole disk
- `fstype`, `uuid`, `label`: Filesystem info on whole disk (rare)
- `parttable_type`: Partition table type (gpt, msdos, none)
- `safety_class`: `available` or `protected`
- `safety_reason`: Comma-separated reasons if protected

**Safety classes:**
- `available`: No partition table or only empty partitions, not mounted, not read-only, not part of running system, no filesystem with data, not encrypted foreign, not RAID/LVM/ZFS member
- `protected`: One or more of: system-disk, mounted, read-only, has-data, encrypted-foreign, raid-member, lvm-member, zfs-member

Only `available` disks may be formatted.

### Add Storage Area

```bash
taosctl storage add \
  --disk <by-id-path> \
  --confirm "<model> <serial>" \
  --name <area-name> \
  [--encrypt system-unlock|none]
```

Adds a disk as a storage area. The `--confirm` string must exactly match the disk's `<model> <serial>` as shown by `taosctl storage disks`.

**Operations performed:**
1. Re-verify disk is `available` and confirm string matches
2. Create GPT partition table with single partition covering entire disk
3. If `--encrypt system-unlock`: Format partition as LUKS2 with random key file in `/etc/taos/keys/` (root-only 0400)
4. Create ext4 filesystem with label `taos-<area-name>`
5. Write `/etc/crypttab` entry with key file and `nofail` (if encrypted)
6. Create systemd mount unit at `/srv/taos/storage/<area-name>` with `nofail`
7. Mount filesystem
8. Register in storage registry (id, name, mount, size, free, encryption, disk serial)

### Set Primary Home

```bash
taosctl storage set-primary --person <person-id> --area <area-name>
```

Assigns a storage area as a person's primary home. Creates the directory `/srv/taos/storage/<area>/homes/<person-id>` with correct permissions. Records the assignment in config.

### List Storage Areas

```bash
taosctl storage areas [--json]
```

Lists all registered storage areas with current capacity.

### Show Primary Home Capacity

```bash
taosctl storage primary <person-id> [--json]
```

Shows capacity and free space for a person's primary home. Used by phones and other devices to display storage info.

## API Endpoints

All endpoints require admin authentication unless noted.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/storage/disks` | List disks with safety classification |
| POST | `/api/storage/add` | Add disk as storage area |
| POST | `/api/storage/set-primary` | Set storage area as primary home |
| GET | `/api/storage/areas` | List registered storage areas |
| GET | `/api/storage/primary/{person_id}` | Get primary home capacity (person or admin) |

### POST /api/storage/add

**Request:**
```json
{
  "disk": "/dev/disk/by-id/ata-Samsung_SSD_123",
  "confirm": "Samsung SSD 870 EVO 123456",
  "name": "mystorage",
  "encrypt": "system-unlock"
}
```

**Response (200):**
```json
{
  "id": "uuid",
  "name": "mystorage",
  "mount": "/srv/taos/storage/mystorage",
  "size_bytes": 1000000000000,
  "free_bytes": 950000000000,
  "encryption": "system-unlock",
  "disk_serial": "123456",
  "disk_path": "/dev/disk/by-id/ata-Samsung_SSD_123",
  "created_at": 1234567890.0
}
```

### POST /api/storage/set-primary

**Request:**
```json
{
  "person": "person-id",
  "area": "mystorage"
}
```

**Response (200):**
```json
{
  "ok": true,
  "person_id": "person-id",
  "area": "mystorage",
  "home_path": "/srv/taos/storage/mystorage/homes/person-id"
}
```

## Privileged Helper

All formatting operations run via a narrowly-scoped privileged helper at `/usr/local/bin/taos-storage-helper`, invoked via passwordless sudo. The helper only accepts these allowlisted commands:

- `partition-disk <by-id-path>`
- `luks-format <part-path> <name> <key-file>`
- `luks-open <part-path> <name> <key-file>`
- `luks-close <name>`
- `make-fs <device> <label>`
- `mount-fs <mountpoint>`
- `unmount-fs <mountpoint>`
- `write-crypttab <name> <device> <key-file>`
- `write-mount-unit <name> <device> <mountpoint> <encrypted>`
- `remove-mount-unit <name>`
- `get-mount-info <mountpoint>`

The controller calls the helper only for admin/owner sessions. An agent path exists through the same API and requires the person's consent.

## Filesystem Choice: ext4

The storage engine uses **ext4** for the following reasons:

- Broad compatibility across all Linux kernels and initramfs
- Mature, stable tooling (mkfs.ext4, fsck, resize2fs)
- No userspace dependencies for mount (unlike btrfs which may need btrfs-progs in initramfs)
- Predictable performance for general-purpose workloads
- Well-understood failure modes and recovery procedures

btrfs was considered for snapshots and compression, but adds complexity and is not universally supported in minimal initramfs environments.

## Boot Safety

- All mounts use systemd mount units with `nofail` — a missing or failed disk never blocks boot
- Encrypted disks use `/etc/crypttab` with `nofail` and key files — systemd-cryptsetup handles unlocking automatically
- Key files are stored in `/etc/taos/keys/` with mode 0400 (root read-only)

## Storage Registry

Storage areas are recorded in the taOS config (`config.yaml`) under `storage_areas`:

```yaml
storage_areas:
  - id: "uuid"
    name: "mystorage"
    mount: "/srv/taos/storage/mystorage"
    size_bytes: 1000000000000
    free_bytes: 950000000000
    encryption: "system-unlock"
    disk_serial: "123456"
    disk_path: "/dev/disk/by-id/ata-Samsung_SSD_123"
    created_at: 1234567890.0
```

## Primary Home Assignment

When a storage area is set as a person's primary home:
1. The person's config entry gets `primary_home: "mystorage"`
2. Directory `/srv/taos/storage/mystorage/homes/<person-id>` is created (mode 0750)
3. Capacity is exposed via `GET /api/storage/primary/{person_id}` for device UIs

## Error Handling

| Error | HTTP Status | Cause |
|-------|-------------|-------|
| Disk not found | 404 | by-id path doesn't exist |
| Confirm mismatch | 400 | Confirm string doesn't match model+serial |
| Disk not available | 409 | Safety class is protected |
| Area not found | 404 | Storage area doesn't exist |
| Person not found | 404 | Person ID not in config |
| Forbidden | 403 | Non-admin trying to set another's primary home |
| Internal error | 500 | Helper command failed |

## Testing

Unit tests cover the safety classifier with fake lsblk/blkid output for every protected case:
- Mounted (whole disk or partition)
- Read-only hardware
- System disk (root/boot)
- Has data filesystem (ext4, xfs, btrfs, zfs, vfat, ntfs, etc.)
- Encrypted foreign (LUKS1/2, BitLocker)
- RAID member (linux_raid_member)
- LVM member (LVM2_member)
- ZFS member (zfs_*)

Integration tests use mocked loop devices to verify:
- Add storage area with encryption
- Add storage area without encryption
- Refusal on mismatched confirm string
- Refusal on unavailable disk
- Refusal on disk that gained data between list and add
- Rollback on partial helper failure
- Primary home assignment
- Capacity reporting

All tests run without touching real block devices.