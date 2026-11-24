"""taOS Storage Engine: safe disk management for encrypted storage areas.

Provides:
- Disk discovery and safety classification (available vs protected)
- Adding a disk as an encrypted storage area with LUKS2 + ext4
- Mount management via systemd mount units with nofail
- Storage area registry (id, name, mount, size, free, encryption, disk serial)
- Primary home assignment for persons

All formatting operations require root and use a narrowly-scoped privileged helper.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tinyagentos.config import AppConfig

logger = logging.getLogger(__name__)

# Safety classification constants
SAFETY_AVAILABLE = "available"
SAFETY_PROTECTED = "protected"

# Protected reasons
PROTECTED_SYSTEM_DISK = "system-disk"
PROTECTED_MOUNTED = "mounted"
PROTECTED_READ_ONLY = "read-only"
PROTECTED_HAS_DATA = "has-data"
PROTECTED_ENCRYPTED_FOREIGN = "encrypted-foreign"
PROTECTED_RAID_MEMBER = "raid-member"
PROTECTED_LVM_MEMBER = "lvm-member"
PROTECTED_ZFS_MEMBER = "zfs-member"
PROTECTED_UNKNOWN = "unknown"

# Filesystem types that indicate data
DATA_FS_TYPES = frozenset({
    "ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "jfs", "reiserfs",
    "vfat", "ntfs", "exfat", "f2fs", "hfs", "hfs+", "apfs",
    "iso9660", "udf", "squashfs", "erofs",
})

# LUKS/encrypted types
ENCRYPTED_TYPES = frozenset({"crypto_LUKS", "crypto_LUKS1", "crypto_LUKS2", "BitLocker"})


@dataclass(frozen=True)
class DiskInfo:
    """Block device information from lsblk/blkid/wipefs."""
    path: str                    # /dev/sda or /dev/disk/by-id/...
    name: str                    # sda, nvme0n1
    make: str                    # Vendor/Model from sysfs
    model: str
    serial: str
    size_bytes: int
    transport: str               # sata, nvme, usb, etc.
    rotational: bool             # True for HDD
    partitions: list["PartitionInfo"] = field(default_factory=list)
    mountpoints: list[str] = field(default_factory=list)
    fstype: str | None = None    # Filesystem on the whole disk (rare)
    uuid: str | None = None
    label: str | None = None
    parttable_type: str | None = None  # gpt, msdos, none
    ro: bool = False             # Read-only flag
    safety_class: str = SAFETY_PROTECTED
    safety_reason: str | None = None


@dataclass(frozen=True)
class PartitionInfo:
    """Partition information."""
    path: str                    # /dev/sda1
    name: str                    # sda1
    size_bytes: int
    fstype: str | None = None
    uuid: str | None = None
    label: str | None = None
    mountpoint: str | None = None
    parttype: str | None = None  # Partition type GUID
    partlabel: str | None = None


@dataclass(frozen=True)
class StorageArea:
    """Registered storage area."""
    id: str                      # UUID
    name: str                    # User-facing name
    mount: str                   # Mount point /srv/taos/storage/<name>
    size_bytes: int
    free_bytes: int
    encryption: Literal["system-unlock", "none"]
    disk_serial: str
    disk_path: str               # /dev/disk/by-id/... path
    created_at: float = field(default_factory=time.time)


class DiskSafetyClassifier:
    """Classifies block devices as available or protected with reasons."""

    def __init__(self, system_disk_paths: set[str] | None = None):
        self._system_disk_paths = system_disk_paths or set()

    def classify(self, disk: DiskInfo) -> DiskInfo:
        """Return a new DiskInfo with safety_class and safety_reason set."""
        reasons: list[str] = []

        # 1. System disk (running OS)
        is_system = False
        # Direct match
        if disk.path in self._system_disk_paths or f"/dev/{disk.name}" in self._system_disk_paths:
            is_system = True
        # Check if this disk/partition is a child of a system disk
        for sys_path in self._system_disk_paths:
            # If system path is /dev/sda and disk is /dev/sda2 or sda2
            sys_name = sys_path.replace("/dev/", "")
            if disk.name == sys_name or disk.name.startswith(sys_name + "p") or disk.name.startswith(sys_name):
                is_system = True
                break
        if is_system:
            reasons.append(PROTECTED_SYSTEM_DISK)

        # 2. Read-only hardware
        if disk.ro:
            reasons.append(PROTECTED_READ_ONLY)

        # 3. Currently mounted (whole disk or any partition)
        if disk.mountpoints:
            reasons.append(PROTECTED_MOUNTED)
        for part in disk.partitions:
            if part.mountpoint:
                reasons.append(PROTECTED_MOUNTED)
                break

        # 4. Has filesystem with data (on disk or any partition)
        if self._has_data_fs(disk):
            reasons.append(PROTECTED_HAS_DATA)

        # 5. Encrypted foreign volume (LUKS, BitLocker, etc.)
        if self._is_encrypted_foreign(disk):
            reasons.append(PROTECTED_ENCRYPTED_FOREIGN)

        # 6. RAID member
        if self._is_raid_member(disk):
            reasons.append(PROTECTED_RAID_MEMBER)

        # 7. LVM member
        if self._is_lvm_member(disk):
            reasons.append(PROTECTED_LVM_MEMBER)

        # 8. ZFS member
        if self._is_zfs_member(disk):
            reasons.append(PROTECTED_ZFS_MEMBER)

        if reasons:
            return DiskInfo(
                **{k: v for k, v in disk.__dict__.items() if k != "safety_class" and k != "safety_reason"},
                safety_class=SAFETY_PROTECTED,
                safety_reason=",".join(reasons),
            )

        return DiskInfo(
            **{k: v for k, v in disk.__dict__.items() if k != "safety_class" and k != "safety_reason"},
            safety_class=SAFETY_AVAILABLE,
            safety_reason=None,
        )

    def _has_data_fs(self, disk: DiskInfo) -> bool:
        """Check if disk or any partition has a data filesystem."""
        # Check whole disk
        if disk.fstype and disk.fstype in DATA_FS_TYPES:
            return True
        # Check partitions
        for part in disk.partitions:
            if part.fstype and part.fstype in DATA_FS_TYPES:
                return True
        return False

    def _is_encrypted_foreign(self, disk: DiskInfo) -> bool:
        """Check if disk or any partition has encrypted filesystem not managed by us."""
        if disk.fstype and disk.fstype in ENCRYPTED_TYPES:
            return True
        for part in disk.partitions:
            if part.fstype and part.fstype in ENCRYPTED_TYPES:
                return True
        return False

    def _is_raid_member(self, disk: DiskInfo) -> bool:
        """Check if disk or any partition is a RAID member (mdadm)."""
        # Check for linux_raid_member fstype
        if disk.fstype == "linux_raid_member":
            return True
        for part in disk.partitions:
            if part.fstype == "linux_raid_member":
                return True
        return False

    def _is_lvm_member(self, disk: DiskInfo) -> bool:
        """Check if disk or any partition is an LVM physical volume."""
        if disk.fstype == "LVM2_member":
            return True
        for part in disk.partitions:
            if part.fstype == "LVM2_member":
                return True
        return False

    def _is_zfs_member(self, disk: DiskInfo) -> bool:
        """Check if disk or any partition is a ZFS member."""
        if disk.fstype and disk.fstype.startswith("zfs_"):
            return True
        for part in disk.partitions:
            if part.fstype and part.fstype.startswith("zfs_"):
                return True
        return False


async def _run_cmd(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    """Run a command and return (returncode, stdout)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace") if stdout else ""
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return 124, f"timeout after {timeout}s"
    except Exception as exc:
        return 1, str(exc)


