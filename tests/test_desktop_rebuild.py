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

    rebuilt, msg = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert rebuilt is False
    assert called == []


@pytest.mark.asyncio
async def test_rebuild_skips_when_no_package_json(tmp_path):
    """desktop/src exists (stale) but no package.json → skip without error."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "App.tsx").write_text("// stale source")
    # Deliberately no package.json
    rebuilt, msg = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert rebuilt is False
    assert "package.json" in msg.lower() or "skipping" in msg.lower()


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

    rebuilt, msg = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert rebuilt is False
    assert "npm" in msg.lower()


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

    rebuilt, msg = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert rebuilt is True
    assert "npm install failed" in msg


@pytest.mark.asyncio
async def test_rebuild_returns_true_on_npm_build_failure(tmp_path, monkeypatch):
    """If npm install succeeds but npm run build fails, return (True, error_msg)."""
    src_dir = tmp_path / "desktop" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "App.tsx").write_text("// stale")
    (tmp_path / "desktop" / "package.json").write_text('{"name":"x"}')

    call_count = [0]

    class InstallProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    class BuildProc:
        returncode = 1

        async def communicate(self):
            return b"", b"build error"

    async def fake_exec(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return InstallProc()
        return BuildProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    rebuilt, msg = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert rebuilt is True
    assert "npm run build failed" in msg


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

    rebuilt, msg = await rebuild_desktop_bundle_if_stale(tmp_path)
    assert rebuilt is True
    assert "successfully" in msg.lower()
