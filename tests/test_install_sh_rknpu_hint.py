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
    assert "install-rknpu.sh" in text, (
        "install.sh Rockchip NPU hint must point at scripts/install-rknpu.sh"
    )
