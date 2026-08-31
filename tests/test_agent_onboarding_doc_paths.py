import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ONBOARDING = REPO_ROOT / "docs" / "agent-onboarding.md"

_PATH_RE = re.compile(r"`([^`]+\.(?:md|txt|rst|yaml|yml|json))`")


def _extract_doc_paths(text: str) -> set[str]:
    paths = set()
    for match in _PATH_RE.finditer(text):
        candidate = match.group(1)
        if candidate.startswith(("/", "~", "http")):
            continue
        if "<" in candidate or ">" in candidate:
            continue
        if "/" not in candidate:
            continue
        paths.add(candidate)
    return paths


class TestAgentOnboardingDocPaths:
    def test_all_repo_relative_doc_paths_resolve(self):
        text = ONBOARDING.read_text(encoding="utf-8")
        paths = _extract_doc_paths(text)
        missing = [p for p in sorted(paths) if not (REPO_ROOT / p).exists()]
        assert not missing, (
            "docs/agent-onboarding.md references paths not present in the tree:\n"
            + "\n".join(f"  FAIL  {p}" for p in missing)
        )

    def test_does_not_reference_itself_as_agent_handoff(self):
        text = ONBOARDING.read_text(encoding="utf-8")
        assert "docs/AGENT_HANDOFF.md" not in text
