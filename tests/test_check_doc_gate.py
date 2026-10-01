from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
_validate_config = _MOD._validate_config


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


class TestEvaluateRulesRenameCopy:
    """Rename (R) and copy (C) must trigger rules but must not satisfy require_doc."""

    def test_rename_triggers_rule_by_name(self):
        """A rename of a when_changed path must trigger the rule."""
        config = _base_config()
        changed = [("R", "tinyagentos/routes/themes.py")]
        failures = evaluate_rules(changed, [], config)
        assert len(failures) == 1
        assert "test_route" in failures[0]

    def test_copy_triggers_rule_by_name(self):
        """A copy of a when_changed path must trigger the rule."""
        config = _base_config()
        changed = [("C", "tinyagentos/routes/themes.py")]
        failures = evaluate_rules(changed, [], config)
        assert len(failures) == 1
        assert "test_route" in failures[0]

    def test_deletion_still_triggers_rule(self):
        """A deletion of a when_changed path must still trigger the rule (pinning)."""
        config = _base_config()
        changed = [("D", "tinyagentos/routes/themes.py")]
        failures = evaluate_rules(changed, [], config)
        assert len(failures) == 1
        assert "test_route" in failures[0]

    def test_rename_does_not_satisfy_require_doc(self):
        """Renaming the require_doc does NOT satisfy it (pinning).

        If the satisfaction set were widened to include R, this would pass
        silently and the assertion below would fail.
        """
        config = _base_config()
        changed = [
            ("R", "tinyagentos/routes/themes.py"),
            ("R", "CHANGELOG.md"),
        ]
        failures = evaluate_rules(changed, [], config)
        assert len(failures) == 1
        assert "test_route" in failures[0]

    def test_rename_with_doc_added_passes(self):
        """A route rename with the required doc added passes."""
        config = _base_config()
        changed = [
            ("R", "tinyagentos/routes/themes.py"),
            ("A", "CHANGELOG.md"),
        ]
        failures = evaluate_rules(changed, [], config)
        assert failures == []


class TestParseNameStatus:
    """Parser-level tests for _parse_name_status.

    These tests feed literal multi-line name-status output STRINGS to
    _parse_name_status (no git repo, no filesystem changes needed). They
    pin the parser's behaviour so a regression (keeping the full R100 token
    or taking parts[1] instead of parts[-1]) breaks them immediately.
    """

    def test_r100_rename(self):
        """R100<TAB>old/path.py<TAB>new/path.py -> ('R', 'new/path.py')."""
        output = "R100\told/path.py\tnew/path.py"
        changed = _MOD._parse_name_status(output)
        assert changed == [("R", "new/path.py")]

    def test_c75_copy(self):
        """C75<TAB>src.md<TAB>copy.md -> ('C', 'copy.md')."""
        output = "C75\tsrc.md\tcopy.md"
        changed = _MOD._parse_name_status(output)
        assert changed == [("C", "copy.md")]

    def test_m_single_path(self):
        """Plain M<TAB>path -> ('M', 'path')."""
        output = "M\ttinyagentos/routes/themes.py"
        changed = _MOD._parse_name_status(output)
        assert changed == [("M", "tinyagentos/routes/themes.py")]

    def test_a_single_path(self):
        """Plain A<TAB>path -> ('A', 'path')."""
        output = "A\tsome/file.py"
        changed = _MOD._parse_name_status(output)
        assert changed == [("A", "some/file.py")]

    def test_d_single_path(self):
        """Plain D<TAB>path -> ('D', 'path')."""
        output = "D\tdeleted/file.py"
        changed = _MOD._parse_name_status(output)
        assert changed == [("D", "deleted/file.py")]

    def test_blank_lines_skipped(self):
        """Blank lines in input are skipped."""
        output = "\nR100\told.py\tnew.py\n\nA\tfile.py\n"
        changed = _MOD._parse_name_status(output)
        assert changed == [("R", "new.py"), ("A", "file.py")]

    def test_composed_r_status_red(self):
        """Mixed block: R-status when_changed hit with no doc goes RED.

        Parses a realistic mixed name-status block, pipes the result through
        evaluate_rules, and asserts an R-status change matching a when_changed
        rule hits a failure (no doc added, no trailer).
        """
        config = _base_config()
        # R rename where the new path matches the rule's when_changed,
        # plus an unrelated M change.
        output = "R100\told/routes.py\ttinyagentos/routes/themes.py\nM\tother/file.py\n"
        changed = _MOD._parse_name_status(output)
        failures = evaluate_rules(changed, [], config)
        assert len(failures) == 1
        assert "test_route" in failures[0]


