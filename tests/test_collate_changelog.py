"""Tests for scripts/collate_changelog.py.

The collator is what makes changelog fragments safe to adopt: if it loses a
bullet, drops a section, or leaves fragments behind, the release notes go out
wrong and nothing else catches it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(tmp_root: Path):
    """Import collate_changelog with its paths pointed at a temp repo."""
    spec = importlib.util.spec_from_file_location(
        f"collate_changelog_{tmp_root.name}", SCRIPTS / "collate_changelog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.ROOT = tmp_root
    mod.FRAGMENT_DIR = tmp_root / "changelog.d"
    mod.CHANGELOG = tmp_root / "CHANGELOG.md"
    return mod


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [1.0.0-beta.46] - 2026-08-03\n\n"
        "### Added\n\n- older thing (#1).\n",
        encoding="utf-8",
    )
    return tmp_path


def test_folds_fragments_and_deletes_them(repo: Path):
    mod = _load(repo)
    (repo / "changelog.d" / "2291-notes.md").write_text("- Notes area (#2291).\n", encoding="utf-8")
    (repo / "changelog.d" / "2292-fix.md").write_text("### Fixed\n\n- A bug (#2292).\n", encoding="utf-8")

    assert mod.main(["1.0.0-beta.47", "--date", "2026-08-05"]) == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [1.0.0-beta.47] - 2026-08-05" in text
    assert "- Notes area (#2291)." in text
    assert "- A bug (#2292)." in text
    # Unreleased stays, and stays ABOVE the new section, ready for the next cycle.
    assert text.index("## [Unreleased]") < text.index("## [1.0.0-beta.47]")
    # The previous release is untouched and still below the new one.
    assert text.index("## [1.0.0-beta.47]") < text.index("## [1.0.0-beta.46]")
    assert "- older thing (#1)." in text
    # Fragments are consumed.
    assert not (repo / "changelog.d" / "2291-notes.md").exists()
    assert not (repo / "changelog.d" / "2292-fix.md").exists()


def test_no_fragments_is_a_noop_not_an_error(repo: Path):
    mod = _load(repo)
    before = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert mod.main(["1.0.0-beta.47"]) == 0
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == before


def test_sections_are_grouped_and_ordered(repo: Path):
    mod = _load(repo)
    (repo / "changelog.d" / "a.md").write_text("### Fixed\n\n- fix one (#1).\n", encoding="utf-8")
    (repo / "changelog.d" / "b.md").write_text("- add one (#2).\n", encoding="utf-8")
    (repo / "changelog.d" / "c.md").write_text("### Fixed\n\n- fix two (#3).\n", encoding="utf-8")

    assert mod.main(["9.9.9", "--date", "2026-08-05"]) == 0
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    # Added precedes Fixed (Keep-a-Changelog order), and both Fixed bullets sit
    # under ONE heading rather than one heading per fragment.
    assert text.index("### Added") < text.index("### Fixed")
    assert text.count("### Fixed") == 1
    assert "- fix one (#1)." in text and "- fix two (#3)." in text


def test_wrapped_bullet_continuation_lines_are_kept(repo: Path):
    mod = _load(repo)
    (repo / "changelog.d" / "wrap.md").write_text(
        "- A long entry that wraps across\n  two lines and must stay whole (#4).\n",
        encoding="utf-8",
    )
    assert mod.main(["9.9.9", "--date", "2026-08-05"]) == 0
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- A long entry that wraps across" in text
    assert "  two lines and must stay whole (#4)." in text


def test_dry_run_changes_nothing(repo: Path, capsys):
    mod = _load(repo)
    (repo / "changelog.d" / "2294-y.md").write_text("- Y (#2294).\n", encoding="utf-8")
    before = (repo / "CHANGELOG.md").read_text(encoding="utf-8")

    assert mod.main(["1.0.0-beta.47", "--dry-run"]) == 0
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == before
    assert (repo / "changelog.d" / "2294-y.md").exists()
    assert "- Y (#2294)." in capsys.readouterr().out


def test_missing_unreleased_anchor_fails_loudly_and_keeps_fragments(repo: Path):
    mod = _load(repo)
    (repo / "CHANGELOG.md").write_text("# Changelog\n\nno anchor here\n", encoding="utf-8")
    (repo / "changelog.d" / "2295-z.md").write_text("- Z (#2295).\n", encoding="utf-8")

    assert mod.main(["1.0.0-beta.47"]) == 1
    # The fragment survives a failed run so nothing is lost.
    assert (repo / "changelog.d" / "2295-z.md").exists()
