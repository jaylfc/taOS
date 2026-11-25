# RED Proof for tsk-2urrcn

## Issue 3: FrozenInstanceError in routes/storage.py

```bash
FAILED ../test_red_frozen.py::test_storage_disks_route_frozen_dataclass - dataclasses.FrozenInstanceError: cannot assign to field 'by_id_path'
```

### Traceback
```
File "/tmp/exec-tsk-2urrcn/tinyagentos/routes/storage.py", line 74, in list_disks
    disk.by_id_path = await _get_disk_by_id_path(disk.path)
    ^^^^^^^^^^^^^^^
dataclasses.FrozenInstanceError: cannot assign to field 'by_id_path'
```

## Issue 1: lsblk -d omits children (partitions)

The current `discover_disks()` uses `lsblk -d` which only lists parent devices, omitting children. A disk with ext4/NTFS/BitLocker/LVM/RAID partitions incorrectly classifies as AVAILABLE.

## Issue 2: Uncertainty classifies as available (should be PROTECTED)

- Parse error in `discover_disks()` returns `[]` (lines ~300-310)
- `_get_system_disk_paths()` swallows errors into empty set (line ~272)
- findmnt failure makes every disk non-system

## Issue 4: CLI storage command --json handling

`cli/taosctl/commands/storage.py:66` reads `args.json` but `add` subparser doesn't define `--json`; only global flag exists.

## Issue 5: taos-storage-helper TOCTOU

ROOT helper must re-validate argument itself before any write; `wipefs -a` must run with `check=True`.

## Issue 6: Install path missing

Helper, sudoers rule, and `/etc/taos/keys` (0700 root) not installed by installer; key file created with `os.open(O_CREAT|O_EXCL, 0o400)`.

## Issue 7: Tests must drive real route/CLI

No test touches real block device; loop devices via losetup in opt-in test only, skipped in CI unless root.