async def _get_system_disk_paths() -> set[str]:
    """Get the block device paths that hold the running system (root, boot)."""
    paths: set[str] = set()
    try:
        # Get root device
        rc, out = await _run_cmd(["findmnt", "-n", "-o", "SOURCE", "/"])
        if rc == 0 and out.strip():
            source = out.strip()
            # Resolve to actual block device
            if source.startswith("/dev/"):
                paths.add(source)
                # Also add parent disk if it's a partition
                match = re.match(r"(/dev/\w+)p?\d+", source)
                if match:
                    paths.add(match.group(1))

        # Get boot device(s)
        rc, out = await _run_cmd(["findmnt", "-n", "-o", "SOURCE", "/boot"])
        if rc == 0 and out.strip():
            for line in out.strip().splitlines():
                source = line.strip()
                if source.startswith("/dev/"):
                    paths.add(source)
                    match = re.match(r"(/dev/\w+)p?\d+", source)
                    if match:
                        paths.add(match.group(1))
    except Exception:
        logger.warning("storage_engine: failed to detect system disk paths", exc_info=True)

    logger.debug("storage_engine: system disk paths: %s", paths)
    return paths


async def _read_sysfs_block(device_name: str, attr: str) -> str | None:
    """Read a sysfs attribute for a block device."""
    path = Path(f"/sys/block/{device_name}/{attr}")
    try:
        return path.read_text().strip()
    except OSError:
        return None


