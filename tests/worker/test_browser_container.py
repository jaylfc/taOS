"""Tests for tinyagentos.worker.browser_container."""
from __future__ import annotations

import pytest

from tinyagentos.worker.browser_container import (
    DEFAULT_NEKO_GPU_IMAGE,
    DEFAULT_NEKO_IMAGE,
    NEKO_PROFILE_MOUNT,
    NEKO_SCREEN,
    BrowserContainerRunner,
    PortAllocator,
    build_neko_run_args,
)


# ---------------------------------------------------------------------------
# build_neko_run_args
# ---------------------------------------------------------------------------

class TestBuildNekoRunArgs:
    """Pure-function arg builder — no I/O."""

    def _base_kwargs(self) -> dict:
        return dict(
            container_name="taos-neko-abc123",
            profile_volume="taos-browser-abc123456",
            node_ip="10.0.0.5",
            http_port=8800,
            epr_lo=59000,
            epr_hi=59009,
            user_pwd="userpwd1234",
            admin_pwd="adminpwd5678",
        )

    def test_starts_with_docker_run(self):
        args = build_neko_run_args(**self._base_kwargs())
        assert args[:3] == ["docker", "run", "-d"]

    def test_container_name(self):
        args = build_neko_run_args(**self._base_kwargs())
        idx = args.index("--name")
        assert args[idx + 1] == "taos-neko-abc123"

    def test_http_port_mapping(self):
        args = build_neko_run_args(**self._base_kwargs())
        assert "-p" in args
        port_args = [args[i + 1] for i, a in enumerate(args) if a == "-p"]
        assert "8800:8080" in port_args

    def test_udp_epr_port_mapping(self):
        args = build_neko_run_args(**self._base_kwargs())
        port_args = [args[i + 1] for i, a in enumerate(args) if a == "-p"]
        assert "59000-59009:59000-59009/udp" in port_args

    def test_screen_env(self):
        args = build_neko_run_args(**self._base_kwargs())
        env_pairs = {args[i + 1] for i, a in enumerate(args) if a == "-e"}
        assert f"NEKO_DESKTOP_SCREEN={NEKO_SCREEN}" in env_pairs

    def test_user_password_env(self):
        args = build_neko_run_args(**self._base_kwargs())
        env_pairs = {args[i + 1] for i, a in enumerate(args) if a == "-e"}
        assert "NEKO_MEMBER_MULTIUSER_USER_PASSWORD=userpwd1234" in env_pairs

    def test_admin_password_env(self):
        args = build_neko_run_args(**self._base_kwargs())
        env_pairs = {args[i + 1] for i, a in enumerate(args) if a == "-e"}
        assert "NEKO_MEMBER_MULTIUSER_ADMIN_PASSWORD=adminpwd5678" in env_pairs

    def test_webrtc_epr_env(self):
        args = build_neko_run_args(**self._base_kwargs())
        env_pairs = {args[i + 1] for i, a in enumerate(args) if a == "-e"}
        assert "NEKO_WEBRTC_EPR=59000-59009" in env_pairs

    def test_nat1to1_env(self):
        args = build_neko_run_args(**self._base_kwargs())
        env_pairs = {args[i + 1] for i, a in enumerate(args) if a == "-e"}
        assert "NEKO_WEBRTC_NAT1TO1=10.0.0.5" in env_pairs

    def test_shm_size(self):
        args = build_neko_run_args(**self._base_kwargs())
        assert "--shm-size=2g" in args

    def test_volume_mount(self):
        args = build_neko_run_args(**self._base_kwargs())
        vol_args = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
        assert f"taos-browser-abc123456:{NEKO_PROFILE_MOUNT}" in vol_args

    def test_default_image_cpu(self):
        args = build_neko_run_args(**self._base_kwargs())
        assert args[-1] == DEFAULT_NEKO_IMAGE

    def test_gpu_flag_adds_gpus_all(self):
        args = build_neko_run_args(**self._base_kwargs(), gpu=True)
        assert "--gpus" in args
        idx = args.index("--gpus")
        assert args[idx + 1] == "all"

    def test_gpu_selects_gpu_image(self):
        args = build_neko_run_args(**self._base_kwargs(), gpu=True)
        assert args[-1] == DEFAULT_NEKO_GPU_IMAGE

    def test_explicit_image_overrides_default(self):
        args = build_neko_run_args(**self._base_kwargs(), image="custom/image:tag")
        assert args[-1] == "custom/image:tag"

    def test_cpu_no_gpus_flag(self):
        args = build_neko_run_args(**self._base_kwargs(), gpu=False)
        assert "--gpus" not in args


