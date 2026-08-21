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


def test_rerun_after_partial_unlink_failure_is_idempotent(repo: Path, monkeypatch):
    mod = _load(repo)
    (repo / "changelog.d" / "2291-notes.md").write_text("- Notes area (#2291).\n", encoding="utf-8")

    fail_once = True
    import pathlib

    _real_unlink = pathlib.Path.unlink

    def fake_unlink(self):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise OSError("simulated unlink failure")
        _real_unlink(self)

    monkeypatch.setattr(pathlib.Path, "unlink", fake_unlink)

    # First run: writes the section, then fails on unlink (exception propagates).
    with pytest.raises(OSError, match="simulated unlink failure"):
        mod.main(["1.0.0-beta.47", "--date", "2026-08-05"])

    # Second run: fragment is still present, so without idempotency the section
    # would be inserted a second time.
    assert mod.main(["1.0.0-beta.47", "--date", "2026-08-05"]) == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.count("## [1.0.0-beta.47]") == 1
    assert "- Notes area (#2291)." in text
    assert not (repo / "changelog.d" / "2291-notes.md").exists()


def test_rerun_keeps_fragment_that_landed_after_the_partial_failure(repo: Path, monkeypatch):
    """A fragment merged between the failed run and the rerun was never folded.

    The rerun must not silently unlink it: its content exists nowhere in
    CHANGELOG.md, so deleting it loses the release note. Stale (already
    folded) leftovers are still consumed; the unfolded one is kept and the
    rerun refuses loudly so the operator folds it under the right version.
    """
    mod = _load(repo)
    (repo / "changelog.d" / "2291-notes.md").write_text("- Notes area (#2291).\n", encoding="utf-8")

    fail_once = True
    import pathlib

    _real_unlink = pathlib.Path.unlink

    def fake_unlink(self):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise OSError("simulated unlink failure")
        _real_unlink(self)

    monkeypatch.setattr(pathlib.Path, "unlink", fake_unlink)

    with pytest.raises(OSError, match="simulated unlink failure"):
        mod.main(["1.0.0-beta.47", "--date", "2026-08-05"])

    # A new PR merges its fragment between the failed run and the rerun.
    (repo / "changelog.d" / "2299-new.md").write_text("- Brand new feature (#2299).\n", encoding="utf-8")

    rc = mod.main(["1.0.0-beta.47", "--date", "2026-08-05"])
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")

    # The stale leftover was already folded by the first run: consumed.
    assert not (repo / "changelog.d" / "2291-notes.md").exists()
    # The unfolded fragment survives, its content is not lost and not
    # half-inserted anywhere.
    assert (repo / "changelog.d" / "2299-new.md").exists()
    assert "- Brand new feature (#2299)." not in text
    assert text.count("## [1.0.0-beta.47]") == 1
    # And the rerun says NO loudly instead of pretending it consumed cleanly.
    assert rc == 1


def test_rerun_keeps_unfolded_fragment_whose_text_matches_an_older_release(repo: Path, monkeypatch):
    """A late-landing fragment whose bullet also exists under an OLDER release.

    The rerun's folded-check must scope its match to the target version's
    section: a whole-file match sees the older release's identical bullet,
    counts the fragment as folded, and unlinks it -- silently losing the new
    release note.
    """
    mod = _load(repo)
    (repo / "changelog.d" / "2291-notes.md").write_text("- Notes area (#2291).\n", encoding="utf-8")

    fail_once = True
    import pathlib

    _real_unlink = pathlib.Path.unlink

    def fake_unlink(self):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise OSError("simulated unlink failure")
        _real_unlink(self)

    monkeypatch.setattr(pathlib.Path, "unlink", fake_unlink)

    with pytest.raises(OSError, match="simulated unlink failure"):
        mod.main(["1.0.0-beta.47", "--date", "2026-08-05"])

    # A new PR merges a fragment whose text duplicates a bullet ALREADY present
    # in the older 1.0.0-beta.46 section of the fixture changelog.
    (repo / "changelog.d" / "2299-new.md").write_text("- older thing (#1).\n", encoding="utf-8")

    rc = mod.main(["1.0.0-beta.47", "--date", "2026-08-05"])

    # The unfolded fragment survives and the rerun refuses loudly.
    assert (repo / "changelog.d" / "2299-new.md").exists()
    assert rc == 1
