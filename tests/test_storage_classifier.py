"""Safety classifier: every protected case, and every probe failure, from captured tool output."""
from __future__ import annotations

import dataclasses
import json

import pytest

from storage_test_support import (
    GPT_LINUX, disk, device_probes, fixture, lsblk, make, node, part, probes_from,
    snapshot, system_disk,
)
from tinyagentos import storage_classifier as sc
from tinyagentos.storage_classifier import Probe, classify

BY_ID = {"/dev/sdb": ["ata-FAKEDRIVE_1000_FAKE-SERIAL-0001"], "/dev/sda": ["ata-FAKESYS_500_SYS-0001"]}


def build(case, *, disk_kw=None, parts=None, part_case=None, head="zero", part_head="zero", **snap_kw):
    """One candidate disk (sdb) with probe output from fixtures/<case>.json, beside a system disk."""
    disk_kw = dict(disk_kw or {})
    d = fixture(case)
    top = d["disk"] if "disk" in d else d
    children = parts if parts is not None else []
    if children:
        disk_kw.setdefault("pttype", "gpt")
    sdb = disk("sdb", children=children or None, **disk_kw)
    devices = {
        "/dev/sdb": {"/dev/sdb": probes_from(top, head)},
        "/dev/sda": {"/dev/sda": probes_from(fixture("gpt_emptypart")["disk"], "not-needed"),
                     "/dev/sda2": probes_from(fixture("ext4"), "zero")},
    }
    if children:
        pc = fixture(part_case or case)
        pc = pc["part1"] if "part1" in pc else pc
        devices["/dev/sdb"]["/dev/sdb1"] = probes_from(pc, part_head)
    snap_kw.setdefault("by_id", BY_ID)
    return snapshot(lsblk(system_disk(), sdb), devices, **snap_kw)


def verdict(snap, **kw):
    res = classify(snap, **kw)
    assert res.error is None, res.error
    return {d["path"]: d for d in res.disks}["/dev/sdb"]


def assert_protected(snap, needle, **kw):
    d = verdict(snap, **kw)
    assert d["class"] == "protected", d
    assert any(needle in r for r in d["reasons"]), (needle, d["reasons"])


# ------------------------------------------------------------------ available


def test_blank_disk_without_table_is_available():
    d = verdict(build("blank"))
    assert d["class"] == "available", d["reasons"]
    assert d["confirm"] == "FAKEDRIVE 1000 FAKE-SERIAL-0001"
    assert d["by_id"] == "/dev/disk/by-id/ata-FAKEDRIVE_1000_FAKE-SERIAL-0001"


def test_table_with_no_partitions_is_available():
    d = verdict(build("gpt_no_parts", disk_kw={"pttype": "gpt"}))
    assert d["class"] == "available", d["reasons"]


@pytest.mark.parametrize("case,pttype,ptype", [("gpt_emptypart", "gpt", GPT_LINUX), ("dos_emptypart", "dos", "0x83")])
def test_table_with_only_empty_partitions_is_available(case, pttype, ptype):
    p = part("sdb", 1, parttype=ptype)
    d = verdict(build(case, parts=[p], disk_kw={"pttype": pttype}))
    assert d["class"] == "available", d["reasons"]


def test_nvme_disk_is_a_candidate():
    snap = build("blank", disk_kw={})
    nodes = json.loads(snap["lsblk"].out)["blockdevices"]
    n = disk("nvme1n1", serial="NV-1")
    n["path"] = "/dev/nvme1n1"
    snap["lsblk"] = Probe(True, lsblk(nodes[0], n))
    snap["devices"]["/dev/nvme1n1"] = {"/dev/nvme1n1": probes_from(fixture("blank"))}
    snap["by_id"] = Probe(True, json.dumps({**BY_ID, "/dev/nvme1n1": ["nvme-FAKE_NV-1"]}))
    res = {d["path"]: d for d in classify(snap).disks}
    assert res["/dev/nvme1n1"]["class"] == "available", res["/dev/nvme1n1"]["reasons"]


# ------------------------------------------------------- signatures on the disk


@pytest.mark.parametrize("case,needle", [
    ("ext4", "ext4"), ("ntfs", "ntfs"), ("swap", "swap"), ("vfat", "vfat"), ("exfat", "exfat"), ("luks", "crypto_LUKS"),
])
def test_filesystem_or_member_signature_on_the_disk_protects(case, needle):
    assert_protected(build(case), needle)


def test_signature_only_wipefs_sees_protects():
    # the LVM capture: wipefs finds LVM2_member, blkid -p finds nothing
    assert_protected(build("lvm"), "LVM2_member")


