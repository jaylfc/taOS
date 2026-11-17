"""taos-wallpapers package: tree lint, index.json, image sizes and the taos-wallpaper helper."""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "distro" / "packages" / "taos-wallpapers"
CLI = PKG / "usr" / "bin" / "taos-wallpaper"
WALLS = PKG / "usr" / "share" / "taos" / "wallpapers"
INDEX = WALLS / "index.json"
SETUP = REPO / "distro" / "setup.sh"
SHELLS = ["sh"] + (["/bin/dash"] if Path("/bin/dash").exists() else [])
SIZES = {"3840x2160", "3840x1200", "1080x2400"}


def jpeg_size(path: Path) -> tuple[int, int]:
    """Width and height from the first SOF marker (no imaging dependency)."""
    data = path.read_bytes()
    assert data[:2] == b"\xff\xd8", f"{path} is not a JPEG"
    i = 2
    while i < len(data):
        assert data[i] == 0xFF, f"{path}: bad marker at {i}"
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + length
    raise AssertionError(f"{path}: no SOF marker")


def run(*args, shell="sh", root: Path = WALLS):
    env = {"PATH": os.environ["PATH"], "TAOS_WALLPAPER_DIR": str(root)}
    return subprocess.run([shell, str(CLI), *args], env=env, capture_output=True, text=True)


def test_index_valid_and_files_exist():
    idx = json.loads(INDEX.read_text())
    assert [w["id"] for w in idx["wallpapers"]] == ["neural", "flow", "mesh", "terrain"]
    ids = {w["id"] for w in idx["wallpapers"]}
    assert idx["default"] in ids
    assert [w["id"] for w in idx["wallpapers"] if w["default"]] == [idx["default"]]
    listed = set()
    for w in idx["wallpapers"]:
        assert w["title"] and w["variants"] == ["dark", "light"]
        assert set(w["files"]) == set(w["variants"])
        for variant, by_size in w["files"].items():
            assert set(by_size) == SIZES
            for size, rel in by_size.items():
                f = WALLS / rel
                assert f.is_file() and f.stat().st_size > 10000, rel
                assert rel == f"{w['id']}/{variant}-{size}.jpg"
                listed.add(f)
                assert "x".join(map(str, jpeg_size(f))) == size, rel
    on_disk = {p for p in WALLS.rglob("*") if p.is_file() and p != INDEX}
    assert on_disk == listed
    assert (WALLS / idx["wallpapers"][0]["files"]["dark"]["3840x2160"]).is_file()


def test_package_size_reasonable():
    total = sum(p.stat().st_size for p in PKG.rglob("*") if p.is_file())
    assert total < 40 * 1024 * 1024


def test_images_are_not_blank():
    pil = pytest.importorskip("PIL.Image")
    from PIL import ImageStat
    for jpg in sorted(WALLS.rglob("*.jpg")):
        im = pil.open(jpg).convert("L")
        im.thumbnail((480, 480))
        stat = ImageStat.Stat(im)
        assert stat.stddev[0] > 1.0, f"{jpg} looks blank"
        assert ("dark" in jpg.name) == (stat.mean[0] < 100), f"{jpg}: variant brightness"


def test_list_marks_default():
    res = run("list")
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == ["neural (default)", "flow", "mesh", "terrain"]


@pytest.mark.parametrize("shell", SHELLS)
def test_path_defaults_and_sizes(shell):
    res = run("path", shell=shell)
    assert res.stdout.strip() == str(WALLS / "neural/dark-3840x2160.jpg"), res.stderr
    cases = [
        ("3840x2160", "3840x2160"), ("3840x1200", "3840x1200"), ("1080x2400", "1080x2400"),
        ("2560x1440", "3840x2160"), ("1920x1080", "3840x2160"),
        ("1170x2532", "1080x2400"), ("720x1600", "1080x2400"), ("5120x1440", "3840x1200"),
        ("7680x4320", "3840x2160"),
    ]
    for want, got in cases:
        res = run("path", "--variant", "light", "--size", want, "mesh", shell=shell)
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == str(WALLS / f"mesh/light-{got}.jpg"), want


def test_path_errors():
    for args in (("path", "nosuch"), ("path", "--variant", "sepia"), ("path", "--size", "big"),
                 ("path", "--size"), ("bogus",), ()):
        res = run(*args)
        assert res.returncode != 0 and res.stderr, args
    assert run("path", "nosuch").returncode == 1
    assert run("list", root=Path("/nonexistent")).returncode == 1


def test_scripts_pass_sh_n_and_dash():
    scripts = [CLI, SETUP, REPO / "distro/build-debs.sh"]
    for script in scripts:
        for shell in SHELLS:
            res = subprocess.run([shell, "-n", str(script)], capture_output=True, text=True)
            assert res.returncode == 0, f"{script}: {res.stderr}"
        assert os.access(script, os.X_OK), script


def test_control_and_tree():
    ctl = (PKG / "DEBIAN/control").read_text().splitlines()
    for line in ("Package: taos-wallpapers", "Version: 0.1.0", "Architecture: all",
                 "Maintainer: jaylfc <jaylfc25@gmail.com>"):
        assert line in ctl
    assert {p.name for p in (PKG / "DEBIAN").iterdir()} == {"control"}
    assert {p.name for p in WALLS.iterdir()} == {"index.json", "neural", "flow", "mesh", "terrain"}


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not installed")
def test_package_builds(tmp_path: Path):
    deb = tmp_path / "taos-wallpapers.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(PKG), str(deb)],
                   check=True, capture_output=True)
    listing = subprocess.run(["dpkg-deb", "-c", str(deb)], check=True,
                             capture_output=True, text=True).stdout
    for f in ("./usr/bin/taos-wallpaper", "./usr/share/taos/wallpapers/index.json",
              "./usr/share/taos/wallpapers/neural/dark-3840x2160.jpg",
              "./usr/share/taos/wallpapers/terrain/light-1080x2400.jpg"):
        assert f in listing


def test_setup_sh_step7_readme_changelog():
    text = SETUP.read_text()
    assert "Step 7: wallpapers (taos-wallpapers)" in text
    assert "taos-wallpapers_*_all.deb" in text and "taos-wallpaper list" in text
    assert text.index("Step 6: power") < text.index("Step 7: wallpapers")
    readme = (REPO / "distro/README.md").read_text()
    assert "| 7 | taos-wallpapers |" in readme and "## Wallpapers" in readme
    frag = (REPO / "changelog.d/distro-wallpapers.md").read_text()
    assert frag.startswith("### Added\n")


def test_no_em_dashes_or_ai_mentions():
    files = [CLI, SETUP, REPO / "distro/README.md", Path(__file__), INDEX,
             REPO / "changelog.d/distro-wallpapers.md", PKG / "DEBIAN/control"]
    for p in files:
        assert chr(0x2014) not in p.read_text(), p
