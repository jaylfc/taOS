"""Integration test: logging configuration applies on every start path."""
from __future__ import annotations

import logging
from io import StringIO
from unittest.mock import patch

import pytest


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
            root.setLevel(saved_level)

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
            root.setLevel(saved_level)

    def test_configure_logging_respects_TAOS_LOG_LEVEL_WARNING(self, monkeypatch):
        """configure_logging() respects TAOS_LOG_LEVEL=WARNING."""
        from tinyagentos.logging_config import configure_logging

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        root.handlers = []
        root.setLevel(logging.NOTSET)

        try:
            monkeypatch.setenv("TAOS_LOG_LEVEL", "WARNING")
            configure_logging()
            assert root.level == logging.WARNING, f"Root level should be WARNING, got {root.level}"
        finally:
            root.handlers = saved_handlers
            root.setLevel(saved_level)
            monkeypatch.delenv("TAOS_LOG_LEVEL", raising=False)

    def test_configure_logging_defaults_to_INFO(self, monkeypatch):
        """configure_logging() defaults to INFO when TAOS_LOG_LEVEL unset."""
        from tinyagentos.logging_config import configure_logging

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        root.handlers = []
        root.setLevel(logging.NOTSET)

        try:
            monkeypatch.delenv("TAOS_LOG_LEVEL", raising=False)
            configure_logging()
            assert root.level == logging.INFO, f"Root level should be INFO, got {root.level}"
        finally:
            root.handlers = saved_handlers
            root.setLevel(saved_level)

    def test_configure_logging_idempotent_no_duplicate_handlers(self):
        """configure_logging() is idempotent - doesn't add duplicate handlers."""
        from tinyagentos.logging_config import configure_logging

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        root.handlers = []
        root.setLevel(logging.NOTSET)

        try:
            configure_logging()
            handler_count_after_first = len(root.handlers)
            configure_logging()
            handler_count_after_second = len(root.handlers)
            assert handler_count_after_first == 1, f"Expected 1 handler after first call, got {handler_count_after_first}"
            assert handler_count_after_second == 1, f"Expected 1 handler after second call, got {handler_count_after_second}"
        finally:
            root.handlers = saved_handlers
            root.setLevel(saved_level)

    def test_configure_logging_preserves_existing_handler(self):
        """configure_logging() preserves pre-existing handler."""
        from tinyagentos.logging_config import configure_logging

        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        root.handlers = []
        root.setLevel(logging.NOTSET)

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
            root.setLevel(saved_level)


class TestRootLoggerRestoredBetweenTests:
    """create_app() calls configure_logging(), which leaves the root logger at
    INFO whenever handlers already exist.  The _restore_root_logger autouse
    fixture (tests/conftest.py) must restore the pre-test level so the NEXT
    test sees the level that was in effect before create_app() ran."""

    def test_autouse_fixture_is_active(self, request):
        """The _restore_root_logger autouse fixture is present in fixturenames."""
        assert "_restore_root_logger" in request.fixturenames

    def test_root_logger_state_restored_after_create_app(self, tmp_data_dir):
        """Root logger level and handlers are restored after create_app() runs.

        This test is self-contained and order-independent: it snapshots the
        root logger state, calls create_app() (which changes the level to INFO),
        then calls the same restore helper the fixture uses, and asserts the
        state matches the snapshot.
        """
        from tests.conftest import _save_root_logger_state, _restore_root_logger_state
        from tinyagentos.app import create_app

        root = logging.getLogger()
        # Snapshot state before any mutation
        before_state = _save_root_logger_state()
        before_level, before_handlers = before_state

        # Call create_app which will set root level to INFO
        create_app(data_dir=tmp_data_dir)

        # Verify the level was changed by create_app
        assert root.level == logging.INFO

        # Restore using the same helper the fixture uses
        _restore_root_logger_state(before_state)

        # Assert level and handlers match the snapshot
        assert root.level == before_level, f"Expected level {before_level}, got {root.level}"
        assert list(root.handlers) == before_handlers, f"Handlers mismatch: {root.handlers} != {before_handlers}"

    def test_restore_clears_child_logger_level_cache(self):
        """Restoring root level with setLevel() clears child logger isEnabledFor cache.

        When root is INFO, a child logger caches isEnabledFor(INFO) == True.
        After restoring root to WARNING via setLevel(), the child's cache
        must be invalidated so isEnabledFor(INFO) returns False.
        """
        from tests.conftest import _save_root_logger_state, _restore_root_logger_state

        root = logging.getLogger()
        # Ensure root starts at INFO so child caches INFO as enabled
        root.setLevel(logging.INFO)

        # Create a child logger and prime its cache
        child = logging.getLogger("tinyagentos.test_child_cache")
        assert child.isEnabledFor(logging.INFO) is True, "Child should have INFO enabled when root is INFO"

        # Snapshot state (root at INFO)
        state_at_info = _save_root_logger_state()

        # Change root to WARNING and restore back to the saved state (INFO)
        # This simulates what happens when a test runs with root at WARNING,
        # create_app sets it to INFO, then fixture restores to WARNING
        root.setLevel(logging.WARNING)
        _restore_root_logger_state(state_at_info)

        # Root should be back at INFO
        assert root.level == logging.INFO

        # Now simulate the reverse: root at WARNING, create_app sets to INFO, restore to WARNING
        root.setLevel(logging.WARNING)
        # Prime child cache at WARNING (INFO should be disabled)
        assert child.isEnabledFor(logging.INFO) is False, "Child should have INFO disabled when root is WARNING"

        # Snapshot state at WARNING
        state_at_warning = _save_root_logger_state()

        # Change root to INFO (simulating create_app)
        root.setLevel(logging.INFO)
        assert child.isEnabledFor(logging.INFO) is True, "Child should have INFO enabled when root is INFO"

        # Restore to WARNING using the helper (uses setLevel)
        _restore_root_logger_state(state_at_warning)

        # Root should be WARNING
        assert root.level == logging.WARNING

        # Child's cache should be cleared: isEnabledFor(INFO) must be False
        assert child.isEnabledFor(logging.INFO) is False, (
            "Child logger cache not cleared after restore: "
            "isEnabledFor(INFO) should be False when root is WARNING"
        )