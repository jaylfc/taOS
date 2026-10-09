"""Test that installer pip install lines do not swallow failures with || true."""
from pathlib import Path


def test_installer_pip_install_lines_do_not_swallow_failure():
    scripts_dir = Path(__file__).resolve().parents[1] / "tinyagentos" / "scripts"
    install_scripts = list(scripts_dir.glob("install_*.sh"))
    assert install_scripts, f"No install_*.sh scripts found in {scripts_dir}"

    for script_path in install_scripts:
        content = script_path.read_text()
        for line_num, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("pip3 install "):
                assert not stripped.endswith(
                    "|| true"
                ), f"{script_path.name}:{line_num}: pip install line ends with '|| true' which swallows failures: {stripped}"
