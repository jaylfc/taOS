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


# ---------------------------------------------------------------------------
# Task 2: device passthrough in build_neko_run_args
# ---------------------------------------------------------------------------

from tinyagentos.worker.browser_container import build_neko_run_args


def _args(**kw):
    base = dict(container_name="c", profile_volume="v", node_ip="10.0.0.2",
                http_port=8800, epr_lo=59000, epr_hi=59009, user_pwd="u", admin_pwd="a")
    base.update(kw)
    return build_neko_run_args(**base)


def test_device_args_spliced_when_given():
    argv = _args(device_args=["/dev/mpp_service", "/dev/dri"])
    assert "--device" in argv
    assert "/dev/mpp_service" in argv
    # each device is preceded by a --device flag
    assert argv.count("--device") == 2


def test_no_device_args_by_default():
    argv = _args()
    assert "--device" not in argv


# ---------------------------------------------------------------------------
# Task 3: BrowserContainerRunner consumes the resolver
# ---------------------------------------------------------------------------

import pytest
from tinyagentos.worker.browser_container import BrowserContainerRunner, DEFAULT_NEKO_RK3588_IMAGE


@pytest.mark.asyncio
async def test_runner_uses_rk3588_spec_in_mock():
    hw = _hw(soc="rk3588")  # helper from Task 1 test
    runner = BrowserContainerRunner(node_ip="10.0.0.2", mock=True, hw_profile=hw)
    out = await runner.start(session_id="s1", profile_volume="v")
    assert out["image"] == DEFAULT_NEKO_RK3588_IMAGE
    assert out["encode"] == "rkmpp"


@pytest.mark.asyncio
async def test_runner_software_fallback_in_mock():
    runner = BrowserContainerRunner(node_ip="10.0.0.2", mock=True, hw_profile=None)
    out = await runner.start(session_id="s2", profile_volume="v")
    assert out["encode"] == "software"
