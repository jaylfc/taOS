from types import SimpleNamespace
from tinyagentos.worker.browser_container import (
    resolve_neko_image, NekoImageSpec,
    DEFAULT_NEKO_CDP_IMAGE, DEFAULT_NEKO_IMAGE, DEFAULT_NEKO_GPU_IMAGE,
    DEFAULT_NEKO_RK3588_IMAGE,
    NEKO_SCREEN, NEKO_SCREEN_MOBILE, MOBILE_CHROMIUM_CONF,
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


# ---------------------------------------------------------------------------
# Task 3: volume export/import argv builders
# ---------------------------------------------------------------------------

from tinyagentos.worker.browser_container import build_volume_export_args, build_volume_import_args


def test_volume_export_args_tars_the_mount():
    argv = build_volume_export_args("taos-browser-s1")
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "taos-browser-s1:/from" in argv
    assert "tar" in argv and "-C" in argv


def test_volume_import_args_untars_into_target_volume():
    argv = build_volume_import_args("taos-browser-s1")
    assert "taos-browser-s1:/to" in argv
    assert "tar" in argv


# ---------------------------------------------------------------------------
# Mobile mode: build_neko_run_args(mobile=True/False)
# ---------------------------------------------------------------------------

def test_mobile_args_uses_portrait_screen_and_conf_mount():
    argv = _args(mobile=True)
    # Portrait screen env var
    assert f"NEKO_DESKTOP_SCREEN={NEKO_SCREEN_MOBILE}" in argv
    # mobile-chromium.conf bind-mount present
    conf_mount = f"{MOBILE_CHROMIUM_CONF}:/etc/neko/supervisord/chromium.conf:ro"
    assert conf_mount in argv


def test_desktop_args_uses_landscape_screen_no_conf_mount():
    argv = _args(mobile=False)
    # Landscape screen
    assert f"NEKO_DESKTOP_SCREEN={NEKO_SCREEN}" in argv
    # No conf mount
    assert "/etc/neko/supervisord/chromium.conf" not in " ".join(argv)


def test_mobile_conf_file_exists():
    """The mobile-chromium.conf must actually exist in the repo."""
    assert MOBILE_CHROMIUM_CONF.exists(), f"Missing: {MOBILE_CHROMIUM_CONF}"


# ---------------------------------------------------------------------------
# BrowserContainerRunner.start mobile mock round-trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runner_start_mobile_mock_returns_details():
    runner = BrowserContainerRunner(node_ip="10.0.0.2", mock=True, hw_profile=None)
    out = await runner.start(session_id="mobile-session", profile_volume="v", mobile=True)
    assert "container_id" in out
    assert "neko_url" in out


@pytest.mark.asyncio
async def test_runner_start_desktop_mock_returns_details():
    runner = BrowserContainerRunner(node_ip="10.0.0.2", mock=True, hw_profile=None)
    out = await runner.start(session_id="desktop-session", profile_volume="v", mobile=False)
    assert "container_id" in out
    assert "neko_url" in out


# ---------------------------------------------------------------------------
# CDP image (option C foundation)
# ---------------------------------------------------------------------------

def test_rk3588_image_is_cdp_image():
    """RK3588 must resolve to the CDP-enabled custom image."""
    assert DEFAULT_NEKO_RK3588_IMAGE == DEFAULT_NEKO_CDP_IMAGE


@pytest.mark.asyncio
async def test_rk3588_runner_exposes_cdp_url():
    """Running on RK3588 hardware must yield a cdp_url pointing to 127.0.0.1:9222."""
    hw = _hw(soc="rk3588")
    runner = BrowserContainerRunner(node_ip="10.0.0.2", mock=True, hw_profile=hw)
    out = await runner.start(session_id="cdp-session", profile_volume="v")
    assert out["cdp_url"] == "http://127.0.0.1:9222"
