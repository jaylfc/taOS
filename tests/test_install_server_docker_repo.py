"""Regression tests for the Docker official-repo fallback in install-server.sh.

The fallback (`_apt_install_docker_official_repo`, added for Debian/Armbian
trixie — taOS#2) writes /etc/apt/keyrings/docker.asc and
/etc/apt/sources.list.d/docker.list, then rolls the repo config back when
`apt-get update` or `apt-get install` fails.

The rollback originally tracked "did we successfully write this file", not
"did this file exist beforehand", so a host that already carried a customised
Docker repo (an internal mirror, a pinned suite, a corporate signing key) had
that configuration OVERWRITTEN and then DELETED by the failure path. These
tests run the real function against a fake apt root with stubbed
sudo/curl/gpg/apt-get and assert the host is left exactly as it was found:

* pre-existing docker.asc / docker.list are restored byte-for-byte on failure
* files this invocation created are still deleted on failure
* the success path leaves Docker's own repo config in place
"""
import os
import subprocess
from pathlib import Path

import pytest

INSTALL_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-server.sh"

PREEXISTING_KEY = "PRE-EXISTING-CORPORATE-DOCKER-KEY\n"
PREEXISTING_LIST = "deb [signed-by=/etc/apt/keyrings/docker.asc] https://mirror.internal/docker bookworm stable\n"

# The fingerprint the script pins for download.docker.com's signing key; the
# gpg stub below must echo it or the function bails before touching apt.
DOCKER_FP = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"


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


# Stubs standing in for everything the fallback shells out to. `sudo` just
# drops a leading VAR=value env prefix and exec's the rest unprivileged; the
# /etc/apt prefix is redirected onto a temp root by _APT_ROOT_SUB below, so
# the test exercises the real control flow without touching the host.
_PREAMBLE = r"""
set -euo pipefail
log()  { printf '[log] %s\n' "$*" >&2; }
warn() { printf '[warn] %s\n' "$*" >&2; }

sudo() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            [A-Z_]*=*) shift; continue ;;
        esac
        break
    done
    "$@"
}

lsb_release() { echo "trixie"; }
dpkg() { return 0; }

curl() {
    local out=""
    while [[ $# -gt 0 ]]; do
        [[ "$1" == "-o" ]] && { out="$2"; shift 2; continue; }
        shift
    done
    printf 'DOCKER-COM-OFFICIAL-KEY\n' > "$out"
}

gpg() { printf 'fpr:::::::::%s:\n' "$DOCKER_FP"; }

apt-get() {
    for a in "$@"; do
        case "$a" in
            update)  return "$APT_UPDATE_RC" ;;
            install) return "$APT_INSTALL_RC" ;;
            remove)  return 0 ;;
        esac
    done
    return 0
}
"""


def _run_fallback(tmp_path, *, preexisting: bool, update_rc: int, install_rc: int):
    """Run the real fallback against a fake apt root; return (rc, keyring, list).

    `keyring` / `list` are the files' contents afterwards, or None if absent.
    """
    fake = tmp_path / "fakeroot"
    keyrings = fake / "etc/apt/keyrings"
    sources = fake / "etc/apt/sources.list.d"
    keyrings.mkdir(parents=True)
    sources.mkdir(parents=True)
    keyring = keyrings / "docker.asc"
    listfile = sources / "docker.list"
    if preexisting:
        keyring.write_text(PREEXISTING_KEY)
        listfile.write_text(PREEXISTING_LIST)

    # The fallback hardcodes absolute /etc/apt paths (correct in production,
    # untestable in place — `[[ -e ]]` is a builtin, so a sudo stub cannot
    # redirect it). Rebase that one prefix onto the temp root; every branch,
    # backup and restore below is the script's own code, unmodified.
    script = "\n".join([
        _PREAMBLE,
        _extract_func("_docker_apt_restore"),
        _extract_func("_apt_install_docker_official_repo"),
        "_apt_install_docker_official_repo && echo RC=0 || echo RC=$?",
    ]).replace("/etc/apt", f"{fake}/etc/apt")
    env = os.environ.copy()
    env.update({
        "DOCKER_FP": DOCKER_FP,
        "APT_UPDATE_RC": str(update_rc),
        "APT_INSTALL_RC": str(install_rc),
    })
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=30
    )
    assert "RC=" in proc.stdout, (
        f"fallback never reached its exit line.\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    rc = int(proc.stdout.strip().rsplit("RC=", 1)[1].split()[0])
    return (
        rc,
        keyring.read_text() if keyring.exists() else None,
        listfile.read_text() if listfile.exists() else None,
        proc.stderr,
    )


@pytest.mark.skipif(os.name != "posix", reason="bash-only test")
class TestDockerRepoFallbackRollback:
    """The fallback must never destroy apt config it did not create."""

    def test_preexisting_files_restored_when_apt_update_fails(self, tmp_path):
        """A host's own docker.asc/docker.list survive an apt-get update failure."""
        rc, keyring, listfile, stderr = _run_fallback(
            tmp_path, preexisting=True, update_rc=1, install_rc=0
        )
        assert rc == 1, f"fallback should report failure; stderr={stderr}"
        assert keyring == PREEXISTING_KEY, (
            "pre-existing /etc/apt/keyrings/docker.asc was not restored "
            f"(got {keyring!r}); stderr={stderr}"
        )
        assert listfile == PREEXISTING_LIST, (
            "pre-existing /etc/apt/sources.list.d/docker.list was not restored "
            f"(got {listfile!r}); stderr={stderr}"
        )

    def test_preexisting_files_restored_when_apt_install_fails(self, tmp_path):
        """Same guarantee on the later apt-get install failure path."""
        rc, keyring, listfile, stderr = _run_fallback(
            tmp_path, preexisting=True, update_rc=0, install_rc=1
        )
        assert rc == 1, f"fallback should report failure; stderr={stderr}"
        assert keyring == PREEXISTING_KEY, (
            f"pre-existing docker.asc was not restored (got {keyring!r}); stderr={stderr}"
        )
        assert listfile == PREEXISTING_LIST, (
            f"pre-existing docker.list was not restored (got {listfile!r}); stderr={stderr}"
        )

    def test_files_we_created_are_deleted_when_apt_update_fails(self, tmp_path):
        """The original cleanup still applies to files this invocation created."""
        rc, keyring, listfile, stderr = _run_fallback(
            tmp_path, preexisting=False, update_rc=1, install_rc=0
        )
        assert rc == 1, f"fallback should report failure; stderr={stderr}"
        assert keyring is None, (
            f"docker.asc created by this run should be removed; stderr={stderr}"
        )
        assert listfile is None, (
            f"docker.list created by this run should be removed; stderr={stderr}"
        )

    def test_success_keeps_dockers_own_repo_config(self, tmp_path):
        """On success the fetched key and docker.com suite stay in place."""
        rc, keyring, listfile, stderr = _run_fallback(
            tmp_path, preexisting=True, update_rc=0, install_rc=0
        )
        assert rc == 0, f"fallback should succeed; stderr={stderr}"
        assert keyring == "DOCKER-COM-OFFICIAL-KEY\n", (
            f"the fetched docker.com key should be installed (got {keyring!r})"
        )
        assert listfile is not None and "download.docker.com" in listfile, (
            f"docker.list should point at download.docker.com (got {listfile!r})"
        )
