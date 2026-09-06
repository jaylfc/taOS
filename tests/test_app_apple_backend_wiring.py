"""Smoke test: when detect_runtime returns 'apple', create_app installs AppleContainerBackend."""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_create_app_installs_apple_backend_on_darwin(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_CONTAINER_BIN", "/usr/local/bin/container")
    with patch("tinyagentos.containers.backend.detect_runtime", return_value="apple"):
        from tinyagentos.app import create_app
        from tinyagentos.containers.backend import get_backend
        from tinyagentos.containers.apple_backend import AppleContainerBackend

        app = create_app(data_dir=tmp_path)
        async with app.router.lifespan_context(app):
            backend = get_backend()
            assert isinstance(backend, AppleContainerBackend)
