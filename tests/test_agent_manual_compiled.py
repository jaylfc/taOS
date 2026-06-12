"""CI guard: the compiled docs/taos-agent-manual.md must match the source library.

A contributor who edits docs/agent-manual/ but forgets to rebuild will fail here.
"""

import pathlib
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-agent-manual.py"
COMMITTED_OUTPUT = REPO_ROOT / "docs" / "taos-agent-manual.md"

MAX_CHARS = 16000

MUST_CONTAIN = [
    "You are the **taOS agent**",
    "Controller port",
    "install-server.sh",
    "phoning home",
]


def test_build_script_exists():
    assert BUILD_SCRIPT.exists(), f"Build script not found: {BUILD_SCRIPT}"


def test_compiled_output_matches_committed():
    """Running the build script into a temp file must match the committed file."""
    committed = COMMITTED_OUTPUT.read_text(encoding="utf-8")

    with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as tmp:
        tmp_path = pathlib.Path(tmp.name)

    try:
        result = subprocess.run(
            [sys.executable, str(BUILD_SCRIPT), "--output", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Build script failed:\n{result.stderr}"

        fresh = tmp_path.read_text(encoding="utf-8")
        assert fresh == committed, (
            "docs/taos-agent-manual.md is out of sync with docs/agent-manual/. "
            "Run: python3 scripts/build-agent-manual.py"
        )
    finally:
        tmp_path.unlink(missing_ok=True)


def test_compiled_size_under_limit():
    content = COMMITTED_OUTPUT.read_text(encoding="utf-8")
    assert len(content) <= MAX_CHARS, (
        f"Compiled manual is {len(content)} chars, exceeds {MAX_CHARS} limit. "
        "Trim the source files to keep the prompt injectable on small context windows."
    )


def test_must_have_substrings():
    content = COMMITTED_OUTPUT.read_text(encoding="utf-8")
    for substring in MUST_CONTAIN:
        assert substring in content, (
            f"Required substring missing from compiled manual: {substring!r}"
        )
