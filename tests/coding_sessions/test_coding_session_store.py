"""Tests for the coding session store."""
import pytest
import pytest_asyncio

from tinyagentos.coding_sessions.store import CodingSessionStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = CodingSessionStore(tmp_path / "coding_sessions.db")
    await s.init()
    yield s
    await s.close()


def _make_kwargs(**overrides):
    base = {
        "cli": "claude",
        "launch_target": "host-folder",
        "workdir": "/home/jay/myrepo",
        "repo_source": {"kind": "local", "value": "/home/jay/myrepo"},
        "created_by": "user-abc",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_returns_starting_status(store):
    session = await store.create_session(**_make_kwargs())
    assert session["id"].startswith("cs-")
    assert session["status"] == "starting"
    assert session["tmux_session"] is None
    assert session["cli"] == "claude"
    assert session["launch_target"] == "host-folder"
    assert session["created_by"] == "user-abc"


@pytest.mark.asyncio
async def test_alias_defaults_to_repo_slug(store):
    session = await store.create_session(**_make_kwargs(workdir="/home/jay/my-project"))
    assert session["alias"] == "my-project"


@pytest.mark.asyncio
async def test_explicit_alias_is_used(store):
    session = await store.create_session(**_make_kwargs(alias="my-alias"))
    assert session["alias"] == "my-alias"


@pytest.mark.asyncio
async def test_get_session_returns_none_for_missing(store):
    assert await store.get_session("cs-missing") is None


@pytest.mark.asyncio
async def test_get_session_round_trip(store):
    created = await store.create_session(**_make_kwargs())
    fetched = await store.get_session(created["id"])
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["repo_source"] == {"kind": "local", "value": "/home/jay/myrepo"}


@pytest.mark.asyncio
async def test_list_sessions_excludes_archived_by_default(store):
    s1 = await store.create_session(**_make_kwargs(alias="active"))
    s2 = await store.create_session(**_make_kwargs(alias="to-archive"))
    await store.archive_session(s2["id"])
    results = await store.list_sessions("user-abc")
    ids = [s["id"] for s in results]
    assert s1["id"] in ids
    assert s2["id"] not in ids


@pytest.mark.asyncio
async def test_list_sessions_include_archived(store):
    s1 = await store.create_session(**_make_kwargs(alias="active"))
    s2 = await store.create_session(**_make_kwargs(alias="archived"))
    await store.archive_session(s2["id"])
    results = await store.list_sessions("user-abc", include_archived=True)
    ids = [s["id"] for s in results]
    assert s1["id"] in ids
    assert s2["id"] in ids


@pytest.mark.asyncio
async def test_list_sessions_scoped_to_user(store):
    s1 = await store.create_session(**_make_kwargs(created_by="alice"))
    await store.create_session(**_make_kwargs(created_by="bob"))
    results = await store.list_sessions("alice")
    assert all(s["created_by"] == "alice" for s in results)
    assert len(results) == 1
    assert results[0]["id"] == s1["id"]


@pytest.mark.asyncio
async def test_set_status_transitions(store):
    session = await store.create_session(**_make_kwargs())
    assert session["status"] == "starting"
    updated = await store.set_status(session["id"], "running")
    assert updated["status"] == "running"
    updated = await store.set_status(session["id"], "waiting_input")
    assert updated["status"] == "waiting_input"
    updated = await store.set_status(session["id"], "stopped")
    assert updated["status"] == "stopped"


@pytest.mark.asyncio
async def test_set_tmux_session(store):
    session = await store.create_session(**_make_kwargs())
    assert session["tmux_session"] is None
    updated = await store.set_tmux_session(session["id"], "taos:cs-abc")
    assert updated["tmux_session"] == "taos:cs-abc"


@pytest.mark.asyncio
async def test_set_alias(store):
    session = await store.create_session(**_make_kwargs(alias="old-alias"))
    updated = await store.set_alias(session["id"], "new-alias")
    assert updated["alias"] == "new-alias"


@pytest.mark.asyncio
async def test_archive_sets_status_and_archived_at(store):
    session = await store.create_session(**_make_kwargs())
    archived = await store.archive_session(session["id"])
    assert archived["status"] == "archived"
    assert archived["archived_at"] is not None


@pytest.mark.asyncio
async def test_archive_is_not_delete(store):
    session = await store.create_session(**_make_kwargs())
    await store.archive_session(session["id"])
    fetched = await store.get_session(session["id"])
    assert fetched is not None
    assert fetched["status"] == "archived"


@pytest.mark.asyncio
async def test_worker_lxc_stores_worker(store):
    session = await store.create_session(
        **_make_kwargs(launch_target="worker-lxc", worker="pi-worker-01")
    )
    assert session["worker"] == "pi-worker-01"
    assert session["launch_target"] == "worker-lxc"
