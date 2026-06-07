"""Tests for agent base image prefetch opt-in and progress tracking."""
import os
import pytest
from unittest import mock

from tinyagentos.agent_image import (
    is_prefetch_enabled,
    get_prefetch_state,
    _prefetch_state,
)


class TestIsPrefetchEnabled:
    """Environment-variable gating for TAOS_PREFETCH_BASE_IMAGE."""

    def test_default_is_disabled(self, monkeypatch):
        monkeypatch.delenv("TAOS_PREFETCH_BASE_IMAGE", raising=False)
        assert is_prefetch_enabled() is False

    def test_empty_string_is_disabled(self, monkeypatch):
        monkeypatch.setenv("TAOS_PREFETCH_BASE_IMAGE", "")
        assert is_prefetch_enabled() is False

    def test_one_enables(self, monkeypatch):
        monkeypatch.setenv("TAOS_PREFETCH_BASE_IMAGE", "1")
        assert is_prefetch_enabled() is True

    def test_true_enables(self, monkeypatch):
        monkeypatch.setenv("TAOS_PREFETCH_BASE_IMAGE", "true")
        assert is_prefetch_enabled() is True

    def test_yes_enables(self, monkeypatch):
        monkeypatch.setenv("TAOS_PREFETCH_BASE_IMAGE", "yes")
        assert is_prefetch_enabled() is True

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("TAOS_PREFETCH_BASE_IMAGE", "YES")
        assert is_prefetch_enabled() is True

    def test_zero_is_disabled(self, monkeypatch):
        monkeypatch.setenv("TAOS_PREFETCH_BASE_IMAGE", "0")
        assert is_prefetch_enabled() is False

    def test_garbage_is_disabled(self, monkeypatch):
        monkeypatch.setenv("TAOS_PREFETCH_BASE_IMAGE", "maybe")
        assert is_prefetch_enabled() is False


class TestGetPrefetchState:
    """Progress state tracking."""

    def test_initial_state_is_idle(self):
        _prefetch_state.clear()
        _prefetch_state["status"] = "idle"
        state = get_prefetch_state()
        assert state["status"] == "idle"

    def test_returns_copy_not_reference(self):
        _prefetch_state.clear()
        _prefetch_state["status"] = "idle"
        state = get_prefetch_state()
        state["status"] = "modified"
        assert _prefetch_state["status"] == "idle"

    def test_set_status_updates_state(self):
        _prefetch_state.clear()
        _prefetch_state.update(status="downloading", url="http://example.com/img.tar.gz")
        state = get_prefetch_state()
        assert state["status"] == "downloading"
        assert state["url"] == "http://example.com/img.tar.gz"


class TestServiceTemplate:
    """Verify the systemd service template substitution works."""

    def test_service_file_has_taos_prefetch_placeholder(self):
        service_path = os.path.join(
            os.path.dirname(__file__),
            "..", "scripts", "systemd", "tinyagentos.service",
        )
        content = open(service_path).read()
        assert "TAOS_PREFETCH_BASE_IMAGE=TAOS_PREFETCH" in content, (
            "service template must have the TAOS_PREFETCH placeholder "
            "so install-server.sh can substitute it"
        )

    def test_install_script_has_taos_prefetch_doc(self):
        script_path = os.path.join(
            os.path.dirname(__file__),
            "..", "scripts", "install-server.sh",
        )
        content = open(script_path).read()
        assert "TAOS_PREFETCH_BASE_IMAGE" in content, (
            "install script must document TAOS_PREFETCH_BASE_IMAGE env var"
        )

    def test_install_script_substitutes_taos_prefetch(self):
        """sed must have a substitution for TAOS_PREFETCH."""
        script_path = os.path.join(
            os.path.dirname(__file__),
            "..", "scripts", "install-server.sh",
        )
        content = open(script_path).read()
        # The sed line that substitutes TAOS_PREFETCH into the service template
        assert 's|TAOS_PREFETCH|$' in content, (
            "install script must have sed substitution for TAOS_PREFETCH placeholder"
        )
