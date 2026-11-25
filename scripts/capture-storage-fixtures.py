#!/usr/bin/env python3
"""Regenerate tests/fixtures/storage/*.json from real tools.

Builds small image FILES in a scratch directory (never a block device), runs the
real ``blkid -p -o export`` and ``wipefs -n -J`` on them, and records rc and
output. Needs util-linux and e2fsprogs/ntfs-3g/dosfstools/exfatprogs for the
filesystems; a case whose tool is missing is skipped and reported.

The LUKS and LVM cases cannot be made without cryptsetup and lvm2, so their headers are written by hand (just the magic
and the few fields the probes check) and then read by the REAL blkid/wipefs.
Those fixtures say so in their ``synthetic_image`` field. Re-capture them on a
host that has the tools whenever possible.

Usage: capture-storage-fixtures.py SCRATCH_DIR OUT_DIR
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import uuid

MIB = 1024 * 1024
os.environ["PATH"] += ":/usr/sbin:/sbin"


def sh(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def blank(path, mib=64):
    with open(path, "wb") as f:
        f.truncate(mib * MIB)


def probe(path, synthetic=False):
    b = sh(["blkid", "-p", "-o", "export", path])
    w = sh(["wipefs", "-n", "-J", path])
    return {
        "blkid": {"rc": b.returncode, "out": b.stdout},
        "wipefs": {"rc": w.returncode, "out": w.stdout},
        "synthetic_image": synthetic,
    }


def mkfs(tool, args, path):
    if shutil.which(tool) is None:
        return False
    return sh([tool, *args, path]).returncode == 0


def put(path, offset, data):
    with open(path, "r+b") as f:
        f.seek(offset)
        f.write(data)


def main(scratch, out):
    os.makedirs(scratch, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    cases = {}
    skipped = []

    def case(name, builder, synthetic=False):
        p = os.path.join(scratch, name + ".img")
        blank(p)
        if builder(p) is False:
            skipped.append(name)
            return
        os.chdir(scratch)  # stable device name in the output: just "<name>.img"
        cases[name] = probe(name + ".img", synthetic)

    case("blank", lambda p: None)
    case("ext4", lambda p: mkfs("mkfs.ext4", ["-q", "-L", "data"], p))
    case("ntfs", lambda p: mkfs("mkfs.ntfs", ["-q", "-F", "-L", "win"], p))
    case("swap", lambda p: mkfs("mkswap", ["-q"], p))
    case("vfat", lambda p: mkfs("mkfs.fat", ["-F", "32"], p))
    case("exfat", lambda p: mkfs("mkfs.exfat", ["-L", "x"], p))

    def luks(p):  # LUKS2 magic + version
        put(p, 0, b"LUKS\xba\xbe" + struct.pack(">H", 2))
        put(p, 168, str(uuid.uuid4()).encode())

    def lvm(p):  # LVM2 label in sector 1
        put(p, 512, b"LABELONE" + struct.pack("<QII", 1, 0, 32) + b"LVM2 001")

    case("luks", luks, synthetic=True)
    case("lvm", lvm, synthetic=True)

    # partitioned disks: whole-disk probe plus a probe of each partition's bytes
    def partitioned(label, ptype, fs_tool, fs_args):
        name = f"{label}_{'ext4' if fs_tool else 'emptypart'}"
        p = os.path.join(scratch, name + ".img")
        blank(p, 128)
        script = f"label: {label}\nstart=2048, size=100000, type={ptype}\n"
        if sh(["sfdisk", "-q", p], input=script).returncode != 0:
            skipped.append(name)
            return
        part = os.path.join(scratch, name + "-part1.img")
        with open(p, "rb") as f:
            f.seek(2048 * 512)
            data = f.read(100000 * 512)
        with open(part, "wb") as f:
            f.write(data)
        if fs_tool and not mkfs(fs_tool, fs_args, part):
            skipped.append(name)
            return
        os.chdir(scratch)
        cases[name] = {
            "disk": probe(name + ".img"),
            "part1": probe(name + "-part1.img"),
        }

    partitioned("gpt", "0fc63daf-8483-4772-8e79-3d69d8477de4", None, [])
    partitioned("gpt", "0fc63daf-8483-4772-8e79-3d69d8477de4", "mkfs.ext4", ["-q", "-L", "d"])
    partitioned("dos", "83", None, [])
    partitioned("dos", "83", "mkfs.ext4", ["-q"])

    # a blank disk that still carries a bare table with no partitions
    p = os.path.join(scratch, "gpt_no_parts.img")
    blank(p, 128)
    sh(["sfdisk", "-q", p], input="label: gpt\n")
    os.chdir(scratch)
    cases["gpt_no_parts"] = probe("gpt_no_parts.img")

    for name, doc in cases.items():
        with open(os.path.join(out, name + ".json"), "w") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
            f.write("\n")
    print("captured:", ", ".join(sorted(cases)))
    if skipped:
        print("SKIPPED (tool missing or failed):", ", ".join(skipped))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]))