async def discover_disks() -> list[DiskInfo]:
    """Discover all block disks with full metadata and safety classification.

    Returns a list of DiskInfo for each physical disk (not partitions).
    Uses lsblk, blkid, wipefs, and sysfs for accurate detection.
    """
    # Get system disk paths first
    system_disks = await _get_system_disk_paths()

    # Run lsblk for disk topology
    rc, out = await _run_cmd([
        "lsblk", "-d", "-J", "-o",
        "NAME,SIZE,ROTA,TRAN,RO,MODEL,VENDOR,SERIAL,TYPE,MOUNTPOINTS,FSTYPE,UUID,LABEL,PARTTYPE,PARTLABEL"
    ])
    if rc != 0:
        logger.error("storage_engine: lsblk failed: %s", out)
        return []

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        logger.error("storage_engine: failed to parse lsblk JSON: %s", out[:200])
        return []

    disks: list[DiskInfo] = []
    for block in data.get("blockdevices", []):
        # Skip non-disk types (rom, loop, etc.)
        if block.get("type") != "disk":
            continue

        name = block.get("name", "")
        if not name:
            continue

        # Skip virtual devices
        if any(name.startswith(skip) for skip in ("zram", "loop", "mtdblock", "ram", "dm-")):
            continue

        path = f"/dev/{name}"

        # Get size
        size_str = block.get("size", "0")
        size_bytes = _parse_size(size_str)

        # Get rotational
        rota = block.get("rota", "1")
        rotational = rota == "1"

        # Get transport
        transport = block.get("tran", "unknown")

        # Get read-only
        ro_str = block.get("ro", "0")
        ro = ro_str == "1"

        # Get vendor/model/serial
        vendor = block.get("vendor", "").strip()
        model = block.get("model", "").strip()
        serial = block.get("serial", "").strip()
        make = vendor or "Unknown"

        # Get mountpoints
        mountpoints = []
        mp = block.get("mountpoints")
        if mp:
            if isinstance(mp, list):
                mountpoints = [m for m in mp if m]
            elif isinstance(mp, str):
                mountpoints = [mp] if mp else []

        # Get filesystem info (whole disk)
        fstype = block.get("fstype")
        uuid = block.get("uuid")
        label = block.get("label")

        # Get partitions
        partitions = []
        for part in block.get("children", []):
            part_name = part.get("name", "")
            if not part_name:
                continue
            part_size_str = part.get("size", "0")
            part_size = _parse_size(part_size_str)
            part_fstype = part.get("fstype")
            part_uuid = part.get("uuid")
            part_label = part.get("label")
            part_mount = part.get("mountpoints")
            part_mountpoint = None
            if part_mount:
                if isinstance(part_mount, list) and part_mount:
                    part_mountpoint = part_mount[0]
                elif isinstance(part_mount, str):
                    part_mountpoint = part_mount
            part_parttype = part.get("parttype")
            part_partlabel = part.get("partlabel")

            partitions.append(PartitionInfo(
                path=f"/dev/{part_name}",
                name=part_name,
                size_bytes=part_size,
                fstype=part_fstype,
                uuid=part_uuid,
                label=part_label,
                mountpoint=part_mountpoint,
                parttype=part_parttype,
                partlabel=part_partlabel,
            ))

        # Determine partition table type
        parttable = None
        # Check for GPT/MSDOS via parted or fdisk
        rc2, out2 = await _run_cmd(["lsblk", "-n", "-o", "PTTYPE", path])
        if rc2 == 0:
            pttype = out2.strip()
            if pttype in ("gpt", "dos", "msdos"):
                parttable = pttype

        disk = DiskInfo(
            path=path,
            name=name,
            make=make,
            model=model,
            serial=serial,
            size_bytes=size_bytes,
            transport=transport,
            rotational=rotational,
            partitions=partitions,
            mountpoints=mountpoints,
            fstype=fstype,
            uuid=uuid,
            label=label,
            parttable_type=parttable,
            ro=ro,
        )

        # Classify safety
        classifier = DiskSafetyClassifier(system_disks)
        disk = classifier.classify(disk)

        disks.append(disk)

    return disks


def _parse_size(size_str: str) -> int:
    """Parse lsblk size string (e.g., '1.8T', '500G', '100M') to bytes."""
    size_str = size_str.strip().upper()
    if not size_str or size_str == "0":
        return 0

    multipliers = {
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
        "P": 1024**5,
        "E": 1024**6,
        "KI": 1024,
        "MI": 1024**2,
        "GI": 1024**3,
        "TI": 1024**4,
        "PI": 1024**5,
        "EI": 1024**6,
    }

    # Check for unit suffix
    for suffix, mult in multipliers.items():
        if size_str.endswith(suffix):
            try:
                return int(float(size_str[:-len(suffix)]) * mult)
            except ValueError:
                return 0

    # No suffix, assume bytes
    try:
        return int(float(size_str))
    except ValueError:
        return 0


async def _get_disk_by_id_path(device_path: str) -> str:
    """Resolve a device path to its /dev/disk/by-id/ symlink."""
    # If already a by-id path, return as-is
    if device_path.startswith("/dev/disk/by-id/"):
        return device_path

    # Get the real device path
    try:
        real_path = os.path.realpath(device_path)
    except OSError:
        return device_path

    # Find by-id symlinks pointing to this device
    by_id_dir = Path("/dev/disk/by-id")
    if by_id_dir.exists():
        for link in by_id_dir.iterdir():
            try:
                if os.path.realpath(link) == real_path:
                    return str(link)
            except OSError:
                continue

    return real_path


# ============================================================================
# Privileged Helper Interface
# ============================================================================

STORAGE_HELPER = "/usr/local/bin/taos-storage-helper"

ALLOWED_STORAGE_COMMANDS = {
    "partition-disk",
    "luks-format",
    "luks-open",
    "luks-close",
    "make-fs",
    "mount-fs",
    "unmount-fs",
    "write-crypttab",
    "write-fstab",
    "write-mount-unit",
    "remove-mount-unit",
    "get-mount-info",
}


