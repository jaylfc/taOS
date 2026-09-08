"""LiteLLM startup guard for the Postgres/prisma path.

taOS never runs LiteLLM's Postgres-backed virtual-key mode, so this module
no longer shells out to ``prisma generate``.  If a ``.litellm_db_url`` file
is present the operator has explicitly opted into Postgres mode — that path
is not supported without the prisma package, so we raise a clear error
instead of attempting a runtime download.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


async def migrate(data_dir: Path) -> str:
    """Gate Postgres mode and report status.

    Returns:
        "no-db-configured" — no ``.litellm_db_url`` file, nothing to do.

    Raises:
        RuntimeError: if ``.litellm_db_url`` exists, because Postgres mode
        is not supported without the prisma package.
    """
    db_url_path = data_dir / ".litellm_db_url"
    if not db_url_path.exists():
        logger.info("litellm_migrate: no .litellm_db_url — skipping")
        return "no-db-configured"
    db_url = db_url_path.read_text().strip()
    if not db_url:
        logger.info("litellm_migrate: .litellm_db_url empty — skipping")
        return "no-db-configured"

    raise RuntimeError(
        "litellm_migrate: Postgres/virtual-key mode is not supported "
        "without the prisma package"
    )
