"""Layer A0 pre-flight: a doc-gate config that NAMES a tree, scan target or
rule target this repo does not have must FAIL LOUDLY (tsk-s73zth).

The measured defect, from @taOSmobile-dev's port of this gate (A2A 3407, their
commit 599fdea): the path-token regex hardcodes the prefix list
`scripts|tinyagentos|docs|desktop`. taOSmobile has neither `tinyagentos/` nor
`desktop/`, so the gate copied verbatim matched ZERO path tokens in every doc
and printed "doc-gate: clean" forever -- green because it was measuring
nothing. A rule or scan target that has since been renamed away is dormant in
exactly the same way, and nothing in the config said so.

Every assertion below is on the PROCESS EXIT CODE and on the NAMED OFFENDER:

  * never on stdout containing the word "clean" alone, because an assertion
    one level coarser than the defect cannot fail on it;
  * never through a pipe, because `cmd | tail` reports tail's status, which is
    how a real fake green (a pytest run that collected no tests and still
    exited 0) got through in the first place.

The gate is exercised as a copied-out process, not an imported function, so
`result.returncode` is the gate's own and the repo it measures is the fixture,
not this checkout. Same trick the pre-commit / commit-msg hook tests use
(tests/test_doc_gate.py).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_doc_gate as dg  # noqa: E402

# The trees the path-token regex recognises, spelled out here as well as in the
# gate so a prefix that vanishes from one and not the other is visible.
PREFIXES = ("scripts", "tinyagentos", "docs", "desktop")


def _config(
    scan: str = '["docs/gate.md"]',
    section_doc: str = '"docs/gate.md"',
    when_changed: str = '["docs/thing.md"]',
    require_doc: str = '["docs/gate.md"]',
) -> str:
    """A config TOML with one swappable field per kind of named location."""
    return (
        "[gate]\n"
        'trailer = "Docs-Reviewed:"\n'
        "\n"
        "[invariants]\n"
        f"referenced_paths_scan = {scan}\n"
        "ignore_tokens = []\n"
        "\n"
        "[[invariants.required_sections]]\n"
        f"doc = {section_doc}\n"
        'headings = ["Gate"]\n'
        "\n"
        "[[rules]]\n"
        'name = "example"\n'
        "on_modify = true\n"
        f"when_changed = {when_changed}\n"
        f"require_doc = {require_doc}\n"
        'hint = "example rule"\n'
    )


def _fixture_repo(
    tmp_path: Path, config: str, *, drop: str | None = None
) -> Path:
    """Build a throwaway repo the GATE ITSELF runs against.

    The gate script is copied in (not imported) because it derives its
    REPO_ROOT from `__file__`: that is the only way to measure it against a
    repo that is not this checkout. All four token prefixes exist unless the
    test asks for one to be missing, and every file the clean config names is
    written, so a failure below is always ITS offender and never an unrelated
    name.
    """
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in ("check_doc_gate.py", "_gitutil.py"):
        shutil.copy(REPO_ROOT / "scripts" / name, repo / "scripts" / name)
    for tree in PREFIXES:
        (repo / tree).mkdir(exist_ok=True)
    (repo / "docs" / "gate.md").write_text("# Gate\n\n## Gate\n")
    (repo / "docs" / "thing.md").write_text("# Thing\n")
    if drop is not None:
        shutil.rmtree(repo / drop)
    # The config lives at the fixture root, outside every prefix tree, so that
    # dropping one cannot make the gate fail merely by not finding its config:
    # the run below must fail on the PREFIX, and the exit code plus the named
    # offender are what prove it did.
    (repo / "doc-gate.toml").write_text(config)
    return repo


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=doc-gate-test", "-c", "user.email=doc-gate-test@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _run_gate(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the gate as its own process; `returncode` is the gate's own status.

    No shell and no pipe, so nothing downstream can stand in for the gate's
    verdict.
    """
    return subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "check_doc_gate.py"),
            "--config",
            str(repo / "doc-gate.toml"),
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