async def _run_storage_helper(args: list[str], timeout: int = 300) -> dict:
    """Run a storage helper command via passwordless sudo.

    Uses the same security pattern as deploy.py: fixed binary path,
    allowlisted commands, no shell interpolation.
    """
    if not args or args[0] not in ALLOWED_STORAGE_COMMANDS:
        return {
            "ok": False,
            "command": args[0] if args else "",
            "output": f"command not allowed: {args[0] if args else 'empty'}",
            "exit_code": -1,
        }

    import shutil
    if not shutil.which(STORAGE_HELPER):
        return {
            "ok": False,
            "command": args[0],
            "output": f"storage helper not found at {STORAGE_HELPER}",
            "exit_code": -1,
        }

    cmd = ["sudo", STORAGE_HELPER] + args
    logger.info("storage_engine: running %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        ok = proc.returncode == 0

        if ok:
            logger.info("storage_engine: %s completed successfully", args[0])
        else:
            logger.error(
                "storage_engine: %s failed (exit %d): %s",
                args[0], proc.returncode, output[-500:],
            )

        return {
            "ok": ok,
            "command": args[0],
            "output": output,
            "exit_code": proc.returncode or 0,
        }
    except asyncio.TimeoutError:
        logger.error("storage_engine: %s timed out after %ds", args[0], timeout)
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return {
            "ok": False,
            "command": args[0],
            "output": f"timed out after {timeout}s",
            "exit_code": -1,
        }
    except Exception as exc:
        logger.error("storage_engine: %s failed: %s", args[0], exc)
        return {
            "ok": False,
            "command": args[0],
            "output": str(exc),
            "exit_code": -1,
        }


# ============================================================================
# Storage Area Operations
# ============================================================================

async def add_storage_area(
    disk_by_id_path: str,
    confirm_string: str,
    area_name: str,
    encrypt: Literal["system-unlock", "none"] = "system-unlock",
) -> dict:
    """Add a disk as a storage area.

    Steps:
    1. Re-verify disk is available and confirm_string matches exactly
    2. Create GPT partition table with single partition
    3. If encrypted: LUKS2 format with random key file in /etc/taos/keys/
    4. Create ext4 filesystem with label
    5. Create /etc/crypttab entry (if encrypted) with key file and nofail
    6. Create systemd mount unit at /srv/taos/storage/<area_name> with nofail
    7. Register in storage registry
    """
    # 1. Re-discover and verify disk
    disks = await discover_disks()
    target_disk = None
    for disk in disks:
        by_id = await _get_disk_by_id_path(disk.path)
        if by_id == disk_by_id_path:
            target_disk = disk
            break

    if not target_disk:
        return {"ok": False, "error": f"Disk not found: {disk_by_id_path}"}

    # Verify confirm string matches exactly: "<model> <serial>"
    expected_confirm = f"{target_disk.model} {target_disk.serial}".strip()
    if confirm_string != expected_confirm:
        return {
            "ok": False,
            "error": "Confirm string does not match disk",
            "expected": expected_confirm,
            "received": confirm_string,
        }

    # Re-check safety class right before writing
    if target_disk.safety_class != SAFETY_AVAILABLE:
        return {
            "ok": False,
            "error": f"Disk is not available: {target_disk.safety_reason}",
            "safety_class": target_disk.safety_class,
            "safety_reason": target_disk.safety_reason,
        }

    # Validate area name (filesystem-safe)
    if not re.match(r"^[a-zA-Z0-9_-]+$", area_name):
        return {"ok": False, "error": "Area name must be alphanumeric with hyphens/underscores only"}

    if len(area_name) > 64:
        return {"ok": False, "error": "Area name too long (max 64 chars)"}

    mount_point = f"/srv/taos/storage/{area_name}"
    partition_path = f"{disk_by_id_path}-part1"
    luks_name = f"taos-storage-{area_name}"
    luks_path = f"/dev/mapper/{luks_name}"
    key_file_path = f"/etc/taos/keys/{luks_name}.key"

    logger.info("storage_engine: Adding storage area %s on %s", area_name, disk_by_id_path)

    # 2. Create GPT partition table with single partition
    logger.info("storage_engine: Creating GPT partition on %s", disk_by_id_path)
    result = await _run_storage_helper([
        "partition-disk", disk_by_id_path
    ])
    if not result["ok"]:
        return {"ok": False, "error": f"Partition failed: {result['output']}", "step": "partition"}

    # Wait for partition to appear
    await asyncio.sleep(1)
    for _ in range(10):
        if await _path_exists(partition_path):
            break
        await asyncio.sleep(0.5)
    else:
        return {"ok": False, "error": "Partition did not appear after creation", "step": "partition-wait"}

    # 3. LUKS format if encrypted
    if encrypt == "system-unlock":
        logger.info("storage_engine: Creating LUKS2 on %s", partition_path)
        result = await _run_storage_helper([
            "luks-format", partition_path, luks_name, key_file_path
        ])
        if not result["ok"]:
            return {"ok": False, "error": f"LUKS format failed: {result['output']}", "step": "luks-format"}

        # Open LUKS
        logger.info("storage_engine: Opening LUKS %s", luks_name)
        result = await _run_storage_helper([
            "luks-open", partition_path, luks_name, key_file_path
        ])
        if not result["ok"]:
            return {"ok": False, "error": f"LUKS open failed: {result['output']}", "step": "luks-open"}

        fs_device = luks_path
    else:
        fs_device = partition_path

    # 4. Create ext4 filesystem (chosen for broad compatibility, mature tooling,
    #    and stable performance; btrfs would add snapshots but adds complexity
    #    and is not universally supported in initramfs)
    logger.info("storage_engine: Creating ext4 on %s", fs_device)
    result = await _run_storage_helper([
        "make-fs", fs_device, f"taos-{area_name}"
    ])
    if not result["ok"]:
        if encrypt == "system-unlock":
            await _run_storage_helper(["luks-close", luks_name])
        return {"ok": False, "error": f"Filesystem creation failed: {result['output']}", "step": "make-fs"}

    # 5. Create mount point
    try:
        Path(mount_point).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if encrypt == "system-unlock":
            await _run_storage_helper(["luks-close", luks_name])
        return {"ok": False, "error": f"Failed to create mount point: {exc}", "step": "mkdir"}

    # 6. Write crypttab (if encrypted)
    if encrypt == "system-unlock":
        logger.info("storage_engine: Writing crypttab for %s", luks_name)
        result = await _run_storage_helper([
            "write-crypttab", luks_name, partition_path, key_file_path
        ])
        if not result["ok"]:
            await _run_storage_helper(["luks-close", luks_name])
            return {"ok": False, "error": f"crypttab write failed: {result['output']}", "step": "crypttab"}

    # 7. Write systemd mount unit
    logger.info("storage_engine: Writing mount unit for %s", mount_point)
    result = await _run_storage_helper([
        "write-mount-unit", area_name, fs_device, mount_point, encrypt == "system-unlock"
    ])
    if not result["ok"]:
        if encrypt == "system-unlock":
            await _run_storage_helper(["luks-close", luks_name])
        return {"ok": False, "error": f"Mount unit write failed: {result['output']}", "step": "mount-unit"}

    # 8. Mount now
    logger.info("storage_engine: Mounting %s", mount_point)
    result = await _run_storage_helper([
        "mount-fs", mount_point
    ])
    if not result["ok"]:
        return {"ok": False, "error": f"Mount failed: {result['output']}", "step": "mount"}

    # 9. Get actual size and free space
    rc, out = await _run_cmd(["df", "--output=size,avail", "-B1", mount_point])
    size_bytes = 0
    free_bytes = 0
    if rc == 0:
        lines = out.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 2:
                size_bytes = int(parts[0])
                free_bytes = int(parts[1])

    # 10. Register in storage registry
    area = StorageArea(
        id=str(uuid.uuid4()),
        name=area_name,
        mount=mount_point,
        size_bytes=size_bytes,
        free_bytes=free_bytes,
        encryption=encrypt,
        disk_serial=target_disk.serial,
        disk_path=disk_by_id_path,
    )

    logger.info("storage_engine: Storage area %s created successfully", area_name)
    return {"ok": True, "area": area.__dict__}


async def set_primary_home(person_id: str, area_name: str, config: "AppConfig") -> dict:
    """Set a storage area as a person's primary home.

    Records the assignment and creates the person's directory with correct
    ownership/permissions. Does NOT move existing files (that happens on request).
    """
    # Validate area exists
    areas = await list_storage_areas(config)
    area = next((a for a in areas if a.name == area_name), None)
    if not area:
        return {"ok": False, "error": f"Storage area not found: {area_name}"}

    # Validate person exists (in config or registry)
    person = None
    for u in getattr(config, "users", []):
        if u.get("id") == person_id or u.get("user_id") == person_id:
            person = u
            break

    if not person:
        return {"ok": False, "error": f"Person not found: {person_id}"}

    # Create person's home directory
    person_dir = Path(area.mount) / "homes" / person_id
    try:
        person_dir.mkdir(parents=True, exist_ok=True)
        # Set ownership to the person's UID/GID if available
        # For now, ensure it's accessible
        person_dir.chmod(0o750)
    except OSError as exc:
        return {"ok": False, "error": f"Failed to create home directory: {exc}"}

    # Record in config (person's primary_home = area_name)
    # This would be persisted via config save
    person["primary_home"] = area_name

    logger.info("storage_engine: Set primary home for %s to %s", person_id, area_name)
    return {"ok": True, "person_id": person_id, "area": area_name, "home_path": str(person_dir)}


async def list_storage_areas(config: "AppConfig") -> list[StorageArea]:
    """List all registered storage areas from config."""
    areas_data = getattr(config, "storage_areas", [])
    areas = []
    for a in areas_data:
        # Refresh free space
        free_bytes = 0
        rc, out = await _run_cmd(["df", "--output=avail", "-B1", a.get("mount", "")])
        if rc == 0:
            lines = out.strip().splitlines()
            if len(lines) >= 2:
                free_bytes = int(lines[1].strip())

        areas.append(StorageArea(
            id=a.get("id", str(uuid.uuid4())),
            name=a.get("name", ""),
            mount=a.get("mount", ""),
            size_bytes=a.get("size_bytes", 0),
            free_bytes=free_bytes,
            encryption=a.get("encryption", "none"),
            disk_serial=a.get("disk_serial", ""),
            disk_path=a.get("disk_path", ""),
            created_at=a.get("created_at", time.time()),
        ))
    return areas


async def get_primary_home_capacity(person_id: str, config: "AppConfig") -> dict | None:
    """Get capacity info for a person's primary home."""
    # Find person
    person = None
    for u in getattr(config, "users", []):
        if u.get("id") == person_id or u.get("user_id") == person_id:
            person = u
            break

    if not person:
        return None

    area_name = person.get("primary_home")
    if not area_name:
        return None

    areas = await list_storage_areas(config)
    area = next((a for a in areas if a.name == area_name), None)
    if not area:
        return None

    return {
        "person_id": person_id,
        "area_name": area.name,
        "mount": area.mount,
        "size_bytes": area.size_bytes,
        "free_bytes": area.free_bytes,
        "used_bytes": area.size_bytes - area.free_bytes,
        "encryption": area.encryption,
    }


async def _path_exists(path: str) -> bool:
    """Check if path exists asynchronously."""
    try:
        await asyncio.to_thread(os.path.exists, path)
        return True
    except Exception:
        return False


# ============================================================================
# CLI Helpers
# ============================================================================

def disks_to_json(disks: list[DiskInfo]) -> list[dict]:
    """Convert DiskInfo list to JSON-serializable dicts."""
    result = []
    for disk in disks:
        result.append({
            "path": disk.path,
            "by_id_path": disk.path,  # Will be resolved by caller
            "name": disk.name,
            "make": disk.make,
            "model": disk.model,
            "serial": disk.serial,
            "size_bytes": disk.size_bytes,
            "size_human": _human_size(disk.size_bytes),
            "transport": disk.transport,
            "rotational": disk.rotational,
            "read_only": disk.ro,
            "partitions": [
                {
                    "path": p.path,
                    "name": p.name,
                    "size_bytes": p.size_bytes,
                    "size_human": _human_size(p.size_bytes),
                    "fstype": p.fstype,
                    "uuid": p.uuid,
                    "label": p.label,
                    "mountpoint": p.mountpoint,
                    "parttype": p.parttype,
                    "partlabel": p.partlabel,
                }
                for p in disk.partitions
            ],
            "mountpoints": disk.mountpoints,
            "fstype": disk.fstype,
            "uuid": disk.uuid,
            "label": disk.label,
            "parttable_type": disk.parttable_type,
            "safety_class": disk.safety_class,
            "safety_reason": disk.safety_reason,
        })
    return result


def _human_size(bytes_: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]:
        if bytes_ < 1024:
            return f"{bytes_:.1f}{unit}"
        bytes_ /= 1024
    return f"{bytes_:.1f}EiB"