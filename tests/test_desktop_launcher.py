#!/usr/bin/env python3
"""
Tests for scripts/desktop-launcher.sh
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "desktop-launcher.sh"
STATIC_ICON_512 = REPO_ROOT / "static" / "icon-512.png"
STATIC_ICON_192 = REPO_ROOT / "static" / "icon-192.png"


def run_launcher(root: Path, args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the desktop-launcher.sh script with the given root and args.
    
    TAOS_PORT is popped from the base environment so a runner with TAOS_PORT
    set does not leak into tests that expect the default port.
    """
    test_env = os.environ.copy()
    test_env.pop("TAOS_PORT", None)
    test_env["TAOS_LAUNCHER_ROOT"] = str(root)
    if env:
        test_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        env=test_env,
    )


def create_stub_browser(bin_dir: Path, name: str) -> None:
    """Create a stub browser executable in the given bin directory."""
    stub = bin_dir / name
    stub.write_text("#!/bin/sh\necho stub\n")
    stub.chmod(0o755)


class TestDesktopLauncher:
    """Tests for the desktop-launcher.sh script."""

    def test_detect_empty_root_exits_1(self, tmp_path: Path) -> None:
        """detect with an empty root exits 1."""
        root = tmp_path / "root"
        root.mkdir(parents=True)
        result = run_launcher(root, ["detect"])
        assert result.returncode == 1
        assert "existing desktop: no" in result.stdout.strip()

    def test_detect_with_wayland_session_exits_0(self, tmp_path: Path) -> None:
        """detect with a wayland session file exits 0."""
        root = tmp_path / "root"
        sessions_dir = root / "usr" / "share" / "wayland-sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "sway.desktop").write_text("[Desktop Entry]\nName=Sway\n")
        result = run_launcher(root, ["detect"])
        assert result.returncode == 0
        assert "existing desktop: yes" in result.stdout.strip()

    def test_install_with_chromium_browser_stub(self, tmp_path: Path) -> None:
        """install with a stub chromium-browser writes correct Exec line and icons."""
        root = tmp_path / "root"
        root.mkdir(parents=True)

        # Create a temp bin dir with chromium-browser stub first in PATH
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        create_stub_browser(bin_dir, "chromium-browser")

        # PATH: stub bin dir first, then system paths
        test_path = f"{bin_dir}:/usr/bin:/bin"

        result = run_launcher(root, ["install"], env={"PATH": test_path})
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "launcher installed:" in result.stdout.strip()

        # Check .desktop file
        desktop_file = root / "usr" / "share" / "applications" / "taos.desktop"
        assert desktop_file.exists(), f"Desktop file not found at {desktop_file}"
        content = desktop_file.read_text()
        assert "Exec=chromium-browser --app=http://127.0.0.1:6969/desktop/ --start-fullscreen" in content
        assert "Icon=taos" in content
        assert "Type=Application" in content
        assert "Name=taOS" in content
        assert "Comment=Open the taOS desktop" in content
        assert "Terminal=false" in content
        assert "Categories=Utility;" in content
        assert "StartupNotify=true" in content

        # Check icon files
        icon_512 = root / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps" / "taos.png"
        icon_192 = root / "usr" / "share" / "icons" / "hicolor" / "192x192" / "apps" / "taos.png"
        assert icon_512.exists(), f"512 icon not found at {icon_512}"
        assert icon_192.exists(), f"192 icon not found at {icon_192}"

    def test_install_with_custom_port(self, tmp_path: Path) -> None:
        """install with TAOS_PORT=7000 puts port 7000 in the Exec URL."""
        root = tmp_path / "root"
        root.mkdir(parents=True)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        create_stub_browser(bin_dir, "chromium-browser")

        test_path = f"{bin_dir}:/usr/bin:/bin"

        result = run_launcher(root, ["install"], env={"PATH": test_path, "TAOS_PORT": "7000"})
        assert result.returncode == 0, f"stderr: {result.stderr}"

        desktop_file = root / "usr" / "share" / "applications" / "taos.desktop"
        content = desktop_file.read_text()
        assert "Exec=chromium-browser --app=http://127.0.0.1:7000/desktop/ --start-fullscreen" in content

    def test_install_without_browser_uses_xdg_open(self, tmp_path: Path) -> None:
        """install with no browser on PATH writes Exec=xdg-open ..."""
        # Skip if any real browser exists in /usr/bin or /bin
        for browser in ("chromium-browser", "chromium", "google-chrome-stable"):
            if shutil.which(browser, path="/usr/bin:/bin"):
                pytest.skip(f"Real {browser} found in system PATH, cannot test xdg-open fallback")

        root = tmp_path / "root"
        root.mkdir(parents=True)

        # Empty bin dir (no stubs) + system paths
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        test_path = f"{bin_dir}:/usr/bin:/bin"

        result = run_launcher(root, ["install"], env={"PATH": test_path})
        assert result.returncode == 0, f"stderr: {result.stderr}"

        desktop_file = root / "usr" / "share" / "applications" / "taos.desktop"
        content = desktop_file.read_text()
        assert "Exec=xdg-open http://127.0.0.1:6969/desktop/" in content

    def test_remove_after_install_deletes_files(self, tmp_path: Path) -> None:
        """remove after install deletes the .desktop file and both icons."""
        root = tmp_path / "root"
        root.mkdir(parents=True)

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        create_stub_browser(bin_dir, "chromium-browser")

        test_path = f"{bin_dir}:/usr/bin:/bin"

        # First install
        result = run_launcher(root, ["install"], env={"PATH": test_path})
        assert result.returncode == 0

        # Then remove
        result = run_launcher(root, ["remove"], env={"PATH": test_path})
        assert result.returncode == 0
        assert "launcher removed" in result.stdout.strip()

        # Check files are gone
        desktop_file = root / "usr" / "share" / "applications" / "taos.desktop"
        icon_512 = root / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps" / "taos.png"
        icon_192 = root / "usr" / "share" / "icons" / "hicolor" / "192x192" / "apps" / "taos.png"

        assert not desktop_file.exists()
        assert not icon_512.exists()
        assert not icon_192.exists()

    def test_remove_on_empty_root_exits_0(self, tmp_path: Path) -> None:
        """remove on an empty root exits 0 (idempotent)."""
        root = tmp_path / "root"
        root.mkdir(parents=True)

        result = run_launcher(root, ["remove"])
        assert result.returncode == 0
        assert "launcher removed" in result.stdout.strip()

    def test_unknown_argument_exits_2(self, tmp_path: Path) -> None:
        """An unknown argument exits 2 with usage to stderr."""
        root = tmp_path / "root"
        root.mkdir(parents=True)

        result = run_launcher(root, ["invalid-command"])
        assert result.returncode == 2
        assert "usage" in result.stderr.lower()


def test_run_launcher_pops_tos_port_from_base_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that run_launcher does not leak TAOS_PORT from the environment."""
    # Set TAOS_PORT in the process environment
    monkeypatch.setenv("TAOS_PORT", "9999")

    root = tmp_path / "root"
    root.mkdir(parents=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    create_stub_browser(bin_dir, "chromium-browser")

    test_path = f"{bin_dir}:/usr/bin:/bin"

    # Run without explicit TAOS_PORT - should use default 6969, not 9999
    result = run_launcher(root, ["install"], env={"PATH": test_path})
    assert result.returncode == 0, f"stderr: {result.stderr}"

    desktop_file = root / "usr" / "share" / "applications" / "taos.desktop"
    content = desktop_file.read_text()
    # Should use default port 6969, not the env's 9999
    assert "Exec=chromium-browser --app=http://127.0.0.1:6969/desktop/ --start-fullscreen" in content
    assert "7000" not in content
    assert "9999" not in content


if __name__ == "__main__":
    pytest.main([__file__, "-q"])