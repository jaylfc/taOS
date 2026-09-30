from __future__ import annotations

import yaml
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "changelog-fragment-gate.yml"


REQUIRED_TYPES = {"opened", "synchronize", "reopened", "labeled", "unlabeled", "edited"}


def test_changelog_gate_workflow_has_all_trigger_types() -> None:
    """Assert that the changelog fragment gate workflow triggers on all required PR event types.

    The workflow must have `on.pull_request.types` containing at least:
    opened, synchronize, reopened, labeled, unlabeled, edited.
    """
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    # YAML parses `on:` as boolean True in some loaders; read defensively
    on_section = data.get("on") or data.get(True)
    assert on_section is not None, "workflow missing 'on' key"

    pr_section = on_section.get("pull_request")
    assert pr_section is not None, "workflow missing 'on.pull_request' key"

    types = pr_section.get("types")
    assert types is not None, "workflow missing 'on.pull_request.types' key"

    actual_types = set(types)
    missing = REQUIRED_TYPES - actual_types
    assert not missing, f"on.pull_request.types missing {sorted(missing)}"


def test_changelog_gate_workflow_types_is_list() -> None:
    """Assert that types is a list (not a string or other scalar)."""
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    on_section = data.get("on") or data.get(True)
    pr_section = on_section.get("pull_request")
    types = pr_section.get("types")

    assert isinstance(types, list), f"on.pull_request.types must be a list, got {type(types).__name__}"