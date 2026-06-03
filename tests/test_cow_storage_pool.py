"""Tests for the CoW storage pool feature in scripts/install-server.sh.

Tests validate:
1. Script syntax and presence of required functions/variables
2. Filesystem detection logic (sourced from the real script)
3. Storage init logic branches (sourced from the real script)
4. Env var defaults
"""
import os
import subprocess
from pathlib import Path

import pytest

INSTALL_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-server.sh"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bash_run(script: str, env: dict | None = None) -> tuple[int, str, str]:
    """Run a bash snippet and return (exit_code, stdout, stderr)."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=10,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _extract_func(name: str) -> str:
    """Return the shell source text for a single function from install-server.sh.

    Extracts from `name() {` up to and including the closing `}` on its own
    line (standard bash function layout used throughout install-server.sh).
    """
    lines = INSTALL_SCRIPT.read_text().splitlines()
    collecting = False
    brace_depth = 0
    collected: list[str] = []
    for line in lines:
        if not collecting:
            if line.startswith(f"{name}()"):
                collecting = True
        if collecting:
            collected.append(line)
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0 and len(collected) > 1:
                break
    return "\n".join(collected)


# Source preamble used by every test that exercises real script functions.
# Sets up stubs for `log` and `warn` so diagnostics go to stderr only, and
# defines globals the functions may reference.
_PREAMBLE = """
log()  { printf '[log] %s\\n' "$*" >&2; }
warn() { printf '[warn] %s\\n' "$*" >&2; }
COW_POOL_MODE="${COW_POOL_MODE:-auto}"
COW_EFFECTIVE_MODE="n/a"
"""


# ---------------------------------------------------------------------------
# Issue 5 fix — safe bash -n invocation (argv list, no shell interpolation)
# ---------------------------------------------------------------------------

class TestInstallScriptIntegrity:
    """Validate the install script is well-formed."""

    def test_syntax_valid(self):
        """The install script must pass bash -n."""
        result = subprocess.run(
            ["bash", "-n", str(INSTALL_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_taos_cow_pool_env_var(self):
        """TAOS_COW_POOL must be defined and default to auto."""
        content = INSTALL_SCRIPT.read_text()
        assert "TAOS_COW_POOL" in content
        assert 'COW_POOL_MODE="${TAOS_COW_POOL:-auto}"' in content

    def test_detect_function_present(self):
        """detect_cow_filesystem function must exist."""
        content = INSTALL_SCRIPT.read_text()
        assert "detect_cow_filesystem()" in content
        assert "_incus_storage_init()" in content

    def test_cow_init_call_present(self):
        """The _incus_storage_init call must be in incus init section."""
        content = INSTALL_SCRIPT.read_text()
        assert '_incus_storage_init "$COW_FS_TYPE"' in content

    def test_success_summary_has_cow_info(self):
        """Success summary must include storage pool info."""
        content = INSTALL_SCRIPT.read_text()
        assert "Storage pool" in content


# ---------------------------------------------------------------------------
# Issue 4 fix — real function sourced from the script
# Issue 1 regression guard — stdout must be a single clean token
# ---------------------------------------------------------------------------

class TestDetectCowFilesystem:
    """Test filesystem type detection using the real detect_cow_filesystem()."""

    _FUNC = _extract_func("detect_cow_filesystem")

    def _run_detect(self, stat_output: str, df_output: str | None = None) -> str:
        """Stub stat/df and call the real function; return its stdout."""
        df_fs = df_output or stat_output
        script = _PREAMBLE + self._FUNC + f"""