@pytest.mark.parametrize("case,pttype,ptype", [("gpt_ext4", "gpt", GPT_LINUX), ("dos_ext4", "dos", "0x83")])
def test_partition_with_a_filesystem_protects(case, pttype, ptype):
    p = part("sdb", 1, parttype=ptype, fstype="ext4", uuid="x", label="d")
    assert_protected(build(case, parts=[p], disk_kw={"pttype": pttype}), "sdb1")


def test_partition_signature_found_only_by_probes_protects():
    # lsblk (udev cache) is stale and shows nothing, the probes see ext4
    p = part("sdb", 1)
    assert_protected(build("gpt_ext4", parts=[p]), "carries")


# ------------------------------------------------------------ mounted / in use


def test_mounted_partition_protects():
    p = part("sdb", 1, mountpoints=["/mnt/x"], mountpoint="/mnt/x")
    assert_protected(build("gpt_emptypart", parts=[p]), "mounted at /mnt/x")


def test_mount_only_the_mount_table_knows_protects():
    snap = build("gpt_emptypart", parts=[part("sdb", 1)],
                 mounts="/dev/sda2 / ext4 rw 0 0\n/dev/sdb1 /srv/x ext4 rw 0 0\n")
    assert_protected(snap, "/dev/sdb1 is mounted at /srv/x")


def test_mount_only_findmnt_knows_protects():
    fm = json.dumps({"filesystems": [
        {"target": "/", "source": "/dev/sda2"},
        {"target": "/data", "source": "/dev/sdb1[/@data]"}]})
    assert_protected(build("gpt_emptypart", parts=[part("sdb", 1)], findmnt=fm), "mounted at /data")


def test_swap_protects():
    snap = build("gpt_emptypart", parts=[part("sdb", 1)],
                 swaps="Filename Type Size Used Priority\n/dev/sdb1 partition 100 0 -2\n")
    assert_protected(snap, "swap")


def test_read_only_disk_and_partition_protect():
    assert_protected(build("blank", disk_kw={"ro": True}), "read-only")
    assert_protected(build("gpt_emptypart", parts=[part("sdb", 1, ro=True)]), "read-only")


def test_system_disk_is_protected():
    snap = build("blank")
    res = {d["path"]: d for d in classify(snap).disks}
    sda = res["/dev/sda"]
    assert sda["class"] == "protected"
    assert any("system disk" in r for r in sda["reasons"])


def test_system_disk_on_dm_stack_resolved_through_children():
    # root on a dm-crypt volume stacked on sdb: the disk holding / is sdb
    crypt = node("cryptroot", type="crypt", pkname="sdb1", mountpoints=["/"], mountpoint="/")
    p = part("sdb", 1)
    p["children"] = [crypt]
    snap = build("gpt_emptypart", parts=[p],
                 findmnt=json.dumps({"filesystems": [{"target": "/", "source": "/dev/mapper/cryptroot"}]}),
                 mounts="/dev/mapper/cryptroot / ext4 rw 0 0\n")
    # the dm node is only resolvable if lsblk lists it; here it does not
    assert_protected(snap, "stacked")


def test_unresolvable_root_source_protects_every_disk():
    fm = json.dumps({"filesystems": [{"target": "/", "source": "tank/subvol-101-disk-0"}]})
    res = classify(build("blank", findmnt=fm, mounts="tank/subvol-101-disk-0 / zfs rw 0 0\n"))
    assert res.disks and all(d["class"] == "protected" for d in res.disks)
    assert all(any("system disk cannot be identified" in r for r in d["reasons"]) for d in res.disks)


def test_missing_root_mount_protects_every_disk():
    res = classify(build("blank", findmnt=json.dumps({"filesystems": []}), mounts="proc /proc proc rw 0 0\n"))
    assert all(d["class"] == "protected" for d in res.disks)


# ----------------------------------------------------------------- membership


@pytest.mark.parametrize("ctype", ["lvm", "raid1", "crypt", "md", "dm", "zfs"])
def test_anything_stacked_on_the_disk_protects(ctype):
    p = part("sdb", 1)
    p["children"] = [node("x0", type=ctype, pkname="sdb1")]
    assert_protected(build("gpt_emptypart", parts=[p]), "stacked")


def test_device_stacked_directly_on_disk_protects():
    sdb = build("blank")
    nodes = json.loads(sdb["lsblk"].out)["blockdevices"]
    nodes[1]["children"] = [node("md0", type="raid1", pkname="sdb")]
    sdb["lsblk"] = Probe(True, json.dumps({"blockdevices": nodes}))
    assert_protected(sdb, "stacked")


