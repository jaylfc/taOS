"""Tests for .github/workflows/build-agent-images.yml"""

import fnmatch
import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml


WORKFLOW_PATH = Path(".github/workflows/build-agent-images.yml")
INSTALL_SH_PATH = Path("app-catalog/agents/openclaw/scripts/install.sh")


def load_workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def load_install_sh():
    return INSTALL_SH_PATH.read_text()


def test_release_pattern_matches_all_artifacts():
    """Release download pattern must match artifact names for every base in matrix."""
    wf = load_workflow()

    # Build matrix bases and arches
    matrix = wf["jobs"]["build"]["strategy"]["matrix"]
    bases = matrix["base"]
    arches = matrix["arch"]

    # ALIAS mapping from workflow env
    alias_map = {}
    for base in bases:
        if base == "openclaw":
            alias_map[base] = "taos-openclaw-base"
        elif base == "generic":
            alias_map[base] = "taos-base"
        elif base == "hermes":
            alias_map[base] = "taos-hermes-base"
        else:
            raise ValueError(f"Unknown base: {base}")

    # Artifact names are ${ALIAS}-${arch}
    artifact_names = [f"{alias_map[base]}-{arch}" for base in bases for arch in arches]

    # Release job download pattern: actions/download-artifact@v8 reads this as
    # ONE glob string (newlines are literal, matching nothing).
    release_steps = wf["jobs"]["release"]["steps"]
    download_step = next(s for s in release_steps if s.get("uses", "").startswith("actions/download-artifact"))
    pattern = download_step["with"]["pattern"]

    assert "\n" not in pattern, (
        "download-artifact pattern must be a single glob (no newlines), "
        f"got: {pattern!r}"
    )

    unmatched = [
        name for name in artifact_names
        if not fnmatch.fnmatchcase(name, pattern)
    ]

    assert not unmatched, (
        f"Release pattern {pattern!r} does not match artifacts: {unmatched}. "
        f"All artifacts: {artifact_names}"
    )


def test_openclaw_bake_uses_pinned_version_from_install_sh():
    """Openclaw bake step must not use @latest; must read version from install.sh."""
    wf = load_workflow()
    install_sh = load_install_sh()

    # Find the openclaw bake step
    build_steps = wf["jobs"]["build"]["steps"]
    bake_step = next(s for s in build_steps if s.get("name") == "Bake openclaw into the base container")

    run_content = bake_step["run"]

    # Must not contain @latest
    assert "openclaw@latest" not in run_content, (
        f"Bake step uses 'openclaw@latest' but should use pinned version from install.sh. "
        f"Run content: {run_content}"
    )

    # Bake step should use the OPENCLAW_VERSION env var (set by extract step)
    assert "openclaw@${OPENCLAW_VERSION}" in run_content, (
        f"Bake step should use 'openclaw@${{OPENCLAW_VERSION}}' from env. "
        f"Run content: {run_content}"
    )

    # Verify the extract step exists and reads from install.sh
    extract_step = next(s for s in build_steps if s.get("name") == "Extract openclaw version from install.sh")
    extract_run = extract_step["run"]
    assert "install.sh" in extract_run, "Extract step should read from install.sh"
    assert "openclaw@" in extract_run, "Extract step should parse openclaw version"

    # Extract pinned version from install.sh (line like: npm install -g --unsafe-perm openclaw@0.2.0)
    match = re.search(r"npm install -g --unsafe-perm openclaw@([\d.]+)", install_sh)
    assert match, "Could not find pinned openclaw version in install.sh"
    pinned_version = match.group(1)

    # The extract step should output this version
    assert pinned_version in extract_run or "VERSION" in extract_run, (
        f"Extract step should extract version {pinned_version}"
    )


def test_release_runs_when_detect_succeeded_not_cancelled():
    """Release job should run when detect succeeded and run not cancelled, not blocked by build failures."""
    wf = load_workflow()

    release_job = wf["jobs"]["release"]
    needs = release_job["needs"]

    assert "build" in needs, (
        "Release job should depend on 'build' so it waits for all build legs. "
        f"Got needs: {needs}"
    )

    if_cond = release_job.get("if", "")
    assert "!cancelled()" in if_cond, (
        "Release job if condition should include !cancelled() so partial "
        f"success still publishes. Got: {if_cond}"
    )


