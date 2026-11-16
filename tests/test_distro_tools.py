"""taos-tools package: taos-screenshot on the Wayland session and the console."""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKGS = REPO / "distro" / "packages"
PKG = PKGS / "taos-tools"
SHOT = PKG / "usr" / "bin" / "taos-screenshot"


def _png(w: int, h: int) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _stub(bindir: Path, name: str, png: Path | None, log: Path | None = None) -> None:
    """A stub grim/fbgrab: writes `png` to its last argument (or nothing)."""
    bindir.mkdir(exist_ok=True)
    body = "#!/bin/sh\n"
    if log:
        body += f'echo "$@" > {log}\n'
        body += f'echo "$XDG_RUNTIME_DIR $WAYLAND_DISPLAY" >> {log}\n'
    if png:
        body += 'for a; do out=$a; done\n' + f'cat {png} > "$out"\n'
    p = bindir / name
    p.write_text(body)
    p.chmod(0o755)


@pytest.fixture
def env(tmp_path: Path):
    rundir = tmp_path / "run"
    rundir.mkdir()
    fb = tmp_path / "fb0"
    fb.write_text("")
    png = tmp_path / "small.png"
    png.write_bytes(_png(7, 3))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    e = {"PATH": f"{bindir}:/usr/bin:/bin", "TAOS_SHOT_RUNDIR": str(rundir),
         "TAOS_SHOT_FB": str(tmp_path / "nofb")}
    return {"tmp": tmp_path, "run": rundir, "fb": fb, "png": png, "bin": bindir, "env": e}


def _run(env, *args, shell="sh"):
    return subprocess.run([shell, str(SHOT), *args], env=env["env"], capture_output=True, text=True)


def _wayland_sock(env) -> socket.socket:
    d = env["run"] / "1000"
    d.mkdir(exist_ok=True)
    # unix socket paths are short; tmp_path is fine on Linux.
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(d / "wayland-1"))
    return s


def test_wayland_path_with_output_and_json(env):
    s = _wayland_sock(env)
    try:
        log = env["tmp"] / "grim.log"
        _stub(env["bin"], "grim", env["png"], log)
        out = env["tmp"] / "shot.png"
        res = _run(env, "--json", "--output", "DP-1", str(out))
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert data == {"path": str(out), "source": "wayland", "width": 7, "height": 3}
        assert out.stat().st_mode & 0o777 == 0o644
        args, rt = log.read_text().splitlines()
        assert args.split() == ["-o", "DP-1", str(out)]
        assert rt == f"{env['run'] / '1000'} wayland-1"
    finally:
        s.close()


def test_wayland_plain_output_and_default_path(env):
    s = _wayland_sock(env)
    try:
        _stub(env["bin"], "grim", env["png"])
        res = _run(env, str(env["tmp"] / "a.png"))
        assert res.returncode == 0
        assert res.stdout.strip() == f"{env['tmp'] / 'a.png'} (wayland)"
        res = _run(env)
        assert res.returncode == 0
        path = res.stdout.split()[0]
        try:
            assert path.startswith("/tmp/taos-screenshot-") and path.endswith(".png")
            assert Path(path).stat().st_size > 0
        finally:
            Path(path).unlink(missing_ok=True)
    finally:
        s.close()


def test_console_path(env):
    env["env"]["TAOS_SHOT_FB"] = str(env["fb"])
    log = env["tmp"] / "fb.log"
    _stub(env["bin"], "fbgrab", env["png"], log)
    out = env["tmp"] / "c.png"
    res = _run(env, "--json", str(out))
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == {"path": str(out), "source": "console", "width": 7, "height": 3}
    assert log.read_text().split()[:3] == ["-d", str(env["fb"]), str(out)]


def test_neither_available_exits_1_without_path(env):
    res = _run(env, str(env["tmp"] / "x.png"))
    assert res.returncode == 1
    assert res.stdout == ""
    assert "no Wayland session" in res.stderr


