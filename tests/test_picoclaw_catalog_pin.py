import re
import subprocess
from pathlib import Path

INSTALL_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "app-catalog"
    / "agents"
    / "picoclaw"
    / "scripts"
    / "install-picoclaw.sh"
)


def _script() -> str:
    return INSTALL_SCRIPT.read_text(encoding="utf-8")


def test_pinned_version_is_0_3_1() -> None:
    text = _script()
    m = re.search(r'PICOCLAW_VERSION="(v[\d.]+)"', text)
    assert m, "PICOCLAW_VERSION not found in install-picoclaw.sh"
    assert m.group(1) == "v0.3.1", f"expected v0.3.1, got {m.group(1)}"


def test_arm64_and_amd64_checksums_present_and_64hex() -> None:
    text = _script()
    arm64 = re.search(
        r"aarch64\|arm64\).*?PICOCLAW_SHA256=\"([0-9a-f]{64})\"", text, re.DOTALL
    )
    amd64 = re.search(
        r"x86_64\|amd64\).*?PICOCLAW_SHA256=\"([0-9a-f]{64})\"", text, re.DOTALL
    )
    assert arm64, "arm64 PICOCLAW_SHA256 not found in install-picoclaw.sh"
    assert amd64, "amd64 PICOCLAW_SHA256 not found in install-picoclaw.sh"


def test_checksum_mismatch_refuses_install(tmp_path: Path) -> None:
    text = _script()
    arm64 = re.search(
        r"aarch64\|arm64\).*?PICOCLAW_SHA256=\"([0-9a-f]{64})\"", text, re.DOTALL
    )
    assert arm64, "arm64 PICOCLAW_SHA256 not found in install-picoclaw.sh"
    expected = arm64.group(1)

    tarball = tmp_path / "tampered.tar.gz"
    tarball.write_bytes(b"tampered content")

    verify_script = tmp_path / "verify.sh"
    verify_script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'echo "wrong_checksum_64hex_chars_here  {tarball}" | sha256sum -c - >/dev/null 2>&1\n'
        'echo "verify unexpectedly passed"\n',
        encoding="utf-8",
    )
    verify_script.chmod(0o755)

    result = subprocess.run(
        ["bash", str(verify_script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "expected checksum mismatch to refuse install"
