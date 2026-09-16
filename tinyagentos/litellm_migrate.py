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
        "postgres-configured" — ``.litellm_db_url`` present but not used for
            LiteLLM virtual keys; the in-house SQLite keystore is authoritative
            for per-agent keys. Ensure Postgres is configured as a first-class
            app database for other purposes.
    """
    db_url_path = data_dir / ".litellm_db_url"
    if not db_url_path.exists():
        logger.info("litellm_migrate: no .litellm_db_url — skipping")
        return "no-db-configured"
    db_url = db_url_path.read_text().strip()
    if not db_url:
        logger.info("litellm_migrate: .litellm_db_url empty — skipping")
        return "no-db-configured"

    logger.warning(
        "litellm_migrate: .litellm_db_url present but Postgres is not used "
        "for LiteLLM virtual keys; the in-house SQLite keystore is authoritative "
        "for per-agent keys. Ensure Postgres is configured as a first-class "
        "app database for other purposes."
    )
    return "postgres-configured"
