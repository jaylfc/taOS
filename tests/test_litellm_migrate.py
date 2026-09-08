"""Tests for litellm_migrate — ensures LiteLLM's Postgres/prisma path is
gated and that the proxy starts without prisma when no DATABASE_URL is set.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tinyagentos import litellm_migrate


def _write_db_url(tmp_path: Path, url: str = "postgresql://u:p@h/db") -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / ".litellm_db_url").write_text(url)
    return data_dir


@pytest.mark.asyncio
async def test_migrate_noop_when_no_db_file(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    assert await litellm_migrate.migrate(data_dir) == "no-db-configured"


@pytest.mark.asyncio
async def test_migrate_noop_when_db_url_empty(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / ".litellm_db_url").write_text("   \n")
    assert await litellm_migrate.migrate(data_dir) == "no-db-configured"


@pytest.mark.asyncio
async def test_migrate_skips_when_prisma_client_already_importable(tmp_path):
    """If a DATABASE_URL is present, Postgres mode is not supported without
    the prisma package — raise a clear error regardless of client state."""
    data_dir = _write_db_url(tmp_path)
    with pytest.raises(RuntimeError, match="not supported"):
        await litellm_migrate.migrate(data_dir)


@pytest.mark.asyncio
async def test_migrate_raises_not_supported_when_db_url_set_without_prisma(tmp_path):
    """With a DATABASE_URL configured and no prisma installed, migrate must
    raise a clear 'not supported' error rather than attempting a runtime
    prisma download."""
    data_dir = _write_db_url(tmp_path)
    with pytest.raises(RuntimeError, match="not supported"):
        await litellm_migrate.migrate(data_dir)


@pytest.mark.asyncio
async def test_migrate_does_not_set_database_url(tmp_path):
    """Postgres mode is not supported without the prisma package."""
    data_dir = _write_db_url(tmp_path)
    with pytest.raises(RuntimeError, match="not supported"):
        await litellm_migrate.migrate(data_dir)


@pytest.mark.asyncio
async def test_migrate_raises_when_generate_fails(tmp_path):
    """Postgres mode is not supported without the prisma package."""
    data_dir = _write_db_url(tmp_path)
    with pytest.raises(RuntimeError, match="not supported"):
        await litellm_migrate.migrate(data_dir)


@pytest.mark.asyncio
async def test_migrate_raises_when_client_still_missing_after_generate(tmp_path):
    """Postgres mode is not supported without the prisma package."""
    data_dir = _write_db_url(tmp_path)
    with pytest.raises(RuntimeError, match="not supported"):
        await litellm_migrate.migrate(data_dir)


@pytest.mark.asyncio
async def test_migrate_raises_when_schema_missing(tmp_path):
    """Postgres mode is not supported without the prisma package."""
    data_dir = _write_db_url(tmp_path)
    with pytest.raises(RuntimeError, match="not supported"):
        await litellm_migrate.migrate(data_dir)


@pytest.mark.asyncio
async def test_migrate_raises_when_prisma_cli_missing(tmp_path):
    """Postgres mode is not supported without the prisma package."""
    data_dir = _write_db_url(tmp_path)
    with pytest.raises(RuntimeError, match="not supported"):
        await litellm_migrate.migrate(data_dir)


def test_prisma_declared_nowhere_in_packaging():
    """R2-18: the prisma dependency is gone from BOTH pyproject.toml and uv.lock.

    pyproject alone is not enough -- a stale lock keeps shipping the package
    (and its runtime query-engine download) to every `uv sync`.
    """
    root = Path(__file__).resolve().parents[1]
    for name in ("pyproject.toml", "uv.lock"):
        text = (root / name).read_text(encoding="utf-8")
        assert "prisma" not in text.lower(), f"{name} still references prisma"
