"""Device class probe: a handset requires BOTH kiosk and plasma units."""

import subprocess
from unittest.mock import patch
from tinyagentos import hardware


def test_desk_with_kiosk_but_no_plasma_is_not_mobile(monkeypatch):
    """A desk with only taos-kiosk.service must NOT be detected as mobile."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if cmd == ["systemctl", "cat", "taos-kiosk.service"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="# taos-kiosk.service", stderr="")
        if cmd == ["systemctl", "cat", "plasma-mobile.service"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No files found")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert hardware._detect_device_class() is None
    assert calls.count(["systemctl", "cat", "taos-kiosk.service"]) == 1
    assert calls.count(["systemctl", "cat", "plasma-mobile.service"]) == 1


def test_handset_with_both_units_is_mobile(monkeypatch):
    """A handset with both taos-kiosk.service and plasma-mobile.service is mobile."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if cmd == ["systemctl", "cat", "taos-kiosk.service"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="# taos-kiosk.service", stderr="")
        if cmd == ["systemctl", "cat", "plasma-mobile.service"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="# plasma-mobile.service", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert hardware._detect_device_class() == "mobile"
    assert calls.count(["systemctl", "cat", "taos-kiosk.service"]) == 1
    assert calls.count(["systemctl", "cat", "plasma-mobile.service"]) == 1


def test_missing_kiosk_unit_is_not_mobile(monkeypatch):
    """A machine without taos-kiosk.service must NOT be detected as mobile."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if cmd == ["systemctl", "cat", "taos-kiosk.service"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No files found")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert hardware._detect_device_class() is None
    assert calls.count(["systemctl", "cat", "taos-kiosk.service"]) == 1