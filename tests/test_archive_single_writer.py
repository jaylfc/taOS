"""One writer per archive directory inside the controller (card tsk-6i7tqw).

Every test here imports the real ``tinyagentos.archive_writer`` and boots the
real application lifespan (``create_app`` + ``TestClient`` as a context
manager).  No helper in this file re-implements the lock.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import taosmd.archive as taosmd_archive
from taos_test_csrf import sync_csrf_event_hooks
from tinyagentos import archive_writer as aw
from tinyagentos.app import create_app


def _real(p) -> str:
    return os.path.realpath(str(p))


@pytest.fixture(autouse=True)
def _no_leaked_writer():
    """A test must leave no writer registered; a leak fails the test itself."""
    yield
    leaked = aw.held_archive_writers()
    assert leaked == {}, f"writer left registered after the test: {leaked}"


@pytest.fixture
def release_dir(tmp_path):
    """A data directory as a current release leaves it: config, an archive
    with a day file old enough to compress, and its index database."""
    d = tmp_path / "data"
    d.mkdir()
    (d / "config.yaml").write_text(yaml.dump({
        "server": {"host": "127.0.0.1", "port": 6969},
        "backends": [],
        "agents": [],
    }))
    (d / ".setup_complete").touch()
    seed = taosmd_archive.ArchiveStore(d / "archive", d / "archive-index.db")

    async def _seed():
        await seed.init()
        await seed.record("conversation", {"content": "from the old release"},
                          summary="old")
        await seed.close()

    asyncio.run(_seed())
    old_day = d / "archive" / "2020" / "01"
    old_day.mkdir(parents=True)
    day_file = old_day / "01.jsonl"
    day_file.write_text('{"timestamp": 1577836800.0, "event_type": "conversation"}\n')
    os.utime(day_file, (1577836800, 1577836800))
    return d


@pytest.fixture
def archive_constructions(monkeypatch):
    """Record the real directory of every ArchiveStore built from now on."""
    built: list[str] = []
    real_init = taosmd_archive.ArchiveStore.__init__

    def spy(self, archive_dir="data/archive", index_path="data/archive-index.db"):
        built.append(_real(archive_dir))
        real_init(self, archive_dir, index_path)

    monkeypatch.setattr(taosmd_archive.ArchiveStore, "__init__", spy)
    return built


def _authed_client(app) -> TestClient:
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    rec = app.state.auth.find_user("admin")
    token = app.state.auth.create_session(user_id=rec["id"], long_lived=True)
    client = TestClient(app, cookies={"taos_session": token})
    client.event_hooks = sync_csrf_event_hooks()
    return client


def test_lifespan_holds_exactly_one_writer(release_dir, archive_constructions):
    adir = release_dir / "archive"
    app = create_app(data_dir=release_dir)
    with TestClient(app):
        assert aw.held_archive_writers() == {_real(adir): "controller.app"}
        assert aw.get_archive_writer(adir) is app.state.archive
        assert archive_constructions.count(_real(adir)) == 1
        # the index database stays next to the archive directory, as before
        assert (release_dir / "archive-index.db").exists()
        assert not (adir / "archive-index.db").exists()
        assert Path(app.state.archive._index_path) == release_dir / "archive-index.db"


def test_second_open_is_refused_whatever_the_label_or_spelling(
        release_dir, archive_constructions, monkeypatch, tmp_path):
    adir = release_dir / "archive"
    link = tmp_path / "link-to-archive"
    link.symlink_to(adir, target_is_directory=True)
    app = create_app(data_dir=release_dir)
    with TestClient(app):
        first = app.state.archive
        monkeypatch.chdir(release_dir)
        spellings = [
            ("controller.app", adir),
            ("catalog", adir),
            ("catalog", link),
            ("other", Path("archive")),
            ("other", adir / ".." / "archive"),
        ]
        for label, where in spellings:
            with pytest.raises(RuntimeError, match="controller.app"):
                aw.open_archive_writer(label, where)
        assert aw.get_archive_writer(link) is first
        assert aw.held_archive_writers() == {_real(adir): "controller.app"}
        assert archive_constructions.count(_real(adir)) == 1


def test_concurrent_opens_let_exactly_one_through(tmp_path):
    adir = tmp_path / "archive"
    results: list[object] = []
    barrier = threading.Barrier(8)

    def attempt(n: int) -> None:
        barrier.wait()
        try:
            results.append(aw.open_archive_writer(f"t{n}", adir))
        except RuntimeError as exc:
            results.append(exc)

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if not isinstance(r, RuntimeError)]
    assert len(winners) == 1
    assert len(results) == 8
    asyncio.run(aw.release_archive_writer(winners[0]))


def test_every_in_process_user_holds_the_one_writer(release_dir, archive_constructions):
    """Drive the routes named by the card; none may open a second archive."""
    adir = release_dir / "archive"
    app = create_app(data_dir=release_dir)
    with _authed_client(app) as client:
        writer = app.state.archive

        r = client.get("/api/memory/catalog/stats")
        assert r.status_code == 200, r.text

        r = client.get("/api/memory/stats")
        assert r.status_code == 200, r.text
        assert r.json().get("archive"), "memory management did not reach the archive"

        r = client.post("/api/archive/compress", params={"days_old": 1})
        assert r.status_code == 200, r.text
        assert r.json()["compressed"] == 1
        assert (adir / "2020" / "01" / "01.jsonl.gz").exists()

        before = client.get("/api/archive/stats").json()["total_events"]
        ch = client.post("/api/chat/channels", json={
            "name": "g", "type": "group", "description": "", "topic": "",
            "members": ["user", "tom"], "created_by": "user"})
        assert ch.status_code in (200, 201), ch.text
        r = client.post("/api/chat/messages", json={
            "channel_id": ch.json()["id"], "author_id": "user",
            "author_type": "user", "content": "archive me",
            "content_type": "text"})
        assert r.status_code == 200, r.text
        after = client.get("/api/archive/stats").json()["total_events"]
        assert after == before + 1

        # every holder is the very same object
        from tinyagentos.routes import memory_management
        request = type("R", (), {"app": app})()
        assert memory_management._backend(request)._archive is writer
        assert app.state.channel_hub_router._archive is writer
        assert aw.get_archive_writer(adir) is writer
        assert archive_constructions.count(_real(adir)) == 1
        assert aw.held_archive_writers() == {_real(adir): "controller.app"}


def test_shutdown_releases_and_a_second_start_works(release_dir, archive_constructions):
    adir = release_dir / "archive"
    app1 = create_app(data_dir=release_dir)
    with TestClient(app1):
        first = app1.state.archive
    assert aw.held_archive_writers() == {}
    assert aw.get_archive_writer(adir) is None
    assert first._conn is None, "writer connection left open after shutdown"

    app2 = create_app(data_dir=release_dir)
    with _authed_client(app2) as client:
        second = app2.state.archive
        assert second is not first
        assert aw.held_archive_writers() == {_real(adir): "controller.app"}
        r = client.get("/api/archive/stats")
        assert r.status_code == 200, r.text
        assert r.json()["total_events"] >= 1
    assert aw.held_archive_writers() == {}
    assert archive_constructions.count(_real(adir)) == 2


def test_second_app_over_a_live_directory_is_refused(release_dir):
    app1 = create_app(data_dir=release_dir)
    with TestClient(app1):
        app2 = create_app(data_dir=release_dir)
        with pytest.raises(RuntimeError, match="controller.app"):
            with TestClient(app2):
                pass
        # the refused start must not have released the live writer
        assert aw.held_archive_writers() == {_real(release_dir / "archive"): "controller.app"}
        assert aw.get_archive_writer(release_dir / "archive") is app1.state.archive


def test_failed_startup_does_not_leave_the_writer_held(release_dir, monkeypatch):
    async def boom(self):
        raise OSError("disk went away")

    monkeypatch.setattr(taosmd_archive.ArchiveStore, "init", boom)
    app = create_app(data_dir=release_dir)
    with pytest.raises(OSError, match="disk went away"):
        with TestClient(app):
            pass
    assert aw.held_archive_writers() == {}


def test_create_app_without_lifespan_takes_no_lock(release_dir):
    """The eager part of create_app only wires objects; the writer is taken by
    the lifespan, so tests that never start the lifespan are unaffected."""
    create_app(data_dir=release_dir)
    create_app(data_dir=release_dir)
    assert aw.held_archive_writers() == {}


def test_job_worker_paths_come_from_the_data_root_not_the_cwd(tmp_path, monkeypatch):
    """JobWorker is not constructed anywhere in tinyagentos/; it only builds
    read-side catalog objects.  Its defaults must not depend on the cwd."""
    from tinyagentos.scheduling.job_worker import JobWorker

    monkeypatch.chdir(tmp_path)
    root = tmp_path / "elsewhere"
    worker = JobWorker(queue=None, data_dir=root)
    assert worker._path({}, "archive_dir", "archive") == str(root / "archive")
    assert worker._path({}, "catalog_db", "session-catalog.db") == str(root / "session-catalog.db")
    assert worker._path({"archive_dir": "/x/y"}, "archive_dir", "archive") == "/x/y"