class TestReferencedPathsScan:
    """Invariants layer: glob expansion, tombstones, extractor precision."""

    def _write(self, root: Path, rel: str, text: str) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def test_glob_scan_targets_are_expanded(self, tmp_path):
        """docs/runbooks/*.md style entries must scan every matching file."""
        self._write(tmp_path, "docs/runbooks/one.md", "see tinyagentos/nope.py")
        self._write(tmp_path, "docs/runbooks/two.md", "all good here")
        fails = _MOD.check_referenced_paths(
            tmp_path, ["docs/runbooks/*.md"], {}
        )
        assert len(fails) == 1 and "docs/runbooks/one.md" in fails[0]

    def test_ignore_tokens_tombstone(self, tmp_path):
        """A doc explaining a removal may name the removed file - but ONLY
        the listed tombstone is exempt, other dead paths still fail."""
        self._write(
            tmp_path, "docs/guide.md",
            "docs/STATUS.md was removed. Also see tinyagentos/gone.py",
        )
        cfg = {"invariants": {"ignore_tokens": ["docs/STATUS.md"]}}
        fails = _MOD.check_referenced_paths(tmp_path, ["docs/guide.md"], cfg)
        assert len(fails) == 1 and "tinyagentos/gone.py" in fails[0]
        # Red half: without the tombstone the STATUS.md mention fails too.
        fails = _MOD.check_referenced_paths(tmp_path, ["docs/guide.md"], {})
        assert len(fails) == 2

    def test_missing_scan_target_is_skipped(self, tmp_path):
        """A local-only (gitignored) doc absent from the tree is skipped."""
        fails = _MOD.check_referenced_paths(tmp_path, ["docs/AGENT_HANDOFF.md"], {})
        assert fails == []

    def test_extractor_strips_symbol_suffix(self):
        toks = _MOD.extract_path_tokens(
            "wire it in tinyagentos/routes/__init__.py::register_all_routers()"
        )
        assert toks == ["tinyagentos/routes/__init__.py"]

    def test_extractor_ignores_hyphen_glued_prefix(self):
        """A repo prefix embedded in a home-dir slug is not a repo path."""
        toks = _MOD.extract_path_tokens(
            "read ~/.claude/projects/-home-x-tinyagentos/memory/MEMORY.md at start"
        )
        assert toks == []