@pytest.mark.parametrize("name,typ", [("loop3", "loop"), ("zd0", "disk"), ("md0", "raid1"), ("nbd0", "disk"), ("dm-0", "lvm")])
def test_non_physical_devices_are_never_available(name, typ):
    snap = build("blank")
    nodes = json.loads(snap["lsblk"].out)["blockdevices"]
    n = disk(name, serial="X-1")
    n["type"] = typ
    snap["lsblk"] = Probe(True, lsblk(nodes[0], n))
    snap["devices"][f"/dev/{name}"] = {f"/dev/{name}": probes_from(fixture("blank"))}
    snap["by_id"] = Probe(True, json.dumps({**BY_ID, f"/dev/{name}": ["x"]}))
    res = {d["path"]: d for d in classify(snap).disks}
    assert res[f"/dev/{name}"]["class"] == "protected"


def test_loop_device_only_with_an_explicit_in_process_policy():
    snap = build("blank")
    nodes = json.loads(snap["lsblk"].out)["blockdevices"]
    n = disk("loop7", serial="L-1")
    n["type"] = "loop"
    snap["lsblk"] = Probe(True, lsblk(nodes[0], n))
    snap["devices"]["/dev/loop7"] = {"/dev/loop7": probes_from(fixture("blank"))}
    snap["by_id"] = Probe(True, json.dumps({**BY_ID, "/dev/loop7": ["loop-L-1"]}))
    policy = sc.Policy(real_disk_re=__import__("re").compile(r"^loop\d+$"), allowed_types=frozenset({"loop"}))
    res = {d["path"]: d for d in classify(snap, policy).disks}
    assert res["/dev/loop7"]["class"] == "available", res["/dev/loop7"]["reasons"]


# ------------------------------------------------------------------- identity


def test_missing_model_or_serial_protects():
    assert_protected(build("blank", disk_kw={"model": None}), "model is unknown")
    assert_protected(build("blank", disk_kw={"serial": ""}), "serial is unknown")


def test_known_taos_disk_protects_even_though_it_reads_empty():
    assert_protected(build("blank"), "already a taOS storage area", known_serials={"FAKE-SERIAL-0001"})


def test_taos_partition_label_protects():
    p = part("sdb", 1, partlabel="taos-photos")
    assert_protected(build("gpt_emptypart", parts=[p]), "taOS storage partition")


@pytest.mark.parametrize("ptype", ["c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
                                   "e3c9e316-0b5c-4db8-817d-f92df00215ae", "0x07", "0xee", None])
def test_unusual_partition_types_protect(ptype):
    p = part("sdb", 1, parttype=ptype)
    assert_protected(build("gpt_emptypart", parts=[p]), "partition type")


def test_data_without_a_signature_protects_disk_and_partition():
    assert_protected(build("blank", head="nonzero"), "no signature explains")
    assert_protected(build("gpt_emptypart", parts=[part("sdb", 1)], part_head="nonzero"), "no signature explains")


def test_no_stable_by_id_name_protects():
    assert_protected(build("blank", by_id={}), "by-id")


# ------------------------------------------------------- every probe failure


def _fail(snap, where, probe):
    if where in ("lsblk", "findmnt", "mounts", "swaps", "by_id"):
        snap[where] = probe
    else:
        dev, key = where
        snap["devices"]["/dev/sdb"][dev][key] = probe
    return snap


@pytest.mark.parametrize("key", ["blkid", "wipefs", "head"])
@pytest.mark.parametrize("target", ["/dev/sdb", "/dev/sdb1"])
def test_failed_device_probe_protects(key, target):
    snap = build("gpt_emptypart", parts=[part("sdb", 1)])
    _fail(snap, (target, key), Probe.failed(f"{key} boom", rc=1))
    assert_protected(snap, f"{key} on {target} failed")


def test_missing_device_probe_protects():
    snap = build("gpt_emptypart", parts=[part("sdb", 1)])
    del snap["devices"]["/dev/sdb"]["/dev/sdb1"]
    assert_protected(snap, "probe missing")
    snap = build("blank")
    snap["devices"].pop("/dev/sdb")
    assert_protected(snap, "not probed")


@pytest.mark.parametrize("where", ["findmnt", "mounts", "swaps", "by_id"])
def test_failed_global_probe_protects_every_disk(where):
    snap = _fail(build("blank"), where, Probe.failed("boom", rc=1))
    res = classify(snap)
    assert res.disks and all(d["class"] == "protected" for d in res.disks), [d["reasons"] for d in res.disks]


def test_failed_lsblk_lists_nothing_and_says_why():
    res = classify(_fail(build("blank"), "lsblk", Probe.failed("boom", rc=1)))
    assert res.disks == [] and "cannot list disks" in res.error


