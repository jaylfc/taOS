"""Tests for the action-pinning guard (scripts/check_action_pinning.py).

The guard must assert on the REF SHAPE (a full 40-hex commit SHA), not on a
trailing `# v7` comment, and must be a live regression guard: a real-tree test
asserts the committed workflows on this branch are clean.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ is not a package; make it importable like the other scripts/*.py
# gate tests (see tests/test_check_secret_ignores.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_action_pinning as cap  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


def _repo(tmp_path: Path, workflows: dict[str, str]) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    for name, text in workflows.items():
        (wf_dir / name).write_text(text)
    return tmp_path


@pytest.mark.parametrize(
    "value",
    [
        f"actions/checkout@{SHA}",
        f"owner/repo/sub/path@{SHA}",
        "./.github/actions/local",
        "docker://alpine@sha256:" + "a" * 64,
    ],
)
def test_pinned_refs_pass(value: str) -> None:
    assert cap.is_pinned(value)


@pytest.mark.parametrize(
    "value",
    [
        "actions/checkout@v7",
        "actions/checkout@main",
        "actions/checkout@3d3c42e",  # short SHA
        f"actions/checkout@{SHA.upper()}",  # not the canonical lowercase form
        f"actions/checkout@{SHA}0",  # 41 chars
        "actions/checkout",  # no ref at all
        "docker://alpine:3.20",
        "docker://alpine@sha256:abc",
    ],
)
def test_unpinned_refs_fail(value: str) -> None:
    assert not cap.is_pinned(value)


def test_version_comment_does_not_make_a_tag_pinned() -> None:
    text = "    steps:\n      - uses: actions/checkout@v7  # " + SHA + "\n"
    assert cap.check_text(text, "wf.yml") == [cap.Violation("wf.yml", 2, "actions/checkout@v7")]


def test_check_text_finds_step_and_job_level_uses_quoted_or_not() -> None:
    text = (
        "jobs:\n"
        "  call:\n"
        "    uses: owner/repo/.github/workflows/x.yml@main\n"
        "  build:\n"
        "    steps:\n"
        f"      - uses: actions/checkout@{SHA}  # v7\n"
        "      - uses: 'actions/setup-python@v7'\n"
        '      - name: n\n        uses: "docker/login-action@v3"\n'
    )
    got = [(v.line, v.value) for v in cap.check_text(text, "wf.yml")]
    assert got == [
        (3, "owner/repo/.github/workflows/x.yml@main"),
        (7, "actions/setup-python@v7"),
        (9, "docker/login-action@v3"),
    ]


def test_main_fails_and_names_each_unpinned_use(tmp_path, capsys) -> None:
    root = _repo(
        tmp_path,
        {
            "a.yml": f"steps:\n  - uses: actions/checkout@{SHA}  # v7\n",
            "b.yaml": "steps:\n  - uses: actions/setup-node@v7\n",
        },
    )
    assert cap.main(["--repo-root", str(root)]) == 1
    out = capsys.readouterr().out
    assert ".github/workflows/b.yaml:2: actions/setup-node@v7" in out
    assert "a.yml" not in out


def test_main_clean_tree_passes(tmp_path) -> None:
    root = _repo(tmp_path, {"a.yml": f"steps:\n  - uses: actions/checkout@{SHA}  # v7\n"})
    assert cap.main(["--repo-root", str(root)]) == 0


def test_main_with_no_workflows_fails(tmp_path) -> None:
    # Nothing to scan must never read as clean.
    assert cap.main(["--repo-root", str(tmp_path)]) == 1


def test_real_tree_is_pinned() -> None:
    assert cap.check_action_pinning(REPO_ROOT) == []