class TestGitCommandErrorHandling:
    """Git infrastructure failures must never be confused with rule violations."""

    def test_nonexistent_base_ref_exits_git_error(self, capsys):
        """A bad --base ref should produce the new exit code with a clear message."""
        error = subprocess.CalledProcessError(
            128,
            ["git", "diff", "--name-status", "origin/no-such-ref...HEAD"],
        )
        error.stderr = "fatal: bad revision 'origin/no-such-ref'\n"
        
        with patch.object(_MOD.subprocess, "run", side_effect=error):
            code = _MOD.main(["diff-gate", "--base", "origin/no-such-ref"])
            assert code == _MOD.EXIT_GIT_ERROR
            captured = capsys.readouterr()
            assert "diff" in captured.err
            assert "origin/no-such-ref" in captured.err
            assert "Traceback" not in captured.err

    def test_genuine_violation_still_exits_1(self, capsys):
        """A real rule violation must still exit 1."""
        mock_result_diff = MagicMock()
        mock_result_diff.stdout = "A\ttinyagentos/routes/themes.py\n"
        mock_result_log = MagicMock()
        mock_result_log.stdout = "\x00"
        mock_result_ls_tree = MagicMock()
        mock_result_ls_tree.stdout = ""
        
        with patch.object(_MOD.subprocess, "run", side_effect=[mock_result_diff, mock_result_log, mock_result_ls_tree]):
            code = _MOD.main(["diff-gate", "--base", "origin/HEAD"])
            assert code == _MOD.EXIT_VIOLATION

    def test_clean_run_still_exits_0(self, capsys):
        """A clean run must still exit 0."""
        mock_result_diff = MagicMock()
        mock_result_diff.stdout = ""
        mock_result_log = MagicMock()
        mock_result_log.stdout = "\x00"
        mock_result_ls_tree = MagicMock()
        mock_result_ls_tree.stdout = ""
        
        with patch.object(_MOD.subprocess, "run", side_effect=[mock_result_diff, mock_result_log, mock_result_ls_tree]):
            code = _MOD.main(["diff-gate", "--base", "origin/HEAD"])
            assert code == _MOD.EXIT_OK


class TestConfigValidation:
    """Tests for the _validate_config function."""
    
    def test_valid_config(self):
        """Test that a valid config passes validation."""
        valid_config = {
            "rules": [
                {
                    "name": "test-rule",
                    "when_changed": ["test/*"],
                    "require_doc": ["README.md"],
                    "hint": "Test rule",
                    "on_modify": True
                }
            ],
            "gate": {
                "trailer": "Docs-Reviewed:"
            },
            "invariants": {
                "referenced_paths_scan": ["file1.md", "file2.md"],
                "ignore_tokens": ["ignore.md"]
            }
        }
        # Should not raise an exception
        _validate_config(valid_config)
    
    def test_invalid_rule_name_type(self):
        """Test that non-string name in rule raises error."""
        invalid_config = {
            "rules": [
                {
                    "name": 123,  # Should be string
                    "when_changed": ["test/*"],
                    "require_doc": ["README.md"],
                    "hint": "Test rule",
                    "on_modify": True
                }
            ]
        }
        with pytest.raises(ValueError, match="rules\[0\].name must be a string"):
            _validate_config(invalid_config)
    
    def test_invalid_when_changed_type(self):
        """Test that non-string in when_changed raises error."""
        invalid_config = {
            "rules": [
                {
                    "name": "test-rule",
                    "when_changed": [123],  # Should be string
                    "require_doc": ["README.md"],
                    "hint": "Test rule",
                    "on_modify": True
                }
            ]
        }
        with pytest.raises(ValueError, match="rules\[0\].when_changed\[0\] must be a string"):
            _validate_config(invalid_config)
    
    def test_invalid_require_doc_type(self):
        """Test that non-string in require_doc raises error."""
        invalid_config = {
            "rules": [
                {
                    "name": "test-rule",
                    "when_changed": ["test/*"],
                    "require_doc": [123],  # Should be string
                    "hint": "Test rule",
                    "on_modify": True
                }
            ]
        }
        with pytest.raises(ValueError, match="rules\[0\].require_doc\[0\] must be a string"):
            _validate_config(invalid_config)
    
    def test_invalid_hint_type(self):
        """Test that non-string hint raises error."""
        invalid_config = {
            "rules": [
                {
                    "name": "test-rule",
                    "when_changed": ["test/*"],
                    "require_doc": ["README.md"],
                    "hint": 123,  # Should be string
                    "on_modify": True
                }
            ]
        }
        with pytest.raises(ValueError, match="rules\[0\].hint must be a string"):
            _validate_config(invalid_config)
    
    def test_invalid_on_modify_type(self):
        """Test that non-boolean on_modify raises error."""
        invalid_config = {
            "rules": [
                {
                    "name": "test-rule",
                    "when_changed": ["test/*"],
                    "require_doc": ["README.md"],
                    "hint": "Test rule",
                    "on_modify": "yes"  # Should be boolean
                }
            ]
        }
        with pytest.raises(ValueError, match="rules\[0\].on_modify must be a boolean"):
            _validate_config(invalid_config)
    
    def test_invalid_referenced_paths_scan_entry_type(self):
        """Test that non-string in referenced_paths_scan raises error."""
        invalid_config = {
            "invariants": {
                "referenced_paths_scan": [123],  # Should be string
                "ignore_tokens": []
            }
        }
        with pytest.raises(ValueError, match="invariants.referenced_paths_scan\[0\] must be a string"):
            _validate_config(invalid_config)
    
    def test_invalid_ignore_tokens_entry_type(self):
        """Test that non-string in ignore_tokens raises error."""
        invalid_config = {
            "invariants": {
                "referenced_paths_scan": [],
                "ignore_tokens": [123]  # Should be string
            }
        }
        with pytest.raises(ValueError, match="invariants.ignore_tokens\[0\] must be a string"):
            _validate_config(invalid_config)