class TestPreflightGreenControl:
    """The control: a config whose every named location exists must still pass
    after the pre-flight lands, or the new layer has broken the real gate."""

    def test_clean_config_exits_zero(self, tmp_path: Path):
        repo = _fixture_repo(tmp_path, _config())
        result = _run_gate(repo, "invariants")
        assert result.returncode == dg.EXIT_OK, (
            f"clean config must pass, got {result.returncode}: {result.stderr}"
        )


class TestPreflightRejectsUnresolvableNames:
    """Each configured name that does not resolve must exit non-zero AND name
    the offender. Both halves matter: the exit code is what CI reads, the name
    is what a human needs to fix it."""

    def test_rule_target_naming_a_nonexistent_tree_fails(self, tmp_path: Path):
        repo = _fixture_repo(tmp_path, _config(when_changed='["no_such_tree/**"]'))
        result = _run_gate(repo, "invariants")
        assert result.returncode != 0
        assert result.returncode == dg.EXIT_CONFIG_ERROR
        assert "no_such_tree/**" in result.stderr

    def test_require_doc_naming_a_nonexistent_file_fails(self, tmp_path: Path):
        repo = _fixture_repo(tmp_path, _config(require_doc='["docs/never-written.md"]'))
        result = _run_gate(repo, "invariants")
        assert result.returncode != 0
        assert result.returncode == dg.EXIT_CONFIG_ERROR
        assert "docs/never-written.md" in result.stderr

    def test_scan_target_matching_no_file_fails(self, tmp_path: Path):
        repo = _fixture_repo(tmp_path, _config(scan='["docs/no-such-dir/*.md"]'))
        result = _run_gate(repo, "invariants")
        assert result.returncode != 0
        assert result.returncode == dg.EXIT_CONFIG_ERROR
        assert "docs/no-such-dir/*.md" in result.stderr

    def test_required_section_doc_that_does_not_exist_fails(self, tmp_path: Path):
        repo = _fixture_repo(tmp_path, _config(section_doc='"docs/no-such-doc.md"'))
        result = _run_gate(repo, "invariants")
        assert result.returncode != 0
        assert result.returncode == dg.EXIT_CONFIG_ERROR
        assert "docs/no-such-doc.md" in result.stderr

    def test_every_unresolvable_name_is_listed_not_just_the_first(
        self, tmp_path: Path
    ):
        repo = _fixture_repo(
            tmp_path,
            _config(scan='["docs/gone-a/*.md"]', require_doc='["docs/gone-b.md"]'),
        )
        result = _run_gate(repo, "invariants")
        assert result.returncode != 0
        assert "docs/gone-a/*.md" in result.stderr
        assert "docs/gone-b.md" in result.stderr

    def test_preflight_runs_before_diff_gate_evaluates_any_rule(
        self, tmp_path: Path
    ):
        """Layer A0 gates the diff path too, and it is checked on the exit code
        rather than on the message: a broken-config diff-gate that fell through
        to the git layer would exit 4 (git error) and a clean one would exit 0,
        so pinning EXIT_CONFIG_ERROR distinguishes the pre-flight from both."""
        repo = _fixture_repo(tmp_path, _config(when_changed='["no_such_tree/**"]'))
        _git(repo, "init", "-q")
        result = _run_gate(repo, "diff-gate", "--staged")
        assert result.returncode == dg.EXIT_CONFIG_ERROR
        assert "no_such_tree/**" in result.stderr


