import asyncio
import os
import time

import pytest

from tinyagentos.desktop_rebuild import _is_bundle_stale, rebuild_desktop_bundle_if_stale


# ---------------------------------------------------------------------------
# _is_bundle_stale
# ---------------------------------------------------------------------------


def test_is_bundle_stale_returns_true_when_no_bundle(tmp_path):
    """No index.html means stale (never built)."""
    (tmp_path / "desktop" / "src").mkdir(parents=True)
    (tmp_path / "desktop" / "src" / "App.tsx").write_text("// app")
    assert _is_bundle_stale(tmp_path) is True


def test_is_bundle_stale_returns_false_when_no_desktop_dir(tmp_path):
    """Backend-only deploys with no desktop/ are not considered stale."""
    assert _is_bundle_stale(tmp_path) is False


def test_is_bundle_stale_returns_false_when_bundle_newer(tmp_path):
    """Bundle newer than all source files → not stale."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "App.tsx").write_text("// app")
    static_dir = tmp_path / "static" / "desktop"
    static_dir.mkdir(parents=True)
    bundle = static_dir / "index.html"
    bundle.write_text("<html />")
    # Make bundle 60 s newer than source
    os.utime(bundle, (time.time() + 60, time.time() + 60))
    assert _is_bundle_stale(tmp_path) is False


def test_is_bundle_stale_returns_true_when_source_newer(tmp_path):
    """Any source file newer than bundle → stale."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    static_dir = tmp_path / "static" / "desktop"
    static_dir.mkdir(parents=True)
    bundle = static_dir / "index.html"
    bundle.write_text("<html />")
    src_file = src_dir / "App.tsx"
    src_file.write_text("// edit")
    os.utime(src_file, (time.time() + 60, time.time() + 60))
    assert _is_bundle_stale(tmp_path) is True


def test_is_bundle_stale_returns_false_when_no_src_dir(tmp_path):
    """desktop/ exists but no src/ → nothing to compare; not stale."""
    (tmp_path / "desktop").mkdir()
    static_dir = tmp_path / "static" / "desktop"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<html />")
    assert _is_bundle_stale(tmp_path) is False


def test_is_bundle_stale_returns_true_despite_fresh_bundle_with_future_mtime(tmp_path):
    """Mtime check can falsely report stale when source files carry future mtimes
    (e.g., from clock-skewed CI commits) even though the bundle was just fetched
    and index.html was touched fresh.  The provenance check below rescues this."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    static_dir = tmp_path / "static" / "desktop"
    static_dir.mkdir(parents=True)
    bundle = static_dir / "index.html"
    bundle.write_text("<html />")
    src_file = src_dir / "App.tsx"
    src_file.write_text("// app")
    # Simulate: bundle was just installed (touched 10 s ago), but source has a
    # future mtime (clock skew during the original commit).
    os.utime(bundle, (time.time() - 10, time.time() - 10))
    os.utime(src_file, (time.time() + 3600, time.time() + 3600))
    assert _is_bundle_stale(tmp_path) is True


# ---------------------------------------------------------------------------
# rebuild_desktop_bundle_if_stale
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_skips_when_bundle_current(tmp_path, monkeypatch):
    """If bundle is current, no subprocess is spawned."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    static_dir = tmp_path / "static" / "desktop"
    static_dir.mkdir(parents=True)
    bundle = static_dir / "index.html"
    bundle.write_text("<html />")
    os.utime(bundle, (time.time() + 60, time.time() + 60))

    called = []

    async def fake_exec(*args, **kwargs):
        called.append(args)
        raise AssertionError("subprocess should NOT be called when bundle is current")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert result.rebuilt is False
    assert called == []


