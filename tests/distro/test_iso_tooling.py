#!/usr/bin/env python3
"""Tests for taOS distro ISO tooling."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import pytest
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent.parent
ISO_DIR = REPO_ROOT / "distro" / "iso"


class TestPreseedCfg:
    """Tests for preseed.cfg."""

    def test_preseed_exists(self):
        assert (ISO_DIR / "preseed.cfg").exists(), "preseed.cfg should exist"

    def test_no_partman_keys(self):
        """preseed.cfg must not contain any partman keys."""
        content = (ISO_DIR / "preseed.cfg").read_text()
        partman_lines = [line for line in content.splitlines() if re.match(r"^d-i\s+partman", line)]
        assert not partman_lines, f"Found partman keys: {partman_lines}"

    def test_no_passwd_or_hostname_keys(self):
        """preseed.cfg must not contain passwd/ or netcfg/get_hostname keys."""
        content = (ISO_DIR / "preseed.cfg").read_text()
        forbidden = ["passwd/", "netcfg/get_hostname"]
        for f in forbidden:
            assert f not in content, f"Found forbidden key: {f}"

    def test_tasksel_no_desktop(self):
        """tasksel line must not contain desktop tasks."""
        content = (ISO_DIR / "preseed.cfg").read_text()
        tasksel_lines = [line for line in content.splitlines() if "tasksel/first" in line]
        assert tasksel_lines, "tasksel/first line should exist"
        for line in tasksel_lines:
            forbidden_tasks = ["desktop", "gnome", "kde", "xfce"]
            for task in forbidden_tasks:
                assert task not in line.lower(), f"Found desktop task '{task}' in: {line}"

    def test_pkgsel_includes_curl_and_sudo(self):
        """pkgsel/include must contain curl and sudo."""
        content = (ISO_DIR / "preseed.cfg").read_text()
        pkgsel_lines = [line for line in content.splitlines() if "pkgsel/include" in line]
        assert pkgsel_lines, "pkgsel/include line should exist"
        pkgsel_line = pkgsel_lines[0]
        assert "curl" in pkgsel_line, "pkgsel/include should contain curl"
        assert "sudo" in pkgsel_line, "pkgsel/include should contain sudo"

    def test_late_command_enables_firstboot(self):
        """late_command must enable taos-firstboot.service."""
        content = (ISO_DIR / "preseed.cfg").read_text()
        assert "taos-firstboot.service" in content, "late_command should reference taos-firstboot.service"
        assert "systemctl enable" in content, "late_command should enable the service"


class TestFirstBootService:
    """Tests for taos-firstboot.service."""

    def test_service_exists(self):
        assert (ISO_DIR / "taos-firstboot.service").exists(), "taos-firstboot.service should exist"

    def test_service_has_condition_path_exists(self):
        content = (ISO_DIR / "taos-firstboot.service").read_text()
        assert "ConditionPathExists=!/var/lib/taos/firstboot.done" in content

    def test_service_has_network_online_ordering(self):
        content = (ISO_DIR / "taos-firstboot.service").read_text()
        assert "After=network-online.target" in content
        assert "Wants=network-online.target" in content

    def test_service_wanted_by_multi_user(self):
        content = (ISO_DIR / "taos-firstboot.service").read_text()
        assert "WantedBy=multi-user.target" in content


class TestFirstBootScript:
    """Tests for taos-firstboot.sh."""

    def test_script_exists(self):
        assert (ISO_DIR / "taos-firstboot.sh").exists(), "taos-firstboot.sh should exist"

    def test_script_executable(self):
        assert os.access(ISO_DIR / "taos-firstboot.sh", os.X_OK), "taos-firstboot.sh should be executable"

    def _run_firstboot(self, tmp_path, install_sh_exit_code=0):
        """Run taos-firstboot.sh with PATH stubs."""
        # Create fake install.sh at the expected location
        fake_install_dir = tmp_path / "usr" / "local" / "share" / "taos"
        fake_install_dir.mkdir(parents=True, exist_ok=True)
        fake_install = fake_install_dir / "install.sh"
        fake_install.write_text(f"#!/bin/bash\nexit {install_sh_exit_code}\n")
        fake_install.chmod(0o755)

        # Create fake systemctl
        fake_systemctl = tmp_path / "systemctl"
        fake_systemctl.write_text("#!/bin/bash\necho \"systemctl called with: $@\"\nexit 0\n")
        fake_systemctl.chmod(0o755)

        # Create fake runuser
        fake_runuser = tmp_path / "runuser"
        fake_runuser.write_text("#!/bin/bash\necho \"runuser called with: $@\"\nexit 0\n")
        fake_runuser.chmod(0o755)

        # Create fake getent
        fake_getent = tmp_path / "getent"
        fake_getent.write_text("#!/bin/bash\nif [ \"$1\" = \"passwd\" ] && [ \"$2\" = \"1000\" ]; then echo \"testuser:x:1000:1000::/home/testuser:/bin/bash\"; fi\nexit 0\n")
        fake_getent.chmod(0o755)

        # Set up environment
        env = os.environ.copy()
        env["PATH"] = f"{tmp_path}:{env['PATH']}"
        env["TAOS_FIRSTBOOT_ROOT"] = str(tmp_path)
        env["TAOS_DESKTOP"] = "local"

        # Run the script
        result = subprocess.run(
            [str(ISO_DIR / "taos-firstboot.sh")],
            env=env,
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        return result

    def test_success_writes_done_and_disables(self, tmp_path):
        """On success: writes firstboot.done and calls systemctl disable."""
        result = self._run_firstboot(tmp_path, install_sh_exit_code=0)

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        done_file = tmp_path / "var" / "lib" / "taos" / "firstboot.done"
        assert done_file.exists(), "firstboot.done should be created on success"

        # Check that systemctl disable was called
        assert "systemctl disable taos-firstboot.service" in result.stdout or "disable" in result.stdout

    def test_failure_exits_nonzero_no_done_marker(self, tmp_path):
        """On failure: exits non-zero and writes no done marker."""
        result = self._run_firstboot(tmp_path, install_sh_exit_code=1)

        assert result.returncode != 0, "Script should exit non-zero on failure"

        done_file = tmp_path / "var" / "lib" / "taos" / "firstboot.done"
        assert not done_file.exists(), "firstboot.done should NOT be created on failure"


class TestBuildIsoScript:
    """Tests for build-iso.sh."""

    def test_script_exists(self):
        assert (ISO_DIR / "build-iso.sh").exists(), "build-iso.sh should exist"

    def test_script_executable(self):
        assert os.access(ISO_DIR / "build-iso.sh", os.X_OK), "build-iso.sh should be executable"

    def test_bash_syntax(self):
        """build-iso.sh should pass bash -n."""
        result = subprocess.run(
            ["bash", "-n", str(ISO_DIR / "build-iso.sh")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed: {result.stderr}"

    def test_exits_2_when_xorriso_missing(self):
        """With xorriso absent from PATH, exits 2 and names xorriso."""
        env = os.environ.copy()
        # Remove xorriso from PATH by using a minimal PATH
        env["PATH"] = "/usr/bin:/bin"
        result = subprocess.run(
            [str(ISO_DIR / "build-iso.sh"), "--help"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2, f"Expected exit code 2, got {result.returncode}"
        assert "xorriso" in result.stderr.lower(), f"Error message should mention xorriso: {result.stderr}"

    def test_builds_iso_with_xorriso(self, tmp_path):
        """If xorriso is installed, build a fake netinst and verify output."""
        # Check if xorriso is available
        if not shutil.which("xorriso"):
            pytest.skip("xorriso not installed")

        # Create a minimal fake netinst ISO
        fake_iso = tmp_path / "fake-netinst.iso"
        tree = tmp_path / "tree"
        tree.mkdir()

        # Create minimal ISO structure
        install_amd = tree / "install.amd"
        install_amd.mkdir(parents=True)
        initrd = install_amd / "initrd"
        initrd.mkdir()
        # Create empty cpio for initrd.gz
        empty_cpio = tmp_path / "empty.cpio"
        subprocess.run(["cpio", "-H", "newc", "-o", "-F", str(empty_cpio)], input=b"", check=True)
        subprocess.run(["gzip", "-c", str(empty_cpio)], stdout=open(install_amd / "initrd.gz", "wb"), check=True)

        # Create md5sum.txt
        (tree / "md5sum.txt").write_text("")

        # Build the fake netinst ISO
        subprocess.run([
            "xorriso", "-as", "mkisofs",
            "-o", str(fake_iso),
            "-r", "-J",
            str(tree)
        ], check=True)

        # Run build-iso.sh
        out_iso = tmp_path / "taos-amd64.iso"
        result = subprocess.run([
            str(ISO_DIR / "build-iso.sh"),
            "--netinst", str(fake_iso),
            "--out", str(out_iso)
        ], capture_output=True, text=True)

        assert result.returncode == 0, f"build-iso.sh failed: {result.stderr}"

        # Verify output ISO contains /taos/install.sh
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()
        subprocess.run([
            "xorriso", "-osirrox", "on", "-indev", str(out_iso), "-extract", "/", str(extract_dir)
        ], check=True)

        assert (extract_dir / "taos" / "install.sh").exists(), "Output ISO should contain /taos/install.sh"

        # Verify initrd.gz contains preseed.cfg in cpio
        initrd_gz = extract_dir / "install.amd" / "initrd.gz"
        assert initrd_gz.exists(), "initrd.gz should exist in output ISO"
        # Extract and check cpio contents
        initrd_extract = tmp_path / "initrd_extract"
        initrd_extract.mkdir()
        subprocess.run(["gunzip", "-c", str(initrd_gz)], stdout=open(initrd_extract / "initrd", "wb"), check=True)
        subprocess.run(["cpio", "-id", "-H", "newc", "-F", str(initrd_extract / "initrd")], cwd=initrd_extract, check=True)
        assert (initrd_extract / "preseed.cfg").exists(), "preseed.cfg should be in initrd.gz cpio archive"
