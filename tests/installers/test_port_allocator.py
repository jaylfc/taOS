"""Tests for host-port allocation: every handed-out port is probed free."""
import socket

from tinyagentos.installers.docker_installer import DockerInstaller
from tinyagentos.installers.lxc_installer import LXCInstaller
from tinyagentos.installers.port_allocator import (
    RESERVED_PORTS,
    _POOL_END,
    _POOL_START,
    allocate_host_port,
)


class TestReservedPorts:
    def test_legacy_litellm_port_reserved(self):
        assert 4000 in RESERVED_PORTS

    def test_qmd_port_reserved(self):
        assert 7832 in RESERVED_PORTS

    def test_rkllama_port_reserved(self):
        assert 7833 in RESERVED_PORTS

    def test_litellm_new_host_port_reserved(self):
        assert 7834 in RESERVED_PORTS

    def test_llamacpp_port_reserved(self):
        assert 7835 in RESERVED_PORTS

    def test_hailo_ollama_port_reserved(self):
        """7836 is the hailo-ollama NPU backend; apps must never be allocated it."""
        assert 7836 in RESERVED_PORTS


class TestAllocateHostPort:
    def test_deterministic_for_same_app_id(self):
        assert allocate_host_port("searxng") == allocate_host_port("searxng")

    def test_within_pool_and_not_reserved(self):
        port = allocate_host_port("some-app")
        assert _POOL_START <= port < _POOL_END
        assert port not in RESERVED_PORTS

    def test_never_allocates_hailo_ollama_port(self):
        """7836 must stay reserved so no app host-port can squat hailo-ollama."""
        assert 7836 in RESERVED_PORTS
        for i in range(50):
            port = allocate_host_port(f"hailo-port-guard-{i}")
            assert port != 7836
            assert port not in RESERVED_PORTS

    def test_walks_past_a_bound_port(self):
        preferred = allocate_host_port("walk-test-app")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", preferred))
            s.listen(1)
            moved = allocate_host_port("walk-test-app")
        assert moved != preferred
        assert _POOL_START <= moved < _POOL_END

    def test_exclude_skips_unbound_claims(self):
        first = allocate_host_port("multi-port-app")
        second = allocate_host_port("multi-port-app", exclude={first})
        assert second != first


class TestDockerComposeMultiPort:
    def test_multi_port_app_gets_distinct_probed_ports(self, tmp_path):
        installer = DockerInstaller.__new__(DockerInstaller)
        installer.apps_dir = tmp_path
        compose, host_port = installer._generate_compose(
            "multi-port-compose-app",
            {"image": "example/image:latest", "requires": {"ports": [8080, 9090, 7070]}},
        )
        mappings = compose["services"]["multi-port-compose-app"]["ports"]
        host_side = [int(m.split(":")[0]) for m in mappings]
        container_side = [int(m.split(":")[1]) for m in mappings]

        assert container_side == [8080, 9090, 7070]
        assert len(set(host_side)) == 3
        assert host_port == host_side[0]
        for hp in host_side:
            assert _POOL_START <= hp < _POOL_END
            assert hp not in RESERVED_PORTS

    def test_extra_port_not_blindly_consecutive_when_taken(self, tmp_path):
        installer = DockerInstaller.__new__(DockerInstaller)
        installer.apps_dir = tmp_path
        app_id = "occupied-second-port-app"
        first = allocate_host_port(app_id)
        # Whatever the second port's deterministic seed resolves to, occupy
        # the naive `first + 1` slot; the mapping must still be conflict-free.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", first + 1))
            s.listen(1)
            compose, _ = installer._generate_compose(
                app_id,
                {"image": "example/image:latest", "requires": {"ports": [8080, 9090]}},
            )
            host_side = [
                int(m.split(":")[0])
                for m in compose["services"][app_id]["ports"]
            ]
        assert first + 1 not in host_side
        assert len(set(host_side)) == 2


class TestLxcUsesCentralizedAllocator:
    """LXCInstaller must route through the centralized allocator, not a standalone scanner."""

    def test_no_standalone_find_free_port(self):
        """_find_free_port must not exist on the module (removed in favour of allocate_host_port)."""
        import tinyagentos.installers.lxc_installer as mod
        assert not hasattr(mod, "_find_free_port"), (
            "lxc_installer must not carry its own _find_free_port; "
            "use allocate_host_port from port_allocator instead"
        )

    def test_allocate_host_port_imported(self):
        """lxc_installer imports allocate_host_port (not RESERVED_PORTS) from port_allocator."""
        import tinyagentos.installers.lxc_installer as mod
        # The only port-allocation import should be allocate_host_port.
        # RESERVED_PORTS was only used by the removed _find_free_port.
        assert hasattr(mod, "allocate_host_port"), (
            "lxc_installer must import allocate_host_port"
        )

    def test_installer_class_available(self):
        """LXCInstaller is importable (sanity check — no import errors from the refactor)."""
        assert LXCInstaller is not None
