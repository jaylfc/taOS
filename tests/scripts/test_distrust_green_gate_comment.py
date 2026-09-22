"""Gate-integrity tests for .github/workflows/distrust-green-gate.yml."""
from __future__ import annotations

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
        
        # Find the 'other_failure' branch body segment by splitting on branch boundaries
        # The branch starts with "} else if (reason === 'other_failure') {"
        branch_marker = "} else if (reason === 'other_failure') {"
        parts = script.split(branch_marker)
        if len(parts) < 2:
            # Try with double quotes in case YAML uses them
            branch_marker = '"} else if (reason === "other_failure") {'
            parts = script.split(branch_marker)
        if len(parts) < 2:
            raise AssertionError("other_failure branch not found")
        
        # Extract the entire branch body
        branch_body = parts[1]
        
        # Find the matching closing brace to isolate just this branch
        # The other_failure branch ends with "} else {" (next branch) or "}" (final else)
        # Find the first "}" after the opening
        open_brace_pos = branch_body.find("{")
        if open_brace_pos == -1:
            raise AssertionError("other_failure branch missing opening brace")
        
        # Find the matching closing brace
        brace_count = 1
        pos = open_brace_pos + 1
        while brace_count > 0 and pos < len(branch_body):
            if branch_body[pos] == '{':
                brace_count += 1
            elif branch_body[pos] == '}':
                brace_count -= 1
            pos += 1
        
        branch_body = branch_body[:pos - 1].strip()
        
        # Check for waiver text in the extracted branch body
        if "Tests-Skipped-Intentionally" in branch_body:
            # If waiver is found, we need to fail to prove the guard
            raise AssertionError("Tests-Skipped-Intentionally found in other_failure branch")
        
        # If we get here, the waiver is not in the branch (should be green)
        assert "Tests-Skipped-Intentionally" not in branch_body

    def test_no_raw_github_expression_in_script_body(self) -> None:
        script = _extract_script_body()
        assert "${{" not in script, "raw ${{ expression found in script body"
