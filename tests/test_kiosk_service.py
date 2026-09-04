"""Unit tests for kiosk-setup.sh service file generation.

Asserts that the generated taos-kiosk.service file declares the seatd dependency.
"""
from __future__ import annotations

import re

import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SYSTEMD_DIR = REPO_ROOT / "scripts" / "systemd"


class TestKioskServiceGeneration:
    def test_service_has_seatd_after(self):
        """The generated service must declare After=seatd.service."""
        service_path = (
            Path("/etc/systemd/system/taos-kiosk.service")
            if (Path("/etc/systemd/system/taos-kiosk.service").exists())
            else SYSTEMD_DIR / "taos-kiosk.service"
        )
        # Fallback: read from repo's systemd dir if deployed path not available
        if not service_path.exists():
            # Read the kiosk-setup.sh output that would be generated
            # and verify the template contains seatd
            setuppy = REPO_ROOT / "scripts" / "kiosk-setup.sh"
            assert setuppy.exists()
            # The template in the script must contain the seatd references
            content = setuppy.read_text()
            assert re.search(r"After=.*seatd\.service", content), (
                "kiosk-setup.sh template must contain After=seatd.service"
            )
            assert re.search(r"Wants=.*seatd\.service", content), (
                "kiosk-setup.sh template must contain Wants=seatd.service"
            )
            return

        service = service_path.read_text()
        assert re.search(r"After=.*seatd\.service", service), (
            f"{service_path} must contain After=seatd.service"
        )
        assert re.search(r"Wants=.*seatd\.service", service), (
            f"{service_path} must contain Wants=seatd.service"
        )

    def test_service_has_seatd_wants(self):
        """The generated service must declare Wants=seatd.service."""
        service_path = (
            Path("/etc/systemd/system/taos-kiosk.service")
            if (Path("/etc/systemd/system/taos-kiosk.service").exists())
            else SYSTEMD_DIR / "taos-kiosk.service"
        )
        if not service_path.exists():
            setuppy = REPO_ROOT / "scripts" / "kiosk-setup.sh"
            content = setuppy.read_text()
            assert re.search(r"After=.*seatd\.service", content)
            assert re.search(r"Wants=.*seatd\.service", content)
            return

        service = service_path.read_text()
        assert re.search(r"Wants=seatd\.service", service), (
            f"{service_path} must contain Wants=seatd.service"
        )