# ---------------------------------------------------------------------------
# Red-first integration tests for Defect 1 (non-ASCII paths) and Defect 2
# (mid-pattern ** in _glob_match).
# ---------------------------------------------------------------------------


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, capture_output=True, text=True, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, capture_output=True, text=True, check=True)


def _write(repo: Path, rel_path: str, content: str) -> None:
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def _git_commit(repo: Path, rel_path: str, content: str, message: str) -> None:
    _write(repo, rel_path, content)
    subprocess.run(["git", "add", rel_path], cwd=repo, capture_output=True, text=True, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, capture_output=True, text=True, check=True)


def _get_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


class TestDefectNonAsciiPaths:
    def test_non_ascii_path_triggers_rule(self, tmp_path: Path):
        """A change to docs/café.md must trigger a docs/** rule.

        Today the path comes back quoted from git, so the glob never fires.
        """
        repo = tmp_path / "repo"
        _init_repo(repo)
        _git_commit(repo, "README.md", "# hello\n", "init")
        base_tip = _get_head(repo)

        subprocess.run(["git", "branch", "pr"], cwd=repo, capture_output=True, text=True, check=True)
        subprocess.run(["git", "checkout", "-q", "pr"], cwd=repo, capture_output=True, text=True, check=True)
        _git_commit(repo, "docs/café.md", "# café\n", "add café doc")
        subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, capture_output=True, text=True, check=True)
        subprocess.run(["git", "merge", "pr", "--no-edit"], cwd=repo, capture_output=True, text=True, check=True)

        original_repo_root = _MOD.REPO_ROOT
        try:
            _MOD.REPO_ROOT = repo
            changed = _MOD._git_changed_base(base_tip)
        finally:
            _MOD.REPO_ROOT = original_repo_root

        config = {
            "gate": {"trailer": "Docs-Reviewed:"},
            "rules": [
                {
                    "name": "docs",
                    "when_changed": ["docs/**"],
                    "require_doc": ["README.md"],
                    "hint": "a doc was added",
                }
            ],
        }
        failures = _MOD.evaluate_rules(changed, [], config)
        assert len(failures) == 1
        assert "docs" in failures[0]


class TestDefectGlobMidPattern:
    def test_glob_double_star_mid_pattern(self):
        assert _MOD._glob_match("docs/x.md", "docs/**/*.md") is True
        assert _MOD._glob_match("docs/a/b/x.md", "docs/**/*.md") is True
        assert _MOD._glob_match("a/b", "a/**/b") is True
        assert _MOD._glob_match("a/x/y/b", "a/**/b") is True
        assert _MOD._glob_match("a/x/y/b", "a/*/b") is False
        assert _MOD._glob_match("a", "a/**") is True


