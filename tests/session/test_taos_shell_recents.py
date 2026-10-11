"""Test for taos-shell recents arm that opens the taOS app switcher.

This test verifies that the recents) arm of taos-shell properly opens the
taOS desktop switcher on workspace 1 then F14, exactly like the home button
does with workspace 1 then F13.
"""
import pathlib
import subprocess
import tempfile
import os

ROOT = pathlib.Path(__file__).resolve().parents[2]

def test_taos_shell_recents_opens_switcher():
    """Test that recents) arm opens the taOS app switcher."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create stub executables in a directory
        stubs_dir = pathlib.Path(tmpdir) / "bin"
        stubs_dir.mkdir()
        
        # Create log file
        log_file = pathlib.Path(tmpdir) / "test.log"
        
        # Create stub scripts that append to the log file
        for cmd in ["swaymsg", "wtype", "chromium"]:
            stub_content = f'''#!/bin/sh
 echo "$0 $@" >> "{log_file}"
 '''
            stub_path = stubs_dir / cmd
            stub_path.write_text(stub_content)
            stub_path.chmod(0o755)
        
        # Create environment
        env = os.environ.copy()
        env["PATH"] = str(stubs_dir) + ":" + env["PATH"]
        env["XDG_RUNTIME_DIR"] = tmpdir
        
        # Run taos-shell recents
        result = subprocess.run(
            ["sh", ROOT / "pmos/kiosk/bin/taos-shell", "recents"],
            env=env,
            capture_output=True,
            text=True
        )
        
        # Read the log
        log_content = log_file.read_text()
        
        # Assertions
        assert result.returncode == 0, f"taos-shell failed with return code {result.returncode}"
        assert "-q workspace 1" in log_content, "Should call swaymsg -q workspace 1"
        
        # Find the wtype line (expecting "-k F14" exactly)
        wtype_lines = [line for line in log_content.splitlines() if "-k F14" in line]
        assert wtype_lines, f"Should call wtype -k F14. Got lines:\n{log_content}"
        
        # wtype F14 should come after swaymsg workspace 1
        lines = log_content.splitlines()
        swaymsg_index = next(i for i, line in enumerate(lines) if "-q workspace 1" in line)
        wtype_index = next(i for i, line in enumerate(lines) if "-k F14" in line)
        assert wtype_index > swaymsg_index, "wtype -k F14 should come after swaymsg -q workspace 1"
        
        # Should NOT contain chromium or workspace recents
        assert "chromium" not in log_content, "Should NOT launch chromium"
        assert "workspace recents" not in log_content, "Should NOT call swaymsg -q workspace recents"

def test_taos_shell_home_control():
    """Control test that home) arm calls wtype -k F13."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create stub executables in a directory
        stubs_dir = pathlib.Path(tmpdir) / "bin"
        stubs_dir.mkdir()
        
        # Create log file
        log_file = pathlib.Path(tmpdir) / "test.log"
        
        # Create stub scripts that append to the log file
        for cmd in ["swaymsg", "wtype", "chromium"]:
            stub_content = f'''#!/bin/sh
 echo "$0 $@" >> "{log_file}"
 '''
            stub_path = stubs_dir / cmd
            stub_path.write_text(stub_content)
            stub_path.chmod(0o755)
        
        # Create environment
        env = os.environ.copy()
        env["PATH"] = str(stubs_dir) + ":" + env["PATH"]
        env["XDG_RUNTIME_DIR"] = tmpdir
        
        # Run taos-shell home
        result = subprocess.run(
            ["sh", ROOT / "pmos/kiosk/bin/taos-shell", "home"],
            env=env,
            capture_output=True,
            text=True
        )
        
        # Read the log
        log_content = log_file.read_text()
        
        # Assertions
        assert result.returncode == 0, f"taos-shell home failed with return code {result.returncode}"
        assert "-q workspace 1" in log_content, "Should call swaymsg -q workspace 1"
        
        # Should call wtype -k F13 (expecting "-k F13" exactly)
        wtype_lines = [line for line in log_content.splitlines() if "-k F13" in line]
        assert wtype_lines, f"Should call wtype -k F13. Got lines:\n{log_content}"
