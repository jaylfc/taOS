### Added
- Storage engine for adding disks as encrypted storage areas and assigning primary homes
- `taos-storage disks --json`: lists block devices with make, model, size, serial, transport, partitions, mounts, and safety classification (available/protected with reasons)
- `taos-storage add --disk <by-id> --confirm "<model> <serial>" --name <area> [--encrypt system-unlock|none]`: safely formats an available disk with GPT, LUKS2 (random key in /etc/taos/keys/), ext4, systemd mount unit with nofail, and registers in storage registry
- `taos-storage set-primary --person <id> --area <area>`: assigns a storage area as a person's primary home, creates home directory with correct permissions
- API endpoints: GET /api/storage/disks, POST /api/storage/add, POST /api/storage/set-primary, GET /api/storage/areas, GET /api/storage/primary/{person_id}
- Privileged helper `taos-storage-helper` with allowlisted commands for root operations (partition, LUKS, fs, mount, crypttab, systemd units)
- Unit tests for safety classifier covering all protected cases (mounted, ro, system, has-data, encrypted-foreign, raid, lvm, zfs)
- Integration tests for add/encrypt/mount/registry flows with mocked loop devices