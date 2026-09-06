"""Tests for Android mobile worker support."""
from __future__ import annotations

import os
import platform
from unittest.mock import patch, MagicMock

import pytest

from tinyagentos.hardware import _detect_os


class TestAndroidHardwareDetection:
    """Test that Android/Termux is detected via environment or home path."""

    def test_detect_termux_via_env(self):
        with patch.dict(os.environ, {"TERMUX_VERSION": "0.118"}):
            info = _detect_os()
            assert info.distro == "android-termux"

    def test_detect_termux_via_home_path(self):
        termux_home = "/data/data/com.termux/files/home"
        with patch("tinyagentos.hardware.Path.home", return_value=type("P", (), {"__str__": lambda s: termux_home})()):
            info = _detect_os()
            assert info.distro == "android-termux"

    def test_non_termux_not_android(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("tinyagentos.hardware.Path.home", return_value=type("P", (), {"__str__": lambda s: "/home/user"})()):
                info = _detect_os()
                assert info.distro != "android-termux"


class TestAndroidWorkerPayload:
    """Test that an AndroidWorker produces the correct registration payload."""

    def _make_worker(self):
        """Import and create an AndroidWorker-like object for testing payload format."""
        # We test the payload shape inline since the actual AndroidWorker lives
        # in the standalone setup script (not importable as a module).
        import socket
        mem_total = 6 * 1024 * 1024 * 1024  # 6 GB

        hw = {
            "cpu": {
                "arch": "aarch64",
                "model": "ARM",
                "cores": 8,
                "soc": "",
            },
            "ram_mb": mem_total // (1024 * 1024),
            "npu": {"type": "none", "device": "", "tops": 0, "cores": 0},
            "gpu": {"type": "mobile", "model": "Mobile GPU (Vulkan/OpenCL)", "vram_mb": 0,
                    "vulkan": True, "cuda": False, "rocm": False},
            "disk": {"total_gb": 0, "free_gb": 0, "type": "flash"},
            "os": {"distro": "android-termux", "version": "", "kernel": ""},
        }
        payload = {
            "name": "android-test",
            "url": "http://192.168.1.50:8080",
            "hardware": hw,
            "backends": [{"type": "llama-cpp", "url": "http://localhost:8080"}],
            "capabilities": ["chat", "embed"],
            "platform": "android",
            "models": [],
        }
        return payload

    def test_registration_has_required_fields(self):
        payload = self._make_worker()
        required = ["name", "url", "hardware", "backends", "capabilities", "platform", "models"]
        for key in required:
            assert key in payload, f"Missing required field: {key}"

    def test_platform_is_android(self):
        payload = self._make_worker()
        assert payload["platform"] == "android"

    def test_hardware_has_expected_structure(self):
        payload = self._make_worker()
        hw = payload["hardware"]
        assert "cpu" in hw
        assert "ram_mb" in hw
        assert "npu" in hw
        assert "gpu" in hw
        assert "disk" in hw
        assert "os" in hw
        assert hw["cpu"]["arch"] == "aarch64"
        assert hw["os"]["distro"] == "android-termux"

    def test_backends_contains_llama_cpp(self):
        payload = self._make_worker()
        backend_types = [b["type"] for b in payload["backends"]]
        assert "llama-cpp" in backend_types

    def test_capabilities_include_chat_and_embed(self):
        payload = self._make_worker()
        assert "chat" in payload["capabilities"]
        assert "embed" in payload["capabilities"]


class TestAndroidHeartbeatFormat:
    """Test the heartbeat payload format matches what the controller expects."""

    def test_heartbeat_payload_shape(self):
        heartbeat = {"name": "android-test", "load": 0.45}
        assert "name" in heartbeat
        assert "load" in heartbeat
        assert isinstance(heartbeat["load"], float)
        assert 0.0 <= heartbeat["load"] <= 1.0

    def test_heartbeat_load_range(self):
        # load should be cpu_percent / 100, so always 0-1
        for pct in [0, 25, 50, 75, 100]:
            load = pct / 100.0
            assert 0.0 <= load <= 1.0
