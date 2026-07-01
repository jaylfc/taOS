"""Unit tests for scripts/check_doc_gate.py.

These call evaluate_rules() and check_referenced_paths() directly with
synthetic inputs -- no shelling out to git, no dependence on the state of
this checkout's actual history.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
