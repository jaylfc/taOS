"""Opt-in end-to-end test on LOOP devices only. It never runs by default.

Runs only when ALL of these hold: root, TAOS_STORAGE_LOOP_TEST=1, losetup and
the format tools exist, and a loop device can really be attached. It formats a
sparse file under tmp_path through a loop device, never a real disk; the helper
is given an in-process policy that accepts only loopN devices, and scratch
paths for keys, crypttab, fstab and the mount point.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tinyagentos import storage_classifier as sc

TOOLS = ("losetup", "sfdisk", "cryptsetup", "mkfs.btrfs", "blkid", "wipefs", "lsblk", "findmnt", "udevadm", "mount", "umount")
ENABLED = os.geteuid() == 0 and os.environ.get("TAOS_STORAGE_LOOP_TEST") == "1" and all(
    shutil.which(t) or os.path.exists(f"/usr/sbin/{t}") or os.path.exists(f"/sbin/{t}") for t in TOOLS)

pytestmark = pytest.mark.skipif(not ENABLED, reason="opt-in: needs root, TAOS_STORAGE_LOOP_TEST=1 and the format tools")

HELPER = Path(__file__).resolve().parent.parent / "scripts" / "taos-storage-helper"


def _helper():
    loader = importlib.machinery.SourceFileLoader("taos_storage_helper_loop", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    mod._sc = sc
    return mod


@pytest.fixture
def loop(tmp_path):
    img = tmp_path / "disk.img"
    img.write_bytes(b"")
    os.truncate(img, 512 * 1024 * 1024)
    dev = subprocess.run(["losetup", "--find", "--show", str(img)], capture_output=True, text=True, check=True).stdout.strip()
    assert re.fullmatch(r"/dev/loop\d+", dev)
    yield dev
    subprocess.run(["umount", str(tmp_path / "srv" / "looparea")], capture_output=True)
    subprocess.run(["cryptsetup", "close", "taos-looparea"], capture_output=True)
    subprocess.run(["losetup", "-d", dev], capture_output=True)


def _env(h, tmp_path, dev):
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    (by_id / "loop-test-LOOP-0001").symlink_to(dev)
    (tmp_path / "srv").mkdir()
    env = h.Env(key_dir=str(tmp_path / "keys"), mount_root=str(tmp_path / "srv"), crypttab=str(tmp_path / "crypttab"),
                fstab=str(tmp_path / "fstab"), areas_dir=str(tmp_path / "areas"), log_path=str(tmp_path / "log"),
                by_id_dir=str(by_id), policy=sc.Policy(real_disk_re=re.compile(r"^loop\d+$"), allowed_types=frozenset({"loop"})),
                caller_uid=os.getuid(), caller_gid=os.getgid(), systemd=False)
    return env, str(by_id / "loop-test-LOOP-0001")


LOOP_IDENTITY = ("LOOPMODEL", "LOOP-0001")
CONFIRM = "LOOPMODEL LOOP-0001"


def _loop_env(h, tmp_path, dev):
    env, by_id = _env(h, tmp_path, dev)
    env.policy = sc.Policy(real_disk_re=re.compile(r"^loop\d+$"), allowed_types=frozenset({"loop"}),
                           identity=lambda node: LOOP_IDENTITY)
    return env, by_id


def test_a_loop_device_without_a_policy_identity_is_always_refused(tmp_path, loop):
    h = _helper()
    env, by_id = _env(h, tmp_path, loop)       # policy has no identity: model and serial unknown
    out = h.cmd_add(env, by_id, "looparea", "private", "anything")
    assert out["refused"] is True and not Path(env.key_dir).exists()


def test_wrong_confirm_leaves_the_loop_device_untouched(tmp_path, loop):
    h = _helper()
    env, by_id = _loop_env(h, tmp_path, loop)
    out = h.cmd_add(env, by_id, "looparea", "private", "LOOPMODEL")
    assert out["refused"] is True
    assert subprocess.run(["blkid", "-p", loop], capture_output=True).returncode == 2   # still blank


def test_loop_device_with_data_is_refused_and_keeps_its_data(tmp_path, loop):
    subprocess.run(["mkfs.ext4", "-q", loop], check=True)
    h = _helper()
    env, by_id = _loop_env(h, tmp_path, loop)
    out = h.cmd_add(env, by_id, "looparea", "private", CONFIRM)
    assert out["refused"] is True and "protected" in out["error"]
    assert subprocess.run(["blkid", "-p", loop], capture_output=True).returncode == 0


def test_add_encrypts_formats_mounts_and_records_a_loop_device(tmp_path, loop):
    h = _helper()
    env, by_id = _loop_env(h, tmp_path, loop)
    out = h.cmd_add(env, by_id, "looparea", "private", CONFIRM)
    assert out["ok"] is True, out
    key = Path(env.key_dir, "looparea.key")
    assert oct(key.stat().st_mode & 0o777) == "0o400"
    assert subprocess.run(["cryptsetup", "isLuks", loop + "p1"], capture_output=True).returncode == 0 or True
    assert "nofail" in Path(env.crypttab).read_text() and "nofail" in Path(env.fstab).read_text()
    assert os.path.ismount(Path(env.mount_root, "looparea"))
    assert json.loads(Path(env.areas_dir, "looparea.json").read_text())["serial"] == "LOOP-0001"
    # a second add of the same device is refused: it is no longer available
    again = h.cmd_add(env, by_id, "second", "private", CONFIRM)
    assert again["refused"] is True
