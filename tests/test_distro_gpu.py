"""taos-gpu package: display GPU policy, run against a fake sysfs/etc tree."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "distro" / "packages" / "taos-gpu"
CLI = PKG / "usr" / "bin" / "taos-gpu"
SETUP = REPO / "distro" / "setup.sh"

INTEL = "0000:00:02.0"
RTX3090 = "0000:01:00.0"
RTX3060 = "0000:02:00.0"

SHELLS = ["sh"] + (["/bin/dash"] if Path("/bin/dash").exists() else [])


def _card(n, pci, vendor, driver, boot, connectors=None, cls="0x030000"):
    """n=None means no class/drm node (driverless card); driver=None means unbound."""
    return dict(n=n, pci=pci, vendor=vendor, driver=driver, boot=boot,
                connectors=connectors or {}, cls=cls)


def default_cards():
    return [
        _card(0, INTEL, "0x8086", "i915", 1, {"HDMI-A-1": "connected", "DP-1": "disconnected"}),
        _card(1, RTX3090, "0x10de", "nouveau", 0),
        _card(2, RTX3060, "0x10de", "nouveau", 0),
    ]


def post_blacklist_cards():
    return [
        _card(0, INTEL, "0x8086", "i915", 1, {"HDMI-A-1": "connected"}),
        _card(None, RTX3090, "0x10de", None, 0),
        _card(None, RTX3060, "0x10de", None, 0),
    ]


def make_sysfs(root: Path, cards) -> Path:
    for c in cards:
        dev = root / "devices" / "pci0000:00" / c["pci"]
        dev.mkdir(parents=True, exist_ok=True)
        (dev / "vendor").write_text(c["vendor"] + "\n")
        (dev / "class").write_text(c["cls"] + "\n")
        (dev / "boot_vga").write_text(f"{c['boot']}\n")
        if c["driver"]:
            drv = root / "bus" / "pci" / "drivers" / c["driver"]
            drv.mkdir(parents=True, exist_ok=True)
            (dev / "driver").symlink_to(drv)
        bus = root / "bus" / "pci" / "devices"
        bus.mkdir(parents=True, exist_ok=True)
        (bus / c["pci"]).symlink_to(dev)
        if c["n"] is None:
            continue
        (dev / "drm" / f"card{c['n']}").mkdir(parents=True)
        card = root / "class" / "drm" / f"card{c['n']}"
        card.mkdir(parents=True, exist_ok=True)
        (card / "device").symlink_to(dev)
        for name, status in c["connectors"].items():
            conn = root / "class" / "drm" / f"card{c['n']}-{name}"
            conn.mkdir()
            (conn / "status").write_text(status + "\n")
    (root / "class" / "drm").mkdir(parents=True, exist_ok=True)
    return root


def make_platform_sysfs(root: Path, name="display-subsystem", driver="rockchip-drm") -> Path:
    dev = root / "devices" / "platform" / name
    dev.mkdir(parents=True)
    drv = root / "bus" / "platform" / "drivers" / driver
    drv.mkdir(parents=True)
    (dev / "driver").symlink_to(drv)
    card = root / "class" / "drm" / "card0"
    card.mkdir(parents=True)
    (card / "device").symlink_to(dev)
    conn = root / "class" / "drm" / "card0-HDMI-A-1"
    conn.mkdir()
    (conn / "status").write_text("connected\n")
    return root


@pytest.fixture
def env(tmp_path: Path):
    sysfs = tmp_path / "sys"
    etc = tmp_path / "etc"
    etc.mkdir()
    return {"sysfs": sysfs, "etc": etc}


def run(env, *args, shell="sh", extra=None):
    e = {"PATH": os.environ["PATH"], "SYSFS_ROOT": str(env["sysfs"]),
         "ETC_ROOT": str(env["etc"]), "TAOS_GPU_NO_APPLY": "1"}
    e.update(extra or {})
    return subprocess.run([shell, str(CLI), *args], env=e, capture_output=True, text=True)


def files(env):
    return {
        "rules": env["etc"] / "udev/rules.d/61-taos-display-gpu.rules",
        "env": env["etc"] / "taos/session.env.d/10-gpu.env",
        "mod": env["etc"] / "modprobe.d/taos-gpu.conf",
        "conf": env["etc"] / "taos/gpu.conf",
    }


def snapshot(env):
    return {str(p.relative_to(env["etc"])): p.read_bytes()
            for p in env["etc"].rglob("*") if p.is_file()}


@pytest.fixture
def full(env):
    make_sysfs(env["sysfs"], default_cards())
    return env


@pytest.mark.parametrize("shell", SHELLS)
def test_list_three_gpus(full, shell):
    res = run(full, "list", shell=shell)
    assert res.returncode == 0, res.stderr
    lines = res.stdout.strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith(INTEL) and "Intel" in lines[0] and "i915" in lines[0]
    assert "integrated" in lines[0] and "HDMI-A-1" in lines[0] and "[display]" in lines[0]
    for line, pci in ((lines[1], RTX3090), (lines[2], RTX3060)):
        assert line.startswith(pci) and "NVIDIA" in line and "nouveau" in line
        assert "discrete" in line and "[display]" not in line


@pytest.mark.parametrize("shell", SHELLS)
def test_list_json(full, shell):
    res = run(full, "list", "--json", shell=shell)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == [
        {"pci": INTEL, "vendor": "Intel", "driver": "i915", "kind": "integrated",
         "boot_vga": True, "connected": ["HDMI-A-1"], "display": True},
        {"pci": RTX3090, "vendor": "NVIDIA", "driver": "nouveau", "kind": "discrete",
         "boot_vga": False, "connected": [], "display": False},
        {"pci": RTX3060, "vendor": "NVIDIA", "driver": "nouveau", "kind": "discrete",
         "boot_vga": False, "connected": [], "display": False},
    ]


def test_unknown_vendor_shows_hex(env):
    make_sysfs(env["sysfs"], [_card(0, "0000:03:00.0", "0x1234", "foo", 1)])
    assert "0x1234" in run(env, "list").stdout


def test_amd_kind_rule(env):
    make_sysfs(env["sysfs"], [_card(0, "0000:00:08.1", "0x1002", "amdgpu", 1),
                              _card(1, "0000:03:00.0", "0x1002", "amdgpu", 0)])
    data = json.loads(run(env, "list", "--json").stdout)
    assert [d["kind"] for d in data] == ["integrated", "discrete"]


def test_auto_picks_intel_and_default_files(full):
    res = run(full, "apply")
    assert res.returncode == 0, res.stderr
    assert "reboot required" in res.stdout
    f = files(full)
    rules = f["rules"].read_text()
    assert INTEL in rules and 'SYMLINK+="dri/taos-display"' in rules
    assert 'SUBSYSTEM=="drm"' in rules and 'KERNEL=="card[0-9]*"' in rules
    assert f["env"].read_text() == "WLR_DRM_DEVICES=/dev/dri/taos-display\n"
    mod = f["mod"].read_text()
    assert "blacklist nouveau" in mod
    assert "options nouveau modeset=0" in mod
    assert "options nvidia-drm modeset=0" in mod
    assert "compute only" in mod
    assert not list(full["etc"].rglob("*.tmp.*"))


def test_status_default(full):
    out = run(full, "status").stdout
    assert "policy: auto" in out
    assert f"display: {INTEL}" in out
    assert f"compute-only: {RTX3090} {RTX3060}" in out
    js = json.loads(run(full, "status", "--json").stdout)
    assert js == {"policy": "auto", "display": INTEL, "compute_only": [RTX3090, RTX3060]}


def test_auto_without_intel_picks_boot_vga_nvidia(env):
    make_sysfs(env["sysfs"], [
        _card(1, RTX3090, "0x10de", "nouveau", 0),
        _card(2, RTX3060, "0x10de", "nouveau", 1),
    ])
    assert run(env, "apply").returncode == 0
    f = files(env)
    assert RTX3060 in f["rules"].read_text()
    mod = f["mod"].read_text()
    assert "options nvidia-drm modeset=1" in mod
    assert "blacklist nouveau" not in mod


def test_auto_prefers_integrated_with_connector(env):
    make_sysfs(env["sysfs"], [
        _card(0, INTEL, "0x8086", "i915", 0),
        _card(1, "0000:00:08.1", "0x1002", "amdgpu", 1, {"eDP-1": "connected"}),
        _card(2, RTX3090, "0x10de", "nouveau", 0),
    ])
    assert json.loads(run(env, "status", "--json").stdout)["display"] == "0000:00:08.1"


def test_no_nvidia_modprobe_is_comment_only(env):
    make_sysfs(env["sysfs"], [_card(0, INTEL, "0x8086", "i915", 1)])
    assert run(env, "apply").returncode == 0
    lines = files(env)["mod"].read_text().strip().splitlines()
    assert lines and all(l.startswith("#") for l in lines)


@pytest.mark.parametrize("shell", SHELLS)
def test_set_display_switches_everything(full, shell):
    res = run(full, "set-display", RTX3090, shell=shell)
    assert res.returncode == 0, res.stderr
    f = files(full)
    assert f["conf"].read_text() == f"DISPLAY_GPU={RTX3090}\n"
    assert RTX3090 in f["rules"].read_text() and INTEL not in f["rules"].read_text()
    mod = f["mod"].read_text()
    assert "options nvidia-drm modeset=1" in mod and "blacklist nouveau" not in mod
    out = run(full, "status").stdout
    assert f"policy: {RTX3090}" in out and f"display: {RTX3090}" in out
    assert f"compute-only: {INTEL} {RTX3060}" in out
    assert "[display]" in [l for l in run(full, "list").stdout.splitlines()
                           if l.startswith(RTX3090)][0]
    # and back to auto
    assert run(full, "set-display", "auto").returncode == 0
    assert INTEL in f["rules"].read_text()
    assert "blacklist nouveau" in f["mod"].read_text()


def test_set_display_unknown_changes_nothing(full):
    assert run(full, "apply").returncode == 0
    before = snapshot(full)
    res = run(full, "set-display", "0000:09:00.0")
    assert res.returncode == 2
    assert "not a listed GPU" in res.stderr
    assert snapshot(full) == before


def test_apply_twice_is_byte_identical(full):
    assert run(full, "apply").returncode == 0
    first = snapshot(full)
    second = run(full, "apply")
    assert second.returncode == 0
    assert "reboot required" not in second.stdout
    assert snapshot(full) == first


def test_empty_sysfs_writes_nothing(env):
    (env["sysfs"] / "class" / "drm").mkdir(parents=True)
    (env["sysfs"] / "bus" / "pci" / "devices").mkdir(parents=True)
    res = run(env, "list")
    assert res.returncode == 0 and res.stdout == ""
    assert json.loads(run(env, "list", "--json").stdout) == []
    res = run(env, "apply")
    assert res.returncode == 0 and "no GPU" in res.stdout
    assert snapshot(env) == {}
    assert run(env, "status").returncode == 0


@pytest.mark.parametrize("shell", SHELLS)
def test_post_blacklist_driverless_nvidia_stay_listed(env, shell):
    make_sysfs(env["sysfs"], post_blacklist_cards())
    lines = run(env, "list", shell=shell).stdout.strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith(INTEL) and "[display]" in lines[0]
    for line, pci in ((lines[1], RTX3090), (lines[2], RTX3060)):
        assert line.startswith(pci) and "NVIDIA" in line and "none" in line
        assert "[display]" not in line
    data = json.loads(run(env, "list", "--json", shell=shell).stdout)
    assert [d["driver"] for d in data] == ["i915", "none", "none"]
    out = run(env, "status", shell=shell).stdout
    assert f"display: {INTEL}" in out
    assert f"compute-only: {RTX3090} {RTX3060}" in out
    assert run(env, "apply", shell=shell).returncode == 0
    assert "blacklist nouveau" in files(env)["mod"].read_text()


def test_auto_never_selects_driverless_gpu(env):
    make_sysfs(env["sysfs"], [
        _card(None, RTX3090, "0x10de", None, 1),
        _card(1, RTX3060, "0x10de", "nvidia", 0),
    ])
    assert json.loads(run(env, "status", "--json").stdout)["display"] == RTX3060
    other = env["sysfs"].parent / "sys2"
    make_sysfs(other, [_card(None, RTX3090, "0x10de", None, 1)])
    js = json.loads(run({**env, "sysfs": other}, "status", "--json").stdout)
    assert js["display"] is None and js["compute_only"] == [RTX3090]


def test_set_display_driverless_warns(env):
    make_sysfs(env["sysfs"], post_blacklist_cards())
    res = run(env, "set-display", RTX3090)
    assert res.returncode == 0, res.stderr
    assert "no display driver" in res.stderr
    assert json.loads(run(env, "status", "--json").stdout)["display"] == RTX3090


@pytest.mark.parametrize("shell", SHELLS)
def test_platform_only_gpu(env, shell):
    make_platform_sysfs(env["sysfs"])
    data = json.loads(run(env, "list", "--json", shell=shell).stdout)
    assert data == [{"pci": "display-subsystem", "vendor": "platform",
                     "driver": "rockchip-drm", "kind": "integrated", "boot_vga": False,
                     "connected": ["HDMI-A-1"], "display": True}]
    assert run(env, "apply", shell=shell).returncode == 0
    assert 'KERNELS=="display-subsystem"' in files(env)["rules"].read_text()


def test_platform_card_not_duplicated_next_to_pci(env):
    make_sysfs(env["sysfs"], [_card(0, INTEL, "0x8086", "i915", 1)])
    assert len(json.loads(run(env, "list", "--json").stdout)) == 1


def test_non_display_pci_class_ignored(env):
    make_sysfs(env["sysfs"], [
        _card(0, INTEL, "0x8086", "i915", 1),
        _card(None, "0000:03:00.0", "0x8086", "e1000e", 0, cls="0x020000"),
    ])
    data = json.loads(run(env, "list", "--json").stdout)
    assert [d["pci"] for d in data] == [INTEL]


def test_usage_and_unknown(full):
    assert run(full, "--help").returncode == 0
    assert "set-display" in run(full, "--help").stdout
    assert run(full, "bogus").returncode == 2
    assert run(full).returncode == 2


@pytest.mark.skipif(os.geteuid() == 0, reason="running as root")
def test_mutation_refused_when_not_root_without_etc_override(full):
    e = {"PATH": os.environ["PATH"], "SYSFS_ROOT": str(full["sysfs"])}
    res = subprocess.run(["sh", str(CLI), "apply"], env=e, capture_output=True, text=True)
    assert res.returncode == 1
    assert "root" in res.stderr


def test_scripts_pass_sh_n():
    scripts = [p for p in (PKG / "DEBIAN").iterdir() if p.name != "control"] + [CLI, SETUP]
    for script in scripts:
        for shell in SHELLS:
            res = subprocess.run([shell, "-n", str(script)], capture_output=True, text=True)
            assert res.returncode == 0, f"{script}: {res.stderr}"


def test_maintainer_scripts_content():
    post = (PKG / "DEBIAN" / "postinst").read_text()
    assert "DISPLAY_GPU=auto" in post and "taos-gpu apply || true" in post
    rm = (PKG / "DEBIAN" / "postrm").read_text()
    for needle in ("61-taos-display-gpu.rules", "10-gpu.env", "taos-gpu.conf",
                   "purge", "gpu.conf", "update-initramfs"):
        assert needle in rm
    assert os.access(CLI, os.X_OK)


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not installed")
def test_package_builds(tmp_path: Path):
    deb = tmp_path / "taos-gpu.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(PKG), str(deb)],
                   check=True, capture_output=True)
    listing = subprocess.run(["dpkg-deb", "-c", str(deb)], check=True,
                             capture_output=True, text=True).stdout
    assert "./usr/bin/taos-gpu" in listing
    info = subprocess.run(["dpkg-deb", "-I", str(deb)], check=True,
                          capture_output=True, text=True).stdout
    assert "Depends: udev" in info and "Version: 0.1.0" in info


def test_setup_sh_step3():
    text = SETUP.read_text()
    assert "Step 3: display GPU (taos-gpu)" in text
    assert "taos-gpu_" in text
    assert 'taos-gpu set-display "$TAOS_DISPLAY_GPU"' in text
    assert "[ -t 0 ]" in text and "Display GPU [auto]:" in text
    assert text.rstrip().endswith("taos-gpu status")
    # steps 1 and 2 untouched
    assert "taos-branding_" in text and "taos-shell_" in text


def test_no_em_dashes():
    for p in [CLI, SETUP, REPO / "distro/README.md", Path(__file__),
              REPO / "changelog.d/distro-taos-gpu.md", *(PKG / "DEBIAN").iterdir()]:
        assert chr(0x2014) not in p.read_text(), p
