"""Tests for the CoW storage pool feature in scripts/install-server.sh.

Tests validate:
1. Script syntax and presence of required functions/variables
2. Filesystem detection logic (using inline test functions)
3. Storage init logic branches (using inline test functions)
4. Env var defaults
"""
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

INSTALL_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-server.sh"


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


# Inline copies of the key functions for unit testing the logic.
DETECT_COW_FS_FUNC = textwrap.dedent("""
    detect_cow_filesystem() {
        local target="/var/lib"
        [[ -d /var/lib/incus ]] && target="/var/lib/incus"
        local fs_type=""
        if stat -f --format=%T "$target" >/dev/null 2>&1; then
            fs_type=$(stat -f --format=%T "$target" 2>/dev/null)
        elif df -T "$target" >/dev/null 2>&1; then
            fs_type=$(df -T "$target" 2>/dev/null | awk 'NR==2 {print $2}')
        fi
        [[ -z "$fs_type" ]] && fs_type="unknown"
        echo "$fs_type"
    }
""")

INCUS_STORAGE_INIT_FUNC = textwrap.dedent("""
    _incus_storage_init() {
        local fs_type="$1"
        case "${COW_POOL_MODE}" in
            btrfs)
                if [[ "$fs_type" != "btrfs" ]]; then
                    echo "WARN: btrfs pool requires btrfs filesystem - falling back"
                    return 0
                fi
                echo "creating incus btrfs storage pool"
                incus storage create default btrfs 2>/dev/null && return 0
                echo "WARN: btrfs create failed - falling back"
                ;;
            zfs)
                if [[ "$fs_type" != "zfs" ]]; then
                    echo "WARN: zfs pool requires zfs filesystem - falling back"
                    return 0
                fi
                echo "creating incus zfs storage pool"
                incus storage create default zfs 2>/dev/null && return 0
                echo "WARN: zfs create failed - falling back"
                ;;
            dir)
                echo "WARN: forcing directory-backed pool"
                return 0
                ;;
            auto|*)
                case "$fs_type" in
                    btrfs|zfs)
                        echo "auto-creating $fs_type storage pool"
                        incus storage create default "$fs_type" 2>/dev/null && return 0
                        echo "WARN: auto-create failed - falling back"
                        ;;
                    ext[2-4]|xfs)
                        echo "$fs_type filesystem - CoW not available"
                        ;;
                    *)
                        echo "filesystem type $fs_type - auto-select"
                        ;;
                esac
                return 0
                ;;
        esac
        return 0
    }
""")


class TestInstallScriptIntegrity:
    """Validate the install script is well-formed."""

    def test_syntax_valid(self):
        """The install script must pass bash -n."""
        rc, out, err = _bash_run(f"bash -n {INSTALL_SCRIPT}")
        assert rc == 0, f"Syntax error: {err}"

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


class TestDetectCowFilesystem:
    """Test filesystem type detection."""

    def _run_detect(self, stat_output, df_output=None):
        script = DETECT_COW_FS_FUNC + textwrap.dedent(f"""
            stat() {{ echo "{stat_output}"; return 0; }}
            df() {{ echo "Filesystem Type 1K-blocks Used Available Use% Mounted on"; echo "/dev/sda1 {df_output or stat_output} 100000 50000 50000 50% /var/lib"; return 0; }}
            detect_cow_filesystem
        """)
        rc, out, err = _bash_run(script)
        if rc != 0:
            return f"ERROR: {err}"
        return out

    def test_detects_btrfs(self):
        assert "btrfs" == self._run_detect("btrfs")

    def test_detects_zfs(self):
        assert "zfs" == self._run_detect("zfs")

    def test_detects_ext4(self):
        assert "ext4" == self._run_detect("ext4")

    def test_detects_xfs(self):
        assert "xfs" == self._run_detect("xfs")

    def test_fallback_to_df(self):
        script = DETECT_COW_FS_FUNC + textwrap.dedent("""
            stat() { return 1; }
            df() { echo "Filesystem Type"; echo "/dev/sda1 ext4 100000 50000 50000 50% /var/lib"; return 0; }
            detect_cow_filesystem
        """)
        rc, out, err = _bash_run(script)
        assert rc == 0, f"failed: {err}"
        assert "ext4" == out

    def test_unknown_when_both_fail(self):
        script = DETECT_COW_FS_FUNC + textwrap.dedent("""
            stat() { return 1; }
            df() { return 1; }
            detect_cow_filesystem
        """)
        rc, out, err = _bash_run(script)
        assert rc == 0, f"failed: {err}"
        assert out == "unknown"


class TestIncusStorageInit:
    """Test storage pool initialization logic."""

    def _run_init(self, cow_pool_mode, fs_type):
        script = INCUS_STORAGE_INIT_FUNC + textwrap.dedent(f"""
            incus() {{ echo "incus $*"; return 0; }}
            COW_POOL_MODE="{cow_pool_mode}"
            _incus_storage_init "{fs_type}"
        """)
        rc, out, err = _bash_run(script)
        if rc != 0:
            return f"ERROR: {err}"
        return out

    def test_auto_mode_btrfs(self):
        out = self._run_init("auto", "btrfs")
        assert "auto-creating btrfs storage pool" in out

    def test_auto_mode_zfs(self):
        out = self._run_init("auto", "zfs")
        assert "auto-creating zfs storage pool" in out

    def test_auto_mode_ext4(self):
        out = self._run_init("auto", "ext4")
        assert "CoW not available" in out

    def test_auto_mode_xfs(self):
        out = self._run_init("auto", "xfs")
        assert "CoW not available" in out

    def test_btrfs_mode_on_btrfs(self):
        out = self._run_init("btrfs", "btrfs")
        assert "creating incus btrfs storage pool" in out

    def test_btrfs_mode_on_ext4(self):
        out = self._run_init("btrfs", "ext4")
        assert "falling back" in out
        assert "btrfs pool requires btrfs" in out

    def test_zfs_mode_on_zfs(self):
        out = self._run_init("zfs", "zfs")
        assert "creating incus zfs storage pool" in out

    def test_zfs_mode_on_ext4(self):
        out = self._run_init("zfs", "ext4")
        assert "falling back" in out
        assert "zfs pool requires zfs" in out

    def test_dir_mode(self):
        out = self._run_init("dir", "btrfs")
        assert "forcing directory-backed pool" in out
        assert "incus storage create" not in out

    def test_auto_create_fails_falls_back(self):
        """When auto pool creation fails, warn and fall back gracefully."""
        script = INCUS_STORAGE_INIT_FUNC + textwrap.dedent("""\
            incus() { echo "incus $*"; return 1; }
            COW_POOL_MODE="auto"
            _incus_storage_init "btrfs"
        """)
        rc, out, err = _bash_run(script)
        assert rc == 0, f"should not crash on failure: {out} {err}"
        assert "auto-create failed - falling back" in out


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
