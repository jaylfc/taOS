"""Tests for agent_name threading through JobWorker._do_enrich."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_worker():
    from tinyagentos.scheduling.job_worker import JobWorker

    worker = JobWorker.__new__(JobWorker)
    worker._llm_url = "http://localhost:11434"
    return worker


def _make_catalog_mock():
    catalog = MagicMock()
    catalog.init = AsyncMock()
    catalog.close = AsyncMock()
    catalog.enrich_session = AsyncMock(return_value={"enriched": True})
    return catalog


@pytest.mark.asyncio
async def test_enrich_passes_agent_name_to_catalog():
    worker = _make_worker()
    catalog = _make_catalog_mock()

    with patch(
        "tinyagentos.scheduling.job_worker.SessionCatalog",
        return_value=catalog,
    ):
        await worker._do_enrich(
            {"session_id": "s1", "agent_name": "alice"}
        )

    catalog.enrich_session.assert_called_once()
    kwargs = catalog.enrich_session.call_args.kwargs
    assert kwargs.get("agent_name") == "alice"


@pytest.mark.asyncio
async def test_enrich_agent_name_defaults_to_none_when_missing():
    worker = _make_worker()
    catalog = _make_catalog_mock()

    with patch(
        "tinyagentos.scheduling.job_worker.SessionCatalog",
        return_value=catalog,
    ):
        await worker._do_enrich({"session_id": "s1"})

    catalog.enrich_session.assert_called_once()
    kwargs = catalog.enrich_session.call_args.kwargs
    assert kwargs.get("agent_name") is None
