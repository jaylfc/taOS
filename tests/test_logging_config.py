import logging
import os

import pytest

from tinyagentos.app import create_app


class TestLoggingConfig:
    def test_root_logger_has_handler_after_create_app(self, tmp_path):
        app = create_app(data_dir=tmp_path / "data")
        handlers = logging.getLogger().handlers
        assert handlers != [], "root logger should have at least one handler after create_app()"

    def test_logging_config_has_formatter_with_asctime_levelname_name(self, tmp_path):
        app = create_app(data_dir=tmp_path / "data")
        root = logging.getLogger()
        assert len(root.handlers) > 0, "root logger should have at least one handler"
        handler = root.handlers[0]
        assert handler.formatter is not None, "handler should have a formatter"
        formatter = handler.formatter
        assert "%(asctime)s" in formatter._fmt, "formatter should include %(asctime)s"
        assert "%(levelname)s" in formatter._fmt, "formatter should include %(levelname)s"
        assert "%(name)s" in formatter._fmt, "formatter should include %(name)s"

    def test_taos_log_level_env_var(self, tmp_path):
        # Test with TAOS_LOG_LEVEL=DEBUG
        with pytest.MonkeyPatch().context() as mp:
            mp.setenv("TAOS_LOG_LEVEL", "DEBUG")
            # Need to re-create app after env change, but create_app reads env at call time
            # So we just verify the dictConfig reads the env var
            app = create_app(data_dir=tmp_path / "data")
            root = logging.getLogger()
            # Level should be DEBUG (10) when TAOS_LOG_LEVEL=DEBUG
            assert root.level == logging.DEBUG, (
                f"root logger level should be DEBUG (10), got {root.level}"
            )

    def test_taos_log_level_defaults_to_info(self, tmp_path):
        # Test default TAOS_LOG_LEVEL=INFO
        # reset env
        if "TAOS_LOG_LEVEL" in os.environ:
            del os.environ["TAOS_LOG_LEVEL"]
        app = create_app(data_dir=tmp_path / "data")
        root = logging.getLogger()
        assert root.level == logging.INFO, (
            f"root logger level should be INFO (20) by default, got {root.level}"
        )