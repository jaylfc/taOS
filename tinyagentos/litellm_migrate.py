"""Ensure LiteLLM's Prisma Python client is importable at taOS startup.

LiteLLM ships its own ``prisma/migrations/`` directory and runs ``prisma
migrate deploy`` against it during proxy startup — that's the
authoritative path for creating/upgrading LiteLLM's tables. Our only
job here is to make sure ``import prisma`` works (i.e. the generated
Python client exists under ``site-packages/prisma/``) so LiteLLM's
own runtime can talk to the DB.

Previously this module also ran ``prisma db push --accept-data-loss``
to set up the schema ourselves. That DDLs the tables directly without
seeding ``_prisma_migrations``, which made LiteLLM's own
``migrate deploy`` try to apply migration #1 on top of already-existing
objects and fail with "type JobStatus already exists" in a loop —
leaving the proxy permanently unhealthy. The fix is to get out of the
DB's way entirely.

Must run BEFORE ``LLMProxy.start()`` so the prisma client is importable
by the time LiteLLM's proxy boots.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _prisma_cli() -> str:
    """Path to the ``prisma`` CLI installed in the current venv.

    The pip ``prisma`` package drops a ``prisma`` binary next to
    ``python`` in the venv's ``bin/``. Falling back to ``sys.executable
    -m prisma`` is unreliable because the package's ``__main__`` isn't a
    proper CLI entrypoint — the shipped binary is.
    """
    return str(Path(sys.executable).parent / "prisma")


def _schema_path() -> Path | None:
    """Locate the LiteLLM-bundled ``schema.prisma`` file.

    Returns None if LiteLLM isn't installed or the schema file is
    missing — callers log and bail.
    """
    try:
        import litellm.proxy  # type: ignore
    except Exception as exc:
        logger.error("litellm_migrate: cannot import litellm.proxy: %s", exc)
        return None
    path = Path(litellm.proxy.__file__).parent / "schema.prisma"
    if not path.exists():
        logger.error("litellm_migrate: schema not found at %s", path)
        return None
    return path


def _prisma_client_importable() -> bool:
    """True iff ``import prisma.client`` works — i.e. generate has been run.

    The ``prisma`` pip package ships an empty ``prisma/`` namespace that
    only gains a ``client`` submodule after ``prisma generate`` writes
    the generated artifacts. So the presence of ``prisma.client`` is a
    reliable "client is ready" signal.
    """
    try:
        import importlib

        importlib.import_module("prisma.client")
        return True
    except ImportError:
        return False


async def migrate(data_dir: Path) -> str:
    """Ensure LiteLLM's prisma Python client is importable.

    LiteLLM handles its own DB migrations on startup via
    ``prisma migrate deploy`` — do NOT run ``prisma db push`` here; that
    bypasses the ``_prisma_migrations`` history table and causes
    LiteLLM's native migrator to loop on duplicate-type errors.

    Returns a short status string for logging/tests:
        - "no-db-configured"   -> no ``.litellm_db_url`` file, nothing to do
        - "already-generated"  -> ``prisma.client`` already importable
        - "generated"          -> ran ``prisma generate`` successfully

    Raises on failure of ``prisma generate`` or if the client is still
    not importable afterwards, so boot fails loudly instead of LiteLLM
    silently 500ing on every request.
    """
    db_url_path = data_dir / ".litellm_db_url"
    if not db_url_path.exists():
        logger.info("litellm_migrate: no .litellm_db_url — skipping")
        return "no-db-configured"
    db_url = db_url_path.read_text().strip()
    if not db_url:
        logger.info("litellm_migrate: .litellm_db_url empty — skipping")
        return "no-db-configured"

    if _prisma_client_importable():
        logger.info("litellm_migrate: prisma.client already importable — skipping generate")
        return "already-generated"

    schema = _schema_path()
    if schema is None:
        raise RuntimeError(
            "litellm_migrate: cannot locate LiteLLM's schema.prisma — "
            "is litellm[proxy] installed?"
        )

    cli = _prisma_cli()
    if not Path(cli).exists():
        raise RuntimeError(
            f"litellm_migrate: prisma CLI not found at {cli} — "
            "is the 'prisma' pip package installed in this venv?"
        )

    env = os.environ.copy()
    # Prisma's node CLI shells out to ``prisma-client-py`` via ``/bin/sh``
    # during ``generate``. That subprocess inherits PATH, and under systemd
    # the unit file doesn't put the venv's bin/ on PATH by default — so the
    # generator fails with "prisma-client-py: not found". Prepend the bin
    # directory holding the prisma binary so the child shell resolves it.
    venv_bin = str(Path(cli).parent)
    existing_path = env.get("PATH", "")
    if venv_bin not in existing_path.split(os.pathsep):
        env["PATH"] = venv_bin + os.pathsep + existing_path if existing_path else venv_bin

    cmd = [cli, "generate", f"--schema={schema}"]
    logger.info("litellm_migrate: running %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode(errors="replace").strip()
    stderr = stderr_b.decode(errors="replace").strip()
    if stdout:
        logger.info("litellm_migrate stdout: %s", stdout)
    if stderr:
        logger.info("litellm_migrate stderr: %s", stderr)
    if proc.returncode != 0:
        raise RuntimeError(
            f"litellm_migrate: prisma generate exited {proc.returncode}"
        )

    if not _prisma_client_importable():
        raise RuntimeError(
            "litellm_migrate: prisma generate succeeded but prisma.client "
            "is still not importable — check site-packages/prisma/"
        )
    logger.info("litellm_migrate: prisma client generated from %s", schema)
    return "generated"
