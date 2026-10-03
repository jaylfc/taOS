"""scripts/changelog_section.py builds every GitHub Release body (release.yml).

beta.54 and beta.55 were tagged but never published as GitHub Releases, so the
in-app update check (which reads /releases/latest) kept offering beta.53 for 16
days. release.yml now publishes on tag push; these tests pin the extractor it
depends on, including the failure modes that would publish wrong notes.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from changelog_section import extract_section  # noqa: E402

CHANGELOG = """# Changelog

## [Unreleased]

## [1.0.0-beta.55] - 2026-10-01

### Added

- Fifty-five.

## [1.0.0-beta.5] - 2026-06-01

### Fixed

- Five.

## [1.0.0-beta.4] - 2026-05-01
"""


def test_extracts_only_the_requested_section():
    body = extract_section(CHANGELOG, "1.0.0-beta.55")
    assert body == "### Added\n\n- Fifty-five.\n"


def test_version_prefix_does_not_match_a_longer_version():
    # beta.5 must not pick up beta.55's heading (or vice versa).
    assert extract_section(CHANGELOG, "1.0.0-beta.5") == "### Fixed\n\n- Five.\n"


def test_missing_version_raises():
    with pytest.raises(LookupError, match="no '## \\[1.0.0-beta.56\\]'"):
        extract_section(CHANGELOG, "1.0.0-beta.56")


def test_empty_section_raises():
    with pytest.raises(LookupError, match="empty"):
        extract_section(CHANGELOG, "1.0.0-beta.4")


def test_unreleased_only_changelog_raises():
    with pytest.raises(LookupError):
        extract_section("# Changelog\n\n## [Unreleased]\n\n- pending\n", "1.0.0-beta.56")


def test_cli_strips_v_prefix_and_fails_nonzero(tmp_path):
    f = tmp_path / "CHANGELOG.md"
    f.write_text(CHANGELOG, encoding="utf-8")
    script = ROOT / "scripts" / "changelog_section.py"
    ok = subprocess.run([sys.executable, script, "v1.0.0-beta.55", f], capture_output=True, text=True)
    assert ok.returncode == 0 and "Fifty-five" in ok.stdout
    bad = subprocess.run([sys.executable, script, "v9.9.9", f], capture_output=True, text=True)
    assert bad.returncode == 1 and bad.stdout == ""


def test_real_changelog_has_a_section_for_the_current_version():
    # The release commit bumps pyproject and collates the CHANGELOG together;
    # if they disagree, the tag push would fail to publish. Catch it at PR time.
    import tomllib

    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert extract_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
