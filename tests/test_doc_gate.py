"""Unit tests for scripts/check_doc_gate.py.

These call evaluate_rules() and check_referenced_paths() directly with
synthetic inputs -- no shelling out to git, no dependence on the state of
this checkout's actual history.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_doc_gate as dg  # noqa: E402

# scripts/ is not a package; make it importable the same way the other
# scripts/*.py unit tests do (see tests/test_kv_quant_validator.py).
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import check_doc_gate as dg  # noqa: E402 -- path manipulation must precede


APPS_RULE_CONFIG = {
    "gate": {"trailer": "Docs-Reviewed:"},
    "rules": [
        {
            "name": "apps",
            "when_changed": ["desktop/src/apps/*/**"],
            "require_doc": ["README.md"],
            "hint": "a desktop app was added or removed",
        },
        {
            "name": "routes",
            "when_changed": ["tinyagentos/routes/*.py"],
            "require_doc": ["docs/agent-coordination.md", "docs/AGENT_HANDOFF.md"],
            "hint": "an API route module was added or removed",
        },
    ],
}


class TestEvaluateRules:
    def test_no_changes_passes(self):
        assert dg.evaluate_rules([], [], APPS_RULE_CONFIG) == []

    def test_added_app_file_no_doc_no_trailer_fails(self):
        changed = [("A", "desktop/src/apps/Foo/Foo.tsx")]
        failures = dg.evaluate_rules(changed, [], APPS_RULE_CONFIG)
        assert len(failures) == 1
        assert failures[0].startswith("apps -- ")

    def test_added_app_file_with_readme_edit_passes(self):
        changed = [
            ("A", "desktop/src/apps/Foo/Foo.tsx"),
            ("M", "README.md"),
        ]
        assert dg.evaluate_rules(changed, [], APPS_RULE_CONFIG) == []

    def test_added_app_file_with_trailer_passes(self):
        changed = [("A", "desktop/src/apps/Foo/Foo.tsx")]
        messages = ["feat: add Foo app\n\nDocs-Reviewed: reworded the apps table\n"]
        assert dg.evaluate_rules(changed, messages, APPS_RULE_CONFIG) == []

    def test_trailer_with_no_text_after_it_does_not_satisfy(self):
        changed = [("A", "desktop/src/apps/Foo/Foo.tsx")]
        messages = ["feat: add Foo app\n\nDocs-Reviewed:\n"]
        failures = dg.evaluate_rules(changed, messages, APPS_RULE_CONFIG)
        assert len(failures) == 1

    def test_plain_modification_does_not_trigger(self):
        """Editing an existing route file (status M) must not fire the
        'routes' rule -- only structural add/delete counts, to keep the gate
        precise."""
        changed = [("M", "tinyagentos/routes/agents.py")]
        assert dg.evaluate_rules(changed, [], APPS_RULE_CONFIG) == []

    def test_added_test_file_does_not_trigger_structural_rule(self):
        """Adding a co-located test under an app dir is not a structural app
        change and must not fire the 'apps' rule (#171). Same for a new Python
        test module vs the 'routes' rule."""
        changed = [
            ("A", "desktop/src/apps/__tests__/SettingsApp.test.tsx"),
            ("A", "desktop/src/apps/Foo/Foo.test.tsx"),
            ("A", "tests/test_new_route.py"),
        ]
        assert dg.evaluate_rules(changed, [], APPS_RULE_CONFIG) == []

    def test_deleted_route_file_triggers_routes_rule(self):
        changed = [("D", "tinyagentos/routes/old_routes.py")]
        failures = dg.evaluate_rules(changed, [], APPS_RULE_CONFIG)
        assert len(failures) == 1
        assert failures[0].startswith("routes -- ")

    def test_unrelated_rule_is_unaffected_by_other_rule_satisfaction(self):
        """Two rules fire in the same changeset; satisfying only one (via a
        doc edit that doesn't match the other rule's require_doc) still
        leaves the other rule failing."""
        changed = [
            ("A", "desktop/src/apps/Foo/Foo.tsx"),
            ("A", "tinyagentos/routes/new_routes.py"),
            ("M", "README.md"),
        ]
        failures = dg.evaluate_rules(changed, [], APPS_RULE_CONFIG)
        assert len(failures) == 1
        assert failures[0].startswith("routes -- ")


class TestGlobMatch:
    def test_single_star_stays_within_segment(self):
        assert dg._glob_match("tinyagentos/routes/settings.py", "tinyagentos/routes/*.py")
        assert not dg._glob_match(
            "tinyagentos/routes/desktop_browser/__init__.py",
            "tinyagentos/routes/*.py",
        )

    def test_double_star_crosses_segments(self):
        assert dg._glob_match(
            "desktop/src/apps/Foo/sub/x.ts", "desktop/src/apps/*/**"
        )

    def test_trailing_star_matches_within_segment(self):
        assert dg._glob_match("scripts/install-server.sh", "scripts/install*")

    def test_double_star_matches_nested_manifest(self):
        assert dg._glob_match("app-catalog/foo/bar.json", "app-catalog/**")


class TestEvaluateRulesRoutesGlobPrecision:
    def test_nested_route_file_does_not_trigger_routes_rule(self):
        changed = [("A", "tinyagentos/routes/desktop_browser/newmod.py")]
        assert dg.evaluate_rules(changed, [], APPS_RULE_CONFIG) == []

    def test_top_level_route_file_triggers_routes_rule(self):
        changed = [("A", "tinyagentos/routes/newroute.py")]
        failures = dg.evaluate_rules(changed, [], APPS_RULE_CONFIG)
        assert len(failures) == 1
        assert failures[0].startswith("routes -- ")


class TestGetTrailer:
    def test_returns_configured_trailer(self):
        config = {"gate": {"trailer": "Docs-Reviewed:"}}
        assert dg.get_trailer(config) == "Docs-Reviewed:"

    def test_falls_back_to_default_when_missing(self):
        assert dg.get_trailer({}) == dg.DEFAULT_TRAILER


class TestCheckReferencedPaths:
    def test_dead_path_token_fails(self, tmp_path: Path):
        readme = tmp_path / "README.md"
        readme.write_text("See `scripts/nope.sh` for details.\n")
        failures = dg.check_referenced_paths(tmp_path, ["README.md"], {})
        assert len(failures) == 1
        assert "scripts/nope.sh" in failures[0]

    def test_only_real_paths_passes(self, tmp_path: Path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "real.sh").write_text("#!/bin/sh\n")
        readme = tmp_path / "README.md"
        readme.write_text("See `scripts/real.sh` for details.\n")
        failures = dg.check_referenced_paths(tmp_path, ["README.md"], {})
        assert failures == []

    def test_missing_scan_target_is_skipped_not_failed(self, tmp_path: Path):
        """A configured scan target that doesn't exist on disk (e.g. a
        local-only, gitignored doc absent from a fresh checkout) must not
        itself be treated as a failure."""
        failures = dg.check_referenced_paths(tmp_path, ["docs/AGENT_HANDOFF.md"], {})
        assert failures == []

    def test_glob_and_placeholder_tokens_are_ignored(self, tmp_path: Path):
        readme = tmp_path / "README.md"
        readme.write_text(
            "Wildcard install scripts live under `scripts/install*`.\n"
            "Per-framework bridges: `tinyagentos/scripts/install_<framework>.sh`.\n"
        )
        failures = dg.check_referenced_paths(tmp_path, ["README.md"], {})
        assert failures == []

    def test_file_line_citation_is_ignored(self, tmp_path: Path):
        """A file:line citation like `scripts/foo.sh:123` must not be treated as
        a nonexistent path; the line-number suffix is stripped before the
        existence check."""
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "foo.sh").write_text("#!/bin/sh\n")
        readme = tmp_path / "README.md"
        readme.write_text("See `scripts/foo.sh:123` for details.\n")
        failures = dg.check_referenced_paths(tmp_path, ["README.md"], {})
        assert failures == []

    @pytest.mark.parametrize(
        "prose",
        [
            "See scripts/foo.sh:123. That is the place.",   # sentence-ending stop
            "In scripts/foo.sh:123, the loop starts.",       # list/apposition comma
            "grep hit at scripts/foo.sh:12:34 today",        # file:line:col
            "the range (scripts/foo.sh:12-20) covers it",    # paren-closed range
            "multi scripts/foo.sh:1,5-9 selection",          # comma range list
        ],
    )
    def test_citation_shapes_with_punctuation_are_ignored(self, tmp_path: Path, prose):
        """The four shapes that false-positived when the suffix strip ran
        BEFORE the trailing-punct loop (lead block on #2307): the punct
        defeats an end-anchored regex. Each proved red on the pre-reorder
        commit."""
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "foo.sh").write_text("#!/bin/sh\n")
        (tmp_path / "README.md").write_text(prose + "\n")
        failures = dg.check_referenced_paths(tmp_path, ["README.md"], {})
        assert failures == []

    def test_colon_in_real_filename_is_preserved(self, tmp_path: Path):
        """A path legitimately containing a colon is NOT truncated - only
        numeric line-citation suffixes are stripped."""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "weird:name.md").write_text("x\n")
        (tmp_path / "README.md").write_text("See docs/weird:name.md here.\n")
        failures = dg.check_referenced_paths(tmp_path, ["README.md"], {})
        assert failures == []

    def test_markdown_link_url_not_consumed_as_path(self, tmp_path: Path):
        """A markdown link whose URL points to another repo must not have the
        URL consumed as part of the path token."""
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "agent-manual.md").write_text("# Agent Manual\n")
        readme = tmp_path / "README.md"
        readme.write_text(
            "See [docs/agent-manual.md]"
            "(https://github.com/other/repo/blob/main/docs/agent-manual.md).\n"
        )
        failures = dg.check_referenced_paths(tmp_path, ["README.md"], {})
        assert failures == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_glob_double_star_matches_bare_parent():
    """A trailing `/**` matches the bare parent path as well as anything under
    it (folds a Kilo review note that `**` was one-or-more, not zero-or-more)."""
    from check_doc_gate import _glob_match

    assert _glob_match("app-catalog", "app-catalog/**") is True
    assert _glob_match("app-catalog/foo/bar.json", "app-catalog/**") is True
    # A single `*` still does not cross a separator.
    assert _glob_match("tinyagentos/routes/desktop_browser/__init__.py",
                       "tinyagentos/routes/*.py") is False


# ---------------------------------------------------------------------------
# Changelog fragments: a new changelog.d/*.md file satisfies the changelog rule
# instead of editing CHANGELOG.md, so concurrent PRs cannot conflict on the
# shared [Unreleased] anchor. The gate must NOT go inert in the process.
# ---------------------------------------------------------------------------

CHANGELOG_RULE_CONFIG = {
    "gate": {"trailer": "Docs-Reviewed:"},
    "rules": [
        {
            "name": "user-visible-changelog",
            "on_modify": True,
            "when_changed": [
                "tinyagentos/routes/*.py",
                "desktop/src/apps/*/**",
                "desktop/src/App.tsx",
                "desktop/src/components/**",
                "desktop/src/stores/**",
                "tinyagentos/installers/*",
            ],
            "require_doc": ["CHANGELOG.md", "changelog.d/*.md"],
            "hint": "user-visible behaviour changed",
        },
    ],
}


class TestChangelogFragments:
    def test_fragment_satisfies_the_changelog_rule(self):
        changed = [
            ("M", "tinyagentos/routes/notifications.py"),
            ("A", "changelog.d/2291-notes.md"),
        ]
        assert dg.evaluate_rules(changed, [], CHANGELOG_RULE_CONFIG) == []

    def test_editing_changelog_directly_still_satisfies_it(self):
        """Additive, not a migration - the old path must keep working."""
        changed = [
            ("M", "tinyagentos/routes/notifications.py"),
            ("M", "CHANGELOG.md"),
        ]
        assert dg.evaluate_rules(changed, [], CHANGELOG_RULE_CONFIG) == []

    def test_neither_fragment_nor_changelog_nor_trailer_STILL_FAILS(self):
        """The point is fewer conflicts, NOT a weaker gate.

        If this ever passes the rule has gone inert and user-visible changes
        ship undocumented. This is the case that caught a real mistake while
        the feature was being built: putting the convention README inside
        changelog.d/ made it match the glob, so the gate went green here.
        """
        changed = [("M", "tinyagentos/routes/notifications.py")]
        failures = dg.evaluate_rules(changed, [], CHANGELOG_RULE_CONFIG)
        assert len(failures) == 1
        assert failures[0].startswith("user-visible-changelog -- ")

    def test_fragment_in_a_subdirectory_does_not_count(self):
        """One segment deep on purpose: a stray markdown file nested under
        changelog.d/ must not silently satisfy the rule."""
        changed = [
            ("M", "tinyagentos/routes/notifications.py"),
            ("A", "changelog.d/archive/old.md"),
        ]
        assert len(dg.evaluate_rules(changed, [], CHANGELOG_RULE_CONFIG)) == 1

    def test_non_markdown_fragment_does_not_count(self):
        changed = [
            ("M", "tinyagentos/routes/notifications.py"),
            ("A", "changelog.d/2291-notes.txt"),
        ]
        assert len(dg.evaluate_rules(changed, [], CHANGELOG_RULE_CONFIG)) == 1

    def test_desktop_app_shell_triggers_changelog_rule(self):
        changed = [("M", "desktop/src/App.tsx")]
        failures = dg.evaluate_rules(changed, [], CHANGELOG_RULE_CONFIG)
        assert len(failures) == 1
        assert failures[0].startswith("user-visible-changelog -- ")

    def test_desktop_component_triggers_changelog_rule(self):
        changed = [("M", "desktop/src/components/Desktop.tsx")]
        failures = dg.evaluate_rules(changed, [], CHANGELOG_RULE_CONFIG)
        assert len(failures) == 1
        assert failures[0].startswith("user-visible-changelog -- ")

    def test_desktop_store_triggers_changelog_rule(self):
        changed = [("M", "desktop/src/stores/theme-store.ts")]
        failures = dg.evaluate_rules(changed, [], CHANGELOG_RULE_CONFIG)
        assert len(failures) == 1
        assert failures[0].startswith("user-visible-changelog -- ")

    def test_desktop_shell_fragment_satisfies_rule(self):
        changed = [
            ("M", "desktop/src/App.tsx"),
            ("A", "changelog.d/2303-reduce-effects.md"),
        ]
        assert dg.evaluate_rules(changed, [], CHANGELOG_RULE_CONFIG) == []

    def test_desktop_shell_test_file_does_not_trigger(self):
        changed = [
            ("A", "desktop/src/components/__tests__/Desktop.test.tsx"),
            ("A", "desktop/src/stores/__tests__/theme-store.test.ts"),
        ]
        assert dg.evaluate_rules(changed, [], CHANGELOG_RULE_CONFIG) == []


class TestConfigErrorExitCode:
    """A broken/unparseable config must exit distinctly from a real violation.

    Regression: previously an unparseable config raised an unhandled traceback
    that exited 1 -- identical to a genuine doc-gate violation -- so a typo in
    docs/doc-gate.toml looked just like a missing changelog."""

    def test_unparseable_config_returns_config_error_code(self, tmp_path, capsys):
        bad = tmp_path / "bad.toml"
        bad.write_text('key = "unterminated string\n')
        rc = dg.main(["--config", str(bad), "print-trailer"])
        captured = capsys.readouterr()
        assert rc == dg.EXIT_CONFIG_ERROR
        assert rc != dg.EXIT_VIOLATION
        assert "config error" in captured.err

    def test_missing_config_file_returns_config_error_code(self, tmp_path):
        missing = tmp_path / "does_not_exist.toml"
        rc = dg.main(["--config", str(missing), "print-trailer"])
        assert rc == dg.EXIT_CONFIG_ERROR
        assert rc != dg.EXIT_VIOLATION

    def test_real_violation_returns_violation_not_config_error(self):
        """Exit code 1 (violation) must remain distinct from the config-error
        code."""
        changed = [("A", "tinyagentos/routes/themes.py")]
        failures = dg.evaluate_rules(changed, [], APPS_RULE_CONFIG)
        assert dg._report(failures) == dg.EXIT_VIOLATION

    def test_structurally_invalid_config_returns_config_error_code(self, tmp_path, capsys):
        """Valid TOML of the wrong shape (rules is a string, not a list of
        tables) must be caught as a config error, not crash the rule loop with
        an AttributeError that exits 1."""
        bad = tmp_path / "struct.toml"
        bad.write_text('rules = "not a list"\n[gate]\ntrailer = "X:"\n')
        rc = dg.main(["--config", str(bad), "print-trailer"])
        captured = capsys.readouterr()
        assert rc == dg.EXIT_CONFIG_ERROR
        assert rc != dg.EXIT_VIOLATION
        assert "config error" in captured.err

    def test_non_utf8_config_returns_config_error_code(self, tmp_path, capsys):
        """A config file with invalid UTF-8 bytes raises UnicodeDecodeError
        inside tomllib.load (which decodes before parsing).  That must be
        caught and reported as a config error, not exit 1."""
        bad = tmp_path / "bad_utf8.toml"
        bad.write_bytes(b'trailer = "Docs-Reviewed:"\n\x80\xff\xfe\n')
        rc = dg.main(["--config", str(bad), "print-trailer"])
        captured = capsys.readouterr()
        assert rc == dg.EXIT_CONFIG_ERROR
        assert rc != dg.EXIT_VIOLATION
        assert "config error" in captured.err

    def test_bad_cli_flag_exits_argparse_code(self):
        """A typo'd flag must exit with argparse's own code (2), distinct from
        both the violation code (1) and the config-error code."""
        with pytest.raises(SystemExit) as exc_info:
            dg.main(["diff-gate"])
        assert exc_info.value.code == 2
        assert exc_info.value.code != dg.EXIT_VIOLATION
        assert exc_info.value.code != dg.EXIT_CONFIG_ERROR

    def test_exit_codes_are_mutually_distinguishable(self):
        """The three non-zero outcomes must be mutually distinguishable:
        violation (1), argparse usage error (2), config error (not 2)."""
        assert dg.EXIT_OK == 0
        assert dg.EXIT_VIOLATION == 1
        assert dg.EXIT_CONFIG_ERROR != 1
        assert dg.EXIT_CONFIG_ERROR != 2


ROUTES_MODIFY_CONFIG = {
    "gate": {"trailer": "Docs-Reviewed:"},
    "rules": [
        {
            "name": "routes",
            "on_modify": True,
            "when_changed": ["tinyagentos/routes/*.py"],
            "require_doc": ["docs/agent-coordination.md"],
            "hint": "an API route module was added, removed, or modified",
        },
    ],
}


class TestModificationTriggersGate:
    def test_modification_to_route_fails_without_doc(self):
        """A plain modification to a route with no doc edit FAILS the gate."""
        changed = [("M", "tinyagentos/routes/agents.py")]
        failures = dg.evaluate_rules(changed, [], ROUTES_MODIFY_CONFIG)
        assert len(failures) == 1
        assert failures[0].startswith("routes -- ")

    def test_modification_to_route_passes_with_doc_edit(self):
        changed = [
            ("M", "tinyagentos/routes/agents.py"),
            ("M", "docs/agent-coordination.md"),
        ]
        assert dg.evaluate_rules(changed, [], ROUTES_MODIFY_CONFIG) == []

    def test_modification_to_route_passes_with_trailer(self):
        changed = [("M", "tinyagentos/routes/agents.py")]
        messages = ["feat: modify agents route\n\nDocs-Reviewed: internal refactor\n"]
        assert dg.evaluate_rules(changed, messages, ROUTES_MODIFY_CONFIG) == []

    def test_modified_test_file_does_not_trigger(self):
        """Modifying a test file is never structural, even with on_modify.

        The path must MATCH the rule's glob, otherwise the test passes for the
        wrong reason -- it would pass with the exemption deleted.
        """
        changed = [("M", "tinyagentos/routes/test_agents.py")]
        assert dg.evaluate_rules(changed, [], ROUTES_MODIFY_CONFIG) == []


BROAD_CHANGELOG_CONFIG = {
    "gate": {"trailer": "Docs-Reviewed:"},
    "rules": [
        {
            "name": "user-visible-changelog",
            "on_modify": True,
            "when_changed": ["tinyagentos/**", "desktop/src/**"],
            "require_doc": ["CHANGELOG.md", "changelog.d/*.md"],
            "hint": "user-visible behaviour changed",
        },
    ],
}


class TestBroadChangelogRequired:
    def test_tinyagentos_modification_requires_changelog(self):
        changed = [("M", "tinyagentos/app.py")]
        failures = dg.evaluate_rules(changed, [], BROAD_CHANGELOG_CONFIG)
        assert len(failures) == 1
        assert failures[0].startswith("user-visible-changelog -- ")

    def test_desktop_src_modification_requires_changelog(self):
        changed = [("M", "desktop/src/components/Foo.tsx")]
        failures = dg.evaluate_rules(changed, [], BROAD_CHANGELOG_CONFIG)
        assert len(failures) == 1
        assert failures[0].startswith("user-visible-changelog -- ")

    def test_changelog_fragment_satisfies_broad_rule(self):
        changed = [
            ("M", "tinyagentos/app.py"),
            ("A", "changelog.d/1234-fix.md"),
        ]
        assert dg.evaluate_rules(changed, [], BROAD_CHANGELOG_CONFIG) == []

    def test_changelog_md_edit_satisfies_broad_rule(self):
        changed = [
            ("M", "tinyagentos/app.py"),
            ("M", "CHANGELOG.md"),
        ]
        assert dg.evaluate_rules(changed, [], BROAD_CHANGELOG_CONFIG) == []

    def test_trailer_satisfies_broad_rule(self):
        changed = [("M", "tinyagentos/app.py")]
        messages = ["feat: update app\n\nDocs-Reviewed: no user-facing change\n"]
        assert dg.evaluate_rules(changed, messages, BROAD_CHANGELOG_CONFIG) == []

    def test_test_file_under_tinyagentos_exempt(self):
        """A test file under tinyagentos/ is not a structural change.

        `tests/` is outside the rule's globs, so it cannot prove the exemption;
        these paths are inside them.
        """
        for path in ("tinyagentos/test_helpers.py", "desktop/src/apps/Foo/Foo.test.tsx"):
            changed = [("M", path)]
            assert dg.evaluate_rules(changed, [], BROAD_CHANGELOG_CONFIG) == [], path

    def test_doc_file_under_tinyagentos_still_triggers(self):
        """A non-test doc file under tinyagentos/ should still require changelog."""
        changed = [("M", "tinyagentos/README.md")]
        failures = dg.evaluate_rules(changed, [], BROAD_CHANGELOG_CONFIG)
        assert len(failures) == 1


class TestRequiredSections:
    """RED 1 and RED 2: content assertion for Layer A.

    Before this check the gate was path-only: a doc emptied of its protected
    sections satisfied every rule as long as the file was touched. These tests
    pin the new content-aware behaviour.
    """

    def _cfg(self, headings):
        return {
            "invariants": {
                "required_sections": [
                    {
                        "doc": "docs/agent-coordination.md",
                        "headings": headings,
                    }
                ]
            }
        }

    def test_all_required_sections_present_passes(self, tmp_path: Path):
        """RED 2: with all required sections present, the gate is GREEN."""
        doc = tmp_path / "docs" / "agent-coordination.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(
            "# Working the repo\n\n"
            "## Agent API surface (scoped registry JWT)\n\n"
            "## Device bearer self-service (second, narrower passthrough)\n\n"
            "## OS change-event stream (`GET /api/os/events`, session-only)\n"
        )
        failures = dg.check_required_sections(
            tmp_path,
            self._cfg([
                "Agent API surface (scoped registry JWT)",
                "Device bearer self-service (second, narrower passthrough)",
                "OS change-event stream (`GET /api/os/events`, session-only)",
            ]),
        )
        assert failures == []

    def test_missing_required_section_fails(self, tmp_path: Path):
        """RED 1: deleting a required section while the file is present and
        touched causes the gate to FAIL."""
        doc = tmp_path / "docs" / "agent-coordination.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(
            "# Working the repo\n\n"
            "## Device bearer self-service (second, narrower passthrough)\n\n"
            "## OS change-event stream (`GET /api/os/events`, session-only)\n"
        )
        failures = dg.check_required_sections(
            tmp_path,
            self._cfg([
                "Agent API surface (scoped registry JWT)",
                "Device bearer self-service (second, narrower passthrough)",
                "OS change-event stream (`GET /api/os/events`, session-only)",
            ]),
        )
        assert len(failures) == 1
        assert "Agent API surface (scoped registry JWT)" in failures[0]
        assert "docs/agent-coordination.md" in failures[0]

    def test_missing_scan_target_is_skipped(self, tmp_path: Path):
        """A required doc that does not exist on disk is skipped, not failed."""
        failures = dg.check_required_sections(
            tmp_path,
            self._cfg(["Agent API surface (scoped registry JWT)"]),
        )
        assert failures == []

    def test_empty_required_sections_list_passes(self, tmp_path: Path):
        """No required_sections configured -> clean."""
        assert dg.check_required_sections(tmp_path, {"invariants": {}}) == []

    def test_invariants_command_reports_required_section_failure(self, tmp_path):
        """The invariants command function must report missing required sections."""
        doc = tmp_path / "docs" / "agent-coordination.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(
            "# Working the repo\n\n"
            "## Device bearer self-service (second, narrower passthrough)\n"
        )
        cfg = {
            "gate": {"trailer": "Docs-Reviewed:"},
            "invariants": {
                "required_sections": [
                    {
                        "doc": "docs/agent-coordination.md",
                        "headings": [
                            "Agent API surface (scoped registry JWT)",
                        ],
                    }
                ]
            }
        }
        failures = dg.check_required_sections(tmp_path, cfg)
        assert len(failures) == 1
        assert "Agent API surface (scoped registry JWT)" in failures[0]


class TestTrailerLogged:
    def test_trailer_usage_is_logged(self, capsys):
        commits = [
            ("abc1234567890", "John Doe", "fix: something\n\nDocs-Reviewed: internal refactor\n"),
        ]
        dg._log_trailer_usage(commits, "Docs-Reviewed:")
        captured = capsys.readouterr()
        assert "trailer override" in captured.out
        assert "John Doe" in captured.out
        assert "abc12345" in captured.out

    def test_no_trailer_no_log(self, capsys):
        commits = [
            ("abc1234567890", "John Doe", "fix: something\n"),
        ]
        dg._log_trailer_usage(commits, "Docs-Reviewed:")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_empty_trailer_text_not_logged(self, capsys):
        commits = [
            ("abc1234567890", "John Doe", "fix: something\n\nDocs-Reviewed:\n"),
        ]
        dg._log_trailer_usage(commits, "Docs-Reviewed:")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_multiple_trailer_commits_all_logged(self, capsys):
        commits = [
            ("aaa1111111111", "Alice", "feat: add feature\n\nDocs-Reviewed: new feature\n"),
            ("bbb2222222222", "Bob", "fix: bug\n\nDocs-Reviewed: bug fix\n"),
        ]
        dg._log_trailer_usage(commits, "Docs-Reviewed:")
        captured = capsys.readouterr()
        assert "Alice" in captured.out
        assert "Bob" in captured.out
        assert "aaa11111" in captured.out
        assert "bbb22222" in captured.out



class TestCommitsWithMessagesParsing:
    """The producer half of the trailer audit.

    The tests above hand-build the tuples, so they pass whether or not
    anything can actually produce them. These drive the parser with the exact
    bytes `git log --format=%H%x1f%an%x1f%B%x1f` emits.
    """

    LOG_FORMAT_OUTPUT = (
        "abc1234567890\x1fJohn Doe\x1ffix: something\n\nDocs-Reviewed: internal refactor\n\x1e"
        "\ndef4567890123\x1fJane Roe\x1ffeat: another thing\n\x1e"
    )

    def _parse(self, monkeypatch, out):
        # Accepts ref= because _run_git now takes it: callers pass the base ref
        # so a git failure can name it. A double that does not accept the real
        # signature fails with TypeError and says nothing about the parser.
        monkeypatch.setattr(dg, "_run_git", lambda args, ref=None: out)
        return dg._git_commits_with_messages("origin/dev")

    def test_parses_one_record_per_commit(self, monkeypatch):
        commits = self._parse(monkeypatch, self.LOG_FORMAT_OUTPUT)
        assert len(commits) == 2
        assert [c[0] for c in commits] == ["abc1234567890", "def4567890123"]
        assert [c[1] for c in commits] == ["John Doe", "Jane Roe"]
        assert "Docs-Reviewed: internal refactor" in commits[0][2]
        assert "Docs-Reviewed" not in commits[1][2]

    def test_parsed_commits_reach_the_log(self, monkeypatch, capsys):
        """End to end over the seam: real log bytes must produce a log line."""
        commits = self._parse(monkeypatch, self.LOG_FORMAT_OUTPUT)
        dg._log_trailer_usage(commits, "Docs-Reviewed:")
        captured = capsys.readouterr()
        assert "trailer override" in captured.out
        assert "John Doe" in captured.out
        assert "Jane Roe" not in captured.out

    def test_empty_range_is_empty(self, monkeypatch):
        assert self._parse(monkeypatch, "") == []


# A config mirroring the real interaction between the routes rule and the
# user-visible-changelog rule: a route addition triggers BOTH rules, so the
# changelog fragment satisfies the latter while only a doc edit can satisfy the
# former. This is the shape that lets the deletion bypass hide -- the
# user-visible-changelog rule is always satisfiable, so a deleted routes
# require_doc only shows as a bug once every OTHER rule is also satisfied.
ROUTES_AND_CHANGELOG_CONFIG = {
    "gate": {"trailer": "Docs-Reviewed:"},
    "rules": [
        {
            "name": "routes",
            "on_modify": True,
            "when_changed": ["tinyagentos/routes/*.py"],
            "require_doc": ["docs/agent-coordination.md"],
            "hint": "an API route module was added, removed, or modified",
        },
        {
            "name": "user-visible-changelog",
            "on_modify": True,
            "when_changed": ["tinyagentos/**", "desktop/src/**"],
            "require_doc": ["CHANGELOG.md", "changelog.d/*.md"],
            "hint": "user-visible behaviour changed",
        },
    ],
}


def _failure_names(failures: list[str]) -> list[str]:
    return [f.split(" -- ")[0] for f in failures]


class TestDeletedRequireDocDoesNotSatisfy:
    """A deleted require_doc must NOT satisfy a rule.

    Bug: scripts/check_doc_gate.py line 247 ``all_paths`` discarded the git
    status, so a ``git rm``'d require_doc was treated as having satisfied the
    rule. These tests assert on the rule NAME in the failures list, not just
    the exit code: with only the route add + doc deletion (no changelog
    fragment) the routes failure is masked by a coinciding user-visible-changelog
    failure, so an exit-code-only assertion stays green even while the bug is
    live. Red-first: criterion 1 fails before the fix, passes after.
    """

    def test1_delete_require_doc_routes_still_fails(self):
        """Criterion 1: add a when_changed path + delete the require_doc +
        satisfy every other rule -- the routes rule MUST appear in failures."""
        changed = [
            ("A", "tinyagentos/routes/zzz_probe.py"),
            ("D", "docs/agent-coordination.md"),
            ("A", "changelog.d/9999-probe.md"),
        ]
        failures = dg.evaluate_rules(changed, [], ROUTES_AND_CHANGELOG_CONFIG)
        names = _failure_names(failures)
        assert "routes" in names
        assert "user-visible-changelog" not in names

    def test2_edit_require_doc_passes(self):
        """Criterion 2: add a when_changed path + genuinely edit the require_doc
        -> clean. Guards against fixing this by deadening satisfaction entirely."""
        changed = [
            ("A", "tinyagentos/routes/zzz_probe.py"),
            ("M", "docs/agent-coordination.md"),
            ("A", "changelog.d/9999-probe.md"),
        ]
        failures = dg.evaluate_rules(changed, [], ROUTES_AND_CHANGELOG_CONFIG)
        assert failures == []

    def test3_add_require_doc_as_new_file_passes(self):
        """Criterion 3: add a when_changed path + ADD the require_doc as a new
        file -> clean."""
        changed = [
            ("A", "tinyagentos/routes/zzz_probe.py"),
            ("A", "docs/agent-coordination.md"),
            ("A", "changelog.d/9999-probe.md"),
        ]
        failures = dg.evaluate_rules(changed, [], ROUTES_AND_CHANGELOG_CONFIG)
        assert failures == []

    def test4_delete_when_changed_path_still_triggers(self):
        """Criterion 4: delete a when_changed path -- still triggers the rule,
        unchanged from today. The triggering path must keep deletions; only
        the satisfaction path must exclude them."""
        changed = [("D", "tinyagentos/routes/zzz_probe.py")]
        failures = dg.evaluate_rules(changed, [], ROUTES_AND_CHANGELOG_CONFIG)
        names = _failure_names(failures)
        assert "routes" in names


class TestGitHooksTrailerEnforcement:
    """End-to-end tests for the pre-commit / commit-msg hook split.

    These create a temp git repo, install the actual hooks, and verify that
    a valid Docs-Reviewed trailer lets a violating commit through while
    missing or empty trailers still block.
    """

    def _write_hooks(self, repo: Path) -> None:
        hooks_dir = repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)

        pre_commit_src = (REPO_ROOT / ".githooks" / "pre-commit").read_text()
        commit_msg_src = (REPO_ROOT / ".githooks" / "commit-msg").read_text()

        (hooks_dir / "pre-commit").write_text(pre_commit_src)
        (hooks_dir / "commit-msg").write_text(commit_msg_src)
        os.chmod(hooks_dir / "pre-commit", 0o755)
        os.chmod(hooks_dir / "commit-msg", 0o755)

    def _setup_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()

        scripts_dir = repo / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "check_doc_gate.py").write_text(
            (REPO_ROOT / "scripts" / "check_doc_gate.py").read_text()
        )

        docs_dir = repo / "docs"
        docs_dir.mkdir()
        (docs_dir / "doc-gate.toml").write_text(
            '[gate]\ntrailer = "Docs-Reviewed:"\n\n[[rules]]\n'
            'name = "test-rule"\non_modify = true\n'
            'when_changed = ["*.py"]\nrequire_doc = ["README.md"]\n'
            'hint = "test rule"\n'
        )

        (repo / "README.md").write_text("# README\n")
        (repo / "feature.py").write_text("# feature\n")

        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

        self._write_hooks(repo)

        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo, check=True, capture_output=True,
        )
        return repo

    def test_trailer_present_commit_succeeds(self, tmp_path: Path):
        """A commit with a valid Docs-Reviewed trailer succeeds with hooks active."""
        repo = self._setup_repo(tmp_path)

        (repo / "feature.py").write_text("# feature v2\n")
        subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True)

        result = subprocess.run(
            [
                "git", "commit",
                "-m", "update feature\n\nDocs-Reviewed: internal change",
            ],
            cwd=repo, capture_output=True,
        )
        assert result.returncode == 0, f"Commit failed: {result.stderr.decode()}"

    def test_no_trailer_commit_fails(self, tmp_path: Path):
        """A commit with no Docs-Reviewed trailer fails with hooks active."""
        repo = self._setup_repo(tmp_path)

        (repo / "feature.py").write_text("# feature v2\n")
        subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True)

        result = subprocess.run(
            ["git", "commit", "-m", "update feature"],
            cwd=repo, capture_output=True,
        )
        assert result.returncode != 0, "Commit should have failed without trailer"

    def test_empty_trailer_commit_fails(self, tmp_path: Path):
        """A commit with an empty Docs-Reviewed trailer fails with hooks active."""
        repo = self._setup_repo(tmp_path)

        (repo / "feature.py").write_text("# feature v2\n")
        subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True)

        result = subprocess.run(
            ["git", "commit", "-m", "update feature\n\nDocs-Reviewed:\n"],
            cwd=repo, capture_output=True,
        )
        assert result.returncode != 0, "Commit should have failed with empty trailer"


CONTRIBUTOR_SKILL_CONFIG = {
    "gate": {"trailer": "Docs-Reviewed:"},
    "rules": [
        {
            "name": "contributor-skill",
            "on_modify": True,
            "when_changed": [".github/workflows/*.yml", "pyproject.toml", "CONTRIBUTING.md"],
            "require_doc": [".claude/skills/taos-development-skill/*.md", "docs/*.md"],
            "hint": "CI, packaging or contribution rules changed; review the contributor skill and docs",
        }
    ],
}


class TestUsesPinOnlyDiff:
    """Content-based detection of a pure `uses:` action-pin version bump."""

    def test_pin_bump_diff_is_pin_only(self):
        """The exact shape of the #2507 dependabot bump: v7 -> v9 on one line."""
        diff = (
            "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
            "--- a/.github/workflows/ci.yml\n"
            "+++ b/.github/workflows/ci.yml\n"
            "@@ -42,1 +42,1 @@\n"
            "-        uses: actions/github-script@v7\n"
            "+        uses: actions/github-script@v9\n"
        )
        assert dg._path_diff_is_uses_pin_only(diff) is True

    def test_two_parallel_pin_bumps_are_pin_only(self):
        diff = (
            "-        uses: actions/checkout@v4\n"
            "+        uses: actions/checkout@v5\n"
            "-      - uses: actions/setup-python@v4\n"
            "+      - uses: actions/setup-python@v5\n"
        )
        assert dg._path_diff_is_uses_pin_only(diff) is True

    def test_sha_pin_with_trailing_comment_is_pin_only(self):
        """A SHA pin carrying a `# vX` comment is still just a pin line."""
        diff = (
            "-        uses: org/action@1111111111111111111111111111111111111111  # v2.6.1\n"
            "+        uses: org/action@2222222222222222222222222222222222222222  # v2.6.1\n"
        )
        assert dg._path_diff_is_uses_pin_only(diff) is True

    def test_new_step_makes_diff_substantive(self):
        diff = (
            "--- a/.github/workflows/ci.yml\n"
            "+++ b/.github/workflows/ci.yml\n"
            "@@ -10,1 +10,2 @@\n"
            "-        uses: actions/checkout@v4\n"
            "+        uses: actions/checkout@v5\n"
            "+      - run: echo bye\n"
        )
        assert dg._path_diff_is_uses_pin_only(diff) is False

    def test_adding_a_with_input_is_substantive(self):
        diff = (
            "-        uses: actions/checkout@v4\n"
            "+        uses: actions/checkout@v5\n"
            "+          persist-credentials: false\n"
        )
        assert dg._path_diff_is_uses_pin_only(diff) is False

    def test_non_pinned_uses_is_substantive(self):
        """A `uses:` with no @ref is not a pin; changing it is substantive."""
        diff = (
            "-        uses: actions/checkout\n"
            "+        uses: actions/checkout@v5\n"
        )
        assert dg._path_diff_is_uses_pin_only(diff) is False

    def test_action_target_replacement_is_substantive(self):
        """A swapped action TARGET must never be exempt (supply-chain swap).

        Both sides are syntactically `uses: <x>@<ref>` pin lines, so a
        per-line classifier calls this a pin bump and lets an attacker-owned
        action through the gate. Only pairing removed with added entries
        catches it.
        """
        diff = (
            "@@ -10,1 +10,1 @@\n"
            "-        uses: actions/checkout@v4\n"
            "+        uses: attacker/checkout@v1\n"
        )
        assert dg._path_diff_is_uses_pin_only(diff) is False

    def test_target_replacement_keeping_the_ref_is_substantive(self):
        """Same ref, different owner — the ref alone proves nothing."""
        diff = (
            "@@ -10,1 +10,1 @@\n"
            "-        uses: actions/checkout@v4\n"
            "+        uses: evil/checkout@v4\n"
        )
        assert dg._path_diff_is_uses_pin_only(diff) is False

    def test_real_bump_in_one_hunk_does_not_launder_a_swap_in_another(self):
        """A genuine bump must not vouch for a target swap elsewhere."""
        diff = (
            "@@ -10,1 +10,1 @@\n"
            "-        uses: actions/checkout@v4\n"
            "+        uses: actions/checkout@v5\n"
            "@@ -40,1 +40,1 @@\n"
            "-        uses: actions/setup-python@v4\n"
            "+        uses: attacker/setup-python@v4\n"
        )
        assert dg._path_diff_is_uses_pin_only(diff) is False

    def test_added_pin_line_without_a_removed_counterpart_is_substantive(self):
        """A brand-new action added to the file is not a version bump."""
        diff = (
            "@@ -10,0 +11,1 @@\n"
            "+        uses: actions/cache@v4\n"
        )
        assert dg._path_diff_is_uses_pin_only(diff) is False

    def test_empty_diff_is_vacuously_pin_only(self):
        assert dg._path_diff_is_uses_pin_only("") is True


class TestEvaluateRulesPinOnlyPaths:
    """The CONTRIBUTOR-SKILL half of #2507: a version-only workflow bump is
    exempt, a substantive workflow edit is not -- regardless of author.

    The exemption is content-based (driven by a caller-computed pin_only_paths
    set), so evaluate_rules itself never inspects identity.
    """

    WF = ".github/workflows/distrust-green-gate.yml"

    def test_version_only_bump_goes_green_without_trailer(self):
        """RED -> GREEN: a version-only workflow change marked pin-only trips no
        rule, so no Docs-Reviewed trailer is needed."""
        changed = [("M", self.WF)]
        failures = dg.evaluate_rules(
            changed, [], CONTRIBUTOR_SKILL_CONFIG, pin_only_paths={self.WF}
        )
        assert failures == []

    def test_substantive_workflow_edit_is_red_without_trailer(self):
        """GREEN must not go inert: a substantive (non-pin) workflow edit still
        fails without a trailer."""
        changed = [("M", self.WF)]
        failures = dg.evaluate_rules(
            changed, [], CONTRIBUTOR_SKILL_CONFIG, pin_only_paths=set()
        )
        assert len(failures) == 1
        assert "contributor-skill" in failures[0]

    def test_substantive_bot_edit_is_red_without_trailer(self):
        """The escape hatch is NOT identity-based: a bot (or any author) making a
        substantive workflow edit still fails without a trailer."""
        changed = [("M", self.WF)]
        messages = ["chore: edit workflow\n\nSigned-off-by: dependabot[bot] <x@x.com>\n"]
        failures = dg.evaluate_rules(
            changed, messages, CONTRIBUTOR_SKILL_CONFIG, pin_only_paths=set()
        )
        assert len(failures) == 1
        assert "contributor-skill" in failures[0]

    def test_default_pin_only_paths_keeps_rule_firing(self):
        """Omitting pin_only_paths (the pre-fix behaviour) still fails a bare M."""
        changed = [("M", self.WF)]
        failures = dg.evaluate_rules(changed, [], CONTRIBUTOR_SKILL_CONFIG)
        assert len(failures) == 1
        assert "contributor-skill" in failures[0]

    def test_added_workflow_file_is_not_exempt(self):
        """A brand-new (status A) workflow file is substantive even if its only
        lines are `uses:` pins -- pin-only detection is M-only."""
        changed = [("A", ".github/workflows/new-ci.yml")]
        failures = dg.evaluate_rules(changed, [], CONTRIBUTOR_SKILL_CONFIG)
        assert len(failures) == 1
        assert "contributor-skill" in failures[0]


class TestEndToEndPinOnlyWorkflowBump:
    """End-to-end over a real temp git repo, exercising `diff-gate --base` with
    the live diff and the live pin-only detection (no mocking of git).

    A dependabot-style version-only bump (no Docs-Reviewed trailer) goes GREEN;
    a substantive workflow edit by a human still goes RED (#2507).
    """

    CONTRIBUTOR_CONFIG = (
        '[gate]\ntrailer = "Docs-Reviewed:"\n\n'
        '[[rules]]\n'
        'name = "contributor-skill"\n'
        'on_modify = true\n'
        'when_changed = [".github/workflows/*.yml", "pyproject.toml", "CONTRIBUTING.md"]\n'
        'require_doc = [".claude/skills/taos-development-skill/*.md", "docs/*.md"]\n'
        'hint = "CI, packaging or contribution rules changed"\n'
    )

    def _setup_repo(self, tmp_path: Path) -> tuple[Path, Path]:
        repo = tmp_path / "repo"
        repo.mkdir()
        wf_dir = repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "name: CI\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: actions/checkout@v4\n"
        )
        docs_dir = repo / "docs"
        docs_dir.mkdir()
        config_path = docs_dir / "doc-gate.toml"
        config_path.write_text(self.CONTRIBUTOR_CONFIG)
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "dev@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "dev"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo, check=True, capture_output=True,
        )
        return repo, config_path

    def test_version_only_bump_is_green(self, tmp_path: Path, monkeypatch):
        repo, cfg = self._setup_repo(tmp_path)
        wf = repo / ".github" / "workflows" / "ci.yml"
        wf.write_text(wf.read_text().replace("actions/checkout@v4", "actions/checkout@v5"))
        # Author it like the bot that actually produces these bumps.
        subprocess.run(
            ["git", "-c", "user.name=dependabot[bot]", "-c", "user.email=bot@bot", "add", "."],
            cwd=repo, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "chore(deps): bump actions/checkout from 4 to 5"],
            cwd=repo, check=True, capture_output=True,
        )
        monkeypatch.setattr(dg, "REPO_ROOT", repo)
        rc = dg.main(["--config", str(cfg), "diff-gate", "--base", "HEAD~1"])
        assert rc == dg.EXIT_OK

    def test_version_only_bump_is_green_staged(self, tmp_path: Path, monkeypatch):
        """The pre-commit/commit-msg hooks run `diff-gate --staged`; the same
        exemption must apply there so a local version-only bump is not wedged."""
        repo, cfg = self._setup_repo(tmp_path)
        wf = repo / ".github" / "workflows" / "ci.yml"
        wf.write_text(wf.read_text().replace("actions/checkout@v4", "actions/checkout@v5"))
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        monkeypatch.setattr(dg, "REPO_ROOT", repo)
        rc = dg.main(["--config", str(cfg), "diff-gate", "--staged"])
        assert rc == dg.EXIT_OK

    def test_substantive_workflow_change_is_red_staged(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        repo, cfg = self._setup_repo(tmp_path)
        wf = repo / ".github" / "workflows" / "ci.yml"
        wf.write_text(wf.read_text() + "      - run: echo bye\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        monkeypatch.setattr(dg, "REPO_ROOT", repo)
        rc = dg.main(["--config", str(cfg), "diff-gate", "--staged"])
        assert rc == dg.EXIT_VIOLATION
        assert "contributor-skill" in capsys.readouterr().out

    def test_substantive_workflow_change_is_red(self, tmp_path: Path, monkeypatch, capsys):
        """A substantive (non-pin) workflow edit by a human still fails the gate
        under the real --base diff path."""
        repo, cfg = self._setup_repo(tmp_path)
        wf = repo / ".github" / "workflows" / "ci.yml"
        wf.write_text(wf.read_text() + "      - run: echo bye\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-m", "human edit: add a run step"],
            cwd=repo, check=True, capture_output=True,
        )
        monkeypatch.setattr(dg, "REPO_ROOT", repo)
        rc = dg.main(["--config", str(cfg), "diff-gate", "--base", "HEAD~1"])
        assert rc == dg.EXIT_VIOLATION
        assert "contributor-skill" in capsys.readouterr().out
