"""The sudoers drop-in and its installer: one account, one program, validated."""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "packaging" / "taos-storage.sudoers"
INSTALLER = ROOT / "scripts" / "install-storage-helper.sh"
HELPER_PATH = "/usr/local/sbin/taos-storage-helper"
VISUDO = shutil.which("visudo") or ("/usr/sbin/visudo" if os.path.exists("/usr/sbin/visudo") else None)


def rendered(user="taos"):
    return TEMPLATE.read_text().replace("@TAOS_USER@", user)


def rule_lines(text):
    """Logical sudoers lines (continuations joined, comments dropped)."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [l.strip() for l in joined.splitlines() if l.strip() and not l.strip().startswith("#")]


def test_exactly_one_rule_for_exactly_one_named_account():
    rules = [l for l in rule_lines(rendered()) if not l.startswith("Cmnd_Alias")]
    assert len(rules) == 1
    who, rest = rules[0].split(None, 1)
    assert who == "taos" and who not in ("ALL", "root")
    assert rest.startswith("ALL=(root) NOPASSWD:")


def test_only_the_helper_with_fixed_subcommands_is_allowed():
    alias = next(l for l in rule_lines(rendered()) if l.startswith("Cmnd_Alias"))
    cmds = [c.strip() for c in alias.split("=", 1)[1].split(",")]
    assert cmds
    for c in cmds:
        assert c.startswith(HELPER_PATH + " "), c
        sub = c.split()[1]
        assert sub in ("list", "status", "add", "prepare-home", "install-veracrypt")
    assert not any(c == HELPER_PATH or c.endswith(" *") and c.split()[1] == "*" for c in cmds)
    assert "ALL" not in alias.split("=", 1)[1]


def test_no_account_wildcard_or_group_grant():
    for l in rule_lines(rendered()):
        assert not l.startswith(("ALL", "%", "+"))


@pytest.mark.skipif(VISUDO is None, reason="visudo not installed")
def test_rendered_file_passes_visudo(tmp_path):
    f = tmp_path / "s"
    f.write_text(rendered())
    r = subprocess.run([VISUDO, "-c", "-f", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(VISUDO is None, reason="visudo not installed")
def test_visudo_check_bites_on_a_broken_file(tmp_path):
    f = tmp_path / "s"
    f.write_text(rendered().replace("NOPASSWD:", "NOPASSWD"))
    assert subprocess.run([VISUDO, "-c", "-f", str(f)], capture_output=True).returncode != 0


@pytest.mark.skipif(VISUDO is None, reason="visudo not installed")
def test_installer_stages_root_files_with_safe_modes(tmp_path):
    out = tmp_path / "stage"
    r = subprocess.run(["bash", str(INSTALLER), "--user", "taos", "--stage", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    mode = lambda p: stat.S_IMODE((out / p).stat().st_mode)
    assert mode("usr/local/sbin/taos-storage-helper") == 0o755
    assert mode("usr/local/lib/taos-storage/storage_classifier.py") == 0o644
    assert mode("etc/sudoers.d/taos-storage") == 0o440
    assert mode("etc/taos/keys") == 0o700
    assert (out / "etc/sudoers.d/taos-storage").read_text() == rendered()
    assert (out / "usr/local/sbin/taos-storage-helper").read_bytes() == (ROOT / "scripts/taos-storage-helper").read_bytes()


@pytest.mark.skipif(VISUDO is None, reason="visudo not installed")
@pytest.mark.parametrize("user", ["ALL", "root", "%admin", "ta os", "a;b", "", "taos\nALL", "Taos"])
def test_installer_refuses_dangerous_accounts_and_installs_nothing(tmp_path, user):
    out = tmp_path / "stage"
    r = subprocess.run(["bash", str(INSTALLER), "--user", user, "--stage", str(out)], capture_output=True, text=True)
    assert r.returncode != 0
    assert not (out / "etc/sudoers.d/taos-storage").exists()


def test_installer_without_root_and_without_stage_refuses():
    if os.geteuid() == 0:
        pytest.skip("running as root")
    r = subprocess.run(["bash", str(INSTALLER), "--user", "taos"], capture_output=True, text=True)
    assert r.returncode != 0 and "root" in r.stderr
