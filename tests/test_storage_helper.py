"""The root helper: refusals, the re-check, step order, failure states, file hygiene.

Nothing here touches a real device: the helper is loaded as a module, its
runner, device checks and paths are injected, and the fake disk is called
/dev/taostest0 (a name no machine has).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

from storage_test_support import disk, lsblk, node, part, system_disk
from tinyagentos import storage_classifier as sc

HELPER = Path(__file__).resolve().parent.parent / "scripts" / "taos-storage-helper"
DEV = "/dev/taostest0"
POLICY = sc.Policy(real_disk_re=re.compile(r"^taostest\d+$"))
CONFIRM = "FAKEDRIVE 1000 FAKE-SERIAL-0001"


def load_helper():
    loader = importlib.machinery.SourceFileLoader("taos_storage_helper", str(HELPER))
    spec = importlib.util.spec_from_loader("taos_storage_helper", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    mod._sc = sc
    return mod


class FakeMachine:
    """Answers the probes like a host with one blank disk, and records every command."""

    def __init__(self, blank=True, fail_on=None, mounted=False, tools_ok=True):
        self.calls: list = []
        self.inputs: dict = {}
        self.blank = blank
        self.partitioned = False
        self.fail_on = fail_on
        self.mounted = mounted

    def disk_node(self):
        kids = [part("taostest0", 1, partuuid="PU-1")] if self.partitioned else None
        for k in kids or []:
            k["path"] = f"{DEV}1"
        d = disk("taostest0", pttype="gpt" if self.partitioned else None, children=kids)
        d["path"] = DEV
        if self.mounted:
            d["mountpoints"] = ["/mnt/x"]
        return d

    def __call__(self, argv, input=None):
        self.calls.append(argv)
        tool = os.path.basename(argv[0])
        if input is not None:
            self.inputs[tool] = input
        if self.fail_on and tool == self.fail_on:
            return 1, "", f"{tool} blew up"
        if tool == "lsblk":
            if len(argv) > 4:  # lsblk -J -b -O <dev>
                return 0, lsblk(self.disk_node()), ""
            return 0, lsblk(system_disk(), self.disk_node()), ""
        if tool == "findmnt":
            return 0, json.dumps({"filesystems": [{"target": "/", "source": "/dev/sda2"}]}), ""
        if tool == "blkid":
            if "-s" in argv:
                return 0, "FS-UUID-1\n", ""
            return (2, "", "") if self.blank else (0, "TYPE=ext4\n", "")
        if tool == "wipefs":
            return 0, ('{"signatures": []}' if self.blank else '{"signatures": [{"type": "ext4"}]}'), ""
        if tool == "sfdisk":
            self.partitioned = True
            return 0, "", ""
        if tool == "cryptsetup" and argv[1] == "luksUUID":
            return 0, "LUKS-UUID-1\n", ""
        return 0, "", ""

    def tools(self):
        return [os.path.basename(c[0]) for c in self.calls]


@pytest.fixture
def helper():
    return load_helper()


@pytest.fixture
def world(tmp_path, helper):
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    (by_id / "ata-FAKEDRIVE_1000_FAKE-SERIAL-0001").symlink_to(DEV)
    (tmp_path / "etc").mkdir()
    m = FakeMachine()
    env = helper.Env(
        key_dir=str(tmp_path / "keys"), mount_root=str(tmp_path / "srv"), crypttab=str(tmp_path / "etc/crypttab"),
        fstab=str(tmp_path / "etc/fstab"), areas_dir=str(tmp_path / "areas"), log_path=str(tmp_path / "taos-storage.log"),
        by_id_dir=str(by_id), run=m, policy=POLICY, head_reader=lambda p, o, n: True,
        read_text=lambda p: "/dev/sda2 / ext4 rw 0 0\n" if p.endswith("mounts") else "Filename Type Size Used Priority\n",
        open_exclusive=lambda p: None, have_tool=lambda t: True, caller_uid=991, caller_gid=991,
        is_block=lambda p: True, systemd=True, now=lambda: 1.0,
    )
    os.makedirs(env.mount_root)
    return env, m, str(by_id / "ata-FAKEDRIVE_1000_FAKE-SERIAL-0001")


def add(helper, env, dev, name="photos", kind="private", confirm=CONFIRM):
    return helper.cmd_add(env, dev, name, kind, confirm)


# ---------------------------------------------------------------- refusals


def test_wrong_confirm_string_refuses_and_runs_no_write(helper, world):
    env, m, dev = world
    for bad in ["", "FAKEDRIVE 1000", "fakedrive 1000 fake-serial-0001", CONFIRM + " ", " " + CONFIRM, "FAKE-SERIAL-0001 FAKEDRIVE 1000"]:
        out = add(helper, env, dev, confirm=bad)
        assert out["ok"] is False and out["refused"] is True, bad
    assert not set(m.tools()) & {"sfdisk", "cryptsetup", "mkfs.btrfs", "mount"}


@pytest.mark.parametrize("setup", ["has_data", "mounted"])
def test_non_available_disk_is_refused_even_with_the_right_confirm(helper, world, setup):
    env, m, dev = world
    if setup == "has_data":
        m.blank = False
    else:
        m.mounted = True
    out = add(helper, env, dev)
    assert out["ok"] is False and out["refused"] is True and "protected" in out["error"]
    assert not set(m.tools()) & {"sfdisk", "cryptsetup", "mkfs.btrfs", "mount"}
    assert not os.path.exists(env.key_dir)


def test_disk_that_gains_data_after_listing_is_refused(helper, world):
    """The disk is listed as available, then data appears; the helper re-checks on its own."""
    env, m, dev = world
    first = helper.cmd_list(env)
    assert first["disks"][-1]["class"] == "available"
    m.blank = False
    out = add(helper, env, dev)
    assert out["refused"] is True and "protected" in out["error"]
    assert "sfdisk" not in m.tools()


def test_failed_probe_inside_the_helper_refuses(helper, world):
    env, m, dev = world
    m.fail_on = "wipefs"
    out = add(helper, env, dev)
    assert out["refused"] is True and "sfdisk" not in m.tools()


def test_head_data_without_signature_refuses(helper, world):
    env, m, dev = world
    env.head_reader = lambda p, o, n: False
    out = add(helper, env, dev)
    assert out["refused"] is True and "sfdisk" not in m.tools()


@pytest.mark.parametrize("path", [
    "/dev/sdb", "/dev/nvme0n1", "/dev/disk/by-id/../../sdb", "/dev/disk/by-id/x/y", "relative", "",
    "/dev/disk/by-id/ata-X-part1", "/dev/disk/by-uuid/abc",
])
def test_only_whole_by_id_paths_are_accepted(helper, world, path):
    env, m, _ = world
    env.by_id_dir = "/dev/disk/by-id"
    out = add(helper, env, path)
    assert out["refused"] is True
    assert m.calls == []


def test_a_by_id_name_that_is_not_a_block_device_is_refused(helper, world):
    env, m, dev = world
    env.is_block = lambda p: False
    assert add(helper, env, dev)["refused"] is True


def test_device_in_use_refused(helper, world):
    env, m, dev = world

    def busy(p):
        raise OSError(16, "Device or resource busy")

    env.open_exclusive = busy
    out = add(helper, env, dev)
    assert out["refused"] is True and "in use" in out["error"]
    assert "sfdisk" not in m.tools()


@pytest.mark.parametrize("name", ["", "A", "a", "photos/x", "../x", "x y", "x;y", "-x", "x-", "a--b", "a" * 40, "x\n", "ünï"])
def test_bad_area_names_refused(helper, world, name):
    env, m, dev = world
    assert add(helper, env, dev, name=name)["refused"] is True
    assert "sfdisk" not in m.tools()


def test_unknown_kind_refused(helper, world):
    env, m, dev = world
    assert add(helper, env, dev, kind="rm -rf")["refused"] is True


def test_missing_tool_is_reported_plainly(helper, world):
    env, m, dev = world
    env.have_tool = lambda t: t != "veracrypt"
    out = add(helper, env, dev, kind="shared")
    assert out["refused"] is True and "VeraCrypt is not installed" in out["error"]
    assert "sfdisk" not in m.tools()


def test_unwritable_log_means_nothing_is_done(helper, world, tmp_path):
    env, m, dev = world
    env.log_path = str(tmp_path / "no" / "such" / "dir" / "log")
    out = add(helper, env, dev)
    assert out["refused"] is True and "log" in out["error"] and "sfdisk" not in m.tools()


def test_existing_area_name_key_or_mount_refused(helper, world):
    env, m, dev = world
    os.makedirs(env.key_dir, mode=0o700)
    Path(env.key_dir, "photos.key").write_bytes(b"x")
    assert add(helper, env, dev)["refused"] is True
    assert "sfdisk" not in m.tools()


# --------------------------------------------------------------- the happy path


def test_private_add_runs_every_step_in_order_with_argument_lists(helper, world):
    env, m, dev = world
    out = add(helper, env, dev)
    assert out["ok"] is True, out
    assert out["steps"] == ["partitioned", "key", "encrypted", "formatted", "configured", "mounted"]
    order = [t for t in m.tools() if t in ("sfdisk", "cryptsetup", "mkfs.btrfs", "mount")]
    assert order == ["sfdisk", "cryptsetup", "cryptsetup", "cryptsetup", "mkfs.btrfs", "mount"]
    assert all(isinstance(c, list) and all(isinstance(a, str) for a in c) for c in m.calls)
    fmt = next(c for c in m.calls if os.path.basename(c[0]) == "cryptsetup" and c[1] == "luksFormat")
    assert fmt[2:4] == ["--type", "luks2"] and fmt[-1] == f"{DEV}1"
    assert "label: gpt" in m.inputs["sfdisk"] and "taos-photos" in m.inputs["sfdisk"]


def test_key_file_is_0400_in_a_0700_directory_and_never_in_argv_or_log(helper, world):
    env, m, dev = world
    add(helper, env, dev)
    key = Path(env.key_dir, "photos.key")
    assert stat.S_IMODE(key.stat().st_mode) == 0o400
    assert stat.S_IMODE(Path(env.key_dir).stat().st_mode) == 0o700
    assert len(key.read_bytes()) == 64
    blob = key.read_bytes().hex()
    assert blob not in json.dumps(m.calls) and blob not in Path(env.log_path).read_text()


def test_crypttab_and_fstab_entries_have_nofail_and_use_uuids(helper, world):
    env, m, dev = world
    add(helper, env, dev)
    ct = Path(env.crypttab).read_text()
    assert ct.strip() == f"taos-photos UUID=LUKS-UUID-1 {env.key_dir}/photos.key luks,nofail"
    fs = Path(env.fstab).read_text()
    assert f"UUID=FS-UUID-1 {env.mount_root}/photos btrfs defaults,nofail" in fs and "/dev/sd" not in fs


def test_existing_crypttab_and_fstab_lines_are_preserved(helper, world):
    env, m, dev = world
    Path(env.fstab).write_text("UUID=abc / ext4 defaults 0 1\n")
    Path(env.crypttab).write_text("other UUID=1 none luks\n")
    add(helper, env, dev)
    assert Path(env.fstab).read_text().startswith("UUID=abc / ext4 defaults 0 1\n")
    assert Path(env.crypttab).read_text().startswith("other UUID=1 none luks\n")


def test_area_record_written_without_secrets(helper, world):
    env, m, dev = world
    out = add(helper, env, dev)
    rec = json.loads(Path(env.areas_dir, "photos.json").read_text())
    assert rec["serial"] == "FAKE-SERIAL-0001" and rec["encryption"] == "luks2" and out["area"]["mount"].endswith("/photos")
    assert "secret" not in json.dumps(rec).lower()


def test_a_disk_taos_formatted_is_protected_afterwards(helper, world):
    env, m, dev = world
    add(helper, env, dev)
    m.partitioned = False  # even if the machine reads empty again (e.g. an unlocked-looking VeraCrypt disk)
    again = helper.cmd_list(env)["disks"][-1]
    assert again["class"] == "protected" and any("taOS storage area" in r for r in again["reasons"])


def test_shared_add_uses_veracrypt_ntfs_and_tcrypt_crypttab(helper, world):
    env, m, dev = world
    out = add(helper, env, dev, kind="shared")
    assert out["ok"] is True, out
    vc = next(c for c in m.calls if os.path.basename(c[0]) == "veracrypt")
    assert "--create" in vc and "--filesystem=NTFS" in vc and f"--keyfiles={env.key_dir}/photos.key" in vc
    ct = Path(env.crypttab).read_text()
    assert "tcrypt-veracrypt" in ct and "nofail" in ct and "PARTUUID=PU-1" in ct
    assert "ntfs-3g" in Path(env.fstab).read_text() and "uid=991" in Path(env.fstab).read_text()


# ------------------------------------------------------------------- failures


@pytest.mark.parametrize("tool,last,word", [
    ("sfdisk", None, "part-way"), ("mkfs.btrfs", "encrypted", "part-way"), ("mount", "configured", "mount"),
])
def test_failure_stops_and_reports_the_known_state(helper, world, tool, last, word):
    env, m, dev = world
    m.fail_on = tool
    out = add(helper, env, dev)
    assert out["ok"] is False and out["refused"] is False
    assert out["failed_step"] and tool in out["error"]
    assert (out["steps"][-1] if out["steps"] else None) == last
    assert word in out["state"]
    # nothing was tried after the failure, and nothing was undone
    assert m.tools()[-1] == tool
    assert not any(c[0].endswith("wipefs") and "-a" in c for c in m.calls)


def test_cryptsetup_failure_leaves_a_key_and_says_not_encrypted(helper, world):
    env, m, dev = world
    m.fail_on = "cryptsetup"
    out = add(helper, env, dev)
    assert out["steps"] == ["partitioned", "key"] and "part-way" in out["state"]
    assert Path(env.crypttab).exists() is False and Path(env.fstab).exists() is False


def test_no_command_is_a_shell_or_a_wipe(helper, world):
    env, m, dev = world
    add(helper, env, dev)
    for c in m.calls:
        assert os.path.basename(c[0]) not in ("sh", "bash", "dd", "mkfs.ext4", "shred", "blkdiscard")
        assert not (os.path.basename(c[0]) == "wipefs" and ("-a" in c or "--all" in c))


# ------------------------------------------------------------- prepare-home


def test_prepare_home_creates_a_private_directory_owned_by_the_caller(helper, world, monkeypatch):
    env, m, dev = world
    add(helper, env, dev)
    chowned = []
    monkeypatch.setattr(os, "chown", lambda p, u, g: chowned.append((p, u, g)))
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    out = helper.cmd_prepare_home(env, "photos", "person-1")
    assert out["ok"] is True and out["path"].endswith("/photos/people/person-1")
    assert stat.S_IMODE(os.stat(out["path"]).st_mode) == 0o700
    assert chowned == [(out["path"], 991, 991)]


@pytest.mark.parametrize("person", ["", "../x", "a/b", "a b", "x" * 80, "a;b"])
def test_prepare_home_rejects_bad_person_ids(helper, world, person, monkeypatch):
    env, m, dev = world
    add(helper, env, dev)
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    assert helper.cmd_prepare_home(env, "photos", person)["ok"] is False


def test_prepare_home_refuses_unknown_area_unmounted_area_and_unknown_caller(helper, world, monkeypatch):
    env, m, dev = world
    add(helper, env, dev)
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    assert helper.cmd_prepare_home(env, "nope", "p1")["ok"] is False
    monkeypatch.setattr(os.path, "ismount", lambda p: False)
    assert helper.cmd_prepare_home(env, "photos", "p1")["ok"] is False
    monkeypatch.setattr(os.path, "ismount", lambda p: True)
    env.caller_uid = None
    assert helper.cmd_prepare_home(env, "photos", "p1")["ok"] is False
    env.caller_uid = 0
    assert helper.cmd_prepare_home(env, "photos", "p1")["ok"] is False


# ------------------------------------------------------------- file hygiene


def test_helper_refuses_to_run_when_its_files_are_writable_by_others(helper, tmp_path):
    f = tmp_path / "h.py"
    f.write_text("x")
    f.chmod(0o666)
    with pytest.raises(helper.Refused):
        helper.check_own_files([str(f)], expected_uid=os.getuid())
    f.chmod(0o644)
    tmp_path.chmod(0o755)
    helper.check_own_files([str(f)], expected_uid=os.getuid())
    with pytest.raises(helper.Refused):
        helper.check_own_files([str(f)], expected_uid=os.getuid() + 1)


def test_main_refuses_when_not_root(helper, capsys, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert helper.main(["list"]) == 1
    assert "must run as root" in capsys.readouterr().out


def test_cli_has_only_the_fixed_subcommands(helper):
    import argparse
    for bad in (["format"], ["add"], ["sh", "-c", "id"], []):
        with pytest.raises(SystemExit):
            helper.main(bad, env=object())


def test_main_add_end_to_end_through_argparse(helper, world, capsys):
    env, m, dev = world
    rc = helper.main(["add", "--device", dev, "--name", "photos", "--kind", "private", "--confirm", CONFIRM], env=env)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True


# -------------------------------------------------------------- VeraCrypt fetch


def test_veracrypt_install_needs_licence_acceptance(helper, world):
    env, m, dev = world
    out = helper.cmd_install_veracrypt(env, False)
    assert out["refused"] is True and "licence" in out["error"]
    assert m.calls == []


def test_veracrypt_install_without_a_pinned_release_says_it_is_unavailable(helper, world, tmp_path):
    env, m, dev = world
    env.manifest = str(tmp_path / "missing.json")
    out = helper.cmd_install_veracrypt(env, True)
    assert out["ok"] is False and "not available" in out["error"]
    assert m.calls == []


def test_veracrypt_checksum_mismatch_installs_nothing(helper, world, tmp_path):
    env, m, dev = world
    man = tmp_path / "vc.json"
    man.write_text(json.dumps({"url": "https://example.invalid/vc.deb", "sha256": "0" * 64}))
    env.manifest = str(man)
    env.fetch = lambda u: b"not the pinned bytes"
    out = helper.cmd_install_veracrypt(env, True)
    assert out["ok"] is False and "checksum" in out["error"]
    assert not any(os.path.basename(c[0]) == "apt-get" for c in m.calls)


def test_veracrypt_manifest_must_be_https(helper, world, tmp_path):
    env, m, dev = world
    man = tmp_path / "vc.json"
    man.write_text(json.dumps({"url": "http://example.invalid/vc.deb", "sha256": "0" * 64}))
    env.manifest = str(man)
    env.fetch = lambda u: pytest.fail("must not fetch over http")
    assert helper.cmd_install_veracrypt(env, True)["ok"] is False


def test_helper_refuses_when_the_disk_listing_itself_fails(helper, world):
    env, m, dev = world
    m.fail_on = "lsblk"
    out = add(helper, env, dev)
    assert out["refused"] is True and "safety check could not run" in out["error"]
    assert "sfdisk" not in m.tools()
