import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INSTALL = REPO_ROOT / "native" / "install-desktop.sh"
RULE_SRC = REPO_ROOT / "native" / "udev" / "71-taos-mesh-radios.rules"
PHONE_RULE = REPO_ROOT / "packaging" / "postmarketos" / "71-taos-mesh-radios.rules"
RULE_NAME = "71-taos-mesh-radios.rules"
# installs before 73-seat-late; assert lexically in the install test
SEAT_LATE = "73-seat-late"

PIECES = [
    "taos-bar", "taos-overview", "taos-dock", "taos-window", "taos-display",
    "taos-theme", "taos-launcher", "taos-search", "taos-frame",
    "taos-notifyd", "taos-appgroups",
]
APPS = [
    "taos-dashboard", "taos-notifications", "taos-decisions", "taos-settings",
    "taos-agents", "taos-messages",
]


def _install_script() -> Path:
    return Path(os.environ.get("TAOS_UDEV_INSTALL_SCRIPT", DEFAULT_INSTALL))


def _stub_exe(directory: Path, name: str) -> None:
    p = directory / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)


def _stub_sway(directory: Path) -> Path:
    p = directory / "sway"
    p.write_text("#!/bin/sh\necho \"Loading config\"\nexit 0\n")
    p.chmod(0o755)
    return p


def _build_stubs(tmp_path: Path):
    """A minimal fake build tree and install root that install-desktop.sh can
    run against: sway stub, shell pieces, apps, base sway config."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "etc" / "taos" / "sway").mkdir(parents=True)
    (root / "etc" / "taos" / "sway" / "config").write_text(
        'bindsym $mod+space floating toggle\n'
    )
    shell_build = tmp_path / "shell-build"
    shell_build.mkdir()
    for name in PIECES:
        _stub_exe(shell_build, name)
    apps_build = tmp_path / "apps-build"
    apps_build.mkdir()
    for name in APPS:
        _stub_exe(apps_build, name)
    return root, shell_build, apps_build, _stub_sway(tmp_path)


def _run_install(script: Path, root: Path, shell_build: Path, apps_build: Path,
                 sway: Path, extra: list[str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["TAOS_VALIDATE_RENDER_NODE"] = "/dev/null"  # no GPU render node in CI
    cmd = ["sh", str(script), "--sway", str(sway),
           "--shell-build", str(shell_build), "--apps-build", str(apps_build),
           "--root", str(root)]
    if extra:
        cmd.extend(extra)
    return subprocess.run(cmd, env=env, check=False, capture_output=True, text=True)


class TestUdevMeshRadioRuleInstall:
    def test_rule_installed_with_right_name_mode_and_content(self, tmp_path: Path) -> None:
        script = _install_script()
        root, shell_build, apps_build, sway = _build_stubs(tmp_path)
        result = _run_install(script, root, shell_build, apps_build, sway)
        assert result.returncode == 0, f"install-desktop.sh failed:\n{result.stderr}"
        rule = root / "etc" / "udev" / "rules.d" / RULE_NAME
        assert rule.exists(), f"{rule} was not installed"
        mode = stat.S_IMODE(rule.stat().st_mode)
        assert mode == 0o644, f"expected mode 0644, got {oct(mode)}"
        assert rule.read_text() == RULE_SRC.read_text(), "installed rule content differs from source"
        assert RULE_NAME < SEAT_LATE, f"{RULE_NAME} must sort before {SEAT_LATE}"

    def test_rule_removed_on_uninstall(self, tmp_path: Path) -> None:
        script = _install_script()
        root, shell_build, apps_build, sway = _build_stubs(tmp_path)
        result = _run_install(script, root, shell_build, apps_build, sway)
        assert result.returncode == 0, f"install-desktop.sh failed:\n{result.stderr}"
        rule = root / "etc" / "udev" / "rules.d" / RULE_NAME
        assert rule.exists()
        result = subprocess.run(
            ["sh", str(script), "--uninstall", "--root", str(root)],
            env={**os.environ, "TAOS_VALIDATE_RENDER_NODE": "/dev/null"},
            check=False, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"uninstall failed:\n{result.stderr}"
        assert "removed" in result.stdout, result.stdout
        assert not rule.exists(), f"{rule} was not removed by uninstall"

    def test_uninstall_is_idempotent_when_rule_absent(self, tmp_path: Path) -> None:
        script = _install_script()
        root, shell_build, apps_build, sway = _build_stubs(tmp_path)
        rule = root / "etc" / "udev" / "rules.d" / RULE_NAME
        assert not rule.exists()
        result = subprocess.run(
            ["sh", str(script), "--uninstall", "--root", str(root)],
            env={**os.environ, "TAOS_VALIDATE_RENDER_NODE": "/dev/null"},
            check=False, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"uninstall failed:\n{result.stderr}"
        assert "absent" in result.stdout, result.stdout
        assert not rule.exists()

    def test_uninstall_requires_root_without_root_flag(self, tmp_path: Path) -> None:
        script = _install_script()
        if os.geteuid() == 0:
            pytest.skip("running as root: no non-root guard to exercise")
        result = subprocess.run(
            ["sh", str(script), "--uninstall"],
            env={**os.environ, "TAOS_VALIDATE_RENDER_NODE": "/dev/null"},
            check=False, capture_output=True, text=True,
        )
        assert result.returncode != 0, "uninstall without --root as non-root should fail"

    def test_phone_rule_is_identical_to_desktop_source(self) -> None:
        assert PHONE_RULE.exists(), "packaging/postmarketos rule missing"
        assert PHONE_RULE.read_bytes() == RULE_SRC.read_bytes(), "phone rule differs from desktop source"
        assert stat.S_IMODE(PHONE_RULE.stat().st_mode) == 0o644

    def test_rule_vendors_covered(self) -> None:
        text = RULE_SRC.read_text()
        for vendor in ("303a", "10c4", "1a86", "239a", "2886", "1915"):
            assert f'ATTRS{{idVendor}}=="{vendor}"' in text, f"vendor {vendor} missing"
        assert 'ATTRS{idProduct}=="ea60"' in text  # Silicon Labs CP210x pinning
        assert text.count('TAG+="uaccess"') == 6


def test_udev_rule_syntax_verify() -> None:
    udevadm = shutil.which("udevadm")
    if not udevadm:
        pytest.skip("udevadm not available")
    result = subprocess.run(
        [udevadm, "verify", str(RULE_SRC), str(PHONE_RULE)],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"udevadm verify failed:\n{result.stderr}"
