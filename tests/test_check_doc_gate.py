from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "check_doc_gate",
    REPO_ROOT / "scripts" / "check_doc_gate.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MOD)
evaluate_rules = _MOD.evaluate_rules


def _base_config() -> dict:
    return {
        "gate": {"trailer": "Docs-Reviewed:"},
        "rules": [
            {
                "name": "test_route",
                "on_modify": True,
                "when_changed": ["tinyagentos/routes/themes.py"],
                "require_doc": ["CHANGELOG.md"],
                "hint": "a route module was modified",
            }
        ],
    }


class TestEvaluateRulesOnModify:
    """Five cases required by the task."""

    def test_m_only_no_doc_fails(self):
        """(a) An M-only change matching an on_modify rule with no doc edit FAILS."""
        config = _base_config()
        changed = [("M", "tinyagentos/routes/themes.py")]
        commit_messages: list[str] = []
        failures = evaluate_rules(changed, commit_messages, config)
        assert len(failures) == 1
        assert "CHANGELOG.md" in failures[0]

    def test_m_only_with_doc_passes(self):
        """(b) The same change WITH the required doc edited PASSES."""
        config = _base_config()
        changed = [
            ("M", "tinyagentos/routes/themes.py"),
            ("M", "CHANGELOG.md"),
        ]
        commit_messages: list[str] = []
        failures = evaluate_rules(changed, commit_messages, config)
        assert failures == []

    def test_m_only_with_trailer_passes(self):
        """(c) The same change with a Docs-Reviewed trailer PASSES."""
        config = _base_config()
        changed = [("M", "tinyagentos/routes/themes.py")]
        commit_messages = ["Fix themes\n\nDocs-Reviewed: reviewed the change"]
        failures = evaluate_rules(changed, commit_messages, config)
        assert failures == []

    def test_m_only_without_on_modify_passes(self):
        """(d) An M-only change matching a rule WITHOUT on_modify still passes,
        proving the default did not change."""
        config = {
            "gate": {"trailer": "Docs-Reviewed:"},
            "rules": [
                {
                    "name": "test_route_default",
                    "when_changed": ["tinyagentos/routes/themes.py"],
                    "require_doc": ["CHANGELOG.md"],
                    "hint": "a route module was modified",
                }
            ],
        }
        changed = [("M", "tinyagentos/routes/themes.py")]
        commit_messages: list[str] = []
        failures = evaluate_rules(changed, commit_messages, config)
        assert failures == []

    def test_ad_triggering_still_works(self):
        """(e) A/D triggering still works as before."""
        config = _base_config()
        # A triggers
        failures = evaluate_rules([("A", "tinyagentos/routes/themes.py")], [], config)
        assert len(failures) == 1
        # D triggers
        failures = evaluate_rules([("D", "tinyagentos/routes/themes.py")], [], config)
        assert len(failures) == 1
        # A with doc edited passes
        failures = evaluate_rules(
            [("A", "tinyagentos/routes/themes.py"), ("A", "CHANGELOG.md")],
            [],
            config,
        )
        assert failures == []


class TestEvaluateRulesEdgeCases:
    """Additional coverage for the on_modify implementation."""

    def test_test_paths_excluded_even_with_on_modify(self):
        """Test paths must stay excluded from triggering, as now.

        The rule glob deliberately MATCHES the test path, so only the
        test-path exclusion keeps it from firing -- without that exclusion
        this test goes red.
        """
        config = _base_config()
        config["rules"][0]["when_changed"] = ["tests/routes/*.py"]
        changed = [("M", "tests/routes/test_themes.py")]
        commit_messages: list[str] = []
        failures = evaluate_rules(changed, commit_messages, config)
        assert failures == []

    def test_multiple_rules_mixed_on_modify(self):
        """Only rules with on_modify=true fire on M; others do not."""
        config = {
            "gate": {"trailer": "Docs-Reviewed:"},
            "rules": [
                {
                    "name": "route_mod",
                    "on_modify": True,
                    "when_changed": ["tinyagentos/routes/themes.py"],
                    "require_doc": ["CHANGELOG.md"],
                    "hint": "route modified",
                },
                {
                    "name": "catalog_add",
                    "when_changed": ["app-catalog/**"],
                    "require_doc": ["README.md"],
                    "hint": "catalog added",
                },
            ],
        }
        changed = [
            ("M", "tinyagentos/routes/themes.py"),
            # Matches catalog_add's glob, so that rule is genuinely exercised:
            # it must NOT fire on a plain modification without on_modify.
            ("M", "app-catalog/foo/app.yml"),
        ]
        failures = evaluate_rules(changed, [], config)
        assert len(failures) == 1
        assert "route_mod" in failures[0]

    def test_on_modify_false_explicit_still_default(self):
        """Explicit on_modify = false behaves the same as omitting it."""
        config = {
            "gate": {"trailer": "Docs-Reviewed:"},
            "rules": [
                {
                    "name": "route_explicit_false",
                    "on_modify": False,
                    "when_changed": ["tinyagentos/routes/themes.py"],
                    "require_doc": ["CHANGELOG.md"],
                    "hint": "route modified",
                }
            ],
        }
        changed = [("M", "tinyagentos/routes/themes.py")]
        failures = evaluate_rules(changed, [], config)
        assert failures == []
