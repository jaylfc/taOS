import pytest
from tinyagentos.containers.backend import ContainerBackend, PtyHandle


def test_pty_handle_has_expected_interface():
    """PtyHandle must expose read, write, resize, and close."""
    methods = {"read", "write", "resize", "close"}
    for method in methods:
        assert hasattr(PtyHandle, method), f"PtyHandle missing method: {method}"


def test_container_backend_spawn_pty_is_abstract():
    """ContainerBackend.spawn_pty must be abstract."""
    import inspect
    assert inspect.isabstract(ContainerBackend) or "spawn_pty" in getattr(
        ContainerBackend, "__abstractmethods__", set()
    )


def test_pty_handle_is_abstract():
    """PtyHandle itself must be abstract."""
    with pytest.raises(TypeError):
        PtyHandle()
