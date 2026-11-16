"""taos-gpu driver (step 4): plans checked with --dry-run against a fake sysfs/etc tree."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_distro_gpu import (CLI, PKG, REPO, RTX3060, RTX3090, SETUP, SHELLS, _card,
                             default_cards, files, make_sysfs)

SB_ON = {"TAOS_GPU_SB_STATE": "enabled", "TAOS_GPU_MOK_STATE": "not_enrolled"}
KEYRING_SHA = "d0d4ef986a44400f9db33c600ef33a985175e7cc63d805a10e1839c7a1e78f5f"


@pytest.fixture
def env(tmp_path: Path):
    etc = tmp_path / "etc"
    etc.mkdir()
    return {"sysfs": tmp_path / "sys", "etc": etc, "tmp": tmp_path}


@pytest.fixture
def full(env):
    make_sysfs(env["sysfs"], default_cards())
    return env


def nvidia_display_cards():
    return [_card(1, RTX3090, "0x10de", "nouveau", 1), _card(2, RTX3060, "0x10de", "nouveau", 0)]


def drv(env, *args, shell="sh", extra=None):
    e = {"PATH": os.environ["PATH"], "SYSFS_ROOT": str(env["sysfs"]),
         "ETC_ROOT": str(env["etc"]), "TAOS_GPU_NO_APPLY": "1",
         "TAOS_GPU_ARCH": "amd64", "TAOS_GPU_SB_STATE": "disabled",
         "TAOS_GPU_FAKE_INSTALLED": "", "TAOS_GPU_DKMS_KEY": str(env["tmp"] / "mok.pub")}
    e.update(extra or {})
    return subprocess.run([shell, str(CLI), "driver", *args], env=e,
                          capture_output=True, text=True)


def plan(res):
    return [l[2:] for l in res.stdout.splitlines() if l.startswith("+ ") and not l.startswith("+ #")]


def idx(lines, needle):
    for i, l in enumerate(lines):
        if needle in l:
            return i
    raise AssertionError(f"{needle!r} not in plan:\n" + "\n".join(lines))


@pytest.mark.parametrize("shell", SHELLS)
def test_default_compute_plan_order(full, shell):
    res = drv(full, "install", "nvidia", "--dry-run", shell=shell, extra=SB_ON)
    assert res.returncode == 0, res.stderr
    p = plan(res)
    order = [idx(p, "curl -fsSL -o"), idx(p, "sha256sum -c"), idx(p, "dpkg -i"),
             idx(p, "apt-get update"),
             idx(p, "linux-headers-amd64 dkms mokutil"),
             idx(p, "--no-install-recommends nvidia-kernel-open-dkms nvidia-driver-cuda"),
             idx(p, "taos-gpu apply"), idx(p, "mokutil --import")]
    assert order == sorted(order) and len(set(order)) == len(order)
    assert KEYRING_SHA in res.stdout
    assert "cuda-debian13-x86_64.list" in res.stdout
    assert "nvidia-open" not in res.stdout
    assert "xserver-xorg-video-nvidia" not in res.stdout
    assert "nvidia-driver-pinning" not in res.stdout
    assert "reboot required" in res.stdout.splitlines()[-1]
    assert chr(0x2014) not in res.stdout


def test_dry_run_changes_nothing(full):
    drv(full, "install", "nvidia", "--dry-run", extra=SB_ON)
    assert list(full["etc"].rglob("*")) == []


def test_env_dry_run_equals_flag(full):
    a = drv(full, "install", "nvidia", "--dry-run", extra=SB_ON)
    b = drv(full, "install", "nvidia", extra={**SB_ON, "TAOS_GPU_DRY_RUN": "1"})
    assert a.stdout == b.stdout and b.returncode == 0


def test_nvidia_display_uses_nvidia_open(env):
    make_sysfs(env["sysfs"], nvidia_display_cards())
    res = drv(env, "install", "nvidia", "--dry-run")
    assert res.returncode == 0, res.stderr
    assert any(l.endswith("apt-get install -y nvidia-open") for l in plan(res))
    assert "nvidia-driver-cuda" not in res.stdout
    assert "--no-install-recommends nvidia-kernel" not in res.stdout


def test_closed_kernel_swaps_dkms_package(full, env):
    res = drv(full, "install", "nvidia", "--kernel", "closed", "--dry-run")
    p = plan(res)
    assert any("--no-install-recommends nvidia-kernel-dkms nvidia-driver-cuda" in l for l in p)
    assert "nvidia-kernel-open-dkms" not in res.stdout
    assert "build-essential" in res.stdout
    assert "alias for the open ones" in res.stderr
    other = env["sysfs"].parent / "sys2"
    make_sysfs(other, nvidia_display_cards())
    res = drv({**env, "sysfs": other}, "install", "nvidia", "--kernel", "closed",
              "--branch", "595", "--dry-run")
    assert any(l.endswith("apt-get install -y nvidia-driver nvidia-kernel-dkms") for l in plan(res))
    assert "alias for the open ones" not in res.stderr


def test_branch_adds_pinning_and_removes_other(full):
    res = drv(full, "install", "nvidia", "--branch", "620", "--dry-run")
    p = plan(res)
    assert "apt-get install -y nvidia-driver-pinning-620" in p
    assert idx(p, "apt-get update") < idx(p, "nvidia-driver-pinning-620") < idx(p, "dkms mokutil")
    res = drv(full, "install", "nvidia", "--branch", "620", "--dry-run",
              extra={"TAOS_GPU_FAKE_INSTALLED": "nvidia-driver-pinning-595"})
    p = plan(res)
    assert idx(p, "apt-get remove -y nvidia-driver-pinning-595") < idx(p, "install -y nvidia-driver-pinning-620")


def test_bad_options(full):
    assert drv(full, "install", "nvidia", "--branch", "x1", "--dry-run").returncode == 2
    assert drv(full, "install", "nvidia", "--kernel", "weird", "--dry-run").returncode == 2
    assert drv(full, "install", "nvidia", "--nope").returncode == 2
    assert drv(full, "bogus").returncode == 2
    assert drv(full, "install", "tpu").returncode == 2


def test_arm64_refuses(full):
    res = drv(full, "install", "nvidia", "--dry-run", extra={"TAOS_GPU_ARCH": "arm64"})
    assert res.returncode == 1
    assert "amd64 only" in res.stderr and "arm64" in res.stderr
    assert "apt-get" not in res.stdout
    assert drv(full, "install", "cuda", "--dry-run",
               extra={"TAOS_GPU_ARCH": "arm64"}).returncode == 1


def test_no_nvidia_refuses_unless_force(env):
    make_sysfs(env["sysfs"], [_card(0, "0000:00:02.0", "0x8086", "i915", 1)])
    res = drv(env, "install", "nvidia", "--dry-run")
    assert res.returncode == 1 and "no NVIDIA GPU" in res.stderr and "--force" in res.stderr
    res = drv(env, "install", "nvidia", "--dry-run", "--force")
    assert res.returncode == 0
    assert any("nvidia-driver-cuda" in l for l in plan(res))


def test_secure_boot_import_in_plan(full):
    res = drv(full, "install", "nvidia", "--dry-run", extra=SB_ON)
    p = plan(res)
    assert any(l.startswith("mokutil --generate-hash=") for l in p)
    imp = [l for l in p if l.startswith("mokutil --import")]
    assert len(imp) == 1 and "--hash-file" in imp[0] and "mok.pub" in imp[0]
    assert "Perform MOK management" in res.stdout and "Enroll MOK" in res.stdout
    assert "mok-enroll-password" in res.stdout
    assert not any("generate_mok" in l for l in p)


def test_secure_boot_missing_key_generates_it(full):
    res = drv(full, "install", "nvidia", "--dry-run",
              extra={"TAOS_GPU_SB_STATE": "enabled", "TAOS_GPU_MOK_STATE": "missing"})
    p = plan(res)
    assert idx(p, "dkms generate_mok") < idx(p, "mokutil --import")


@pytest.mark.parametrize("state", ["enrolled", "pending"])
def test_secure_boot_enrolled_or_pending_does_not_reimport(full, state):
    res = drv(full, "install", "nvidia", "--dry-run",
              extra={"TAOS_GPU_SB_STATE": "enabled", "TAOS_GPU_MOK_STATE": state})
    assert res.returncode == 0
    assert "mokutil --import" not in res.stdout
    assert "Perform MOK management" not in res.stdout


def test_secure_boot_disabled_no_import(full):
    res = drv(full, "install", "nvidia", "--dry-run")
    assert "mokutil --import" not in res.stdout and "Perform MOK" not in res.stdout
    assert "Secure Boot is off" in res.stdout


def test_secure_boot_unknown_only_hints(full):
    res = drv(full, "install", "nvidia", "--dry-run", extra={"TAOS_GPU_SB_STATE": "unknown"})
    assert "could not tell" in res.stdout
    assert not any(l.startswith("mokutil --import") for l in plan(res))


def test_repo_already_configured_skips_download(full):
    lst = full["etc"] / "apt/sources.list.d/cuda-debian13-x86_64.list"
    lst.parent.mkdir(parents=True)
    lst.write_text("deb x\n")
    res = drv(full, "install", "nvidia", "--dry-run")
    p = plan(res)
    assert not any("curl" in l or "dpkg -i" in l or "sha256sum" in l for l in p)
    assert "already configured" in res.stdout
    assert "apt-get update" in p


def test_keyring_checksum_mismatch_refuses(full):
    bad = full["tmp"] / "cuda-keyring.deb"
    bad.write_bytes(b"not the real keyring")
    res = drv(full, "install", "nvidia", "--dry-run", extra={"TAOS_GPU_KEYRING_FILE": str(bad)})
    got = hashlib.sha256(b"not the real keyring").hexdigest()
    assert res.returncode == 1
    assert KEYRING_SHA in res.stderr and got in res.stderr and "mismatch" in res.stderr
    assert "dpkg -i" not in res.stdout and "apt-get" not in res.stdout


def test_keyring_checksum_match_proceeds(full):
    ok = full["tmp"] / "cuda-keyring.deb"
    ok.write_bytes(b"fixture")
    res = drv(full, "install", "nvidia", "--dry-run",
              extra={"TAOS_GPU_KEYRING_FILE": str(ok),
                     "TAOS_GPU_KEYRING_SHA256": hashlib.sha256(b"fixture").hexdigest()})
    assert res.returncode == 0, res.stderr
    assert f"dpkg -i {ok}" in plan(res)
    assert not any("curl" in l for l in plan(res))


def test_non_debian13_refused(full):
    (full["etc"] / "os-release").write_text('ID=debian\nVERSION_ID="12"\n')
    res = drv(full, "install", "nvidia", "--dry-run")
    assert res.returncode == 1 and "Debian 13" in res.stderr
    (full["etc"] / "os-release").write_text('ID=debian\nVERSION_ID="13"\n')
    assert drv(full, "install", "nvidia", "--dry-run").returncode == 0


def test_cuda_plan(full):
    res = drv(full, "install", "cuda", "--dry-run")
    p = plan(res)
    assert "apt-get install -y cuda-toolkit" in p and idx(p, "dpkg -i") < idx(p, "cuda-toolkit")
    res = drv(full, "install", "cuda", "--version", "13-5", "--dry-run")
    assert "apt-get install -y cuda-toolkit-13-5" in plan(res)
    assert drv(full, "install", "cuda", "--version", "x", "--dry-run").returncode == 2


def test_remove_plan(full):
    res = drv(full, "remove", "nvidia", "--dry-run")
    assert res.returncode == 0, res.stderr
    p = plan(res)
    purge = [l for l in p if l.startswith("apt-get purge")][0]
    for pat in ("'nvidia-*'", "'libnvidia-*'", "'cuda-*'"):
        assert pat in purge
    assert idx(p, "apt-get purge") < idx(p, "apt-get autoremove") < idx(p, "taos-nvidia.list") \
        < idx(p, "taos-gpu apply")
    assert "NVIDIA_DRIVER=open" in res.stdout


def test_amd_intel_tiny(full):
    a = drv(full, "install", "amd", "--dry-run")
    assert "apt-get install -y firmware-amd-graphics" in plan(a)
    assert "in-kernel amdgpu" in a.stdout and "ROCm" in a.stdout
    i = drv(full, "install", "intel", "--dry-run")
    assert "apt-get install -y firmware-intel-graphics" in plan(i)
    assert "i915 or xe" in i.stdout


def test_status_json_and_text(full):
    res = drv(full, "status", "--json",
              extra={"TAOS_GPU_FAKE_INSTALLED": "nvidia-driver-cuda=620.70.06-1"})
    assert res.returncode == 0, res.stderr
    data = {v["vendor"]: v for v in json.loads(res.stdout)["vendors"]}
    nv = data["NVIDIA"]
    assert nv["gpus"] == [RTX3090, RTX3060] and nv["bound_drivers"] == ["nouveau"]
    assert nv["choice"] is None and nv["official"]["installed"] is True
    assert nv["official"]["version"] == "620.70.06-1"
    assert nv["official"]["repo_configured"] is False
    assert nv["official"]["secure_boot"] == "disabled"
    assert nv["official"]["dkms_key"] == "not_required"
    intel = data["Intel"]["official"]
    assert intel["kind"] == "in-kernel" and intel["firmware_installed"] is False
    for shell in SHELLS:
        out = drv(full, "status", shell=shell, extra=SB_ON).stdout
        assert "NVIDIA: 2 GPU(s)" in out and "Secure Boot: enabled" in out
        assert "not enrolled" in out and "nothing to install" in out
    out = drv(full, "status", extra={**SB_ON, "TAOS_GPU_MOK_STATE": "pending"}).stdout
    assert "enrollment pending" in out


def test_status_reflects_conf(full):
    (full["etc"] / "taos").mkdir()
    (full["etc"] / "taos/gpu.conf").write_text(
        "DISPLAY_GPU=auto\nNVIDIA_DRIVER=official\nNVIDIA_KERNEL=open\nNVIDIA_BRANCH=620\n")
    nv = [v for v in json.loads(drv(full, "status", "--json").stdout)["vendors"]
          if v["vendor"] == "NVIDIA"][0]
    assert nv["choice"] == "official"
    assert nv["official"]["kernel"] == "open" and nv["official"]["branch"] == "620"


def modprobe_after(env, cards, nvidia_driver):
    make_sysfs(env["sysfs"], cards)
    (env["etc"] / "taos").mkdir(exist_ok=True)
    conf = "DISPLAY_GPU=auto\n"
    if nvidia_driver:
        conf += f"NVIDIA_DRIVER={nvidia_driver}\n"
    (env["etc"] / "taos/gpu.conf").write_text(conf)
    e = {"PATH": os.environ["PATH"], "SYSFS_ROOT": str(env["sysfs"]),
         "ETC_ROOT": str(env["etc"]), "TAOS_GPU_NO_APPLY": "1"}
    res = subprocess.run(["sh", str(CLI), "apply"], env=e, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return files(env)["mod"].read_text()


@pytest.mark.parametrize("choice", [None, "open", "official"])
def test_apply_compute_only_always_blacklists_nouveau(env, choice):
    mod = modprobe_after(env, default_cards(), choice)
    assert "blacklist nouveau" in mod and "options nvidia-drm modeset=0" in mod


def test_apply_nvidia_display_open_is_untouched(env):
    mod = modprobe_after(env, nvidia_display_cards(), "open")
    assert "blacklist nouveau" not in mod and "nvidia-drm" not in mod
    assert all(l.startswith("#") for l in mod.strip().splitlines())


def test_apply_nvidia_display_official(env):
    mod = modprobe_after(env, nvidia_display_cards(), "official")
    assert "blacklist nouveau" in mod and "options nvidia-drm modeset=1" in mod


def test_apply_nvidia_display_unset_is_legacy(env):
    mod = modprobe_after(env, nvidia_display_cards(), None)
    assert "options nvidia-drm modeset=1" in mod and "blacklist nouveau" not in mod


def test_gpu_conf_keeps_display_gpu_through_driver_state(full):
    e = {"PATH": os.environ["PATH"], "SYSFS_ROOT": str(full["sysfs"]),
         "ETC_ROOT": str(full["etc"]), "TAOS_GPU_NO_APPLY": "1"}

    def cli(*a):
        return subprocess.run(["sh", str(CLI), *a], env=e, capture_output=True, text=True)

    assert cli("set-display", "0000:00:02.0").returncode == 0
    conf = full["etc"] / "taos/gpu.conf"
    assert conf.read_text() == "DISPLAY_GPU=0000:00:02.0\n"
    assert cli("driver", "set", "nvidia", "open").returncode == 0
    assert conf.read_text() == "DISPLAY_GPU=0000:00:02.0\nNVIDIA_DRIVER=open\n"
    assert cli("set-display", RTX3090).returncode == 0
    assert conf.read_text() == f"DISPLAY_GPU={RTX3090}\nNVIDIA_DRIVER=open\n"
    assert cli("set-display", "auto").returncode == 0
    assert conf.read_text() == "DISPLAY_GPU=auto\nNVIDIA_DRIVER=open\n"


def test_install_with_stub_tools_records_choice_and_keeps_display(full, tmp_path):
    """Real (non dry-run) install with every external command stubbed out."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("apt-get", "dpkg", "curl", "dkms", "update-initramfs"):
        stub = bindir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n')
        stub.chmod(0o755)
    (full["etc"] / "taos").mkdir()
    (full["etc"] / "taos/gpu.conf").write_text("DISPLAY_GPU=0000:00:02.0\n")
    kr = tmp_path / "kr.deb"
    kr.write_bytes(b"fixture")
    e = {"PATH": f"{bindir}:{os.environ['PATH']}", "SYSFS_ROOT": str(full["sysfs"]),
         "ETC_ROOT": str(full["etc"]), "TAOS_GPU_NO_APPLY": "1", "TAOS_GPU_ARCH": "amd64",
         "TAOS_GPU_SB_STATE": "disabled", "TAOS_GPU_KEYRING_FILE": str(kr),
         "TAOS_GPU_FAKE_INSTALLED": "",
         "TAOS_GPU_KEYRING_SHA256": hashlib.sha256(b"fixture").hexdigest()}
    res = subprocess.run(["sh", str(CLI), "driver", "install", "nvidia", "--branch", "620"],
                         env=e, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    conf = (full["etc"] / "taos/gpu.conf").read_text()
    assert conf == ("DISPLAY_GPU=0000:00:02.0\nNVIDIA_DRIVER=official\n"
                    "NVIDIA_KERNEL=open\nNVIDIA_BRANCH=620\n")
    calls = log.read_text()
    assert "apt-get install -y --no-install-recommends nvidia-kernel-open-dkms nvidia-driver-cuda" in calls
    # the stubbed keyring deb dropped no sources file: the fallback file is written
    lst = (full["etc"] / "apt/sources.list.d/taos-nvidia.list").read_text()
    assert "signed-by=/usr/share/keyrings/cuda-archive-keyring.gpg" in lst
    assert "compute/cuda/repos/debian13/x86_64/ /" in lst
    # second run: repo is configured now, no re-download or keyring install
    log.write_text("")
    e2 = {k: v for k, v in e.items() if k != "TAOS_GPU_KEYRING_FILE"}
    res = subprocess.run(["sh", str(CLI), "driver", "install", "nvidia"],
                         env=e2, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "curl" not in log.read_text() and "dpkg -i" not in log.read_text()
    # remove goes back to the open-source choice and keeps DISPLAY_GPU
    res = subprocess.run(["sh", str(CLI), "driver", "remove", "nvidia"],
                         env=e2, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert (full["etc"] / "taos/gpu.conf").read_text() == \
        "DISPLAY_GPU=0000:00:02.0\nNVIDIA_DRIVER=open\n"
    assert not (full["etc"] / "apt/sources.list.d/taos-nvidia.list").exists()


def test_setup_sh_step4_text():
    text = SETUP.read_text()
    assert "Step 4: GPU drivers" in text
    assert "taos-gpu driver install nvidia" in text and "TAOS_NVIDIA_DRIVER" in text
    assert text.rstrip().endswith("taos-gpu driver status")
    assert text.index("Step 3") < text.index("Step 4")


def step4_calls(tmp_path, shell, nvidia_driver, has_nvidia=True):
    text = SETUP.read_text()
    script = tmp_path / "step4.sh"
    script.write_text("set -eu\n" + text[text.index("# Step 4"):])
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    log.write_text("")
    listing = "0000:01:00.0  NVIDIA  nvidia  discrete  boot_vga=0  connected=-" if has_nvidia else ""
    stub = bindir / "taos-gpu"
    stub.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n'
                    f'[ "$1" = list ] && echo "{listing}"\nexit 0\n')
    stub.chmod(0o755)
    e = {"PATH": f"{bindir}:{os.environ['PATH']}"}
    if nvidia_driver is not None:
        e["TAOS_NVIDIA_DRIVER"] = nvidia_driver
    res = subprocess.run([shell, str(script)], env=e, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return log.read_text().splitlines()


@pytest.mark.parametrize("shell", SHELLS)
def test_setup_step4_behaviour(tmp_path, shell):
    calls = step4_calls(tmp_path, shell, None)
    assert "driver install nvidia" in calls and calls[-1] == "driver status"
    calls = step4_calls(tmp_path, shell, "open")
    assert "driver install nvidia" not in calls and "driver set nvidia open" in calls
    assert calls[-1] == "driver status"
    calls = step4_calls(tmp_path, shell, "skip")
    assert not any(c.startswith(("driver install", "driver set")) for c in calls)
    assert calls[-1] == "driver status"
    assert step4_calls(tmp_path, shell, None, has_nvidia=False) == ["list", "driver status"]


def test_scripts_parse_everywhere():
    for shell in SHELLS:
        for script in (CLI, SETUP):
            res = subprocess.run([shell, "-n", str(script)], capture_output=True, text=True)
            assert res.returncode == 0, res.stderr


def test_driver_help_and_no_em_dashes():
    out = subprocess.run(["sh", str(CLI), "driver", "--help"], capture_output=True, text=True)
    assert out.returncode == 0 and "install nvidia" in out.stdout
    for p in [CLI, SETUP, REPO / "distro/README.md", Path(__file__),
              REPO / "changelog.d/distro-taos-gpu-driver.md", *(PKG / "DEBIAN").iterdir()]:
        assert chr(0x2014) not in p.read_text(), p


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not installed")
def test_package_still_builds(tmp_path: Path):
    deb = tmp_path / "taos-gpu.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(PKG), str(deb)],
                   check=True, capture_output=True)
    info = subprocess.run(["dpkg-deb", "-I", str(deb)], check=True,
                          capture_output=True, text=True).stdout
    assert "Version: 0.2.0" in info
    listing = subprocess.run(["dpkg-deb", "-c", str(deb)], check=True,
                             capture_output=True, text=True).stdout
    assert "usr/bin/taos-gpu" in listing
