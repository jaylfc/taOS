"""RED tests for taos-storage-helper TOCTOU re-validation and install path."""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestStorageHelperTOCTOU:
    """Tests for the taos-storage-helper re-validation (TOCTOU fix)."""

    @pytest.fixture
    def helper_script(self):
        """Path to the taos-storage-helper script."""
        return Path("/tmp/exec-tsk-2urrcn/scripts/taos-storage-helper")

    def test_helper_validates_disk_before_write_RED(self, helper_script):
        """CURRENT BUG: helper does NOT re-validate disk before write operations.
        
        The helper should:
        1. Accept a /dev/disk/by-id path as argument
        2. Resolve it to real device
        3. Re-run the same classifier (check available)
        4. Refuse unless available
        5. Refuse if confirm string doesn't match
        6. Run wipefs -a with check=True
        7. Any failure stops
        
        Currently the helper just executes commands without re-validation.
        """
        # Read the helper script
        content = helper_script.read_text()
        
        # Check for re-validation logic
        has_revalidation = "classify" in content or "safety_class" in content or "available" in content
        has_confirm_check = "confirm" in content.lower()
        has_wipefs_check_true = "check=True" in content and "wipefs" in content
        
        # CURRENT BUG: helper lacks re-validation
        assert has_revalidation, "BUG: helper doesn't re-validate disk safety class before write"
        assert has_confirm_check, "BUG: helper doesn't check confirm string"
        assert has_wipefs_check_true, "BUG: wipefs -a doesn't use check=True"

    def test_helper_refuses_non_available_disk_RED(self, helper_script):
        """CURRENT BUG: helper doesn't check if disk is available before partition-disk."""
        content = helper_script.read_text()
        
        # The partition-disk command should verify disk is available
        # Currently it just runs wipefs -a without check
        assert "def cmd_partition_disk" in content
        # Check if cmd_partition_disk has validation
        partition_func = content[content.index("def cmd_partition_disk"):content.index("def cmd_luks_format")]
        has_validation = "available" in partition_func or "safety" in partition_func or "classify" in partition_func
        has_wipefs_check = "check=True" in partition_func and "wipefs" in partition_func
        
        assert has_validation, "BUG: cmd_partition_disk doesn't validate disk is available"
        assert has_wipefs_check, "BUG: cmd_partition_disk wipefs -a doesn't use check=True"

    def test_helper_runs_wipefs_with_check_true_GREEN(self, helper_script):
        """AFTER FIX: wipefs -a runs with check=True."""
        content = helper_script.read_text()
        
        # Find the line with wipefs -a
        lines = content.split('\n')
        wipefs_line = None
        for line in lines:
            if 'wipefs' in line and '-a' in line:
                wipefs_line = line
                break
        
        assert wipefs_line is not None, "wipefs -a not found"
        # AFTER FIX: check=True
        assert "check=True" in wipefs_line, f"FIXED: wipefs -a should use check=True, found: {wipefs_line}"
        # After fix: should be check=True


class TestInstallPath:
    """Tests for install path: helper, sudoers, /etc/taos/keys."""

    def test_installer_installs_helper_RED(self):
        """CURRENT BUG: installer doesn't install taos-storage-helper to /usr/local/bin/."""
        # Check if install.sh installs the helper
        install_sh = Path("/tmp/exec-tsk-2urrcn/install.sh")
        if install_sh.exists():
            content = install_sh.read_text()
            has_helper_install = "taos-storage-helper" in content and "/usr/local/bin" in content
            assert has_helper_install, "BUG: install.sh doesn't install taos-storage-helper to /usr/local/bin/"

    def test_installer_creates_sudoers_rule_RED(self):
        """CURRENT BUG: installer doesn't create narrowly scoped sudoers rule."""
        install_sh = Path("/tmp/exec-tsk-2urrcn/install.sh")
        if install_sh.exists():
            content = install_sh.read_text()
            has_sudoers = "sudoers" in content and "taos-storage-helper" in content
            assert has_sudoers, "BUG: install.sh doesn't create sudoers rule for taos-storage-helper"

    def test_installer_creates_keys_directory_RED(self):
        """CURRENT BUG: installer doesn't create /etc/taos/keys with 0700 root."""
        install_sh = Path("/tmp/exec-tsk-2urrcn/install.sh")
        if install_sh.exists():
            content = install_sh.read_text()
            has_keys_dir = "/etc/taos/keys" in content and "0700" in content
            assert has_keys_dir, "BUG: install.sh doesn't create /etc/taos/keys with 0700"

    def test_key_file_created_with_o_excl_RED(self):
        """CURRENT BUG: key file created with write-then-chmod instead of os.open(O_CREAT|O_EXCL, 0o400)."""
        helper_script = Path("/tmp/exec-tsk-2urrcn/scripts/taos-storage-helper")
        content = helper_script.read_text()
        
        # Check cmd_luks_format for key file creation
        if "def cmd_luks_format" in content:
            func_start = content.index("def cmd_luks_format")
            func_end = content.index("def cmd_luks_open", func_start)
            func_content = content[func_start:func_end]
            
            # CURRENT BUG: uses Path.write_bytes then os.chmod
            has_write_bytes = "write_bytes" in func_content
            has_chmod = "chmod" in func_content or "0o400" in func_content
            has_o_excl = "O_CREAT" in func_content and "O_EXCL" in func_content
            
            assert has_o_excl, "BUG: key file not created with os.open(O_CREAT|O_EXCL, 0o400)"
            # The current code uses write_bytes + chmod which is a TOCTOU


if __name__ == "__main__":
    pytest.main([__file__, "-v"])