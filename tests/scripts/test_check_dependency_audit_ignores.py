"""Tests for scripts/check_dependency_audit_ignores.py."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_dependency_audit_ignores.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_dependency_audit_ignores", _SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def check_mod():
    return _load_module()


def _fake_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestLoadIgnoreList:
    def test_missing_file_returns_empty(self, check_mod, tmp_path: Path) -> None:
        assert check_mod.load_ignore_list(tmp_path / "nonexistent.toml") == []

    def test_empty_toml_returns_empty(self, check_mod, tmp_path: Path) -> None:
        p = tmp_path / "ignore.toml"
        p.write_text("")
        assert check_mod.load_ignore_list(p) == []

    def test_reads_entries(self, check_mod, tmp_path: Path) -> None:
        p = tmp_path / "ignore.toml"
        p.write_text(
            '[[ignore]]\npackage = "pip"\nid = "CVE-2026-3219"\n'
        )
        entries = check_mod.load_ignore_list(p)
        assert len(entries) == 1
        assert entries[0]["package"] == "pip"
        assert entries[0]["id"] == "CVE-2026-3219"

    def test_reads_multiple_entries(self, check_mod, tmp_path: Path) -> None:
        p = tmp_path / "ignore.toml"
        p.write_text(
            '[[ignore]]\npackage = "pip"\nid = "CVE-2026-3219"\n'
            '[[ignore]]\npackage = "cryptography"\nid = "CVE-2026-69247"\n'
        )
        entries = check_mod.load_ignore_list(p)
        assert len(entries) == 2
        assert {e["id"] for e in entries} == {
            "CVE-2026-3219",
            "CVE-2026-69247",
        }


class TestCheckUpgradeResolves:
    def test_success_when_uv_lock_exits_zero(self, check_mod, tmp_path: Path) -> None:
        lock = tmp_path / "uv.lock"
        lock.write_text("dummy")
        fake = _fake_completed(returncode=0, stdout="resolved to 50.0.0")
        with patch.object(check_mod.subprocess, "run", return_value=fake):
            resolves, detail = check_mod.check_upgrade_resolves("cryptography", tmp_path)
        assert resolves is True
        assert "resolved" in detail

    def test_failure_when_uv_lock_exits_nonzero(self, check_mod, tmp_path: Path) -> None:
        lock = tmp_path / "uv.lock"
        lock.write_text("dummy")
        fake = _fake_completed(returncode=1, stderr="conflict with litellm pin <49.0")
        with patch.object(check_mod.subprocess, "run", return_value=fake):
            resolves, detail = check_mod.check_upgrade_resolves("cryptography", tmp_path)
        assert resolves is False
        assert "conflict" in detail

    def test_failure_when_no_lockfile(self, check_mod, tmp_path: Path) -> None:
        resolves, detail = check_mod.check_upgrade_resolves("pip", tmp_path)
        assert resolves is False
        assert "uv.lock not found" in detail

    def test_backup_restored_after_run(self, check_mod, tmp_path: Path) -> None:
        lock = tmp_path / "uv.lock"
        original = "original content"
        lock.write_text(original)
        fake = _fake_completed(returncode=1)
        with patch.object(check_mod.subprocess, "run", return_value=fake):
            check_mod.check_upgrade_resolves("cryptography", tmp_path)
        assert lock.read_text() == original

    def test_timeout_reports_false(self, check_mod, tmp_path: Path) -> None:
        lock = tmp_path / "uv.lock"
        lock.write_text("dummy")
        with patch.object(
            check_mod.subprocess, "run", side_effect=check_mod.subprocess.TimeoutExpired("uv", 120)
        ):
            resolves, detail = check_mod.check_upgrade_resolves("cryptography", tmp_path)
        assert resolves is False
        assert "timed out" in detail


class TestRunPipAudit:
    def test_no_findings_returns_empty_list(self, check_mod, tmp_path: Path) -> None:
        fake = _fake_completed(returncode=0, stdout='{"dependencies": []}')
        with patch.object(check_mod.subprocess, "run", return_value=fake):
            findings, output = check_mod.run_pip_audit(tmp_path)
        assert findings == []
        assert output == '{"dependencies": []}'

    def test_findings_parsed_from_json(self, check_mod, tmp_path: Path) -> None:
        payload = json.dumps({
            "dependencies": [
                {
                    "name": "pip",
                    "version": "26.0.1",
                    "vulns": [{"id": "CVE-2026-3219", "fix_versions": []}],
                }
            ]
        })
        fake = _fake_completed(returncode=1, stdout=payload)
        with patch.object(check_mod.subprocess, "run", return_value=fake):
            findings, _ = check_mod.run_pip_audit(tmp_path)
        assert len(findings) == 1
        assert findings[0]["package"] == "pip"
        assert findings[0]["id"] == "CVE-2026-3219"

    def test_invalid_json_is_cannot_see_not_empty(self, check_mod, tmp_path: Path) -> None:
        """Unparseable output used to fold into findings=[] (fail-open); it
        is a cannot-see state and must surface as None."""
        fake = _fake_completed(returncode=1, stdout="not json")
        with patch.object(check_mod.subprocess, "run", return_value=fake):
            findings, raw = check_mod.run_pip_audit(tmp_path)
        assert findings is None and "unparseable" in raw

    def test_pip_audit_not_found(self, check_mod, tmp_path: Path) -> None:
        with patch.object(
            check_mod.subprocess, "run", side_effect=FileNotFoundError
        ):
            findings, output = check_mod.run_pip_audit(tmp_path)
        assert findings is None
        assert "not found" in output


class TestMain:
    def test_tool_dep_skips_upgrade_check(self, check_mod, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        sec = tmp_path / "security"
        sec.mkdir()
        (tmp_path / "uv.lock").write_text("")  # main() refuses to run without it
        ignore = sec / "pip-audit-ignore.toml"
        ignore.write_text(
            '[[ignore]]\npackage = "pip"\nid = "CVE-2026-3219"\ncheck_upgrade = false\n'
        )
        fake_audit = _fake_completed(
            returncode=0,
            stdout=json.dumps({
                "dependencies": [
                    {
                        "name": "pip",
                        "version": "26.0.1",
                        "vulns": [{"id": "CVE-2026-3219", "fix_versions": []}],
                    }
                ]
            }),
        )
        with patch.object(check_mod.shutil, "which", return_value="/usr/bin/uv"):
            with patch.object(check_mod, "run_pip_audit", return_value=([
                {"package": "pip", "id": "CVE-2026-3219"},
            ], "")):
                rc = check_mod.main(["--ignore-file", str(ignore)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "SKIPPED: pip (CVE-2026-3219)" in captured.out
        assert "OK: 1 finding(s), all in ignore list" in captured.out
        assert "OK: ignore list is current" in captured.out

    def test_unlisted_finding_goes_red(self, check_mod, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        sec = tmp_path / "security"
        sec.mkdir()
        (tmp_path / "uv.lock").write_text("")  # main() refuses to run without it
        ignore = sec / "pip-audit-ignore.toml"
        ignore.write_text(
            '[[ignore]]\npackage = "pip"\nid = "CVE-2026-3219"\ncheck_upgrade = false\n'
        )
        with patch.object(check_mod.shutil, "which", return_value="/usr/bin/uv"):
            with patch.object(check_mod, "run_pip_audit", return_value=([
                {"package": "pip", "id": "CVE-2026-9999"},
            ], "")):
                rc = check_mod.main(["--ignore-file", str(ignore)])
        captured = capsys.readouterr()
        assert rc == 1
        assert "UNLISTED: pip CVE-2026-9999" in captured.out
        assert "FAIL: ignore list is stale or incomplete" in captured.out

    def test_droppable_ignore_goes_red(self, check_mod, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        sec = tmp_path / "security"
        sec.mkdir()
        (tmp_path / "uv.lock").write_text("")  # main() refuses to run without it
        ignore = sec / "pip-audit-ignore.toml"
        ignore.write_text('[[ignore]]\npackage = "cryptography"\nid = "CVE-2026-69247"\n')
        with patch.object(check_mod.shutil, "which", return_value="/usr/bin/uv"):
            with patch.object(check_mod, "check_upgrade_resolves", return_value=(True, "resolved to 50.0.0")):
                with patch.object(check_mod, "run_pip_audit", return_value=([], "")):
                    rc = check_mod.main(["--ignore-file", str(ignore)])
        captured = capsys.readouterr()
        assert rc == 1
        assert "DROPPABLE: cryptography (CVE-2026-69247)" in captured.out
        assert "FAIL: ignore list is stale or incomplete" in captured.out


class TestCannotSeeIsNotOk:
    """A checker that cannot read its inputs must exit 2, never report
    'current'. Both cases below reported OK rc 0 before the fix (found
    running the checker with a mislocated ignore file: every probe fell
    into NO FIX YET and the run printed 'OK: ignore list is current')."""

    def test_main_errors_when_lockfile_missing(
        self, check_mod, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        sec = tmp_path / "security"
        sec.mkdir()
        # NO uv.lock in this project - that is the case under test.
        ignore = sec / "pip-audit-ignore.toml"
        ignore.write_text('[[ignore]]\npackage = "httpx"\nid = "X-1"\n')
        with patch.object(check_mod.shutil, "which", return_value="/usr/bin/tool"):
            rc = check_mod.main(["--ignore-file", str(ignore)])
        assert rc == 2
        # Pin the REASON: rc 2 must come from the lockfile guard, not a
        # downstream cannot-see (e.g. pip-audit missing) that also exits 2.
        assert "uv.lock not found" in capsys.readouterr().err

    def test_unparseable_pip_audit_output_is_error(self, check_mod, tmp_path: Path) -> None:
        with patch.object(
            check_mod.subprocess, "run",
            return_value=_fake_completed(returncode=0, stdout="this is not json"),
        ):
            findings, raw = check_mod.run_pip_audit(tmp_path)
        assert findings is None and "unparseable" in raw

    def test_pip_audit_timeout_is_error(self, check_mod, tmp_path: Path) -> None:
        with patch.object(
            check_mod.subprocess, "run",
            side_effect=check_mod.subprocess.TimeoutExpired(cmd="pip-audit", timeout=120),
        ):
            findings, raw = check_mod.run_pip_audit(tmp_path)
        assert findings is None and "timed out" in raw

    def test_empty_output_exit_zero_is_still_no_findings(self, check_mod, tmp_path: Path) -> None:
        """The REAL no-findings shape (exit 0, empty stdout) stays OK."""
        with patch.object(
            check_mod.subprocess, "run",
            return_value=_fake_completed(returncode=0, stdout=""),
        ):
            findings, raw = check_mod.run_pip_audit(tmp_path)
        assert findings == []
