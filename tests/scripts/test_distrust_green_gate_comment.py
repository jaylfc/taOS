"""Gate-integrity tests for .github/workflows/distrust-green-gate.yml."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_WORKFLOW = (
    Path(__file__).resolve().parent.parent.parent
    / ".github"
    / "workflows"
    / "distrust-green-gate.yml"
)


def _extract_script_body() -> str:
    """Parse the YAML file and return the raw script: block text of the commenting step."""
    import yaml

    text = _WORKFLOW.read_text()
    data = yaml.safe_load(text)
    steps = data["jobs"]["check-all-skip"]["steps"]
    for step in steps:
        if step.get("name") == "Comment on PR if check fails":
            return step["with"]["script"]
    raise AssertionError("commenting step not found")


class TestDistrustGreenGateComment:
    """The workflow must handle all three failure_reason values correctly."""

    def test_other_failure_branch_exists(self) -> None:
        script = _extract_script_body()
        assert "other_failure" in script
        assert 'reason === \'other_failure\'' in script

    def test_waiver_text_only_in_all_skip_branch(self) -> None:
        script = _extract_script_body()
        # Find the 'other_failure' branch and assert waiver text is not reachable from it
        lines = script.splitlines()
        in_other_failure = False
        body_lines: list[str] = []
        for line in lines:
            if "reason === 'other_failure'" in line or 'reason === "other_failure"' in line:
                in_other_failure = True
                continue
            if in_other_failure:
                stripped = line.lstrip()
                if stripped.startswith("}"):
                    in_other_failure = False
                    continue
                body_lines.append(line)
        assert body_lines, "other_failure branch body is empty; test cannot verify waiver absence"
        waiver_in_other = any("Tests-Skipped-Intentionally" in ln for ln in body_lines)
        assert not waiver_in_other, "Tests-Skipped-Intentionally found in other_failure branch"

    def test_no_raw_github_expression_in_script_body(self) -> None:
        script = _extract_script_body()
        assert "${{" not in script, "raw ${{ expression found in script body"