@pytest.mark.parametrize("bad", ["", "not json", "[]", "{}", '{"blockdevices": "x"}', '{"blockdevices": [5]}',
                                 '{"blockdevices": [{"name": "sdb"}]}'])
def test_unrecognised_lsblk_output_lists_nothing(bad):
    snap = build("blank")
    snap["lsblk"] = Probe(True, bad)
    res = classify(snap)
    assert res.disks == [] and res.error


def test_unknown_ro_flag_is_an_error_not_a_pass():
    snap = build("blank")
    nodes = json.loads(snap["lsblk"].out)["blockdevices"]
    nodes[1]["ro"] = "maybe"
    snap["lsblk"] = Probe(True, json.dumps({"blockdevices": nodes}))
    assert classify(snap).error


@pytest.mark.parametrize("bad", ["", "garbage", "{}", '{"signatures": "x"}', '{"signatures": [{"nope": 1}]}'])
def test_unparseable_wipefs_protects(bad):
    snap = build("blank")
    snap["devices"]["/dev/sdb"]["/dev/sdb"]["wipefs"] = Probe(True, bad)
    d = verdict(snap)
    assert d["class"] == "protected", d


def test_blkid_and_wipefs_disagreement_protects():
    snap = build("blank")
    snap["devices"]["/dev/sdb"]["/dev/sdb"]["blkid"] = probes_from(fixture("gpt_emptypart")["disk"])["blkid"]
    assert_protected(snap, "does not")


def test_lsblk_and_blkid_table_disagreement_protects():
    assert_protected(build("blank", disk_kw={"pttype": "dos"}), "lsblk says table")


def test_lsblk_partitions_without_any_table_protects():
    assert_protected(build("blank", parts=[part("sdb", 1)], part_case="blank", disk_kw={"pttype": None}), "no partition table")


def test_unreadable_head_without_helper_means_protected():
    # collect() with no head reader (an unprivileged caller) can never yield available
    def runner(argv):
        raise FileNotFoundError(argv[0])

    snap = sc.collect(runner, lambda p: (_ for _ in ()).throw(OSError("no")), None, None)
    res = classify(snap)
    assert res.error and res.disks == []


def test_blkid_exit_codes_other_than_0_and_2_are_failures():
    seen = {}

    def runner(argv):
        seen[argv[0]] = seen.get(argv[0], 0) + 1
        if argv[0] == "lsblk":
            return 0, lsblk(disk("sdb")), ""
        if argv[0] == "blkid":
            return 8, "", "ambivalent"
        if argv[0] == "wipefs":
            return 0, '{"signatures": []}', ""
        return 0, json.dumps({"filesystems": []}), ""

    snap = sc.collect(runner, lambda p: "", lambda p, o, n: True, lambda: {})
    assert snap["devices"]["/dev/sdb"]["/dev/sdb"]["blkid"].ok is False


def test_collect_uses_argument_lists_and_the_real_flags():
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[0] == "lsblk":
            return 0, lsblk(disk("sdb")), ""
        return 0, '{"signatures": [], "filesystems": []}', ""

    sc.collect(runner, lambda p: "", lambda p, o, n: True, lambda: {})
    assert ["lsblk", "-J", "-b", "-O"] in calls
    assert ["wipefs", "-n", "-J", "/dev/sdb"] in calls
    assert ["blkid", "-p", "-o", "export", "/dev/sdb"] in calls
    assert all(isinstance(a, list) for a in calls)


def test_collect_then_classify_end_to_end_with_a_fake_machine():
    """collect() -> classify() with a runner that answers like a machine with one blank disk."""
    sdb = disk("sdb")
    sda = system_disk()
    out = {
        ("lsblk",): (0, lsblk(sda, sdb), ""),
        ("findmnt",): (0, json.dumps({"filesystems": [{"target": "/", "source": "/dev/sda2"}]}), ""),
    }

    def runner(argv):
        if argv[0] in ("lsblk", "findmnt"):
            return out[(argv[0],)]
        if argv[0] == "blkid":
            return (2, "", "") if "sdb" in argv[-1] else (0, "TYPE=ext4\n", "")
        return 0, '{"signatures": []}' if "sdb" in argv[-1] else '{"signatures": [{"type": "ext4"}]}', ""

    snap = sc.collect(runner, lambda p: "/dev/sda2 / ext4 rw 0 0\n" if p.endswith("mounts") else "Filename Type Size Used Priority\n",
                      lambda p, o, n: True, lambda: {"/dev/sdb": ["ata-X"], "/dev/sda": ["ata-S"]})
    res = {d["path"]: d for d in classify(snap).disks}
    assert res["/dev/sdb"]["class"] == "available", res["/dev/sdb"]["reasons"]
    assert res["/dev/sda"]["class"] == "protected"
