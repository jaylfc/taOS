import os, re, shutil, subprocess
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-server.sh"


def _extract(text, name):
    m = re.search(rf"^{re.escape(name)}\(\)\s*\{{", text, re.M)
    assert m, f"{name}() not found"
    i, d = m.end() - 1, 0
    while True:
        d += (text[i] == "{") - (text[i] == "}")
        if d == 0:
            return text[m.start():i + 1]
        i += 1


def _run(tmp_path, pm, init, resolvable=True, unit=True, systemctl_enable_rc=0, rc_service_rc=0):
    b = tmp_path / "bin"
    b.mkdir()
    log = tmp_path / "calls.log"
    for tool in ["grep", "sed", "head", "awk", "cat", "mktemp", "mv", "rm", "tr", "chmod", "bash", "sh", "env", "mkdir", "rmdir"]:
        p = shutil.which(tool)
        if p:
            os.symlink(p, b / tool)

    def stub(name, body):
        (b / name).write_text(f'#!/bin/sh\necho "{name} $*" >> {log}\n{body}\n')
        (b / name).chmod(0o755)

    make_incus = (
        f"printf '#!/bin/sh\\necho \"incus $*\" >> {log}\\n' > {b}/incus; chmod +x {b}/incus"
    )
    stub("sudo", 'eval "$*"')
    if pm == "apk":
        search_body = 'echo "incus-7.0.1-r1";' if resolvable else "exit 0;"
        stub("apk", f"case \"$1\" in search) {search_body} ;; add) {make_incus} ;; esac")
    elif pm == "pacman":
        stub("pacman", f'case "$1" in -Si) exit {0 if resolvable else 1} ;; -S) {make_incus} ;; esac')
    elif pm == "apt-get":
        stub("apt-get", f'case "$*" in *install*) {make_incus} ;; esac')
        stub("apt-cache", "echo \"incus-base | 6.0 | http://deb\"")
    if init == "systemd":
        stub("systemctl", f'case "$1" in cat) exit {0 if unit else 1} ;; enable) exit {systemctl_enable_rc} ;; esac')
    elif init == "openrc":
        stub("rc-update", "")
        stub("rc-service", f"exit {rc_service_rc}")
    text = SCRIPT.read_text()
    fns = _extract(text, "ensure_container_runtime")
    if "_start_incusd()" in text:
        fns = _extract(text, "_start_incusd") + "\n" + fns
    w = tmp_path / "w.sh"
    # Set env vars for the /run directory checks so tests can control them
    # Only create the directory for the init system being tested
    systemd_run_dir = tmp_path / "run" / "systemd" / "system"
    openrc_run_dir = tmp_path / "run" / "openrc"
    if init == "systemd":
        systemd_run_dir.mkdir(parents=True, exist_ok=True)
    elif init == "openrc":
        openrc_run_dir.mkdir(parents=True, exist_ok=True)
    w.write_text(
        "log(){ echo \"LOG $*\"; }\n"
        "warn(){ echo \"WARN $*\" >&2; }\n"
        "_incus_storage_init(){ :; }\n"
        "os_name=Linux\n"
        "COW_FS_TYPE=ext4\n"
        + fns
        + "\nensure_container_runtime\necho RC=$?\n"
    )
    r = subprocess.run(
        [shutil.which("bash"), str(w)],
        env={
            "PATH": str(b),
            "HOME": str(tmp_path),
            "TAOS_SYSTEMD_RUN_DIR": str(systemd_run_dir),
            "TAOS_OPENRC_RUN_DIR": str(openrc_run_dir),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    return r, (log.read_text() if log.exists() else "")


def test_apk_openrc_installs_and_starts(tmp_path):
    r, log = _run(tmp_path, "apk", "openrc")
    assert "apk add incus incus-client" in log, log
    assert "apk add incus-openrc" in log, log
    assert "rc-update add" in log, log
    assert "rc-service" in log, log
    assert "incus admin init --auto" in log, log
    assert r.stdout.strip().endswith("RC=0")


def test_pacman_systemd_installs_and_starts(tmp_path):
    r, log = _run(tmp_path, "pacman", "systemd")
    assert "pacman -S --noconfirm --needed incus" in log, log
    assert "systemctl enable --now incus.service" in log, log
    assert "incus admin init --auto" in log, log
    assert r.stdout.strip().endswith("RC=0")


def test_apk_systemd_without_unit_skips_init(tmp_path):
    r, log = _run(tmp_path, "apk", "systemd", unit=False)
    assert "apk add incus incus-client" in log, log
    assert "incus.service unit not found" in r.stderr, r.stderr
    assert "incus admin init --auto" not in log, log
    assert r.stdout.strip().endswith("RC=0")


def test_apk_unresolvable_keeps_hint(tmp_path):
    r, log = _run(tmp_path, "apk", "openrc", resolvable=False)
    assert "apk add" not in log, log
    assert "sudo apk add incus" in r.stderr, r.stderr


def test_apt_path_still_runs_incus_init(tmp_path):
    r, log = _run(tmp_path, "apt-get", "systemd")
    assert "apt-get install" in log, log
    assert "incus admin init --auto" in log, log
    assert "systemctl enable" not in log, log


def test_systemd_systemctl_enable_fails_no_incus_init(tmp_path):
    # systemd running (TAOS_SYSTEMD_RUN_DIR exists), but systemctl enable --now fails
    r, log = _run(tmp_path, "apk", "systemd", systemctl_enable_rc=1)
    assert "apk add incus incus-client" in log, log
    assert "systemctl enable --now incus.service" in log, log
    assert "incus admin init --auto" not in log, log
    assert r.stdout.strip().endswith("RC=0")


def test_openrc_rc_service_fails_no_incus_init(tmp_path):
    # OpenRC running (TAOS_OPENRC_RUN_DIR exists + rc-service), but rc-service start fails
    r, log = _run(tmp_path, "apk", "openrc", rc_service_rc=1)
    assert "apk add incus incus-client" in log, log
    assert "rc-update add" in log, log
    assert "rc-service" in log, log
    assert "incus admin init --auto" not in log, log
    assert r.stdout.strip().endswith("RC=0")