@pytest.mark.asyncio
async def test_rebuild_skips_when_no_package_json(tmp_path):
    """desktop/src exists (stale) but no package.json → skip without error."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "App.tsx").write_text("// stale source")
    # Deliberately no package.json
    result = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert result.rebuilt is False
    assert "package.json" in result.message.lower() or "skipping" in result.message.lower()


@pytest.mark.asyncio
async def test_rebuild_handles_npm_missing(tmp_path, monkeypatch):
    """If npm not on PATH, return graceful (False, msg) — don't crash."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "App.tsx").write_text("// stale")
    (tmp_path / "desktop" / "package.json").write_text('{"name":"x"}')

    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("npm not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert result.rebuilt is False
    assert "npm" in result.message.lower()


@pytest.mark.asyncio
async def test_rebuild_returns_true_on_npm_install_failure(tmp_path, monkeypatch):
    """If npm install exits non-zero, return (True, error_msg)."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "App.tsx").write_text("// stale")
    (tmp_path / "desktop" / "package.json").write_text('{"name":"x"}')

    class FakeProc:
        returncode = 1

        async def communicate(self):
            return b"", b"install error"

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert result.rebuilt is True
    assert result.success is False
    assert "npm install failed" in result.message


@pytest.mark.asyncio
async def test_rebuild_returns_true_on_npm_build_failure(tmp_path, monkeypatch):
    """If npm install succeeds but npm run build fails, return (True, error_msg)."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "App.tsx").write_text("// stale")
    (tmp_path / "desktop" / "package.json").write_text('{"name":"x"}')

    class Proc:
        def __init__(self, rc, err=b""):
            self.returncode = rc
            self._err = err

        async def communicate(self):
            return b"", self._err

    async def fake_exec(*args, **kwargs):
        # The prebuilt-bundle check probes `git rev-parse HEAD:desktop` first;
        # return an empty SHA so it skips straight to the local npm build.
        if args[0] == "git":
            return Proc(0, b"")
        # npm ci / npm install succeed; npm run build fails.
        if args[0] == "npm" and args[1] == "run":
            return Proc(1, b"build error")
        return Proc(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert result.rebuilt is True
    assert result.success is False
    assert "npm run build failed" in result.message


@pytest.mark.asyncio
async def test_rebuild_success(tmp_path, monkeypatch):
    """Happy path: both npm commands succeed → (True, 'rebuilt successfully')."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "App.tsx").write_text("// stale")
    (tmp_path / "desktop" / "package.json").write_text('{"name":"x"}')

    class OkProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        return OkProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert result.rebuilt is True
    assert result.success is True
    assert "successfully" in result.message.lower()


@pytest.mark.asyncio
async def test_rebuild_falls_back_to_npm_install_when_ci_fails(tmp_path, monkeypatch):
    """npm ci failure falls back to npm install, restores the lockfile, then builds."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "App.tsx").write_text("// stale")
    (tmp_path / "desktop" / "package.json").write_text('{"name":"x"}')

    calls = []

    class Proc:
        def __init__(self, rc):
            self.returncode = rc

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        if args[0] == "npm" and args[1] == "ci":
            return Proc(1)  # ci fails -> fallback path
        return Proc(0)  # npm install, git checkout, npm run build all succeed

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert result.rebuilt is True
    assert result.success is True
    cmds = [(a[0], a[1]) for a in calls]
    assert ("npm", "ci") in cmds
    assert ("npm", "install") in cmds  # fallback ran
    assert ("git", "checkout") in cmds  # lockfile restored after install


@pytest.mark.asyncio
async def test_rebuild_skips_when_provenance_matches_despite_mtime(tmp_path, monkeypatch):
    """A provenance marker matching current tree SHA should skip rebuild
    even when the mtime check would falsely report stale (e.g. future-mtime
    source files from clock-skewed commits after a fresh bundle fetch)."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    static_dir = tmp_path / "static" / "desktop"
    static_dir.mkdir(parents=True)
    bundle = static_dir / "index.html"
    bundle.write_text("<html />")
    src_file = src_dir / "App.tsx"
    src_file.write_text("// app")
    # False-positive mtime scenario: source file is 1 h in the future.
    os.utime(bundle, (time.time() - 10, time.time() - 10))
    os.utime(src_file, (time.time() + 3600, time.time() + 3600))
    # Provenance marker says the bundle matches current source.
    (static_dir / ".taos-bundle-provenance").write_text("MATCHING_SHA")

    called = []

    async def fake_exec(*args, **kwargs):
        called.append(args)
        if args[0] == "git":
            class Proc:
                returncode = 0
                async def communicate(self):
                    return b"MATCHING_SHA\n", b""
            return Proc()
        raise AssertionError("subprocess should NOT be called when provenance matches")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert result.rebuilt is False
    assert result.success is True
    assert "provenance" in result.message.lower()
    # Only the provenance-check git call is expected; no npm/build subprocesses.
    assert all(c[0] == "git" for c in called)


# ---------------------------------------------------------------------------
# npm-install gate: only reinstall when package-lock.json changes
# ---------------------------------------------------------------------------

from tinyagentos.desktop_rebuild import (
    _deps_install_needed,
    _record_deps_install,
    _lockfile_hash,
)


def _mk_desktop(tmp_path, *, lock="{}", node_modules=True):
    d = tmp_path / "desktop"
    d.mkdir(parents=True, exist_ok=True)
    if lock is not None:
        (d / "package-lock.json").write_text(lock)
    if node_modules:
        (d / "node_modules").mkdir(exist_ok=True)
    return d


def test_deps_needed_when_node_modules_missing(tmp_path):
    d = _mk_desktop(tmp_path, node_modules=False)
    assert _deps_install_needed(d) is True


def test_deps_needed_when_no_lockfile(tmp_path):
    d = _mk_desktop(tmp_path, lock=None)
    assert _deps_install_needed(d) is True


def test_deps_needed_when_no_marker(tmp_path):
    """node_modules + lockfile but never recorded → must install."""
    d = _mk_desktop(tmp_path)
    assert _deps_install_needed(d) is True


def test_deps_skipped_after_record(tmp_path):
    d = _mk_desktop(tmp_path, lock='{"v":1}')
    _record_deps_install(d)
    assert _deps_install_needed(d) is False


def test_deps_needed_again_when_lockfile_changes(tmp_path):
    d = _mk_desktop(tmp_path, lock='{"v":1}')
    _record_deps_install(d)
    assert _deps_install_needed(d) is False
    # A dependency bump rewrites package-lock.json → hash changes → reinstall.
    (d / "package-lock.json").write_text('{"v":2}')
    assert _deps_install_needed(d) is True


def test_lockfile_hash_none_without_file(tmp_path):
    d = tmp_path / "desktop"
    d.mkdir()
    assert _lockfile_hash(d) is None


# ---------------------------------------------------------------------------
# prebuilt bundle: download instead of building locally when the source matches
# ---------------------------------------------------------------------------

from tinyagentos.desktop_rebuild import _try_prebuilt_desktop_bundle


def _git_proc(sha: str):
    class GitProc:
        returncode = 0

        async def communicate(self):
            return (sha + "\n").encode(), b""

    async def fake_exec(*args, **kwargs):
        return GitProc()

    return fake_exec


@pytest.mark.asyncio
async def test_prebuilt_bundle_installed_on_tree_match(tmp_path, monkeypatch):
    """Matching tree SHA -> bundle is downloaded + swapped into static/desktop/."""
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _git_proc("SHA123"))

    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = b"<html>ok</html>"
        info = tarfile.TarInfo("desktop/index.html")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    tarball = buf.getvalue()

    import hashlib

    async def fake_to_thread(_fn, url, **_kwargs):
        if url.endswith("desktop-tree.txt"):
            return "SHA123"
        if url.endswith("desktop-bundle.sha256"):
            return hashlib.sha256(tarball).hexdigest()
        return tarball

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    assert await _try_prebuilt_desktop_bundle(tmp_path) is True
    assert (tmp_path / "static" / "desktop" / "index.html").read_text() == "<html>ok</html>"
    assert (tmp_path / "static" / "desktop" / ".taos-bundle-provenance").read_text() == "SHA123"


@pytest.mark.asyncio
async def test_prebuilt_bundle_skipped_on_tree_mismatch(tmp_path, monkeypatch):
    """Mismatched tree SHA -> returns False and never downloads the bundle."""
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _git_proc("LOCAL"))

    calls = []

    async def fake_to_thread(_fn, url, **_kwargs):
        calls.append(url)
        return "REMOTE_DIFFERENT"

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    assert await _try_prebuilt_desktop_bundle(tmp_path) is False
    assert calls and all(c.endswith("desktop-tree.txt") for c in calls)


@pytest.mark.asyncio
async def test_prebuilt_bundle_skipped_when_git_missing(tmp_path, monkeypatch):
    """No git on PATH -> returns False (falls back to a local build)."""
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await _try_prebuilt_desktop_bundle(tmp_path) is False


@pytest.mark.asyncio
async def test_prebuilt_bundle_rejected_on_checksum_mismatch(tmp_path, monkeypatch):
    """Tree matches but the published SHA256 does not -> build locally, no install."""
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _git_proc("SHA123"))

    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"x"
        info = tarfile.TarInfo("desktop/index.html")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    tarball = buf.getvalue()

    async def fake_to_thread(_fn, url, **_kwargs):
        if url.endswith("desktop-tree.txt"):
            return "SHA123"
        if url.endswith("desktop-bundle.sha256"):
            return "deadbeef" * 8  # wrong digest
        return tarball

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    assert await _try_prebuilt_desktop_bundle(tmp_path) is False
    assert not (tmp_path / "static" / "desktop").exists()
