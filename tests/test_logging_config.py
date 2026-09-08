import logging

import pytest

from tinyagentos.logging_config import configure_logging


@pytest.fixture(autouse=True)
def _isolate_root_logger():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        yield
    finally:
        root.handlers = saved_handlers
        root.level = saved_level


class TestConfigureLogging:
    def test_installs_single_handler_with_expected_formatter_when_empty(self):
        root = logging.getLogger()
        root.handlers = []
        root.level = logging.WARNING

        configure_logging()

        assert len(root.handlers) == 1
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.formatter is not None
        fmt = handler.formatter._fmt
        assert "%(asctime)s" in fmt
        assert "%(levelname)s" in fmt
        assert "%(name)s" in fmt

    def test_idempotent_does_not_add_second_handler(self):
        root = logging.getLogger()
        root.handlers = []
        root.level = logging.WARNING

        configure_logging()
        configure_logging()

        assert len(root.handlers) == 1

    def test_preserves_pre_existing_handler(self):
        root = logging.getLogger()
        root.handlers = []
        root.level = logging.WARNING
        null_handler = logging.NullHandler()
        root.addHandler(null_handler)

        configure_logging()

        assert null_handler in root.handlers

    def test_debug_env_sets_root_level_debug(self, monkeypatch):
        monkeypatch.setenv("TAOS_LOG_LEVEL", "DEBUG")
        root = logging.getLogger()
        root.handlers = []
        root.level = logging.WARNING

        configure_logging()

        assert root.level == logging.DEBUG

    def test_unset_env_defaults_root_level_info(self, monkeypatch):
        monkeypatch.delenv("TAOS_LOG_LEVEL", raising=False)
        root = logging.getLogger()
        root.handlers = []
        root.level = logging.WARNING

        configure_logging()

        assert root.level == logging.INFO
