### Added

- Storage engine: safe, automated disk management for encrypted storage areas
- `taosctl storage disks --json`: lists block devices with make, model, size, serial, transport, partitions, mounts, and safety classification (`available` vs `protected` with reasons)
- `taosctl storage add --disk <by-id> --confirm "<model> <serial>" --name <area> [--encrypt system-unlock|none]`: adds a disk as a storage area with GPT partition, LUKS2 encryption (system-unlock with key file in `/etc/taos/keys/`), ext4 filesystem, systemd mount unit with `nofail`, and registry entry
- `taosctl storage set-primary --person <id> --area <area>`: assigns a storage area as a person's primary home, creates home directory with correct permissions, exposes capacity via API
- `taosctl storage areas` and `taosctl storage primary <person>`: list areas and show primary home capacity
- API endpoints: `GET /api/storage/disks`, `POST /api/storage/add`, `POST /api/storage/set-primary`, `GET /api/storage/areas`, `GET /api/storage/primary/{person_id}`
- Privileged helper (`taos-storage-helper`) with allowlisted commands for root operations (partitioning, LUKS, filesystem, mount units, crypttab)
- Unit tests for safety classifier covering all protected cases (mounted, read-only, system disk, has-data, encrypted-foreign, RAID/LVM/ZFS members)
- Integration tests on mocked loop devices proving add, encryption, mount, registry, and refusal on mismatched confirm and disk state changes