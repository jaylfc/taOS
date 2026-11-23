"""Regression tests for the incus subuid/subgid mapping added to
install-server.sh's ``ensure_taos_user`` (``_ensure_subid_mapping``).

Covers: both files get ``root:<taos uid>:1`` when missing, the lines are
never duplicated on re-run, and incus is restarted only when a line was
added. The mapping file paths are overridden via ``TAOS_SUBUID`` /
``TAOS_SUBGID`` (defaulting to ``/etc/subuid`` / ``/etc/subgid``) so the
test never touches the real system files.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-server.sh"


def _restart_count(log):
    return sum(
        1 for line in log.splitlines() if line.strip() == "systemctl restart incus.service"
    )


def _extract(text, name):
    m = re.search(rf"^{re.escape(name)}\(\)\s*\{{", text, re.M)
    assert m, f"{name}() not found"
    i, d = m.end() - 1, 0
    while True:
        d += (text[i] == "{") - (text[i] == "}")
        if d == 0:
            return text[m.start():i + 1]
        i += 1


def _run_subid(tmp_path, subuid_has_line, subgid_has_line):
    """Run _ensure_subid_mapping with stubbed commands.

    TAOS_SUBUID/TAOS_SUBGID point at tmp files. Real grep/tee are symlinked
    in (read-only use). id/sudo/systemctl are stubbed as fresh scripts.
    Returns (result, log_text, subuid_path, subgid_path).
    """
    b = tmp_path / "bin"
    b.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "calls.log"
    log.unlink(missing_ok=True)  # fresh log per call so runs stay isolated
    subuid = tmp_path / "subuid"
    subgid = tmp_path / "subgid"
    if subuid_has_line:
        subuid.write_text("root:999:1\n")
    if subgid_has_line:
        subgid.write_text("root:999:1\n")

    # Real tools used directly (never written to): grep, tee, sh.
    for tool in ["grep", "tee", "sh"]:
        p = shutil.which(tool)
        if p and not os.path.lexists(b / tool):
            os.symlink(p, b / tool)

    def stub(name, body):
        (b / name).write_text(f'#!/bin/sh\necho "{name} $*" >> {log}\n{body}\n')
        (b / name).chmod(0o755)

    stub("id", 'echo 999')
    stub("sudo", 'eval "$*"')
    stub("systemctl", 'echo "systemctl $*" >> '"$log"'\n:')

    text = SCRIPT.read_text()
    fns = _extract(text, "_ensure_subid_mapping")
    w = tmp_path / "w.sh"
    w.write_text(
        "log(){ echo \"LOG $*\"; }\n"
        "warn(){ echo \"WARN $*\" >&2; }\n"
        "os_name=Linux\n"
        + fns
        + "\n_ensure_subid_mapping\necho RC=$?\n"
    )
    r = subprocess.run(
        [shutil.which("bash"), str(w)],
        env={
            "PATH": str(b),
            "HOME": str(tmp_path),
            "TAOS_SUBUID": str(subuid),
            "TAOS_SUBGID": str(subgid),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    return r, log.read_text() if log.exists() else "", subuid, subgid


def test_subid_mapping_added_to_both_files_when_missing(tmp_path):
    r, log, subuid, subgid = _run_subid(tmp_path, False, False)
    assert r.stdout.strip().endswith("RC=0"), r.stderr
    assert subuid.read_text() == "root:999:1\n", subuid
    assert subgid.read_text() == "root:999:1\n", subgid
    assert "tee -a " + str(subuid) in log, log
    assert "tee -a " + str(subgid) in log, log
    assert "systemctl restart incus.service" in log, log
    assert _restart_count(log) == 1, log


def test_subid_mapping_idempotent_no_duplicate_no_restart(tmp_path):
    # First run: both missing -> lines added, incus restarted once.
    r1, log1, subuid, subgid = _run_subid(tmp_path, False, False)
    assert r1.stdout.strip().endswith("RC=0"), r1.stderr
    assert subuid.read_text() == "root:999:1\n"
    assert subgid.read_text() == "root:999:1\n"
    assert _restart_count(log1) == 1, log1

    # Second run: both already present -> nothing appended, no restart.
    r2, log2, subuid2, subgid2 = _run_subid(tmp_path, True, True)
    assert r2.stdout.strip().endswith("RC=0"), r2.stderr
    assert subuid2.read_text() == "root:999:1\n", subuid2
    assert subgid2.read_text() == "root:999:1\n", subgid2
    assert "tee -a " + str(subuid2) not in log2, log2
    assert "tee -a " + str(subgid2) not in log2, log2
    assert "systemctl restart incus.service" not in log2, log2


def test_subid_mapping_restarts_only_when_a_line_was_added(tmp_path):
    # subuid present, subgid missing -> only subgid appended, one restart.
    r, log, subuid, subgid = _run_subid(tmp_path, True, False)
    assert r.stdout.strip().endswith("RC=0"), r.stderr
    assert subuid.read_text() == "root:999:1\n", subuid
    assert subgid.read_text() == "root:999:1\n", subgid
    assert "tee -a " + str(subuid) not in log, log
    assert "tee -a " + str(subgid) in log, log
    assert _restart_count(log) == 1, log