# ---------------------------------------------------------------------------
# PortAllocator
# ---------------------------------------------------------------------------

class TestPortAllocator:
    def test_first_allocation(self):
        pa = PortAllocator(http_base=9000, epr_base=60000, epr_span=10)
        http_port, epr_lo, epr_hi = pa.allocate()
        assert http_port == 9000
        assert epr_lo == 60000
        assert epr_hi == 60009

    def test_second_allocation_is_different(self):
        pa = PortAllocator(http_base=9000, epr_base=60000, epr_span=10)
        p1 = pa.allocate()
        p2 = pa.allocate()
        assert p1[0] != p2[0], "http_port must differ"
        # EPR ranges must not overlap
        assert p2[1] >= p1[2] + 1 or p1[1] >= p2[2] + 1

    def test_sequential_http_ports_increase(self):
        pa = PortAllocator(http_base=8800)
        ports = [pa.allocate()[0] for _ in range(5)]
        assert ports == sorted(ports)
        assert len(set(ports)) == 5

    def test_epr_ranges_are_non_overlapping(self):
        pa = PortAllocator(epr_base=59000, epr_span=10)
        ranges = [pa.allocate()[1:] for _ in range(3)]  # [(lo, hi), ...]
        for i in range(len(ranges)):
            for j in range(i + 1, len(ranges)):
                lo_i, hi_i = ranges[i]
                lo_j, hi_j = ranges[j]
                assert hi_i < lo_j or hi_j < lo_i, f"Ranges overlap: {ranges[i]} vs {ranges[j]}"

    def test_release_allows_reuse(self):
        pa = PortAllocator(http_base=8800)
        p1, _, _ = pa.allocate()
        pa.release(p1)
        p2, _, _ = pa.allocate()
        # After release, next allocate should reuse the freed slot
        assert p2 == p1

    def test_release_unknown_port_is_noop(self):
        pa = PortAllocator()
        pa.release(99999)  # should not raise


# ---------------------------------------------------------------------------
# BrowserContainerRunner (mock mode)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBrowserContainerRunnerMock:
    async def test_start_returns_expected_fields(self):
        runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)
        result = await runner.start(session_id="sess-abc12345", profile_volume="taos-browser-sess-abc12345")
        assert "container_id" in result
        assert "neko_url" in result
        assert "cdp_url" in result
        assert "http_port" in result
        assert "epr_lo" in result
        assert "epr_hi" in result

    async def test_start_container_id_prefix(self):
        runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)
        result = await runner.start(session_id="sess-abc12345", profile_volume="vol1")
        assert result["container_id"].startswith("mock-neko-")

    async def test_start_neko_url_contains_node_ip(self):
        runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)
        result = await runner.start(session_id="sess-abc12345", profile_volume="vol1")
        assert "10.0.0.5" in result["neko_url"]

    async def test_start_neko_url_contains_usr_neko(self):
        runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)
        result = await runner.start(session_id="sess-abc12345", profile_volume="vol1")
        assert "usr=neko" in result["neko_url"]

    async def test_start_neko_url_contains_pwd(self):
        runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)
        result = await runner.start(session_id="sess-abc12345", profile_volume="vol1")
        assert "pwd=" in result["neko_url"]

    async def test_start_cdp_url_is_none(self):
        runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)
        result = await runner.start(session_id="sess-abc12345", profile_volume="vol1")
        assert result["cdp_url"] is None

    async def test_start_ports_are_integers(self):
        runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)
        result = await runner.start(session_id="sess-abc12345", profile_volume="vol1")
        assert isinstance(result["http_port"], int)
        assert isinstance(result["epr_lo"], int)
        assert isinstance(result["epr_hi"], int)

    async def test_stop_returns_ok(self):
        runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)
        result = await runner.stop(container_id="mock-neko-abc12345")
        assert result == {"ok": True}

    async def test_stop_with_http_port_releases_port(self):
        runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)
        started = await runner.start(session_id="sess-xyz", profile_volume="vol1")
        http_port = started["http_port"]
        await runner.stop(container_id=started["container_id"], http_port=http_port)
        # After release the next allocation should reuse the port
        next_started = await runner.start(session_id="sess-xyz2", profile_volume="vol2")
        assert next_started["http_port"] == http_port

    async def test_concurrent_starts_get_different_ports(self):
        runner = BrowserContainerRunner(node_ip="10.0.0.5", mock=True)
        r1 = await runner.start(session_id="sess-1", profile_volume="vol1")
        r2 = await runner.start(session_id="sess-2", profile_volume="vol2")
        assert r1["http_port"] != r2["http_port"]
        assert r1["epr_lo"] != r2["epr_lo"]
