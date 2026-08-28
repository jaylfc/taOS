"""Tests for fleet_health.sh BOARD STRANGLED probe.

Verifies the fix for tsk-bvpjun:
  - probe failure is distinguished from a genuine empty offer
  - alarm requires corroboration across 2 consecutive runs
  - alarm is suppressed when claimable count is FALLING
  - prune advice is dropped from the message
"""

import os
import subprocess
import tempfile

FLEET_HEALTH = os.path.expanduser("~/.taos-team/fleet_health.sh")
_STATE_FALLBACK = "/tmp/fleet_health_strangled_state"


def _write_mock_taos_team(team_dir, claimable_count):
    with open(os.path.join(team_dir, "taos_team.py"), "w") as f:
        f.write(
            "import os\n"
            "PROJECT = os.environ.get('TAOS_PROJECTS', 'prj-test').split(',')[0]\n"
            "CFG = {'TAOS_PROJECT': PROJECT}\n"
            "API = 'http://mock'\n"
            "def login(): pass\n"
            "def _req(base, path, payload=None, method=None):\n"
            "    if '/tasks' in path:\n"
            "        count = int(os.environ.get('MOCK_CLAIMABLE', '0'))\n"
            "        return {'items': [\n"
            "            {'id': f'tsk-{i}', 'status': 'open', 'claimer_id': None,\n"
            "             'labels': ['claimable'], 'priority': 5, 'assignee_id': '', 'body': ''}\n"
            "            for i in range(count)\n"
            "        ]}\n"
            "    return {}\n"
        )


def _write_mock_next_card(team_dir, empty=True, rc=0):
    with open(os.path.join(team_dir, "next_card.py"), "w") as f:
        f.write(
            "#!/usr/bin/env python3\n"
            "import sys, os\n"
            f"rc = {rc}\n"
            "if rc != 0:\n"
            "    sys.stderr.write('mock next_card error')\n"
            "    sys.exit(rc)\n"
        )
        if empty:
            f.write("print('')\n")
        else:
            f.write("print('tsk-mock')\n")


def _setup_env(tmp_dir, *, claimable=0, next_empty=True, next_rc=0, clean_state=True):
    team_dir = os.path.join(tmp_dir, ".taos-team")
    os.makedirs(team_dir, exist_ok=True)

    with open(os.path.join(team_dir, "fleet_pr_lib.sh"), "w") as f:
        f.write("#!/bin/bash\nFLEET_MAX_OPEN_PRS=20\nFLEET_LANE_REPOS='jaylfc/taOS'\n")

    with open(os.path.join(team_dir, "lane-hy3.cred"), "w") as f:
        f.write("TAOS_API=http://mock\nTAOS_TOKEN=mock-token\nTAOS_CANONICAL=mock-canonical\nTAOS_PROJECT=prj-test\n")

    with open(os.path.join(team_dir, "quarantine.txt"), "w") as f:
        f.write("")

    with open(os.path.join(team_dir, "lead-hold.txt"), "w") as f:
        f.write("")

    _write_mock_taos_team(team_dir, claimable)
    _write_mock_next_card(team_dir, empty=next_empty, rc=next_rc)

    bin_dir = os.path.join(tmp_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)

    with open(os.path.join(bin_dir, "pgrep"), "w") as f:
        f.write("#!/bin/bash\nif [ \"$1\" = '-cf' ]; then echo '1'; exit 0; fi\nexec /usr/bin/pgrep \"$@\"\n")
    os.chmod(os.path.join(bin_dir, "pgrep"), 0o755)

    with open(os.path.join(bin_dir, "gh"), "w") as f:
        f.write("#!/bin/bash\necho '[]'\nexit 0\n")
    os.chmod(os.path.join(bin_dir, "gh"), 0o755)

    env = os.environ.copy()
    env["HOME"] = tmp_dir
    env["TMPDIR"] = tmp_dir
    env["PATH"] = bin_dir + ":" + env.get("PATH", "")
    env["MOCK_CLAIMABLE"] = str(claimable)

    if clean_state:
        state = os.path.join(tmp_dir, "fleet_health_strangled_state")
        if os.path.exists(state):
            os.remove(state)
        if os.path.exists(_STATE_FALLBACK):
            os.remove(_STATE_FALLBACK)

    return env


def _run(env):
    return subprocess.run(
        ["bash", FLEET_HEALTH],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_strangled_alarm_fires_after_two_consecutive_empty_probes():
    with tempfile.TemporaryDirectory() as tmp_dir:
        env = _setup_env(tmp_dir, claimable=5, next_empty=True, clean_state=True)
        r1 = _run(env)
        assert "BOARD STRANGLED - " not in r1.stdout
        assert "Prune the quarantine" not in r1.stdout

        env = _setup_env(tmp_dir, claimable=5, next_empty=True, clean_state=False)
        r2 = _run(env)
        assert "BOARD STRANGLED - " in r2.stdout
        assert "Prune the quarantine" not in r2.stdout


def test_single_empty_probe_does_not_alarm_without_corroboration():
    with tempfile.TemporaryDirectory() as tmp_dir:
        env = _setup_env(tmp_dir, claimable=5, next_empty=True, clean_state=True)
        r = _run(env)
        assert "BOARD STRANGLED - " not in r.stdout


def test_probe_failure_reports_error_and_does_not_alarm():
    with tempfile.TemporaryDirectory() as tmp_dir:
        env = _setup_env(tmp_dir, claimable=5, next_empty=False, next_rc=1, clean_state=True)
        r = _run(env)
        assert "BOARD STRANGLED - " not in r.stdout
        assert "probe failed" in r.stdout
        assert "rc=1" in r.stdout
        assert "Prune the quarantine" not in r.stdout


def test_next_card_timeout_reports_error_and_does_not_alarm():
    with tempfile.TemporaryDirectory() as tmp_dir:
        env = _setup_env(tmp_dir, claimable=5, next_empty=True, next_rc=124, clean_state=True)
        r = _run(env)
        assert "BOARD STRANGLED - " not in r.stdout
        assert "probe failed" in r.stdout
        assert "Prune the quarantine" not in r.stdout


def test_falling_claimable_count_suppresses_alarm():
    with tempfile.TemporaryDirectory() as tmp_dir:
        env = _setup_env(tmp_dir, claimable=5, next_empty=True, clean_state=True)
        _run(env)

        env = _setup_env(tmp_dir, claimable=3, next_empty=True, clean_state=False)
        r = _run(env)
        assert "BOARD STRANGLED - " not in r.stdout


def test_rising_claimable_count_allows_alarm_after_corroboration():
    with tempfile.TemporaryDirectory() as tmp_dir:
        env = _setup_env(tmp_dir, claimable=3, next_empty=True, clean_state=True)
        _run(env)

        env = _setup_env(tmp_dir, claimable=5, next_empty=True, clean_state=False)
        r = _run(env)
        assert "BOARD STRANGLED - " in r.stdout
        assert "Prune the quarantine" not in r.stdout
