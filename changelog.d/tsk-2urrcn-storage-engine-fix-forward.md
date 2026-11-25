### Fixed
- Storage engine now correctly detects partitions (lsblk without `-d`) and verifies signatures with `blkid -p`/`wipefs -n` on disk and all partitions. A disk with ext4, NTFS, BitLocker, LVM, or RAID partitions is now classified as PROTECTED instead of AVAILABLE.
- Uncertainty (lsblk/blkid/wipefs/findmnt failure, parse error, missing field) now classifies disks as PROTECTED (fail-closed) instead of AVAILABLE.
- Fixed FrozenInstanceError in `/api/storage/disks` route by resolving by-id paths before JSON serialization.
- CLI storage command now uses global `--json` flag consistently; per-verb `--json` flags removed.
- taos-storage-helper re-validates disk safety and confirm string before any write (TOCTOU protection); `wipefs -a` runs with `check=True`; key files created with `os.open(O_CREAT|O_EXCL, 0o400)`.
- Installer now deploys taos-storage-helper, narrowly-scoped sudoers rule, and `/etc/taos/keys` (0700 root).
- Added tests driving real route and CLI with mocked lsblk fixture data; no real block devices touched.