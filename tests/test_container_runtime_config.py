"""Tests for configure_container_runtime() helper in containers.backend."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

import tinyagentos.containers.backend as _be
from tinyagentos.containers.backend import configure_container_runtime


@pytest.fixture(autouse=True)
def restore_backend():
    """Restore the global backend after each test."""
    original = _be._active_backend
    yield
    _be._active_backend = original


class TestConfigureContainerRuntimeFound:
    def test_lxc_sets_lxc_backend(self):
        with patch("tinyagentos.containers.backend.detect_runtime", return_value="lxc"):
            result = configure_container_runtime(None)
        assert result == "lxc"
        from tinyagentos.containers.lxc import LXCBackend
        assert isinstance(_be._active_backend, LXCBackend)

    def test_docker_sets_docker_backend(self):
        with patch("tinyagentos.containers.backend.detect_runtime", return_value="docker"):
            result = configure_container_runtime(None)
        assert result == "docker"
        from tinyagentos.containers.docker import DockerBackend
        assert isinstance(_be._active_backend, DockerBackend)

    def test_podman_sets_docker_backend_with_podman_binary(self):
        with patch("tinyagentos.containers.backend.detect_runtime", return_value="podman"):
            result = configure_container_runtime(None)
        assert result == "podman"
        from tinyagentos.containers.docker import DockerBackend
        assert isinstance(_be._active_backend, DockerBackend)

    def test_apple_sets_apple_backend(self, monkeypatch):
        monkeypatch.setenv("TAOS_CONTAINER_BIN", "/usr/local/bin/container")
        with patch("tinyagentos.containers.backend.detect_runtime", return_value="apple"):
            result = configure_container_runtime(None)
        assert result == "apple"
        from tinyagentos.containers.apple_backend import AppleContainerBackend
        assert isinstance(_be._active_backend, AppleContainerBackend)


class TestConfigureContainerRuntimeNone:
    def test_returns_none_when_no_runtime(self, caplog):
        with patch("tinyagentos.containers.backend.detect_runtime", return_value="none"), \
             caplog.at_level(logging.WARNING, logger="tinyagentos.containers.backend"):
            result = configure_container_runtime(None)
        assert result is None
        assert any(
            "No container backend detected (Incus / Docker / Podman / Apple)" in r.message
            for r in caplog.records
        )

    def test_does_not_set_backend_when_none(self):
        _be._active_backend = None
        with patch("tinyagentos.containers.backend.detect_runtime", return_value="none"):
            configure_container_runtime(None)
        assert _be._active_backend is None


class TestConfigureContainerRuntimeConfigOverride:
    def test_config_pin_bypasses_detect(self):
        config = MagicMock()
        config.container_runtime = "lxc"
        # detect_runtime should never be called when pinned
        with patch("tinyagentos.containers.backend.detect_runtime") as mock_detect:
            result = configure_container_runtime(config)
        mock_detect.assert_not_called()
        assert result == "lxc"

    def test_config_auto_calls_detect(self):
        config = MagicMock()
        config.container_runtime = "auto"
        with patch("tinyagentos.containers.backend.detect_runtime", return_value="docker") as mock_detect:
            result = configure_container_runtime(config)
        mock_detect.assert_called_once()
        assert result == "docker"

    def test_none_config_defaults_to_auto(self):
        with patch("tinyagentos.containers.backend.detect_runtime", return_value="lxc") as mock_detect:
            result = configure_container_runtime(None)
        mock_detect.assert_called_once()
        assert result == "lxc"
