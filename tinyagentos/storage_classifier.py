"""Disk safety classifier for the taOS storage engine.

This module decides whether a block device may EVER be formatted. It is
standard library only, because the root helper (scripts/taos-storage-helper)
imports the very same file and re-runs it immediately before it writes.

THE RULE. A disk is ``available`` only when every one of these holds:

* it is a whole disk of a known real kind (SATA/SAS, virtio, NVMe, MMC), not a
  loop, md, device-mapper, zvol or network device;
* it has either no partition table, or a table whose partitions carry NO
  signature of any kind, are not mounted, and are an ordinary Linux data type;
* nothing is mounted from it, it is not swap, not read-only, not a member of
  any RAID/LVM/ZFS/bcache set, nothing is stacked on it, and it is not the disk
  that holds ``/`` or ``/boot``;
* its model and serial are known (the person has to type them back);
* it is not a disk taOS itself formatted before;
* every probe that was run succeeded and the probes agree with each other;
* the head of the disk (or of each partition) reads as zeros, which catches an
  encrypted container with no signature, such as a VeraCrypt volume.

Anything else, INCLUDING any probe that failed, produced unparseable output or
could not be run, is ``protected`` with the reason. The classifier never fails
open: an unknown is a reason to protect, never a reason to allow.

Shape: ``collect()`` runs the probes through an injected runner and returns a
plain snapshot of results; ``classify()`` is a pure function of that snapshot,
so every case is testable from captured tool output.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable

AVAILABLE = "available"
PROTECTED = "protected"

# Whole-disk kernel names that are real disks taOS may ever format. Everything
# else (loop, zd* zvols, md, dm-, nbd, sr, ram, ...) is protected by default.
REAL_DISK_RE = re.compile(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")

# Partition table signatures wipefs may report on a disk that is still blank.
TABLE_SIGNATURES = frozenset({"gpt", "PMBR", "dos"})

# The only partition types that count as "an ordinary empty data partition".
GPT_LINUX_FS = "0fc63daf-8483-4772-8e79-3d69d8477de4"
DOS_LINUX = "0x83"

# Mount targets that make a disk the system disk. If the source of one of
# these cannot be resolved to a block device we know, EVERY disk is protected.
SYSTEM_TARGETS = ("/", "/boot", "/boot/efi", "/efi", "/usr", "/var", "/home")

HEAD_BYTES = 1024 * 1024

Runner = Callable[[list], "tuple[int, str, str]"]


@dataclass(frozen=True)
class Probe:
    """Result of one probe. ``ok`` is False for any failure or surprise."""

    ok: bool
    out: str = ""
    err: str = ""
    rc: int | None = None

    @staticmethod
    def failed(err: str, rc: int | None = None, out: str = "") -> "Probe":
        return Probe(False, out, err, rc)


@dataclass
class Policy:
    """What counts as a candidate. The default is production; tests (and only
    in-process callers) may widen it. It is never read from argv or the
    environment."""

    real_disk_re: re.Pattern = REAL_DISK_RE
    allowed_types: frozenset = frozenset({"disk"})
    # (lsblk node) -> (model, serial). None in production: identity is what the
    # device reports. Only an in-process caller (the loop-device test) sets it.
    identity: Callable | None = None


@dataclass
class Result:
    disks: list = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------- collection


def _run(runner: Runner, argv: list, ok_rcs=(0,)) -> Probe:
    try:
        rc, out, err = runner(argv)
    except Exception as exc:  # the probe could not even run
        return Probe.failed(f"{argv[0]} could not be run: {exc}")
    if rc not in ok_rcs:
        return Probe.failed(f"{argv[0]} exited {rc}: {err.strip()[:200]}", rc, out)
    return Probe(True, out, err, rc)


def _read_text(reader: Callable[[str], str], path: str) -> Probe:
    try:
        return Probe(True, reader(path))
    except Exception as exc:
        return Probe.failed(f"cannot read {path}: {exc}")


def parse_lsblk(text: str) -> list:
    """Parse ``lsblk -J -b -O`` output into its list of top-level nodes.
    Raises ValueError for anything that is not the expected shape."""
    try:
        doc = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"lsblk output is not JSON: {exc}")
    nodes = doc.get("blockdevices") if isinstance(doc, dict) else None
    if not isinstance(nodes, list):
        raise ValueError("lsblk output has no blockdevices list")

    def check(n):
        if not isinstance(n, dict):
            raise ValueError("lsblk node is not an object")
        for key in ("name", "path", "type", "size", "ro"):
            if key not in n or n[key] is None:
                raise ValueError(f"lsblk node lacks '{key}'")
        _truthy(n["ro"])
        int(n["size"])
        for c in n.get("children") or []:
            check(c)

    for n in nodes:
        check(n)
    return nodes


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if v in (0, "0"):
        return False
    if v in (1, "1"):
        return True
    raise ValueError(f"unrecognised flag value {v!r}")


def collect(
    runner: Runner,
    read_text: Callable[[str], str],
    head_zero: Callable[[str, int, int | None], bool] | None,
    by_id: Callable[[], dict] | None,
) -> dict:
    """Run every probe and return the snapshot ``classify`` consumes.

    ``runner(argv) -> (rc, stdout, stderr)``; ``head_zero(path, offset, length)``
    returns True when those bytes are all zero (length None = the last
    HEAD_BYTES of the device); ``by_id()`` returns {device path: [by-id name]}.
    """
    snap: dict = {
        "lsblk": _run(runner, ["lsblk", "-J", "-b", "-O"]),
        "findmnt": _run(runner, ["findmnt", "-J"]),
        "mounts": _read_text(read_text, "/proc/mounts"),
        "swaps": _read_text(read_text, "/proc/swaps"),
        "devices": {},
    }
    if by_id is None:
        snap["by_id"] = Probe.failed("no by-id reader")
    else:
        try:
            snap["by_id"] = Probe(True, json.dumps(by_id()))
        except Exception as exc:
            snap["by_id"] = Probe.failed(f"cannot read /dev/disk/by-id: {exc}")
    if not snap["lsblk"].ok:
        return snap
    try:
        nodes = parse_lsblk(snap["lsblk"].out)
    except ValueError:
        return snap  # classify reports the parse failure
    for disk in nodes:
        entry = {}
        targets = [("disk", disk)] + [("part", c) for c in disk.get("children") or []]
        for kind, node in targets:
            path = node["path"]
            probes = {
                # blkid -p: 0 = found something, 2 = found nothing. 4 (usage/
                # other error) and 8 (ambivalent) are failures.
                "blkid": _run(runner, ["blkid", "-p", "-o", "export", path], ok_rcs=(0, 2)),
                "wipefs": _run(runner, ["wipefs", "-n", "-J", path]),
            }
            if head_zero is None:
                probes["head"] = Probe.failed("content check needs the root helper")
            else:
                try:
                    if kind == "part":
                        verdict = "zero" if head_zero(path, 0, HEAD_BYTES) else "nonzero"
                    elif node.get("children") or node.get("pttype"):
                        # The table area is judged by the other probes; the
                        # partitions' own heads are read above.
                        verdict = "not-needed"
                    else:
                        both = head_zero(path, 0, HEAD_BYTES) and head_zero(path, -1, HEAD_BYTES)
                        verdict = "zero" if both else "nonzero"
                    probes["head"] = Probe(True, verdict)
                except Exception as exc:
                    probes["head"] = Probe.failed(f"cannot read {path}: {exc}")
            entry[path] = probes
        snap["devices"][disk["path"]] = entry
    return snap


# ------------------------------------------------------------ classification


def _norm_source(src: str) -> str:
    # findmnt shows btrfs subvolumes as /dev/sda2[/@]
    return re.sub(r"\[.*\]$", "", src or "")


def _walk(node):
    yield node
    for c in node.get("children") or []:
        yield from _walk(c)


def _wipefs_types(probe_out: str) -> list:
    doc = json.loads(probe_out)
    sigs = doc["signatures"]
    if not isinstance(sigs, list):
        raise ValueError("signatures is not a list")
    return [str(s["type"]) for s in sigs]


def _blkid_tags(out: str) -> dict:
    tags = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            tags[k.strip()] = v
    return tags


def classify(snap: dict, policy: Policy | None = None, known_serials=(), known_wwns=()) -> Result:
    """Pure classification of a ``collect`` snapshot."""
    policy = policy or Policy()
    res = Result()

    if not snap["lsblk"].ok:
        res.error = "cannot list disks: " + snap["lsblk"].err
        return res
    try:
        nodes = parse_lsblk(snap["lsblk"].out)
    except ValueError as exc:
        res.error = f"cannot list disks: {exc}"
        return res

    # Index every lsblk node by path / realpath so mount sources resolve.
    index: dict = {}
    owner: dict = {}  # node path -> top-level disk path
    for top in nodes:
        for n in _walk(top):
            owner[n["path"]] = top["path"]
            index[n["path"]] = n
            for alias in (os.path.realpath(n["path"]), "/dev/" + n.get("kname", n["name"])):
                index.setdefault(alias, n)
    global_reasons: list = []
    mounted_disks: dict = {}   # disk path -> [reason]
    system_disks: set = set()

    def disk_of(source: str):
        node = index.get(_norm_source(source)) or index.get(os.path.realpath(_norm_source(source)))
        return owner.get(node["path"]) if node else None

    # --- findmnt
    mount_entries: list = []  # (source, target)
    if not snap["findmnt"].ok:
        global_reasons.append("could not read the mount table (findmnt): " + snap["findmnt"].err)
    else:
        try:
            doc = json.loads(snap["findmnt"].out)

            def flat(fs):
                for f in fs:
                    yield f
                    yield from flat(f.get("children") or [])

            for f in flat(doc["filesystems"]):
                mount_entries.append((f.get("source") or "", f["target"]))
        except Exception as exc:
            global_reasons.append(f"could not parse findmnt output: {exc}")
    # --- /proc/mounts
    if not snap["mounts"].ok:
        global_reasons.append("could not read /proc/mounts: " + snap["mounts"].err)
    else:
        for line in snap["mounts"].out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                mount_entries.append((parts[0], parts[1].replace("\\040", " ")))
    # --- swap
    swap_sources: list = []
    if not snap["swaps"].ok:
        global_reasons.append("could not read /proc/swaps: " + snap["swaps"].err)
    else:
        for line in snap["swaps"].out.splitlines()[1:]:
            parts = line.split()
            if parts and parts[0].startswith("/dev/"):
                swap_sources.append(parts[0])

    resolved_targets: set = set()
    for source, target in mount_entries:
        d = disk_of(source) if source.startswith("/dev/") else None
        if d:
            mounted_disks.setdefault(d, []).append(f"{source} is mounted at {target}")
            resolved_targets.add(target)
            if target in SYSTEM_TARGETS:
                system_disks.add(d)
    # An automount placeholder (source "systemd-1") may list a system target
    # before the real mount does; only a target NO entry resolves is a problem.
    for target in SYSTEM_TARGETS:
        if target in resolved_targets:
            continue
        srcs = sorted({s or "unknown" for s, t in mount_entries if t == target})
        if srcs:
            global_reasons.append(
                f"the device behind {target} ({', '.join(srcs)}) cannot be matched to a disk, "
                "so the system disk cannot be identified"
            )
    if "/" not in {t for _, t in mount_entries} and not global_reasons:
        global_reasons.append("no mount for / was found, so the system disk cannot be identified")
    swap_disks: dict = {}
    for src in swap_sources:
        d = disk_of(src)
        if d is None:
            global_reasons.append(f"swap on {src} cannot be matched to a disk")
        else:
            swap_disks.setdefault(d, []).append(src)

    # --- by-id
    by_id: dict = {}
    if snap["by_id"].ok:
        try:
            by_id = json.loads(snap["by_id"].out)
        except ValueError:
            global_reasons.append("could not parse the by-id map")
    else:
        global_reasons.append(snap["by_id"].err)

    for top in nodes:
        res.disks.append(
            _classify_disk(
                top, snap, policy, global_reasons, mounted_disks, swap_disks,
                system_disks, by_id, set(known_serials), set(known_wwns),
            )
        )
    return res


def _classify_disk(top, snap, policy, global_reasons, mounted, swaps, system, by_id, known_serials, known_wwns):
    reasons: list = list(global_reasons)
    path = top["path"]
    kname = top.get("kname") or top["name"]
    model = (top.get("model") or "").strip()
    serial = (top.get("serial") or "").strip()
    if policy.identity is not None:
        model, serial = policy.identity(top)
    parts = top.get("children") or []

    if top["type"] not in policy.allowed_types or not policy.real_disk_re.match(kname):
        reasons.append(f"not a plain physical disk (type {top['type']}, name {kname})")
    if _truthy(top["ro"]):
        reasons.append("read-only")
    if any(_truthy(p["ro"]) for p in parts):
        reasons.append("a partition is read-only")
    if not model:
        reasons.append("model is unknown, so the disk cannot be confirmed by name")
    if not serial:
        reasons.append("serial is unknown, so the disk cannot be confirmed by name")
    if serial and serial in known_serials:
        reasons.append("already a taOS storage area")
    if top.get("wwn") and top["wwn"] in known_wwns:
        reasons.append("already a taOS storage area")
    if path in system:
        reasons.append("system disk (holds the running system)")
    for m in mounted.get(path, []):
        reasons.append(m)
    for s in swaps.get(path, []):
        reasons.append(f"{s} is in use as swap")
    for n in _walk(top):
        mps = [m for m in (n.get("mountpoints") or []) if m]
        if n.get("mountpoint"):
            mps.append(n["mountpoint"])
        if mps:
            reasons.append(f"{n['path']} is mounted at {', '.join(sorted(set(mps)))}")
        if n is not top and n["type"] != "part":
            reasons.append(f"{n['type']} device {n['path']} is stacked on this disk")
        if n is not top and n["type"] == "part" and n.get("children"):
            reasons.append(f"partition {n['path']} has devices stacked on it")

    names = by_id.get(path) or []
    stable = sorted(n for n in names if "-part" not in n)
    stable = [n for n in stable if not n.startswith("wwn-")] + [n for n in stable if n.startswith("wwn-")]
    if not stable:
        reasons.append("no stable /dev/disk/by-id name")

    probes = snap["devices"].get(path)
    if probes is None:
        reasons.append("the disk was not probed")
        probes = {}

    # ----- the whole disk
    dprobes = probes.get(path, {})
    table = None  # what the disk-level probes say about a partition table
    sigs: list = []
    for key in ("blkid", "wipefs", "head"):
        if not dprobes.get(key) or not dprobes[key].ok:
            err = dprobes[key].err if dprobes.get(key) else "probe missing"
            reasons.append(f"{key} on {path} failed: {err}")
    if all(dprobes.get(k) and dprobes[k].ok for k in ("blkid", "wipefs")):
        tags = _blkid_tags(dprobes["blkid"].out)
        try:
            sigs = _wipefs_types(dprobes["wipefs"].out)
        except Exception as exc:
            reasons.append(f"wipefs output on {path} not understood: {exc}")
            sigs = []
        blk_table = tags.get("PTTYPE")
        other = [s for s in sigs if s not in TABLE_SIGNATURES]
        if other:
            reasons.append(f"has a {', '.join(other)} signature on the disk itself")
        if tags.get("TYPE") or tags.get("USAGE"):
            reasons.append(f"has a {tags.get('TYPE', 'recognised')} filesystem or member signature on the disk itself")
        lsblk_table = top.get("pttype")
        if lsblk_table and blk_table != lsblk_table:
            reasons.append(f"lsblk says table '{lsblk_table}' but blkid says '{blk_table}'")
        if blk_table and not any(s in TABLE_SIGNATURES for s in sigs):
            reasons.append("blkid sees a partition table that wipefs does not")
        if not blk_table and any(s in TABLE_SIGNATURES for s in sigs):
            reasons.append("wipefs sees a partition table that blkid does not")
        if parts and not blk_table:
            reasons.append("lsblk shows partitions but no partition table was found")
        table = blk_table
    if top.get("fstype") or top.get("label") or top.get("uuid"):
        reasons.append("lsblk reports a filesystem on the disk itself")
    if dprobes.get("head") and dprobes["head"].ok and dprobes["head"].out not in ("zero", "not-needed"):
        reasons.append("the disk has data that no signature explains (it could be an encrypted volume)")

    # ----- every partition
    for p in parts:
        pp = probes.get(p["path"], {})
        for key in ("blkid", "wipefs", "head"):
            if not pp.get(key) or not pp[key].ok:
                err = pp[key].err if pp.get(key) else "probe missing"
                reasons.append(f"{key} on {p['path']} failed: {err}")
        if p.get("fstype") or p.get("label") or p.get("uuid"):
            reasons.append(f"{p['path']} holds a {p.get('fstype') or 'recognised'} filesystem")
        if (p.get("partlabel") or "").lower().startswith("taos-"):
            reasons.append(f"{p['path']} is a taOS storage partition")
        ptype = (p.get("parttype") or "").lower()
        if ptype not in (GPT_LINUX_FS, DOS_LINUX):
            reasons.append(f"{p['path']} has partition type '{ptype or 'unknown'}', which may hold data or system files")
        if pp.get("blkid") and pp["blkid"].ok:
            # blkid -p on a partition node may print the partition's own entry
            # (PART_ENTRY_*, DEVNAME) even when it is empty; anything else is a
            # filesystem, member or table signature.
            extra = sorted(k for k in _blkid_tags(pp["blkid"].out) if k != "DEVNAME" and not k.startswith(("PART_ENTRY_", "ID_PART_ENTRY_")))
            if extra:
                reasons.append(f"{p['path']} carries a signature (blkid: {', '.join(extra)})")
        if pp.get("wipefs") and pp["wipefs"].ok:
            try:
                psigs = _wipefs_types(pp["wipefs"].out)
            except Exception as exc:
                reasons.append(f"wipefs output on {p['path']} not understood: {exc}")
                psigs = []
            if psigs:
                reasons.append(f"{p['path']} carries a {', '.join(psigs)} signature")
        if pp.get("head") and pp["head"].ok and pp["head"].out not in ("zero", "not-needed"):
            reasons.append(f"{p['path']} has data that no signature explains (it could be an encrypted volume)")

    seen = set()
    uniq = [r for r in reasons if not (r in seen or seen.add(r))]
    return {
        "path": path,
        "by_id": ("/dev/disk/by-id/" + stable[0]) if stable else None,
        "kname": kname,
        "model": model,
        "serial": serial,
        "wwn": top.get("wwn"),
        "vendor": (top.get("vendor") or "").strip(),
        "size": int(top["size"]),
        "transport": top.get("tran"),
        "rotational": top.get("rota"),
        "removable": top.get("rm"),
        "partition_table": table,
        "partitions": [
            {
                "path": p["path"],
                "size": int(p["size"]),
                "fstype": p.get("fstype"),
                "label": p.get("label"),
                "mountpoints": [m for m in (p.get("mountpoints") or []) if m],
            }
            for p in parts
        ],
        "class": PROTECTED if uniq else AVAILABLE,
        "reasons": uniq,
        "confirm": f"{model} {serial}" if model and serial else None,
    }
