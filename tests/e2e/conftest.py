"""Playwright E2E test configuration.

Requires a running TinyAgentOS instance at the BASE_URL.
Start with: python -m uvicorn tinyagentos.app:create_app --factory --port 6969

Install: pip install -e ".[e2e]" && playwright install chromium
Run: pytest tests/e2e/ -v
"""
import os
import pytest

BASE_URL = os.environ.get("TAOS_TEST_URL", "http://localhost:6969")


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL
