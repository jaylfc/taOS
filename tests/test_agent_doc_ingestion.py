from __future__ import annotations

import pytest
import pytest_asyncio
from pathlib import Path

from tinyagentos.knowledge_store import KnowledgeStore
from tinyagentos.knowledge_monitor import (
    ingest_agent_docs,
    ingest_app_guides,
    ingest_installed_app_guides,
    wipe_app_guides,
)


@pytest_asyncio.fixture
async def store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "media")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
def docs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "docs"
    d.mkdir()
    (d / "getting-started.md").write_text("# Getting Started\n\nWelcome to the project.")
    (d / "advanced.md").write_text("# Advanced\n\nDeep dive into internals.")
    # subdirectory
    sub = d / "api"
    sub.mkdir()
    (sub / "reference.md").write_text("# API Reference\n\nEndpoints listed here.")
    return d


@pytest_asyncio.fixture
def app_guides_dir(tmp_path: Path) -> Path:
    """Simulate an app catalog entry with a guides/ directory."""
    app_dir = tmp_path / "some-agent"
    guides = app_dir / "guides"
    guides.mkdir(parents=True)
    (guides / "setup.md").write_text("# Setup\n\nHow to configure the agent.")
    (guides / "usage.md").write_text("# Usage\n\nHow to use the agent.")
    return app_dir


# ---------------------------------------------------------------------------
# ingest_agent_docs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_agent_docs_basic(store, docs_dir):
    n = await ingest_agent_docs(docs_dir, store, source_id="core")
    assert n == 3

    items = await store.list_items(limit=10)
    assert len(items) == 3
    titles = {item["title"] for item in items}
    assert titles == {"getting-started", "advanced", "reference"}

    for item in items:
        assert item["source_type"] == "agent_doc"
        assert item["source_id"] == "core"
        assert item["status"] == "ready"
        assert item["content"]


@pytest.mark.asyncio
async def test_ingest_agent_docs_idempotent(store, docs_dir):
    n1 = await ingest_agent_docs(docs_dir, store, source_id="core")
    assert n1 == 3

    # Second call should skip all files (already present)
    n2 = await ingest_agent_docs(docs_dir, store, source_id="core")
    assert n2 == 0

    items = await store.list_items(limit=10)
    assert len(items) == 3


@pytest.mark.asyncio
async def test_ingest_agent_docs_scoped_by_source_id(store, docs_dir):
    n1 = await ingest_agent_docs(docs_dir, store, source_id="core")
    assert n1 == 3

    # Same docs under a different source_id should be ingested again
    n2 = await ingest_agent_docs(docs_dir, store, source_id="app:test")
    assert n2 == 3

    items = await store.list_items(limit=10)
    assert len(items) == 6

    core_items = await store.list_by_source_id("core")
    app_items = await store.list_by_source_id("app:test")
    assert len(core_items) == 3
    assert len(app_items) == 3


@pytest.mark.asyncio
async def test_ingest_agent_docs_missing_dir(store):
    n = await ingest_agent_docs(Path("/nonexistent/path"), store)
    assert n == 0


@pytest.mark.asyncio
async def test_ingest_agent_docs_empty_files(store, tmp_path):
    d = tmp_path / "empty-docs"
    d.mkdir()
    (d / "empty.md").write_text("")
    (d / "ws-only.md").write_text("   \n\n  ")
    (d / "real.md").write_text("# Real content")

    n = await ingest_agent_docs(d, store, source_id="test")
    assert n == 1

    items = await store.list_items(limit=10)
    assert len(items) == 1
    assert items[0]["title"] == "real"


# ---------------------------------------------------------------------------
# ingest_app_guides
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_app_guides(store, app_guides_dir):
    n = await ingest_app_guides("my-app", app_guides_dir, store)
    assert n == 2

    items = await store.list_by_source_id("app:my-app")
    assert len(items) == 2
    titles = {item["title"] for item in items}
    assert titles == {"setup", "usage"}

    for item in items:
        assert item["source_type"] == "agent_doc"
        assert item["source_id"] == "app:my-app"


@pytest.mark.asyncio
async def test_ingest_app_guides_no_guides_dir(store, tmp_path):
    app_dir = tmp_path / "no-guides-app"
    app_dir.mkdir()
    # No guides/ directory
    n = await ingest_app_guides("no-guides", app_dir, store)
    assert n == 0


# ---------------------------------------------------------------------------
# wipe_app_guides
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wipe_app_guides(store, app_guides_dir):
    await ingest_app_guides("my-app", app_guides_dir, store)

    # Also ingest core docs to verify wipe is scoped
    core_dir = app_guides_dir.parent / "core-docs"
    core_dir.mkdir()
    (core_dir / "intro.md").write_text("# Intro")
    await ingest_agent_docs(core_dir, store, source_id="core")

    deleted = await wipe_app_guides(store, "my-app")
    assert deleted == 2

    # App guides gone
    app_items = await store.list_by_source_id("app:my-app")
    assert len(app_items) == 0

    # Core docs untouched
    core_items = await store.list_by_source_id("core")
    assert len(core_items) == 1


@pytest.mark.asyncio
async def test_wipe_app_guides_no_match(store):
    deleted = await wipe_app_guides(store, "nonexistent")
    assert deleted == 0


# ---------------------------------------------------------------------------
# list_by_source_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_by_source_id_empty(store):
    items = await store.list_by_source_id("nonexistent")
    assert items == []


@pytest.mark.asyncio
async def test_delete_by_source_id(store, app_guides_dir):
    await ingest_app_guides("app-x", app_guides_dir, store)
    await ingest_app_guides("app-y", app_guides_dir, store)

    deleted = await store.delete_by_source_id("app:app-x")
    assert deleted == 2

    remaining = await store.list_by_source_id("app:app-y")
    assert len(remaining) == 2