def test_stub_creating_no_file_exits_1(env):
    env["env"]["TAOS_SHOT_FB"] = str(env["fb"])
    _stub(env["bin"], "fbgrab", None)
    out = env["tmp"] / "none.png"
    res = _run(env, str(out))
    assert res.returncode == 1 and res.stdout == ""
    # A stale file at the target must not count as a capture either.
    out.write_bytes(_png(1, 1))
    res = _run(env, str(out))
    assert res.returncode == 1 and res.stdout == ""


def test_empty_output_exits_1(env):
    env["env"]["TAOS_SHOT_FB"] = str(env["fb"])
    empty = env["tmp"] / "empty"
    empty.write_bytes(b"")
    _stub(env["bin"], "fbgrab", empty)
    res = _run(env, str(env["tmp"] / "e.png"))
    assert res.returncode == 1 and res.stdout == ""


def test_json_omits_size_for_non_png(env):
    env["env"]["TAOS_SHOT_FB"] = str(env["fb"])
    junk = env["tmp"] / "junk"
    junk.write_bytes(b"not a png at all, just bytes")
    _stub(env["bin"], "fbgrab", junk)
    res = _run(env, "--json", str(env["tmp"] / "j.png"))
    assert res.returncode == 0
    data = json.loads(res.stdout)
    assert set(data) == {"path", "source"}


@pytest.mark.skipif(not Path("/bin/dash").exists(), reason="dash not installed")
def test_runs_under_dash(env):
    env["env"]["TAOS_SHOT_FB"] = str(env["fb"])
    _stub(env["bin"], "fbgrab", env["png"])
    res = _run(env, "--json", str(env["tmp"] / "d.png"), shell="/bin/dash")
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["width"] == 7


def test_scripts_pass_sh_n_and_dash():
    scripts = [SHOT] + [p for p in (PKG / "DEBIAN").iterdir() if p.name not in ("control", "triggers")]
    scripts += [REPO / "distro/setup.sh", REPO / "distro/build-debs.sh"]
    scripts += [p for pkg in PKGS.iterdir() if pkg.name in ("taos-shell", "taos-branding")
                for p in (pkg / "DEBIAN").iterdir() if p.name not in ("control", "triggers")]
    for script in scripts:
        for shell in ("sh", "/bin/dash"):
            if shell.startswith("/") and not Path(shell).exists():
                continue
            res = subprocess.run([shell, "-n", str(script)], capture_output=True, text=True)
            assert res.returncode == 0, f"{script}: {res.stderr}"
        assert os.access(script, os.X_OK) or script.name == "triggers", script


def test_control_fields():
    text = (PKG / "DEBIAN/control").read_text()
    assert "Package: taos-tools" in text and "Version: 0.1.0" in text
    assert "Architecture: all" in text and "Depends: grim, fbcat" in text
    assert "Maintainer: jaylfc <jaylfc25@gmail.com>" in text
    assert not (REPO / "distro/taos-screenshot.draft").exists()


def test_no_em_dashes():
    for p in list(PKG.rglob("*")) + list((PKGS / "taos-shell").rglob("*")) \
            + list((PKGS / "taos-branding").rglob("*")) + [REPO / "distro/README.md"]:
        if p.is_file():
            assert "—" not in p.read_text(), p


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not installed")
def test_all_three_debs_build(tmp_path: Path):
    for name, marker in (("taos-tools", "./usr/bin/taos-screenshot"),
                         ("taos-shell", "./etc/update-motd.d/05-taos"),
                         ("taos-branding", "./usr/lib/taos-branding/apply")):
        deb = tmp_path / f"{name}.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(PKGS / name), str(deb)],
                       check=True, capture_output=True)
        listing = subprocess.run(["dpkg-deb", "-c", str(deb)], check=True,
                                 capture_output=True, text=True).stdout
        assert marker in listing
    info = subprocess.run(["dpkg-deb", "-I", str(tmp_path / "taos-tools.deb")], check=True,
                          capture_output=True, text=True).stdout
    assert "grim" in info and "fbcat" in info