def test_release_publishes_existing_artifacts():
    """Release should publish bases whose tarballs exist, not fail when some are missing."""
    wf = load_workflow()

    release_steps = wf["jobs"]["release"]["steps"]
    gh_release_step = next(s for s in release_steps if s.get("uses", "").startswith("softprops/action-gh-release"))

    # Should have fail_on_unmatched_files: false (YAML parses as boolean False)
    with_config = gh_release_step.get("with", {})
    assert with_config.get("fail_on_unmatched_files") is False, (
        "Release should have fail_on_unmatched_files: false to allow partial publishes"
    )


def test_openclaw_bake_passes_version_into_container():
    """Openclaw bake step should pass OPENCLAW_VERSION as env to incus exec."""
    wf = load_workflow()
    
    # Find the openclaw bake step
    build_steps = wf["jobs"]["build"]["steps"]
    bake_step = next(s for s in build_steps if s.get("name") == "Bake openclaw into the base container")
    
    run_content = bake_step["run"]
    
    # Should use --env OPENCLAW_VERSION in incus exec command
    assert "--env OPENCLAW_VERSION" in run_content, (
        "Openclaw bake step should pass OPENCLAW_VERSION with --env to incus exec. "
        f"Run content: {run_content}"
    )
    
    # The version should be expanded from the env var
    assert "${OPENCLAW_VERSION}" in run_content, (
        "Openclaw bake step should reference OPENCLAW_VERSION environment variable. "
        f"Run content: {run_content}"
    )


def test_version_extract_step_fails_on_empty():
    """Extract version step should fail when extracted version is empty or invalid."""
    wf = load_workflow()
    
    # Find the extract version step
    build_steps = wf["jobs"]["build"]["steps"]
    extract_step = next(s for s in build_steps if s.get("name") == "Extract openclaw version from install.sh")
    
    run_content = extract_step["run"]
    
    # Should have validation that checks for empty version
    assert "if [ -z \"$VERSION\" ]; then" in run_content, (
        "Extract version step should check for empty version. "
        f"Run content: {run_content}"
    )
    
    # Should have validation that checks version format
    assert 'if ! [[ "$VERSION" =~ ^[0-9]+(\\.[0-9]+)+$ ]]; then' in run_content, (
        "Extract version step should validate version format with regex ^[0-9]+(\\.[0-9]+)+$. "
        f"Run content: {run_content}"
    )
    
    # Should have exit commands when version is invalid
    assert "exit 1" in run_content, (
        "Extract version step should exit with error code 1 when version is invalid. "
        f"Run content: {run_content}"
    )


def test_extract_step_runs_against_real_install_sh():
    """The extract step must succeed against the actual install.sh and emit exactly one version."""
    wf = load_workflow()
    build_steps = wf["jobs"]["build"]["steps"]
    extract_step = next(s for s in build_steps if s.get("name") == "Extract openclaw version from install.sh")
    run_script = extract_step["run"]

    install_sh = load_install_sh()
    match = re.search(r"npm install -g --unsafe-perm openclaw@([\d.]+)", install_sh)
    assert match, "Could not find pinned openclaw version in install.sh"
    expected_version = match.group(1)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        tmp_path = tmp.name
    try:
        env = os.environ.copy()
        env["GITHUB_OUTPUT"] = tmp_path
        result = subprocess.run(
            ["bash", "-c", run_script],
            cwd=Path(".").resolve(),
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Extract step failed (rc={result.returncode}): {result.stderr}"
        )
        output = Path(tmp_path).read_text()
        assert f"version={expected_version}" in output, (
            f"Expected 'version={expected_version}' in GITHUB_OUTPUT, got: {output}"
        )
    finally:
        os.unlink(tmp_path)


def test_release_waits_for_build():
    """Release must wait for the build matrix and still publish partial success."""
    wf = load_workflow()
    release_job = wf["jobs"]["release"]

    needs = release_job["needs"]
    assert isinstance(needs, list), f"needs should be a list, got: {needs!r}"
    assert "build" in needs, f"'build' should be in needs, got: {needs}"
    assert "detect" in needs, f"'detect' should be in needs, got: {needs}"

    if_cond = release_job.get("if", "")
    assert "!cancelled()" in if_cond, (
        f"Release if condition should include !cancelled(), got: {if_cond}"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])