stat() {{ echo "{stat_output}"; return 0; }}
df() {{ printf 'Filesystem\\tType\\t1K-blocks\\tUsed\\tAvailable\\tUse%%\\tMounted on\\n'; printf '/dev/sda1\\t{df_fs}\\t100000\\t50000\\t50000\\t50%%\\t/var/lib\\n'; return 0; }}
detect_cow_filesystem
"""
        rc, out, err = _bash_run(script)
        if rc != 0:
            return f"ERROR: {err}"
        return out

    # --- Issue 1 regression: stdout must be a single clean token -----------

    def test_stdout_is_single_token_btrfs(self):
        """detect_cow_filesystem stdout must be exactly one word (no log noise)."""
        result = self._run_detect("btrfs")
        assert result == "btrfs", f"Expected 'btrfs', got: {result!r}"

    def test_stdout_is_single_token_zfs(self):
        result = self._run_detect("zfs")
        assert result == "zfs", f"Expected 'zfs', got: {result!r}"

    def test_stdout_is_single_token_ext4(self):
        result = self._run_detect("ext4")
        assert result == "ext4", f"Expected 'ext4', got: {result!r}"

    def test_stdout_no_newlines(self):
        """Captured output must contain no embedded newlines (no log contamination)."""
        script = _PREAMBLE + self._FUNC + """
stat() { echo "btrfs"; return 0; }
df()   { return 1; }
out=$(detect_cow_filesystem)
lines=$(echo "$out" | wc -l)
echo "$lines"
"""
        rc, out, _ = _bash_run(script)
        assert rc == 0
        assert out.strip() == "1", f"Expected 1 line in captured output, got: {out!r}"

    # --- functional correctness --------------------------------------------

    def test_detects_btrfs(self):
        assert self._run_detect("btrfs") == "btrfs"

    def test_detects_zfs(self):
        assert self._run_detect("zfs") == "zfs"

    def test_detects_ext4(self):
        assert self._run_detect("ext4") == "ext4"

    def test_detects_xfs(self):
        assert self._run_detect("xfs") == "xfs"

    def test_fallback_to_df(self):
        """Falls back to df -T when stat returns non-zero."""
        script = _PREAMBLE + self._FUNC + """
stat() { return 1; }
df()   { printf 'Filesystem\\tType\\n'; printf '/dev/sda1\\text4\\n'; return 0; }
detect_cow_filesystem
"""
        rc, out, err = _bash_run(script)
        assert rc == 0, f"failed: {err}"
        assert out == "ext4"

    def test_unknown_when_both_fail(self):
        script = _PREAMBLE + self._FUNC + """
stat() { return 1; }
df()   { return 1; }
detect_cow_filesystem
"""
        rc, out, err = _bash_run(script)
        assert rc == 0, f"failed: {err}"
        assert out == "unknown"


class TestIncusStorageInit:
    """Test storage pool initialisation logic using the real _incus_storage_init()."""

    _FUNC = _extract_func("_incus_storage_init")

    def _run_init(self, cow_pool_mode: str, fs_type: str) -> str:
        script = _PREAMBLE + self._FUNC + f"""
sudo()  {{ "$@"; }}
incus() {{ echo "incus $*"; return 0; }}
COW_POOL_MODE="{cow_pool_mode}"
_incus_storage_init "{fs_type}"
echo "EFFECTIVE=$COW_EFFECTIVE_MODE"
"""
        rc, out, err = _bash_run(script)
        if rc != 0:
            return f"ERROR: {err}"
        return out

    def _run_init_with_stderr(self, cow_pool_mode: str, fs_type: str) -> tuple[str, str]:
        """Like _run_init but returns (stdout, stderr)."""
        script = _PREAMBLE + self._FUNC + f"""
