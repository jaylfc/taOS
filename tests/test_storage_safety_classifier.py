"""Unit tests for storage engine safety classifier.

Tests the DiskSafetyClassifier with fake lsblk/blkid/wipefs output
for every protected case: mounted, ro, system, has filesystem,
encrypted foreign, LVM/ZFS member.
"""
import pytest

from tinyagentos.storage_engine import (
    PROTECTED_ENCRYPTED_FOREIGN,
    PROTECTED_HAS_DATA,
    PROTECTED_LVM_MEMBER,
    PROTECTED_MOUNTED,
    PROTECTED_RAID_MEMBER,
    PROTECTED_READ_ONLY,
    PROTECTED_SYSTEM_DISK,
    PROTECTED_ZFS_MEMBER,
    SAFETY_AVAILABLE,
    SAFETY_PROTECTED,
    DiskInfo,
    DiskSafetyClassifier,
    PartitionInfo,
)


def _make_base_disk(**overrides) -> DiskInfo:
    """Create a base DiskInfo for testing."""
    defaults = {
        "path": "/dev/sdb",
        "name": "sdb",
        "make": "TestVendor",
        "model": "TestModel",
        "serial": "TEST123456",
        "size_bytes": 1000_000_000_000,
        "transport": "sata",
        "rotational": True,
        "partitions": [],
        "mountpoints": [],
        "fstype": None,
        "uuid": None,
        "label": None,
        "parttable_type": None,
        "ro": False,
        "safety_class": SAFETY_PROTECTED,
        "safety_reason": None,
    }
    defaults.update(overrides)
    return DiskInfo(**defaults)


def _make_partition(**overrides) -> PartitionInfo:
    """Create a PartitionInfo for testing."""
    defaults = {
        "path": "/dev/sdb1",
        "name": "sdb1",
        "size_bytes": 1000_000_000_000,
        "fstype": None,
        "uuid": None,
        "label": None,
        "mountpoint": None,
        "parttype": None,
        "partlabel": None,
    }
    defaults.update(overrides)
    return PartitionInfo(**defaults)


