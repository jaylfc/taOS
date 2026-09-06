import pytest


def test_docker_spawn_pty_raises_not_implemented():
    from tinyagentos.containers.docker import DockerBackend
    backend = DockerBackend()
    with pytest.raises(NotImplementedError, match="docker spawn_pty not yet implemented"):
        backend.spawn_pty("any-agent")


def test_apple_spawn_pty_raises_not_implemented():
    try:
        from tinyagentos.containers.apple_backend import AppleContainerBackend
    except ImportError:
        pytest.skip("apple_backend not present on this platform")
    backend = AppleContainerBackend()
    with pytest.raises(NotImplementedError):
        backend.spawn_pty("any-agent")
