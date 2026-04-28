import os
import sys
import pytest
from unittest.mock import patch
from tinyagentos.containers.backend import detect_runtime


class TestDetectRuntime:
    def test_detect_lxc(self):
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/incus" if x == "incus" else None):
            assert detect_runtime() == "lxc"

    def test_detect_docker(self):
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/docker" if x == "docker" else None):
            assert detect_runtime() == "docker"

    def test_detect_podman(self):
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/podman" if x == "podman" else None):
            assert detect_runtime() == "podman"

    def test_detect_none(self):
        with patch("shutil.which", return_value=None):
            assert detect_runtime() == "none"

    def test_prefers_lxc_over_docker(self):
        def which(cmd):
            if cmd in ("incus", "docker"):
                return f"/usr/bin/{cmd}"
            return None
        with patch("shutil.which", side_effect=which):
            assert detect_runtime() == "lxc"


class TestDetectRuntimeApple:
    def test_apple_selected_on_darwin_with_env(self):
        with patch.object(sys, "platform", "darwin"), \
             patch.dict(os.environ, {"TAOS_CONTAINER_BIN": "/x/container"}, clear=False), \
             patch("shutil.which", return_value=None):
            assert detect_runtime() == "apple"

    def test_apple_wins_over_docker_on_darwin(self):
        with patch.object(sys, "platform", "darwin"), \
             patch.dict(os.environ, {"TAOS_CONTAINER_BIN": "/x/container"}, clear=False), \
             patch("shutil.which", side_effect=lambda x: "/usr/bin/docker" if x == "docker" else None):
            assert detect_runtime() == "apple"

    def test_no_apple_without_env_var(self, monkeypatch):
        monkeypatch.delenv("TAOS_CONTAINER_BIN", raising=False)
        with patch.object(sys, "platform", "darwin"), \
             patch("shutil.which", side_effect=lambda x: "/usr/bin/docker" if x == "docker" else None):
            assert detect_runtime() == "docker"

    def test_apple_not_selected_on_linux_even_with_env(self, monkeypatch):
        monkeypatch.setenv("TAOS_CONTAINER_BIN", "/x/container")
        with patch.object(sys, "platform", "linux"), \
             patch("shutil.which", side_effect=lambda x: "/usr/bin/docker" if x == "docker" else None):
            assert detect_runtime() == "docker"