class TestDiskSafetyClassifier:
    """Tests for the DiskSafetyClassifier."""

    def test_available_clean_disk(self):
        """A clean disk with no partitions, no mounts, not system disk should be available."""
        disk = _make_base_disk()
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_AVAILABLE
        assert result.safety_reason is None

    def test_protected_system_disk(self):
        """The system disk (root/boot) should be protected."""
        disk = _make_base_disk(path="/dev/sda", name="sda")
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_PROTECTED
        assert PROTECTED_SYSTEM_DISK in result.safety_reason

    def test_protected_system_disk_by_partition(self):
        """A partition on the system disk should also be protected."""
        disk = _make_base_disk(path="/dev/sda2", name="sda2")
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_PROTECTED
        assert PROTECTED_SYSTEM_DISK in result.safety_reason

    def test_protected_read_only(self):
        """A read-only disk should be protected."""
        disk = _make_base_disk(ro=True)
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_PROTECTED
        assert PROTECTED_READ_ONLY in result.safety_reason

    def test_protected_mounted_whole_disk(self):
        """A disk with a mountpoint should be protected."""
        disk = _make_base_disk(mountpoints=["/mnt/data"])
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_PROTECTED
        assert PROTECTED_MOUNTED in result.safety_reason

    def test_protected_mounted_partition(self):
        """A disk with a mounted partition should be protected."""
        disk = _make_base_disk(partitions=[
            _make_partition(mountpoint="/mnt/data")
        ])
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_PROTECTED
        assert PROTECTED_MOUNTED in result.safety_reason

    def test_protected_has_data_fs_on_disk(self):
        """A disk with a data filesystem should be protected."""
        for fs in ("ext4", "xfs", "btrfs", "zfs", "vfat", "ntfs", "f2fs"):
            disk = _make_base_disk(fstype=fs)
            classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
            result = classifier.classify(disk)

            assert result.safety_class == SAFETY_PROTECTED, f"fs={fs} should be protected"
            assert PROTECTED_HAS_DATA in result.safety_reason, f"fs={fs}"

    def test_protected_has_data_fs_on_partition(self):
        """A disk with a partition that has a data filesystem should be protected."""
        for fs in ("ext4", "xfs", "btrfs", "zfs", "vfat", "ntfs", "f2fs"):
            disk = _make_base_disk(partitions=[
                _make_partition(fstype=fs)
            ])
            classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
            result = classifier.classify(disk)

            assert result.safety_class == SAFETY_PROTECTED, f"fs={fs} should be protected"
            assert PROTECTED_HAS_DATA in result.safety_reason, f"fs={fs}"

    def test_protected_encrypted_foreign_luks_on_disk(self):
        """A disk with LUKS should be protected as encrypted foreign."""
        for fs in ("crypto_LUKS", "crypto_LUKS1", "crypto_LUKS2", "BitLocker"):
            disk = _make_base_disk(fstype=fs)
            classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
            result = classifier.classify(disk)

            assert result.safety_class == SAFETY_PROTECTED, f"fs={fs} should be protected"
            assert PROTECTED_ENCRYPTED_FOREIGN in result.safety_reason, f"fs={fs}"

    def test_protected_encrypted_foreign_luks_on_partition(self):
        """A disk with a LUKS partition should be protected as encrypted foreign."""
        for fs in ("crypto_LUKS", "crypto_LUKS1", "crypto_LUKS2", "BitLocker"):
            disk = _make_base_disk(partitions=[
                _make_partition(fstype=fs)
            ])
            classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
            result = classifier.classify(disk)

            assert result.safety_class == SAFETY_PROTECTED, f"fs={fs} should be protected"
            assert PROTECTED_ENCRYPTED_FOREIGN in result.safety_reason, f"fs={fs}"

    def test_protected_raid_member_on_disk(self):
        """A disk that is a RAID member should be protected."""
        disk = _make_base_disk(fstype="linux_raid_member")
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_PROTECTED
        assert PROTECTED_RAID_MEMBER in result.safety_reason

    def test_protected_raid_member_on_partition(self):
        """A disk with a RAID member partition should be protected."""
        disk = _make_base_disk(partitions=[
            _make_partition(fstype="linux_raid_member")
        ])
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_PROTECTED
        assert PROTECTED_RAID_MEMBER in result.safety_reason

    def test_protected_lvm_member_on_disk(self):
        """A disk that is an LVM physical volume should be protected."""
        disk = _make_base_disk(fstype="LVM2_member")
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_PROTECTED
        assert PROTECTED_LVM_MEMBER in result.safety_reason

    def test_protected_lvm_member_on_partition(self):
        """A disk with an LVM partition should be protected."""
        disk = _make_base_disk(partitions=[
            _make_partition(fstype="LVM2_member")
        ])
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_PROTECTED
        assert PROTECTED_LVM_MEMBER in result.safety_reason

    def test_protected_zfs_member_on_disk(self):
        """A disk that is a ZFS member should be protected."""
        for fs in ("zfs_member", "zfs_pool"):
            disk = _make_base_disk(fstype=fs)
            classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
            result = classifier.classify(disk)

            assert result.safety_class == SAFETY_PROTECTED, f"fs={fs} should be protected"
            assert PROTECTED_ZFS_MEMBER in result.safety_reason, f"fs={fs}"

    def test_protected_zfs_member_on_partition(self):
        """A disk with a ZFS partition should be protected."""
        for fs in ("zfs_member", "zfs_pool"):
            disk = _make_base_disk(partitions=[
                _make_partition(fstype=fs)
            ])
            classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
            result = classifier.classify(disk)

            assert result.safety_class == SAFETY_PROTECTED, f"fs={fs} should be protected"
            assert PROTECTED_ZFS_MEMBER in result.safety_reason, f"fs={fs}"

    def test_multiple_reasons_combined(self):
        """Multiple protection reasons should be combined in safety_reason."""
        disk = _make_base_disk(
            ro=True,
            mountpoints=["/mnt/data"],
            fstype="ext4",
        )
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_PROTECTED
        reasons = result.safety_reason.split(",")
        assert PROTECTED_READ_ONLY in reasons
        assert PROTECTED_MOUNTED in reasons
        assert PROTECTED_HAS_DATA in reasons
        assert len(reasons) == 3

    def test_swap_not_protected_as_data(self):
        """A swap partition should not count as data filesystem."""
        disk = _make_base_disk(partitions=[
            _make_partition(fstype="swap")
        ])
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        # swap is not in DATA_FS_TYPES, so should be available
        assert result.safety_class == SAFETY_AVAILABLE
        assert result.safety_reason is None

    def test_empty_partition_table_not_protected(self):
        """A disk with empty partitions (no fstype) should be available."""
        disk = _make_base_disk(partitions=[
            _make_partition(fstype=None),
            _make_partition(fstype=""),
        ])
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)

        assert result.safety_class == SAFETY_AVAILABLE
        assert result.safety_reason is None

    def test_virtual_devices_filtered_by_discover(self):
        """Virtual devices like zram, loop, dm- are filtered in discover_disks, not classifier."""
        # This test documents the expected behavior - the classifier itself
        # doesn't filter virtual devices, discover_disks does.
        disk = _make_base_disk(name="zram0", path="/dev/zram0")
        classifier = DiskSafetyClassifier(system_disk_paths={"/dev/sda"})
        result = classifier.classify(disk)
        # zram would be classified as available if it reached the classifier
        # but discover_disks filters it out before classification
        assert result.safety_class == SAFETY_AVAILABLE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])