"""taos-shell package: zsh config, starship prompt, fastfetch config, login motd."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "distro" / "packages" / "taos-shell"
MOTD = PKG / "etc" / "update-motd.d" / "05-taos"
SHARE = PKG / "usr" / "share" / "taos-shell"
SETUP = REPO / "distro" / "setup.sh"


def _sh_n(path: Path) -> None:
    res = subprocess.run(["sh", "-n", str(path)], capture_output=True, text=True)
    assert res.returncode == 0, f"{path}: {res.stderr}"


def test_scripts_pass_sh_n():
    scripts = [p for p in (PKG / "DEBIAN").iterdir() if p.name != "control"]
    assert scripts
    for script in scripts + [MOTD]:
        _sh_n(script)


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
def test_zshrc_parses():
    for f in (SHARE / "zshrc", PKG / "etc/skel/.zshrc"):
        res = subprocess.run(["zsh", "-n", str(f)], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr


def test_zshrc_guards_tools():
    text = (SHARE / "zshrc").read_text()
    for needle in ("zsh-autosuggestions.zsh", "zsh-syntax-highlighting.zsh",
                   "zoxide init zsh", "starship init zsh", "/etc/taos/starship.toml",
                   "key-bindings.zsh", "batcat", "fdfind", "--icons=never"):
        assert needle in text
    assert "command -v starship" in text and "command -v zoxide" in text


def test_starship_parses_and_palette_covers_styles():
    cfg = tomllib.loads((PKG / "etc/taos/starship.toml").read_text())
    palette = cfg["palettes"][cfg["palette"]]
    assert palette
    modifiers = {"bold", "italic", "underline", "dimmed", "inverted", "blink",
                 "hidden", "strikethrough", "none", "prev_fg", "prev_bg"}
    ansi = {"black", "red", "green", "yellow", "blue", "purple", "cyan", "white",
            "bright-black", "bright-red", "bright-green", "bright-yellow",
            "bright-blue", "bright-purple", "bright-cyan", "bright-white"}

    def check(style: str) -> None:
        for tok in style.split():
            tok = re.sub(r"^(fg|bg):", "", tok)
            ok = (tok in modifiers or tok in ansi or tok in palette
                  or re.fullmatch(r"#[0-9a-fA-F]{6}", tok) or tok.isdigit())
            assert ok, f"unknown colour {tok!r} in style {style!r}"

    styles = []

    def walk(node):
        for k, v in node.items():
            if isinstance(v, dict) and k != "palettes":
                walk(v)
            elif isinstance(v, str):
                if k == "style" or k.startswith("style_") or k.endswith("_style"):
                    styles.append(v)
                if k in ("format", "right_format", "symbol") or k.endswith("_symbol"):
                    styles.extend(re.findall(r"\]\(([^)$]+)\)", v))

    walk(cfg)
    assert len(styles) >= 8
    for s in styles:
        check(s)


def test_fastfetch_config_is_json():
    text = (PKG / "etc/xdg/fastfetch/config.jsonc").read_text()
    stripped = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("//"))
    cfg = json.loads(stripped)
    assert cfg["logo"]["type"] == "file"
    assert cfg["logo"]["source"] == "/usr/share/taos-shell/logo.txt"
    for mod in ("title", "os", "kernel", "uptime", "packages", "shell", "terminal",
                "cpu", "gpu", "memory", "disk", "localip"):
        assert mod in cfg["modules"]


def test_logo_is_plain_ascii():
    raw = (SHARE / "logo.txt").read_bytes()
    raw.decode("ascii")
    assert b"\t" not in raw
    lines = raw.decode().splitlines()
    assert 5 <= len(lines) <= 7
    assert max(len(l) for l in lines) <= 48


def _motd(env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "TAOS_SHELL_SHARE": str(SHARE)}
    env.update(env_extra)
    return subprocess.run(["sh", str(MOTD)], env=env, capture_output=True, text=True)


def test_motd_renders_with_and_without_colour(tmp_path: Path):
    osrel = tmp_path / "os-release"
    osrel.write_text('PRETTY_NAME="taOS (Debian 13 trixie base)"\n')
    res = _motd({"OS_RELEASE": str(osrel), "TERM": "xterm-256color"})
    assert res.returncode == 0
    assert "taOS (Debian 13 trixie base)" in res.stdout
    assert "\x1b[1;38;5;141m" in res.stdout
    for label in ("Hostname", "Uptime", "Load", "Memory", "Disk /", "IPv4", "taOS service"):
        assert label in res.stdout
    plain = _motd({"OS_RELEASE": str(osrel), "TERM": "dumb"})
    assert plain.returncode == 0
    assert "\x1b" not in plain.stdout
    assert "taOS" in plain.stdout


def test_motd_is_executable():
    assert os.access(MOTD, os.X_OK)


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb not installed")
def test_package_builds_with_expected_files(tmp_path: Path):
    deb = tmp_path / "taos-shell.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(PKG), str(deb)],
                   check=True, capture_output=True)
    listing = subprocess.run(["dpkg-deb", "-c", str(deb)], check=True,
                             capture_output=True, text=True).stdout
    for path in ("./usr/share/taos-shell/zshrc", "./usr/share/taos-shell/logo.txt",
                 "./etc/skel/.zshrc", "./etc/taos/starship.toml",
                 "./etc/xdg/fastfetch/config.jsonc", "./etc/update-motd.d/05-taos"):
        assert path in listing, path
    info = subprocess.run(["dpkg-deb", "-I", str(deb)], check=True,
                          capture_output=True, text=True).stdout
    for dep in ("zsh", "starship", "fastfetch", "fzf", "zoxide", "eza", "bat"):
        assert dep in info


def test_setup_sh_step2():
    _sh_n(SETUP)
    text = SETUP.read_text()
    assert "chsh -s" in text
    assert "/etc/adduser.conf" in text and "DSHELL" in text
    assert "/etc/default/useradd" in text and "SHELL" in text
    assert "taos-shell_" in text


def _fake_net(tmp_path: Path, ifaces: dict[str, tuple[str, str]]) -> Path:
    net = tmp_path / "net"
    net.mkdir()
    for name, (oper, carrier) in ifaces.items():
        d = net / name
        d.mkdir()
        (d / "operstate").write_text(oper + "\n")
        (d / "carrier").write_text(carrier + "\n")
    return net


def _ipv4_row(res: subprocess.CompletedProcess) -> str:
    for line in res.stdout.splitlines():
        if line.strip().startswith("IPv4"):
            return line.split(None, 1)[1].strip()
    raise AssertionError(res.stdout)


def test_motd_ipv4_states(tmp_path: Path):
    base = {"TERM": "dumb"}
    got = _motd({**base, "TAOS_MOTD_IP4": "192.0.2.7"})
    assert _ipv4_row(got) == "192.0.2.7"
    for name, ifaces, want in (
        ("up", {"lo": ("unknown", "1"), "eth0": ("up", "1")}, "connecting..."),
        ("carrier", {"lo": ("unknown", "1"), "eth0": ("down", "1")}, "connecting..."),
        ("down", {"lo": ("unknown", "1"), "eth0": ("down", "0")}, "offline"),
        ("none", {"lo": ("unknown", "1")}, "offline"),
    ):
        d = tmp_path / name
        d.mkdir()
        res = _motd({**base, "TAOS_MOTD_IP4": "", "TAOS_MOTD_SYSNET": str(_fake_net(d, ifaces))})
        assert _ipv4_row(res) == want, name


def test_motd_single_block_footer():
    res = _motd({"TERM": "dumb", "TAOS_MOTD_IP4": "192.0.2.7"})
    assert "Kernel " in res.stdout
    assert "Free software, no warranty. Licences: /usr/share/doc/*/copyright" in res.stdout


def test_uname_motd_diverted_and_restored():
    pre = (PKG / "DEBIAN/preinst").read_text()
    post = (PKG / "DEBIAN/postrm").read_text()
    assert "/etc/update-motd.d/10-uname" in pre
    assert "dpkg-divert --package taos-shell --no-rename" in pre and "--add" in pre
    assert "/etc/update-motd.d/10-uname" in post and "--remove" in post
    assert 'mv -f "$f.debian" "$f"' in post


def test_setup_sh_console_font_and_step5():
    _sh_n(SETUP)
    text = SETUP.read_text()
    assert 'FONTFACE' in text and 'Terminus' in text
    assert 'FONTSIZE' in text and '16x32' in text
    assert "console-setup" in text and "setupcon --save" in text
    assert "TAOS_DRM_SYSFS" in text
    assert "Step 5: tools (taos-tools)" in text and "taos-tools_" in text


def _probe(tmp_path: Path, conns: dict[str, tuple[str, str]]) -> int:
    for name, (status, modes) in conns.items():
        d = tmp_path / name
        d.mkdir()
        (d / "status").write_text(status + "\n")
        (d / "modes").write_text(modes)
    func = subprocess.run(["sed", "-n", "/^display_max_height()/,/^}/p", str(SETUP)],
                          capture_output=True, text=True, check=True).stdout
    assert func
    res = subprocess.run(["sh", "-c", func + "\ndisplay_max_height"],
                         env={"PATH": "/usr/bin:/bin", "TAOS_DRM_SYSFS": str(tmp_path)},
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return int(res.stdout.strip())


def test_display_height_probe(tmp_path: Path):
    (tmp_path / "y").mkdir()
    assert _probe(tmp_path / "y", {
        "card0-DP-1": ("connected", "3840x1200\n1920x1080\n"),
        "card0-HDMI-A-1": ("connected", "1920x1080\n"),
        "card0-DP-2": ("disconnected", "7680x4320\n"),
    }) == 1200
    (tmp_path / "z").mkdir()
    assert _probe(tmp_path / "z", {}) == 0
