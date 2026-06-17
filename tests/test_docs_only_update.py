"""Docs-only commits must not surface as 'Update available'."""
import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.auto_update import (
    AutoUpdateService,
    changes_are_docs_only,
    is_documentation_path,
)


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _sha(cwd, ref="HEAD"):
    return subprocess.run(
        ["git", "rev-parse", ref],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "checkout", "-q", "-b", "master")
    (r / "code.py").write_text("v1")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "base")
    return r


def test_is_documentation_path():
    assert is_documentation_path("docs/STATUS.md") is True
    assert is_documentation_path("README.md") is True
    assert is_documentation_path("notes.txt") is True
    assert is_documentation_path("guide.rst") is True
    assert is_documentation_path("tinyagentos/foo.py") is False
    assert is_documentation_path("docs/scripts/foo.py") is False
    assert is_documentation_path("docs/config.yaml") is False


def test_changes_are_docs_only_true_for_docs_dir(repo):
    old = _sha(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "STATUS.md").write_text("handoff")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "docs only")
    new = _sha(repo)
    assert asyncio.run(changes_are_docs_only(repo, old, new)) is True


def test_changes_are_docs_only_true_for_root_md(repo):
    old = _sha(repo)
    (repo / "README.md").write_text("# taOS")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "readme")
    new = _sha(repo)
    assert asyncio.run(changes_are_docs_only(repo, old, new)) is True


def test_changes_are_docs_only_false_for_code(repo):
    old = _sha(repo)
    (repo / "code.py").write_text("v2")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "code change")
    new = _sha(repo)
    assert asyncio.run(changes_are_docs_only(repo, old, new)) is False


def test_changes_are_docs_only_false_for_mixed(repo):
    old = _sha(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "STATUS.md").write_text("note")
    (repo / "code.py").write_text("v2")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "mixed")
    new = _sha(repo)
    assert asyncio.run(changes_are_docs_only(repo, old, new)) is False


@pytest.mark.asyncio
async def test_update_check_hides_docs_only_diff(client, monkeypatch):
    import asyncio as _asyncio

    import tinyagentos.routes.settings as s

    class _FakeProc:
        def __init__(self, out=b""):
            self._out = out

        async def communicate(self):
            return self._out, b""

    async def fake_resolve(store, project_dir):
        return "dev"

    async def fake_exec(*args, **kwargs):
        if args[1] == "rev-parse":
            ref = args[2]
            if ref == "HEAD":
                return _FakeProc(b"aaa1111\n")
            return _FakeProc(b"bbb2222\n")
        if args[1] == "log":
            return _FakeProc(b"abc def\n")
        return _FakeProc()

    async def fake_strictly_ahead(project_dir, local_sha, remote_sha):
        return True

    async def fake_docs_only(project_dir, local_sha, remote_sha):
        return True

    monkeypatch.setattr(s, "resolve_tracked_branch", fake_resolve, raising=False)
    monkeypatch.setattr(_asyncio, "create_subprocess_exec", fake_exec, raising=False)
    monkeypatch.setattr(
        "tinyagentos.auto_update.remote_is_strictly_ahead",
        fake_strictly_ahead,
        raising=False,
    )
    monkeypatch.setattr(
        "tinyagentos.auto_update.changes_are_docs_only",
        fake_docs_only,
        raising=False,
    )

    r = await client.get("/api/settings/update-check")
    assert r.status_code == 200
    assert r.json()["has_updates"] is False


@pytest.mark.asyncio
async def test_auto_update_skips_docs_only_notification(monkeypatch):
    monkeypatch.delenv("TAOS_NO_UPDATE_PING", raising=False)

    REMOTE = "aabbccdd" * 5
    CURRENT = "11111111" * 5

    notif_count = []

    async def _fake_notify(current, new_commit):
        notif_count.append(new_commit)

    settings = MagicMock()
    settings.get_preference = AsyncMock(
        return_value={
            "check_enabled": True,
            "update_ping_enabled": False,
            "last_notified_commit": None,
        }
    )
    settings.save_preference = AsyncMock()

    svc = AutoUpdateService(
        project_dir=None,
        notif_store=MagicMock(),
        settings_store=settings,
        app_state=None,
    )
    svc._notify_available = _fake_notify

    with patch.object(svc, "_probe_remote", AsyncMock(return_value=REMOTE)):
        with patch.object(svc, "_current_commit", AsyncMock(return_value=CURRENT)):
            with patch(
                "tinyagentos.auto_update.remote_is_strictly_ahead",
                AsyncMock(return_value=True),
            ):
                with patch(
                    "tinyagentos.auto_update.changes_are_docs_only",
                    AsyncMock(return_value=True),
                ):
                    with patch("tinyagentos.auto_update.poll_frameworks", AsyncMock()):
                        with patch("tinyagentos.frameworks.FRAMEWORKS", {}):
                            await svc._run_once()

    assert len(notif_count) == 0