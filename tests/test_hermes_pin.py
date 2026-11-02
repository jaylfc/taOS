"""Test that Hermes is pinned to a specific tag/commit in install scripts."""

import re
from pathlib import Path


INSTALL_HERMES = Path(__file__).resolve().parent.parent / "tinyagentos" / "scripts" / "install_hermes.sh"
BUILD_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build-agent-images.yml"


def test_no_main_branch_url_in_install_hermes():
    """install_hermes.sh must not reference hermes-agent/main/ URLs."""
    text = INSTALL_HERMES.read_text()
    assert "hermes-agent/main/" not in text, "Found hermes-agent/main/ URL in install_hermes.sh"


def test_no_main_branch_url_in_build_workflow():
    """build-agent-images.yml must not reference hermes-agent/main/ URLs."""
    text = BUILD_WORKFLOW.read_text()
    assert "hermes-agent/main/" not in text, "Found hermes-agent/main/ URL in build-agent-images.yml"


def test_install_hermes_has_default_tag_and_commit():
    """install_hermes.sh must have HERMES_TAG and HERMES_COMMIT defaults."""
    text = INSTALL_HERMES.read_text()

    # Check HERMES_TAG default - use [$] to match literal $ in regex
    tag_match = re.search(r'HERMES_TAG\s*=\s*"[\$]\{HERMES_TAG:-([^}]+)\}"', text)
    assert tag_match, "HERMES_TAG default not found in install_hermes.sh"
    tag_value = tag_match.group(1)
    assert tag_value, "HERMES_TAG default is empty"
    assert tag_value == "v2026.8.31", f"HERMES_TAG default is {tag_value!r}, expected v2026.8.31"

    # Check HERMES_COMMIT default
    commit_match = re.search(r'HERMES_COMMIT\s*=\s*"[\$]\{HERMES_COMMIT:-([^}]+)\}"', text)
    assert commit_match, "HERMES_COMMIT default not found in install_hermes.sh"
    commit_value = commit_match.group(1)
    assert commit_value, "HERMES_COMMIT default is empty"
    assert commit_value == "29112bef099274229cadff79cdff7bf7b99c4b77", f"HERMES_COMMIT default is {commit_value!r}, expected 29112bef099274229cadff79cdff7bf7b99c4b77"

    # Verify commit is 40 hex chars
    assert re.fullmatch(r"[0-9a-f]{40}", commit_value), f"HERMES_COMMIT {commit_value!r} is not 40 hex chars"


def test_both_files_carry_same_commit_sha():
    """Both install_hermes.sh and build-agent-images.yml must use the same commit SHA."""
    install_text = INSTALL_HERMES.read_text()
    workflow_text = BUILD_WORKFLOW.read_text()

    # Extract commit from install_hermes.sh
    install_commit_match = re.search(r'HERMES_COMMIT\s*=\s*"[\$]\{HERMES_COMMIT:-([^}]+)\}"', install_text)
    assert install_commit_match, "HERMES_COMMIT not found in install_hermes.sh"
    install_commit = install_commit_match.group(1)

    # Extract commit from build-agent-images.yml (hardcoded in the bake step)
    # Look for --commit <sha> in the Hermes bake step
    workflow_commit_match = re.search(r'--commit\s+"?([0-9a-f]{40})"?', workflow_text)
    assert workflow_commit_match, "--commit <sha> not found in build-agent-images.yml"
    workflow_commit = workflow_commit_match.group(1)

    assert install_commit == workflow_commit, f"Commit mismatch: install_hermes.sh={install_commit} vs build-agent-images.yml={workflow_commit}"


def test_install_hermes_uses_tag_in_url():
    """install_hermes.sh must fetch installer from the TAG, not main."""
    text = INSTALL_HERMES.read_text()
    # Should use HERMES_TAG in the URL
    assert 'hermes-agent/${HERMES_TAG}/scripts/install.sh' in text or "hermes-agent/${HERMES_TAG}/scripts/install.sh" in text.replace('${HERMES_TAG}', 'v2026.8.31'), "install_hermes.sh does not use HERMES_TAG in installer URL"
    # Should pass --commit with HERMES_COMMIT
    assert '--commit "$HERMES_COMMIT"' in text or '--commit ${HERMES_COMMIT}' in text, "install_hermes.sh does not pass --commit with HERMES_COMMIT"


def test_build_workflow_uses_tag_and_commit():
    """build-agent-images.yml bake step must use the tag URL and --commit."""
    text = BUILD_WORKFLOW.read_text()
    # Should use the tag in URL (hardcoded v2026.8.31)
    assert "hermes-agent/v2026.8.31/scripts/install.sh" in text, "build-agent-images.yml does not use v2026.8.31 tag in installer URL"
    # Should pass --commit with the SHA
    assert "--commit" in text and "29112bef099274229cadff79cdff7bf7b99c4b77" in text, "build-agent-images.yml does not pass --commit with the correct SHA"