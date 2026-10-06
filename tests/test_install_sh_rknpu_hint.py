import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"


def test_install_sh_rknpu_hint_does_not_mention_pip_install_rkllama():
    text = INSTALL_SH.read_text()
    assert "pip install rkllama" not in text, (
        "install.sh still suggests 'pip install rkllama', which does not exist on PyPI"
    )


def test_install_sh_rknpu_hint_points_to_install_rknpu_sh():
    text = INSTALL_SH.read_text()
    lines = text.splitlines()

    start = None
    for i, line in enumerate(lines):
        if "/dev/rknpu" in line:
            start = i
            break

    assert start is not None, "install.sh missing /dev/rknpu hint block"

    indent = len(lines[start]) - len(lines[start].lstrip())

    end = None
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped == "fi" and len(lines[i]) - len(lines[i].lstrip()) == indent:
            end = i
            break

    assert end is not None, "install.sh /dev/rknpu hint block missing closing fi"

    hint_block = "\n".join(lines[start:end + 1])
    assert "sudo bash $INSTALL_DIR/scripts/install-rknpu.sh" in hint_block, (
        "install.sh Rockchip NPU hint must point at $INSTALL_DIR/scripts/install-rknpu.sh"
    )
