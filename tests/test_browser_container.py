from types import SimpleNamespace
from tinyagentos.worker.browser_container import (
    resolve_neko_image, NekoImageSpec,
    DEFAULT_NEKO_IMAGE, DEFAULT_NEKO_GPU_IMAGE, DEFAULT_NEKO_RK3588_IMAGE,
)


def _hw(*, soc="", gpu_type="none", cuda=False, vulkan=False):
    return SimpleNamespace(
        cpu=SimpleNamespace(soc=soc),
        gpu=SimpleNamespace(type=gpu_type, cuda=cuda, vulkan=vulkan),
    )


def test_rk3588_uses_rkmpp_image_and_devices():
    spec = resolve_neko_image(_hw(soc="rk3588"))
    assert spec.image == DEFAULT_NEKO_RK3588_IMAGE
    assert spec.encode == "rkmpp"
    assert "/dev/mpp_service" in spec.device_args
    assert "/dev/dri" in spec.device_args
    assert spec.gpu is False


def test_nvidia_cuda_uses_nvenc():
    spec = resolve_neko_image(_hw(gpu_type="nvidia", cuda=True))
    assert spec.image == DEFAULT_NEKO_GPU_IMAGE
    assert spec.encode == "nvenc"
    assert spec.gpu is True
    assert spec.device_args == []


def test_intel_amd_uses_vaapi_dri():
    spec = resolve_neko_image(_hw(gpu_type="intel"))
    assert spec.encode == "vaapi"
    assert "/dev/dri" in spec.device_args
    assert spec.gpu is False


def test_apple_and_unknown_fall_back_to_software():
    for hw in (_hw(soc="apple-silicon"), _hw(soc="m3"), _hw()):
        spec = resolve_neko_image(hw)
        assert spec.image == DEFAULT_NEKO_IMAGE
        assert spec.encode == "software"
        assert spec.device_args == []
        assert spec.gpu is False


def test_resolve_handles_none_profile():
    spec = resolve_neko_image(None)
    assert spec.encode == "software"
