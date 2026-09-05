"""Tests for the kiosk systemd unit and seat setup emitted by kiosk-setup.sh.

Hermetic by construction: the unit is parsed out of the setup script's heredoc
and the seat-configuration block is executed against stub binaries in a
temporary PATH. Nothing is read from the host's /etc/systemd/system, so a stale
installed unit can never mask a broken script.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KIOSK_SETUP = REPO_ROOT / "scripts" / "kiosk-setup.sh"
UNIT_PATH = "/etc/systemd/system/taos-kiosk.service"


def _script_text() -> str:
    assert KIOSK_SETUP.exists(), f"{KIOSK_SETUP} is missing"
    return KIOSK_SETUP.read_text()


def _unit_template() -> str:
    """Return the taos-kiosk.service heredoc body emitted by the setup script."""
    match = re.search(
        r"cat > " + re.escape(UNIT_PATH) + r" << '?EOF'?\n(.*?)\nEOF\n",
        _script_text(),
        re.DOTALL,
    )
    assert match, f"{KIOSK_SETUP} must emit {UNIT_PATH} from an EOF heredoc"
    return match.group(1)


def _unit_sections() -> dict[str, dict[str, list[str]]]:
    """Parse the unit template into {section: {directive: [raw values]}}."""
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for raw_line in _unit_template().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        current.setdefault(key.strip(), []).append(value.strip())
    return sections


def _unit_names(section: str, directive: str) -> list[str]:
    """Return the unit names a directive requests, per systemd's own grammar.

    systemd splits every assignment of a dependency directive on whitespace and
    treats each token as one unit name, so this mirrors the real parser.
    """
    values = _unit_sections().get(section, {}).get(directive, [])
    return [token for value in values for token in value.split()]


class TestKioskUnitDependencies:
    @pytest.mark.parametrize("directive", ["After", "Wants"])
    def test_declares_seatd_and_tinyagentos(self, directive: str) -> None:
        names = _unit_names("Unit", directive)
        assert "seatd.service" in names, (
            f"{directive}= must request seatd.service exactly; got {names}"
        )
        assert "tinyagentos.service" in names, (
            f"{directive}= must request tinyagentos.service exactly; got {names}"
        )

    @pytest.mark.parametrize("directive", ["After", "Wants", "Requires"])
    def test_no_directive_embedded_in_value(self, directive: str) -> None:
        """`Wants=a.service Wants=b.service` is one assignment, not two.

        The second token is not a unit name (`=` is not legal in one), so the
        dependency is silently dropped. Reject any such token.
        """
        for name in _unit_names("Unit", directive):
            assert "=" not in name, (
                f"{directive}= value contains an embedded directive: {name!r}. "
                "Use one assignment with a space-separated unit list."
            )


class TestAptIndexRefresh:
    def test_apt_update_is_unconditional_and_first(self) -> None:
        """A stale index must not be able to fail the seatd install.

        `apt-get update` has to run at top level (not nested in a
        `command -v`-guarded branch) and before the first `apt-get install`.
        """
        lines = _script_text().splitlines()
        updates = [
            i for i, line in enumerate(lines) if re.match(r"apt-get update\b", line)
        ]
        installs = [
            i for i, line in enumerate(lines) if re.search(r"\bapt-get install\b", line)
        ]
        assert updates, (
            "kiosk-setup.sh must run an unindented (unconditional) `apt-get update`"
        )
        assert installs, "expected at least one `apt-get install` in kiosk-setup.sh"
        assert updates[0] < installs[0], (
            "the unconditional `apt-get update` must precede every `apt-get install`"
        )


def _seat_block() -> str:
    """Return the seatd install + seat-group configuration block."""
    match = re.search(
        r"^# Install seatd.*?(?=^# Install chromium)",
        _script_text(),
        re.DOTALL | re.MULTILINE,
    )
    assert match, "kiosk-setup.sh must have a '# Install seatd' block"
    return match.group(0)


def _run_seat_block(
    tmp_path: Path, *, seatd_present: bool, usermod_rc: int = 0
) -> subprocess.CompletedProcess[str]:
    """Execute the seat block with stub binaries and report what it called."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    log = tmp_path / "calls.log"

    def stub(name: str, body: str) -> None:
        path = stub_dir / name
        path.write_text(f"#!/bin/bash\n{body}\n")
        path.chmod(0o755)

    if seatd_present:
        stub("seatd", "exit 0")
    stub("apt-get", f'echo "apt-get $*" >> "{log}"; exit 0')
    stub("usermod", f'echo "usermod $*" >> "{log}"; exit {usermod_rc}')
    stub("systemctl", f'echo "systemctl $*" >> "{log}"; exit 0')
    stub("getent", f'echo "getent $*" >> "{log}"; exit 0')
    stub("groupadd", f'echo "groupadd $*" >> "{log}"; exit 0')

    script = tmp_path / "block.sh"
    script.write_text("set -e\nTAOS_USER=kioskuser\n" + _seat_block())

    env = dict(os.environ, PATH=f"{stub_dir}:{os.environ['PATH']}")
    proc = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env, check=False
    )
    proc.calls = log.read_text() if log.exists() else ""  # type: ignore[attr-defined]
    return proc


class TestSeatGroupConfiguration:
    def test_user_added_to_seat_group_when_seatd_already_installed(
        self, tmp_path: Path
    ) -> None:
        """The seat group must be configured even when seatd needs no install."""
        proc = _run_seat_block(tmp_path, seatd_present=True)
        assert proc.returncode == 0, proc.stderr
        assert "usermod" in proc.calls, (  # type: ignore[attr-defined]
            "seat-group setup was skipped because seatd was already installed; "
            f"calls were:\n{proc.calls}"  # type: ignore[attr-defined]
        )
        assert "kioskuser" in proc.calls  # type: ignore[attr-defined]

    def test_usermod_failure_aborts_setup(self, tmp_path: Path) -> None:
        """A failed group update must not be reported as a successful setup."""
        proc = _run_seat_block(tmp_path, seatd_present=True, usermod_rc=1)
        assert proc.returncode != 0, (
            "kiosk-setup.sh continued after usermod failed, so it reports success "
            "while the kiosk user has no seat access"
        )

    def test_seatd_service_is_enabled(self, tmp_path: Path) -> None:
        """seatd must be enabled so it is running on a fresh boot into kiosk."""
        proc = _run_seat_block(tmp_path, seatd_present=True)
        assert re.search(r"systemctl .*enable.*seatd", proc.calls), (  # type: ignore[attr-defined]
            f"kiosk-setup.sh must enable seatd; calls were:\n{proc.calls}"  # type: ignore[attr-defined]
        )
