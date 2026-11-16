"""taos-branding package: the system identifies as taOS but keeps ID=debian."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "distro" / "packages" / "taos-branding"
APPLY = PKG / "usr" / "lib" / "taos-branding" / "apply"

DEBIAN_OS_RELEASE = """\
PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
NAME="Debian GNU/Linux"
VERSION_ID="13"
VERSION="13 (trixie)"
VERSION_CODENAME=trixie
DEBIAN_VERSION_FULL=13.7
ID=debian
HOME_URL="https://www.debian.org/"
SUPPORT_URL="https://www.debian.org/support"
BUG_REPORT_URL="https://bugs.debian.org/"
"""


def _parse(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            assert k not in out, f"duplicate key {k}"
            out[k] = v.strip('"')
    return out


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "usr/lib").mkdir(parents=True)
    (tmp_path / "etc").mkdir()
    (tmp_path / "usr/lib/os-release.debian").write_text(DEBIAN_OS_RELEASE)
    (tmp_path / "etc/motd").write_text("Debian stock motd\n")
    shutil.copytree(PKG / "usr/share/taos-branding", tmp_path / "usr/share/taos-branding")
    return tmp_path


def _apply(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(APPLY)], env={"ROOT": str(root), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True,
    )


def test_identity_is_taos_but_id_stays_debian(root: Path):
    assert _apply(root).returncode == 0
    rel = _parse((root / "usr/lib/os-release").read_text())
    assert rel["NAME"] == "taOS"
    assert rel["PRETTY_NAME"] == "taOS (Debian 13 trixie base)"
    assert rel["ID"] == "debian"
    assert rel["VARIANT_ID"] == "taos"
    # Keys that apt sources and third-party installers parse are untouched.
    assert rel["VERSION_ID"] == "13"
    assert rel["VERSION_CODENAME"] == "trixie"
    assert rel["DEBIAN_VERSION_FULL"] == "13.7"
    assert "debian.org" not in rel["HOME_URL"]


def test_point_release_flows_through_on_reapply(root: Path):
    assert _apply(root).returncode == 0
    src = root / "usr/lib/os-release.debian"
    src.write_text(DEBIAN_OS_RELEASE.replace("13.7", "13.8"))
    assert _apply(root).returncode == 0
    rel = _parse((root / "usr/lib/os-release").read_text())
    assert rel["DEBIAN_VERSION_FULL"] == "13.8"
    assert rel["NAME"] == "taOS"


def test_banner_motd_and_motd_backup(root: Path):
    assert _apply(root).returncode == 0
    assert (root / "etc/issue").read_text().startswith("taOS ")
    assert (root / "etc/issue.net").read_text().strip() == "taOS"
    assert "taOS" in (root / "etc/motd").read_text()
    assert (root / "etc/motd.debian").read_text() == "Debian stock motd\n"
    # A second run must not overwrite the saved Debian motd with ours.
    assert _apply(root).returncode == 0
    assert (root / "etc/motd.debian").read_text() == "Debian stock motd\n"


def test_missing_divert_refuses_and_writes_nothing(root: Path):
    (root / "usr/lib/os-release.debian").unlink()
    res = _apply(root)
    assert res.returncode != 0
    assert not (root / "usr/lib/os-release").exists()
    assert not (root / "etc/issue").exists()


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not installed")
def test_package_builds_with_maintainer_scripts(tmp_path: Path):
    deb = tmp_path / "taos-branding.deb"
    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "--build", str(PKG), str(deb)],
        check=True, capture_output=True,
    )
    info = subprocess.run(["dpkg-deb", "-I", str(deb)], check=True, capture_output=True, text=True).stdout
    for script in ("preinst", "postinst", "postrm", "triggers"):
        assert script in info
    preinst = (PKG / "DEBIAN/preinst").read_text()
    for path in ("/usr/lib/os-release", "/etc/issue", "/etc/issue.net"):
        assert path in preinst


def test_motd_empty_when_dynamic_motd_present(root: Path):
    (root / "etc/update-motd.d").mkdir()
    dyn = root / "etc/update-motd.d/05-taos"
    dyn.write_text("#!/bin/sh\n")
    dyn.chmod(0o755)
    assert _apply(root).returncode == 0
    assert (root / "etc/motd").read_text() == ""
    assert (root / "etc/motd.debian").read_text() == "Debian stock motd\n"
    assert _apply(root).returncode == 0
    assert (root / "etc/motd.debian").read_text() == "Debian stock motd\n"


def test_motd_static_when_dynamic_motd_not_executable(root: Path):
    (root / "etc/update-motd.d").mkdir()
    (root / "etc/update-motd.d/05-taos").write_text("#!/bin/sh\n")
    assert _apply(root).returncode == 0
    assert "taOS comes with ABSOLUTELY NO WARRANTY" in (root / "etc/motd").read_text()


def test_triggers_watch_dynamic_motd():
    assert "/etc/update-motd.d/05-taos" in (PKG / "DEBIAN/triggers").read_text()
