"""Tests for litellm_migrate — ensures LiteLLM's prisma Python client is
importable before proxy start. The helper deliberately does NOT touch
the database; LiteLLM owns its own migrations and we'd collide with
``_prisma_migrations`` history if we ran ``prisma db push`` here.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    """If ``prisma.client`` imports, generate must not be invoked."""
    data_dir = _write_db_url(tmp_path)

    async def _fake_exec(*args, **kwargs):
        raise AssertionError("create_subprocess_exec must not be called")

    with patch.object(litellm_migrate, "_prisma_client_importable", return_value=True), \
         patch.object(litellm_migrate.asyncio, "create_subprocess_exec", side_effect=_fake_exec):
        assert await litellm_migrate.migrate(data_dir) == "already-generated"


@pytest.mark.asyncio
async def test_migrate_runs_prisma_generate_when_client_missing(tmp_path):
    """When the client is not importable, migrate must shell out to
    ``prisma generate`` against LiteLLM's bundled schema."""
    data_dir = _write_db_url(tmp_path)
    fake_schema = tmp_path / "schema.prisma"
    fake_schema.write_text("// fake")
    fake_cli = tmp_path / "fake-venv" / "bin" / "prisma"
    fake_cli.parent.mkdir(parents=True)
    fake_cli.write_text("#!/bin/sh\nexit 0\n")
    fake_cli.chmod(0o755)

    importable = iter([False, True])

    proc_mock = MagicMock()
    proc_mock.returncode = 0
    proc_mock.communicate = AsyncMock(return_value=(b"Generated prisma client", b""))

    captured_cmd = []

    async def _fake_exec(*args, **kwargs):
        captured_cmd.extend(args)
        return proc_mock

    with patch.object(litellm_migrate, "_schema_path", return_value=fake_schema), \
         patch.object(litellm_migrate, "_prisma_cli", return_value=str(fake_cli)), \
         patch.object(litellm_migrate, "_prisma_client_importable",
                      side_effect=lambda: next(importable)), \
         patch.object(litellm_migrate.asyncio, "create_subprocess_exec",
                      side_effect=_fake_exec) as exec_mock:
        assert await litellm_migrate.migrate(data_dir) == "generated"

    assert exec_mock.call_count == 1
    args, kwargs = exec_mock.call_args
    cmd = list(args)
    assert cmd[0] == str(fake_cli)
    assert cmd[1] == "generate"
    assert any(a.startswith("--schema=") for a in cmd)
    assert "push" not in cmd
    env = kwargs["env"]
    venv_bin = str(fake_cli.parent)
    assert env["PATH"].startswith(venv_bin + ":"), env["PATH"]


@pytest.mark.asyncio
async def test_migrate_does_not_set_database_url(tmp_path):
    """``prisma generate`` doesn't need DATABASE_URL — and setting it would
    signal that we're about to touch the DB, which we explicitly aren't."""
    data_dir = _write_db_url(tmp_path)
    fake_schema = tmp_path / "schema.prisma"
    fake_schema.write_text("// fake")
    fake_cli = tmp_path / "bin" / "prisma"
    fake_cli.parent.mkdir(parents=True)
    fake_cli.write_text("#!/bin/sh\nexit 0\n")
    fake_cli.chmod(0o755)
    importable = iter([False, True])

    proc_mock = MagicMock()
    proc_mock.returncode = 0
    proc_mock.communicate = AsyncMock(return_value=(b"", b""))

    async def _fake_exec(*args, **kwargs):
        return proc_mock

    with patch.object(litellm_migrate, "_schema_path", return_value=fake_schema), \
         patch.object(litellm_migrate, "_prisma_cli", return_value=str(fake_cli)), \
         patch.object(litellm_migrate, "_prisma_client_importable",
                      side_effect=lambda: next(importable)), \
         patch.object(litellm_migrate.asyncio, "create_subprocess_exec",
                      side_effect=_fake_exec) as exec_mock, \
         patch.dict("os.environ", {}, clear=False):
        import os as _os
        _os.environ.pop("DATABASE_URL", None)
        await litellm_migrate.migrate(data_dir)

    env = exec_mock.call_args.kwargs["env"]
    assert "DATABASE_URL" not in env


