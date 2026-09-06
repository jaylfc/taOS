"""Archive-bomb guards on the .taostheme install path.

A valid small theme installs today -- the gap was that a 40 KB upload could
declare gigabytes of uncompressed content and `zf.read()` would inflate all of
it into memory on a 4 GB Pi. These bombs are real: each archive declares more
than the shipped cap while staying tiny on the wire, so they exercise the
constants that actually ship, not a monkeypatched stand-in.
"""

import io
import zipfile

import pytest
import yaml

from tinyagentos import safe_archive
from tinyagentos.themes.package import extract_theme_package, ThemePackageError

MANIFEST = {
    "id": "matrix-terminal",
    "name": "Matrix Terminal",
    "version": "1.0.0",
    "tokens": {"--color-shell-bg": "#000000", "--color-accent": "#00ff46"},
    "structure": {"dock": {"variant": "windows-taskbar"}},
    "effects": [{"module": "crt", "params": {}}],
    "requires": ["assistant", "launcher"],
}

_MIB = 1024 * 1024


def _bomb_zip(*, members: int, member_bytes: int) -> bytes:
    """A .taostheme declaring `members` * `member_bytes` of zeros.

    Zeros deflate to almost nothing (compresslevel=1 keeps the build under a
    second), so the archive is a few hundred KB on the wire while its central
    directory declares hundreds of MB -- exactly the shape of a real bomb.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr("theme.yaml", yaml.safe_dump(MANIFEST))
        chunk = b"\0" * _MIB
        for i in range(members):
            with z.open(f"assets/pad{i}.bin", "w") as fh:
                for _ in range(member_bytes // _MIB):
                    fh.write(chunk)
    return buf.getvalue()


def test_rejects_zip_declaring_more_than_the_uncompressed_cap(tmp_path):
    # Five 52 MiB members: each is under the per-member cap, the 260 MiB total
    # is over MAX_UNCOMPRESSED_BYTES.
    data = _bomb_zip(members=5, member_bytes=52 * _MIB)
    assert len(data) < 4 * _MIB, "the bomb must stay small on the wire"
    with pytest.raises(ThemePackageError, match="uncompressed size too large"):
        extract_theme_package(data, themes_root=tmp_path)
    assert not (tmp_path / "matrix-terminal").exists()


def test_rejects_zip_member_over_the_per_member_cap(tmp_path):
    data = _bomb_zip(members=1, member_bytes=safe_archive.MAX_MEMBER_BYTES + _MIB)
    with pytest.raises(ThemePackageError, match="member too large"):
        extract_theme_package(data, themes_root=tmp_path)
    assert not (tmp_path / "matrix-terminal").exists()


def test_rejects_zip_with_more_members_than_the_cap(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr("theme.yaml", yaml.safe_dump(MANIFEST))
        for i in range(safe_archive.MAX_MEMBERS + 1):
            z.writestr(f"assets/f{i}.txt", "x")
    with pytest.raises(ThemePackageError, match="too many files"):
        extract_theme_package(buf.getvalue(), themes_root=tmp_path)
    assert not (tmp_path / "matrix-terminal").exists()


def test_valid_theme_still_installs(tmp_path):
    """The guards must not turn a legitimate package away."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("theme.yaml", yaml.safe_dump(MANIFEST))
        z.writestr("assets/wall.png", "x")
    manifest = extract_theme_package(buf.getvalue(), themes_root=tmp_path)
    assert manifest["id"] == "matrix-terminal"
    assert (tmp_path / "matrix-terminal" / "assets" / "wall.png").exists()


@pytest.mark.asyncio
async def test_install_route_rejects_an_oversized_upload(client, monkeypatch):
    """The upload is capped before the body is buffered for extraction."""
    import tinyagentos.routes.themes as themes_routes

    monkeypatch.setattr(themes_routes, "_MAX_THEME_PACKAGE_BYTES", 64)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("theme.yaml", yaml.safe_dump(MANIFEST))
    resp = await client.post(
        "/api/themes/install",
        files={"package": ("matrix.taostheme", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 413
    assert "too large" in resp.json()["error"]


def test_rejects_a_member_that_resolves_to_the_theme_directory(tmp_path):
    """A "." member must be refused, not handed to write_bytes().

    The traversal guard only rejected paths outside the theme dir; a member
    resolving to the directory itself slipped through and crashed the install
    with an unhandled IsADirectoryError. userspace/package.py already rejects
    this shape -- the theme extractor has to match it.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("theme.yaml", yaml.safe_dump(MANIFEST))
        z.writestr(".", "x")
    with pytest.raises(ThemePackageError, match="unsafe path in package"):
        extract_theme_package(buf.getvalue(), themes_root=tmp_path)