sudo()  {{ "$@"; }}
incus() {{ echo "incus $*"; return 0; }}
COW_POOL_MODE="{cow_pool_mode}"
_incus_storage_init "{fs_type}"
echo "EFFECTIVE=$COW_EFFECTIVE_MODE"
"""
        rc, out, err = _bash_run(script)
        return out, err

    def test_auto_mode_btrfs(self):
        out, err = self._run_init_with_stderr("auto", "btrfs")
        assert "EFFECTIVE=btrfs" in out
        # log diagnostic should mention CoW filesystem detection (goes to stderr)
        assert "btrfs" in err

    def test_auto_mode_zfs(self):
        out, err = self._run_init_with_stderr("auto", "zfs")
        assert "EFFECTIVE=zfs" in out

    def test_auto_mode_ext4(self):
        out, err = self._run_init_with_stderr("auto", "ext4")
        assert "EFFECTIVE=none" in out
        # log message about CoW not available goes to stderr
        assert "CoW not available" in err

    def test_auto_mode_xfs(self):
        out, err = self._run_init_with_stderr("auto", "xfs")
        assert "EFFECTIVE=none" in out
        assert "CoW not available" in err

    def test_btrfs_mode_on_btrfs(self):
        out, err = self._run_init_with_stderr("btrfs", "btrfs")
        assert "EFFECTIVE=btrfs" in out
        assert "creating incus btrfs storage pool" in err

    def test_btrfs_mode_on_ext4(self):
        out = self._run_init("btrfs", "ext4")
        # warns to stderr; stdout just has the EFFECTIVE line
        assert "EFFECTIVE=" in out  # no crash

    def test_zfs_mode_on_zfs(self):
        out, err = self._run_init_with_stderr("zfs", "zfs")
        assert "EFFECTIVE=zfs" in out
        assert "creating incus zfs storage pool" in err

    def test_zfs_mode_on_ext4(self):
        out = self._run_init("zfs", "ext4")
        assert "EFFECTIVE=" in out  # no crash

    def test_dir_mode_creates_pool(self):
        """dir mode must create a dir-backed pool (not just return early)."""
        script = _PREAMBLE + self._FUNC + """
sudo()  { "$@"; }
incus() {
    # list returns empty (no existing pool); create succeeds
    if [[ "$1" == "storage" && "$2" == "list" ]]; then echo ""; return 0; fi
    echo "incus $*"; return 0;
}
COW_POOL_MODE="dir"
_incus_storage_init "btrfs"
echo "EFFECTIVE=$COW_EFFECTIVE_MODE"
"""
        rc, out, err = _bash_run(script)
        assert rc == 0, f"dir mode crashed: {err}"
        assert "incus storage create default dir" in out
        assert "EFFECTIVE=dir" in out

    def test_dir_mode_skips_existing_pool(self):
        """dir mode must not recreate when 'default' pool already exists."""
        script = _PREAMBLE + self._FUNC + """
sudo()  { "$@"; }
incus() {
    if [[ "$1" == "storage" && "$2" == "list" ]]; then echo "| default |"; return 0; fi
    echo "incus $*"; return 0;
}
COW_POOL_MODE="dir"
_incus_storage_init "ext4"
echo "EFFECTIVE=$COW_EFFECTIVE_MODE"
"""
        rc, out, err = _bash_run(script)
        assert rc == 0, f"should not crash: {err}"
        assert "incus storage create" not in out
        assert "EFFECTIVE=dir" in out

    def test_auto_create_fails_falls_back(self):
        """When auto pool creation fails, warn and fall back gracefully."""
        script = _PREAMBLE + self._FUNC + """
sudo()  { "$@"; }
incus() { echo "incus $*"; return 1; }
COW_POOL_MODE="auto"
_incus_storage_init "btrfs"
"""
        rc, out, err = _bash_run(script)
        assert rc == 0, f"should not crash on failure: {out} {err}"
        assert "failed" in err or "failed" in out


class TestEnvVarDefaults:
    """Test TAOS_COW_POOL defaults."""

    def test_default_is_auto(self):
        script = 'COW_POOL_MODE="${TAOS_COW_POOL:-auto}"; echo "MODE=$COW_POOL_MODE"'
        rc, out, err = _bash_run(script, env={})
        assert rc == 0
        assert "MODE=auto" in out

    def test_explicit_btrfs(self):
        script = 'COW_POOL_MODE="${TAOS_COW_POOL:-auto}"; echo "MODE=$COW_POOL_MODE"'
        rc, out, err = _bash_run(script, env={"TAOS_COW_POOL": "btrfs"})
        assert rc == 0
        assert "MODE=btrfs" in out

    def test_explicit_dir(self):
        script = 'COW_POOL_MODE="${TAOS_COW_POOL:-auto}"; echo "MODE=$COW_POOL_MODE"'
        rc, out, err = _bash_run(script, env={"TAOS_COW_POOL": "dir"})
        assert rc == 0
        assert "MODE=dir" in out
