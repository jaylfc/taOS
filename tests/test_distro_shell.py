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
    assert 5 <= len(lines) <= 10
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
    assert "\x1b[1;38;5;" in res.stdout
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