class TestParseNameStatusPreservesWhitespace:
    def test_parse_name_status_preserves_trailing_whitespace(self):
        """NUL-mode output with a path that has trailing space must preserve it."""
        output = "M\x00README.md \x00"
        changed = _MOD._parse_name_status(output)
        assert changed == [("M", "README.md ")]


class TestDiffNameStatusZRequiresBaseRef:
    def test_diff_name_status_z_requires_base_ref(self, tmp_path: Path):
        """diff_name_status_z with base_ref=None must raise ValueError."""
        repo = tmp_path / "repo"
        _init_repo(repo)
        _git_commit(repo, "README.md", "# hello\n", "init")
        with pytest.raises(ValueError, match="base_ref is required when cached=False"):
            _MOD.diff_name_status_z(repo, base_ref=None, cached=False)


def test_two_scoped_trailers_waive_both_rules():
    """A commit with two scoped Docs-Reviewed trailers waives both rules.

    Regression test for: _commit_waivers broke on the first trailer only,
    so a commit like:
        Docs-Reviewed: [changelog] no user-visible change
        Docs-Reviewed: [agent-manual] internal only
    would only waive 'changelog' and fail 'agent-manual'.
    """
    config = {
        "gate": {"trailer": "Docs-Reviewed:"},
        "rules": [
            {
                "name": "changelog",
                "on_modify": True,
                "when_changed": ["tinyagentos/routes/themes.py"],
                "require_doc": ["CHANGELOG.md"],
                "hint": "a route module was modified",
            },
            {
                "name": "agent-manual",
                "on_modify": True,
                "when_changed": ["tinyagentos/routes/themes.py"],
                "require_doc": ["AGENT_MANUAL.md"],
                "hint": "agent manual updated",
            },
        ],
    }
    changed = [("M", "tinyagentos/routes/themes.py")]
    commit_messages = [
        "Fix themes\n\n"
        "Docs-Reviewed: [changelog] no user-visible change\n"
        "Docs-Reviewed: [agent-manual] internal only",
    ]
    failures = evaluate_rules(changed, commit_messages, config)
    assert failures == []


def test_unrelated_commit_trailer_waives_nothing():
    """An unrelated commit's trailer waives nothing.

    Ensures that a trailer on a commit that doesn't touch the relevant paths
    does not waive the rule.
    """
    config = _base_config()
    changed = [("M", "tinyagentos/routes/themes.py")]
    commit_messages = ["Fix something else\n\nDocs-Reviewed: [test_route] some reason"]
    failures = evaluate_rules(changed, commit_messages, config, commit_paths=[{"other/file.py"}])
    assert len(failures) == 1


def test_related_commit_trailer_waives():
    """A related commit's trailer waives the rule.

    Ensures that a trailer on a commit that does touch the relevant paths
    does waive the rule.
    """
    config = _base_config()
    changed = [("M", "tinyagentos/routes/themes.py")]
    commit_messages = ["Fix something else\n\nDocs-Reviewed: [test_route] some reason"]
    failures = evaluate_rules(changed, commit_messages, config, commit_paths=[{"tinyagentos/routes/themes.py"}])
    assert failures == []


def test_log_trailer_usage_prints_every_trailer(capsys):
    """_log_trailer_usage must emit one line per usable scoped trailer.

    A commit whose message carries multiple Docs-Reviewed trailers must
    produce one log line for each trailer, not just the first one.
    """
    commits = [
        (
            "abcd1234",
            "Alice <alice@example.com>",
            "Fix themes\n\n"
            "Docs-Reviewed: [test_route] a\n"
            "Docs-Reviewed: [agent-manual] b",
        )
    ]
    _MOD._log_trailer_usage(commits, "Docs-Reviewed:")
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 2
    assert any("[test_route] a" in line for line in lines)
    assert any("[agent-manual] b" in line for line in lines)
