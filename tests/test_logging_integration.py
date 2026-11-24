"""Integration test: logging configuration applies on every start path."""
from __future__ import annotations

import logging
import os
from io import StringIO
from unittest.mock import patch


def _disable_all_listeners(monkeypatch, m):
    """Force main() down the plain uvicorn.run branch (single-port)."""
    monkeypatch.setattr(m, "device_tls_port", lambda: 0)
    monkeypatch.setenv("TAOS_BROWSER_PROXY_PORT", "0")
    monkeypatch.setenv("TAOS_LLM_GATEWAY_PORT", "0")
    monkeypatch.setenv("TAOS_AGENT_CONTROLLER_PORT", "0")


def _disable_all_listeners_dual_port(monkeypatch, m):
    """Keep dual-port path active but disable sidecars."""
    monkeypatch.setattr(m, "device_tls_port", lambda: 0)
    monkeypatch.setenv("TAOS_BROWSER_PROXY_PORT", "6970")
    monkeypatch.setenv("TAOS_LLM_GATEWAY_PORT", "0")
    monkeypatch.setenv("TAOS_AGENT_CONTROLLER_PORT", "0")


class TestLoggingIntegration:
    """Test that logging configuration is applied on all start paths."""

    def _run_main_with_env(self, monkeypatch, env_vars, runner_type="uvicorn.run"):
        """Run main() with given env vars, return the module and capture configure_logging calls."""
        # Set env vars BEFORE importing the module
        for k, v in env_vars.items():
            monkeypatch.setenv(k, v)

        from tinyagentos import __main__ as m

        if runner_type == "uvicorn.run":
            _disable_all_listeners(monkeypatch, m)
        else:
            _disable_all_listeners_dual_port(monkeypatch, m)

        return m

    def test_main_single_port_calls_configure_logging(self, monkeypatch):
        """python -m tinyagentos (single-port) calls configure_logging()."""
        m = self._run_main_with_env(monkeypatch, {"TAOS_HOST": "127.0.0.1", "TAOS_PORT": "7117"}, "uvicorn.run")

        configure_called = []
        def fake_configure_logging():
            configure_called.append(True)
            from tinyagentos.logging_config import configure_logging as real_configure
            real_configure()

        # Capture formatted log output
        log_buffer = StringIO()
        test_handler = logging.StreamHandler(log_buffer)
        test_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        root.handlers = [test_handler]
        root.setLevel(logging.INFO)

        try:
            with patch("uvicorn.run", side_effect=lambda *a, **k: None), \
                 patch.object(m, "create_app", return_value=object()), \
                 patch("tinyagentos.__main__.configure_logging", side_effect=fake_configure_logging):
                m.main()

            assert configure_called, "configure_logging() was not called"

            # Emit a test log
            logger = logging.getLogger("tinyagentos.test_probe")
            logger.info("probe-line")

            output = log_buffer.getvalue()
            assert "probe-line" in output, f"Expected log line in output: {output}"
            assert "tinyagentos.test_probe" in output, f"Logger name missing in output: {output}"
            assert "INFO" in output, f"Level missing in output: {output}"
        finally:
            root.handlers = saved_handlers
            root.level = saved_level

    def test_main_dual_port_calls_configure_logging(self, monkeypatch):
        """python -m tinyagentos (dual-port default) calls configure_logging()."""
        m = self._run_main_with_env(monkeypatch, {"TAOS_HOST": "127.0.0.1", "TAOS_PORT": "7117"}, "_serve_dual_port")

        configure_called = []
        def fake_configure_logging():
            configure_called.append(True)
            from tinyagentos.logging_config import configure_logging as real_configure
            real_configure()

        log_buffer = StringIO()
        test_handler = logging.StreamHandler(log_buffer)
        test_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        root.handlers = [test_handler]
        root.setLevel(logging.INFO)

        try:
            with patch.object(m, "_serve_dual_port", side_effect=lambda *a, **k: None), \
                 patch.object(m, "create_app", return_value=object()), \
                 patch("tinyagentos.__main__.configure_logging", side_effect=fake_configure_logging):
                m.main()

            assert configure_called, "configure_logging() was not called"

            logger = logging.getLogger("tinyagentos.test_probe")
            logger.info("probe-line")

            output = log_buffer.getvalue()
            assert "probe-line" in output, f"Expected log line in output: {output}"
            assert "tinyagentos.test_probe" in output, f"Logger name missing in output: {output}"
            assert "INFO" in output, f"Level missing in output: {output}"
        finally:
            root.handlers = saved_handlers
            root.level = saved_level

    def test_configure_logging_respects_TAOS_LOG_LEVEL_WARNING(self):
        """configure_logging() respects TAOS_LOG_LEVEL=WARNING."""
        from tinyagentos.logging_config import configure_logging

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        root.handlers = []
        root.level = logging.NOTSET

        try:
            os.environ["TAOS_LOG_LEVEL"] = "WARNING"
            configure_logging()
            assert root.level == logging.WARNING, f"Root level should be WARNING, got {root.level}"
        finally:
            root.handlers = saved_handlers
            root.level = saved_level
            os.environ.pop("TAOS_LOG_LEVEL", None)

    def test_configure_logging_defaults_to_INFO(self):
        """configure_logging() defaults to INFO when TAOS_LOG_LEVEL unset."""
        from tinyagentos.logging_config import configure_logging

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        root.handlers = []
        root.level = logging.NOTSET

        try:
            os.environ.pop("TAOS_LOG_LEVEL", None)
            configure_logging()
            assert root.level == logging.INFO, f"Root level should be INFO, got {root.level}"
        finally:
            root.handlers = saved_handlers
            root.level = saved_level

    def test_configure_logging_idempotent_no_duplicate_handlers(self):
        """configure_logging() is idempotent - doesn't add duplicate handlers."""
        from tinyagentos.logging_config import configure_logging

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        root.handlers = []
        root.level = logging.NOTSET

        try:
            configure_logging()
            handler_count_after_first = len(root.handlers)
            configure_logging()
            handler_count_after_second = len(root.handlers)
            assert handler_count_after_first == 1, f"Expected 1 handler after first call, got {handler_count_after_first}"
            assert handler_count_after_second == 1, f"Expected 1 handler after second call, got {handler_count_after_second}"
        finally:
            root.handlers = saved_handlers
            root.level = saved_level

    def test_configure_logging_preserves_existing_handler(self):
        """configure_logging() preserves pre-existing handler."""
        from tinyagentos.logging_config import configure_logging

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        root.handlers = []
        root.level = logging.NOTSET

        try:
            existing_handler = logging.NullHandler()
            root.addHandler(existing_handler)
            configure_logging()
            assert existing_handler in root.handlers, "Pre-existing handler was removed"
            # Should not add a second StreamHandler since a handler already exists
            stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
            assert len(stream_handlers) == 0, f"Expected 0 StreamHandlers when handler exists, got {len(stream_handlers)}"
        finally:
            root.handlers = saved_handlers
            root.level = saved_level