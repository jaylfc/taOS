"""Tests for rkllama default port (7833) and legacy-fallback probe.

Three cases:
  (a) default_rkllama_url returns 7833 URL when nothing listens on either port.
  (b) returns 8080 URL when only 8080 answers with rkllama's signature.
  (c) regression guard: install-rknpu.sh default is 7833.
"""
from __future__ import annotations

import http.server
import json
import pathlib
import threading


# ---------------------------------------------------------------------------
# (a) Nothing listening -> returns 7833
# ---------------------------------------------------------------------------

class TestDefaultRkllamaUrlNothingListening:
    def test_returns_7833_when_no_server(self, monkeypatch):
        # Monkeypatch _port_responds_with_rkllama to always refuse.
        import tinyagentos.installers.rkllama_installer as mod
        monkeypatch.setattr(mod, "_port_responds_with_rkllama", lambda port, timeout=1.0: False)
        assert mod.default_rkllama_url() == "http://localhost:7833"


# ---------------------------------------------------------------------------
# (b) 8080 answers, 7833 is dead -> returns 8080 with a log warning
# ---------------------------------------------------------------------------

class TestDefaultRkllamaUrlLegacyFallback:
    def test_returns_8080_when_only_legacy_responds(self, monkeypatch):
        import tinyagentos.installers.rkllama_installer as mod

        def fake_probe(port: int, timeout: float = 1.0) -> bool:
            return port == 8080

        monkeypatch.setattr(mod, "_port_responds_with_rkllama", fake_probe)
        result = mod.default_rkllama_url()
        assert result == "http://localhost:8080"

    def test_logs_warning_on_legacy_fallback(self, monkeypatch, caplog):
        import logging
        import tinyagentos.installers.rkllama_installer as mod

        def fake_probe(port: int, timeout: float = 1.0) -> bool:
            return port == 8080

        monkeypatch.setattr(mod, "_port_responds_with_rkllama", fake_probe)

        with caplog.at_level(logging.WARNING, logger="tinyagentos.installers.rkllama_installer"):
            mod.default_rkllama_url()

        assert any("legacy" in r.message.lower() or "8080" in r.message for r in caplog.records)

    def test_returns_7833_when_both_respond(self, monkeypatch):
        """If both ports answer, prefer the new default."""
        import tinyagentos.installers.rkllama_installer as mod

        monkeypatch.setattr(mod, "_port_responds_with_rkllama", lambda port, timeout=1.0: True)
        assert mod.default_rkllama_url() == "http://localhost:7833"


# ---------------------------------------------------------------------------
# (c) Shell script regression guard
# ---------------------------------------------------------------------------

class TestInstallScriptDefault:
    def test_shell_default_is_7833(self):
        script = pathlib.Path(__file__).parents[1] / "scripts" / "install-rknpu.sh"
        assert script.exists(), "scripts/install-rknpu.sh not found"
        content = script.read_text()
        assert "TAOS_RKLLAMA_PORT:-7833" in content, (
            "install-rknpu.sh default port is not 7833; "
            "found: " + repr([l for l in content.splitlines() if "TAOS_RKLLAMA_PORT" in l])
        )