class TestTriggersResolveDifferentlyFromTargets:
    """A trigger is matched against paths in the DIFF, a target against files
    that EXIST, so the pre-flight resolves them differently on purpose. Both
    directions are pinned here, because a pre-flight that is too eager is its
    own kind of false green."""

    def test_deleting_a_file_a_concrete_trigger_names_is_gated(self, tmp_path: Path):
        """A PR that DELETES a file named by when_changed must still be asked
        for a doc update, so a concrete trigger is resolved by the TREE it sits
        in and not by the file: by the time the pre-flight runs, the file is
        already gone from the working tree. Asserting EXIT_VIOLATION pins it to
        the rule firing; a config error (3) would hide the drift instead."""
        repo = _fixture_repo(
            tmp_path, _config(when_changed='["docs/doomed.py"]')
        )
        (repo / "docs" / "doomed.py").write_text("# doomed\n")
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "initial")
        (repo / "docs" / "doomed.py").unlink()
        _git(repo, "add", "-A")
        result = _run_gate(repo, "diff-gate", "--staged")
        assert result.returncode == dg.EXIT_VIOLATION, result.stderr
        assert "DOC-GATE FAIL: example" in result.stdout

    def test_concrete_trigger_whose_whole_tree_is_gone_fails(self, tmp_path: Path):
        repo = _fixture_repo(
            tmp_path, _config(when_changed='["no_such_tree/x.py"]')
        )
        result = _run_gate(repo, "invariants")
        assert result.returncode == dg.EXIT_CONFIG_ERROR
        assert "no_such_tree/x.py" in result.stderr

    def test_rule_glob_whose_tree_exists_but_matches_nothing_today_passes(
        self, tmp_path: Path
    ):
        """`changelog.d/*.md` between releases matches no file today and the
        rule is not dormant, so the gate must not fail: for a rule target the
        TREE is what has to exist, not a current match (a scan target is the
        opposite, and TestPreflightRejectsUnresolvableNames pins that)."""
        repo = _fixture_repo(
            tmp_path, _config(require_doc='["docs/empty-dir/*.md"]')
        )
        (repo / "docs" / "empty-dir").mkdir()
        result = _run_gate(repo, "invariants")
        assert result.returncode == dg.EXIT_OK, result.stderr


class TestPreflightCoversTheTokenRegexPrefixList:
    """The prefix alternation inside the path-token regex is a hardcoded list of
    repo trees, and it is the field that went dormant for taOSmobile. Dropping
    ANY prefix from the repo must fail the gate instead of matching nothing.

    `scripts` is not in the drop list: the gate IS scripts/check_doc_gate.py,
    so a repo with no scripts/ cannot run the gate to be told it has no
    scripts/. It is still validated, via test_every_prefix_is_validated below.
    """

    @pytest.mark.parametrize("prefix", [p for p in PREFIXES if p != "scripts"])
    def test_missing_prefix_fails_rather_than_reporting_clean(
        self, prefix: str, tmp_path: Path
    ):
        repo = _fixture_repo(tmp_path, _config(), drop=prefix)
        result = _run_gate(repo, "invariants")
        assert result.returncode != 0
        assert result.returncode == dg.EXIT_CONFIG_ERROR
        assert prefix in result.stderr
        assert "doc-gate: clean" not in result.stdout

    @pytest.mark.parametrize("prefix", PREFIXES)
    def test_every_prefix_is_validated(self, prefix: str, tmp_path: Path):
        """Each entry of the prefix list, including `scripts`, is a name the
        pre-flight resolves -- so a port of this gate to a repo with different
        trees (taOSmobile has no tinyagentos/ and no desktop/) fails on the
        copy instead of matching nothing forever."""
        repo = _fixture_repo(tmp_path, _config(), drop=prefix)
        config = dg.load_config(repo / "doc-gate.toml")
        failures = dg.check_config_targets_resolve(repo, config)
        assert any(prefix in failure for failure in failures), failures

    def test_the_regex_is_built_from_the_validated_prefix_list(self):
        """One source of truth: the prefixes this file drops are exactly the
        ones the gate validates, and the regex matches every one of them."""
        assert PREFIXES == dg.PATH_PREFIXES
        for prefix in dg.PATH_PREFIXES:
            assert dg._TOKEN_RE.search(f"see {prefix}/thing.py for details")


class TestRealConfigResolves:
    """Acceptance: this repo's own doc-gate.toml names only locations that
    exist, so the pre-flight does not fire on the real config."""

    def test_real_config_names_only_locations_that_exist(self):
        config = dg.load_config(dg.DEFAULT_CONFIG)
        assert dg.check_config_targets_resolve(REPO_ROOT, config) == []