@pytest.mark.asyncio
async def test_migrate_raises_when_generate_fails(tmp_path):
    """A non-zero exit from prisma generate must raise so boot fails loudly."""
    data_dir = _write_db_url(tmp_path)
    fake_schema = tmp_path / "schema.prisma"
    fake_schema.write_text("// fake")
    fake_cli = tmp_path / "bin" / "prisma"
    fake_cli.parent.mkdir(parents=True)
    fake_cli.write_text("#!/bin/sh\nexit 1\n")
    fake_cli.chmod(0o755)

    proc_mock = MagicMock()
    proc_mock.returncode = 1
    proc_mock.communicate = AsyncMock(return_value=(b"", b"boom"))

    async def _fake_exec(*args, **kwargs):
        return proc_mock

    with patch.object(litellm_migrate, "_schema_path", return_value=fake_schema), \
         patch.object(litellm_migrate, "_prisma_cli", return_value=str(fake_cli)), \
         patch.object(litellm_migrate, "_prisma_client_importable", return_value=False), \
         patch.object(litellm_migrate.asyncio, "create_subprocess_exec",
                      side_effect=_fake_exec):
        with pytest.raises(RuntimeError, match="prisma generate exited 1"):
            await litellm_migrate.migrate(data_dir)


@pytest.mark.asyncio
async def test_migrate_raises_when_client_still_missing_after_generate(tmp_path):
    """If prisma generate reports success but prisma.client is still not
    importable, raise — the environment is broken and LiteLLM will fail."""
    data_dir = _write_db_url(tmp_path)
    fake_schema = tmp_path / "schema.prisma"
    fake_schema.write_text("// fake")
    fake_cli = tmp_path / "bin" / "prisma"
    fake_cli.parent.mkdir(parents=True)
    fake_cli.write_text("#!/bin/sh\nexit 0\n")
    fake_cli.chmod(0o755)

    proc_mock = MagicMock()
    proc_mock.returncode = 0
    proc_mock.communicate = AsyncMock(return_value=(b"", b""))

    async def _fake_exec(*args, **kwargs):
        return proc_mock

    with patch.object(litellm_migrate, "_schema_path", return_value=fake_schema), \
         patch.object(litellm_migrate, "_prisma_cli", return_value=str(fake_cli)), \
         patch.object(litellm_migrate, "_prisma_client_importable", return_value=False), \
         patch.object(litellm_migrate.asyncio, "create_subprocess_exec",
                      side_effect=_fake_exec):
        with pytest.raises(RuntimeError, match="still not importable"):
            await litellm_migrate.migrate(data_dir)


@pytest.mark.asyncio
async def test_migrate_raises_when_schema_missing(tmp_path):
    """If LiteLLM isn't installed / schema.prisma missing, raise so the
    operator sees the real cause rather than silent LiteLLM 500s."""
    data_dir = _write_db_url(tmp_path)
    with patch.object(litellm_migrate, "_schema_path", return_value=None), \
         patch.object(litellm_migrate, "_prisma_client_importable", return_value=False):
        with pytest.raises(RuntimeError, match="cannot locate LiteLLM's schema.prisma"):
            await litellm_migrate.migrate(data_dir)


@pytest.mark.asyncio
async def test_migrate_raises_when_prisma_cli_missing(tmp_path):
    data_dir = _write_db_url(tmp_path)
    fake_schema = tmp_path / "schema.prisma"
    fake_schema.write_text("// fake")
    with patch.object(litellm_migrate, "_schema_path", return_value=fake_schema), \
         patch.object(litellm_migrate, "_prisma_client_importable", return_value=False), \
         patch.object(litellm_migrate, "_prisma_cli",
                      return_value=str(tmp_path / "does-not-exist")):
        with pytest.raises(RuntimeError, match="prisma CLI not found"):
            await litellm_migrate.migrate(data_dir)
