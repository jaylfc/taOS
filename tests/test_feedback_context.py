"""Tests for #777 Layer 1: install/version context auto-attached to feedback.

When a user files a bug or feature request through the in-product tracker,
the submission must carry the LOCAL install context -- app version + commit,
the install's identity, and the recorded update history -- so a report can
be traced to a version and to whether it was upgrade-related. No server is
involved (Layer 2 central registration is a separate, opt-in design).
"""
from __future__ import annotations

import sqlite3
import subprocess

import pytest
import pytest_asyncio

import tinyagentos
from tinyagentos.feedback_store import FeedbackStore
from tinyagentos.rollback import record_pre_update

KNOWN_CONTEXT = {
    "version": "9.9.9-test",
    "commit": "a" * 40,
    "install_id": "test-install-id",
    "platform": "linux-x86_64",
    "update_history": {
        "previous_commit": "c" * 40,
        "previous_branch": "dev",
        "previous_ts": "1700000000",
    },
}


@pytest_asyncio.fixture
async def store(tmp_path):
    s = FeedbackStore(tmp_path / "feedback.db", context_provider=lambda: KNOWN_CONTEXT)
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_attaches_context(store):
    row = await store.create(user_id="u1", type="bug", title="t", body="b")
    assert row["context"] == KNOWN_CONTEXT


@pytest.mark.asyncio
async def test_create_within_cap_attaches_context(store):
    row = await store.create_within_cap(
        since="1970-01-01T00:00:00+00:00",
        cap=20,
        user_id="u1",
        type="bug",
        title="t",
        body="b",
    )
    assert row is not None
    assert row["context"] == KNOWN_CONTEXT


@pytest.mark.asyncio
async def test_context_surfaced_on_read_back(store):
    created = await store.create(user_id="u1", type="bug", title="t", body="b")
    fetched = await store.get_by_id(created["id"], "u1")
    assert fetched is not None
    assert fetched["context"] == KNOWN_CONTEXT
    listed = await store.list_for_user("u1")
    assert listed[0]["context"] == KNOWN_CONTEXT


@pytest.mark.asyncio
async def test_submission_survives_broken_context_provider(tmp_path):
    def boom():
        raise RuntimeError("identity source unavailable")

    s = FeedbackStore(tmp_path / "feedback.db", context_provider=boom)
    await s.init()
    try:
        row = await s.create(user_id="u1", type="bug", title="t", body="b")
        assert row["id"]
        assert row["context"] == {}
    finally:
        await s.close()


def test_collect_context_from_install(tmp_path):
    from tinyagentos.feedback_context import collect_feedback_context

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / ".install_id").write_text("seeded-install-id\n", encoding="utf-8")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    record_pre_update(project_dir, branch="dev", sha="c" * 40, ts=1700000000)

    ctx = collect_feedback_context(data_dir, project_dir)
    assert ctx["version"] == tinyagentos.__version__
    assert ctx["install_id"] == "seeded-install-id"
    # project_dir is not a git checkout, so no commit can be read -- the
    # field degrades to "" rather than failing the whole snapshot.
    assert ctx["commit"] == ""
    assert ctx["platform"]
    assert ctx["update_history"] == {
        "previous_commit": "c" * 40,
        "previous_branch": "dev",
        "previous_ts": "1700000000",
    }


def test_collect_context_commit_from_git_checkout(tmp_path):
    from tinyagentos.feedback_context import collect_feedback_context

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=project_dir, check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project_dir,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    ctx = collect_feedback_context(tmp_path / "data", project_dir)
    assert ctx["commit"] == head
    assert ctx["update_history"] == {}


def test_collect_context_with_no_sources(tmp_path):
    from tinyagentos.feedback_context import collect_feedback_context

    # Neither a data dir nor a project dir: version still attaches, the rest
    # degrades to empty values instead of raising.
    ctx = collect_feedback_context(None, None)
    assert ctx["version"] == tinyagentos.__version__
    assert ctx["install_id"] == ""
    assert ctx["commit"] == ""
    assert ctx["update_history"] == {}


@pytest.mark.asyncio
async def test_default_store_context_uses_install_identity(tmp_path):
    (tmp_path / ".install_id").write_text("default-id-123", encoding="utf-8")
    s = FeedbackStore(tmp_path / "feedback.db")
    await s.init()
    try:
        row = await s.create(user_id="u1", type="bug", title="t", body="b")
        assert row["context"]["version"] == tinyagentos.__version__
        assert row["context"]["install_id"] == "default-id-123"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_existing_db_gains_context(tmp_path):
    # A database created by the pre-#777 schema has no context column; the
    # store must add it on init and then attach context as usual.
    db_path = tmp_path / "feedback.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE feedback (
            id TEXT NOT NULL PRIMARY KEY,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            screenshot TEXT NOT NULL DEFAULT '',
            app TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    s = FeedbackStore(db_path, context_provider=lambda: KNOWN_CONTEXT)
    await s.init()
    try:
        row = await s.create(user_id="u1", type="bug", title="legacy", body="")
        assert row["context"] == KNOWN_CONTEXT
        fetched = await s.get_by_id(row["id"], "u1")
        assert fetched is not None
        assert fetched["context"] == KNOWN_CONTEXT
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# Route level: the read paths surface the attached context.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_submission_carries_context(client):
    resp = await client.post(
        "/api/feedback",
        json={"type": "bug", "title": "It broke", "body": "details"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["context"]["version"] == tinyagentos.__version__
    assert data["context"]["install_id"]

    got = await client.get(f"/api/feedback/{data['id']}")
    assert got.status_code == 200
    assert got.json()["context"]["version"] == tinyagentos.__version__

    listed = await client.get("/api/feedback")
    assert listed.status_code == 200
    mine = [i for i in listed.json() if i["id"] == data["id"]]
    assert mine and mine[0]["context"]["version"] == tinyagentos.__version__
