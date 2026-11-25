"""Shared builders for the storage classifier tests.

The blkid and wipefs outputs come from tests/fixtures/storage/*.json, captured
by scripts/capture-storage-fixtures.py from the real tools. lsblk cannot be run
on an image file, so its JSON is built here with the full real key set of
lsblk 2.41 (``-J -b -O``) and invented, clearly fake identities.
"""
from __future__ import annotations

import json
from pathlib import Path

from tinyagentos.storage_classifier import Probe, Policy

FIXTURES = Path(__file__).parent / "fixtures" / "storage"
GPT_LINUX = "0fc63daf-8483-4772-8e79-3d69d8477de4"

# every key a real `lsblk -J -b -O` node carries (lsblk 2.41.5)
_LSBLK_KEYS = (
    "alignment id-link id disc-aln dax disc-gran disk-seq disc-max disc-zero fsavail fsroots "
    "fssize fstype fsused fsuse% fsver group hctl hotplug kname label log-sec maj maj:min min "
    "min-io mode model mountpoint mountpoints mq name opt-io owner partflags partlabel partn "
    "parttype parttypename partuuid path phy-sec pkname pttype ptuuid ra rand rev rm ro rota "
    "rq-size sched serial size start state subsystems tran type uuid vendor wsame wwn zone-amax "
    "zone-app zone-nr zone-omax zone-sz zone-wgran zoned"
).split()


def node(name, type="disk", size=1_000_000_000_000, **kw):
    n = {k: None for k in _LSBLK_KEYS}
    n.update(
        name=name, kname=name, path=f"/dev/{name}", type=type, size=size, ro=False, rm=False,
        hotplug=False, rota=False, mountpoints=[None], fsroots=[None], subsystems="block:scsi:pci",
    )
    n.update(kw)
    return n


def disk(name="sdb", model="FAKEDRIVE 1000", serial="FAKE-SERIAL-0001", pttype=None, children=None, **kw):
    n = node(name, model=model, serial=serial, tran="sata", wwn="0x5000000000000001", pttype=pttype, **kw)
    if children:
        n["children"] = children
    return n


def part(disk_name, n=1, parttype=GPT_LINUX, **kw):
    nm = f"{disk_name}{n}"
    return node(nm, type="part", pkname=disk_name, partn=n, parttype=parttype, size=500_000_000_000, **kw)


def lsblk(*disks):
    return json.dumps({"blockdevices": list(disks)})


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _probe(entry):
    return Probe(True, entry["out"], "", entry["rc"])


def device_probes(name, head="zero", part_fixtures=None, disk_fixture=None):
    """Probes for one disk from a fixture; ``name`` is a case in fixtures/."""
    d = fixture(name)
    if "disk" in d:
        top, p1 = d["disk"], d["part1"]
    else:
        top, p1 = d, None
    return top, p1


SYSTEM_FINDMNT = json.dumps(
    {"filesystems": [{"target": "/", "source": "/dev/sda2", "fstype": "ext4", "options": "rw"}]}
)
SYSTEM_MOUNTS = "/dev/sda2 / ext4 rw 0 0\nproc /proc proc rw 0 0\n"


def system_disk():
    return disk("sda", model="FAKESYS 500", serial="SYS-0001", pttype="gpt",
                children=[part("sda", 2, parttype="x", fstype="ext4", mountpoints=["/"], mountpoint="/")])


def snapshot(lsblk_text, devices, findmnt=None, mounts=None, swaps="Filename Type Size Used Priority\n", by_id=None):
    """Assemble a classify() snapshot. ``devices`` is {path: {path: {blkid,wipefs,head}}}."""
    fm = findmnt if findmnt is not None else SYSTEM_FINDMNT
    mounts = SYSTEM_MOUNTS if mounts is None else mounts
    return {
        "lsblk": Probe(True, lsblk_text),
        "findmnt": Probe(True, fm),
        "mounts": Probe(True, mounts),
        "swaps": Probe(True, swaps),
        "by_id": Probe(True, json.dumps(by_id if by_id is not None else {})),
        "devices": devices,
    }


def probes_from(case, head="zero"):
    """{"blkid":, "wipefs":, "head":} Probes from a single-device fixture dict."""
    return {"blkid": _probe(case["blkid"]), "wipefs": _probe(case["wipefs"]), "head": Probe(True, head)}


def blank_snapshot(path="/dev/sdb", name="sdb", **disk_kw):
    """A healthy, fully available disk with no table, plus the system disk elsewhere."""
    nodes = [disk(name, **disk_kw)]
    devices = {path: {path: probes_from(fixture("blank"))}}
    return nodes, devices, {path: [f"ata-{name}-fake"]}


def make(nodes, devices, by_id, **kw):
    return snapshot(lsblk(*nodes), devices, by_id=by_id, **kw)


FAKE_POLICY = Policy()
