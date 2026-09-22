import importlib.util
import os
import sys
from pathlib import Path

PROBE_DIR = Path(__file__).resolve().parent.parent / "docs" / "design" / "probes"


def load_probe(name: str):
    path = PROBE_DIR / name
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_probe4_appid_stable():
    mod = load_probe("probe4_detach_reattach_appid.py")
    assert mod.appid_stable(1, 2) is False
    assert mod.appid_stable(1, 1) is True


def test_probe5_viewport_moved():
    mod = load_probe("probe5_scroll_viewport.py")
    assert mod.viewport_moved(["a", "b"], ["a", "b"]) is False
    assert mod.viewport_moved(["a", "b"], ["b", "a"]) is True


def test_probe3_ansi_count():
    mod = load_probe("probe3_frame_grid_readback.py")
    assert mod.ansi_count(["a", "\x1b"]) == 1
    assert mod.ansi_count(["a", "b"]) == 0


def test_probe1_input_echoed():
    mod = load_probe("probe1_socket_enumerate_spawn.py")
    assert mod.input_echoed(["got:hello"], "got:hello") is True
    assert mod.input_echoed(["goodbye"], "got:hello") is False


def test_probe2_input_echoed():
    mod = load_probe("probe2_input_bytes_typing.py")
    assert mod.input_echoed(["got:hello"], "got:hello") is True


def test_probe1_first_match_over_window():
    mod = load_probe("probe1_socket_enumerate_spawn.py")
    assert mod.first_match([["hello"], ["got:hello"]], lambda lines: mod.input_echoed(lines, "got:hello")) == ["got:hello"]
    assert mod.first_match([["hello"]], lambda lines: mod.input_echoed(lines, "got:hello")) is None


def test_probe3_first_match_over_window():
    mod = load_probe("probe3_frame_grid_readback.py")
    assert mod.first_match([[""], ["test"]], lambda lines: any("test" in l for l in lines)) == ["test"]


def test_input_probes_send_newline():
    for name in (
        "probe1_socket_enumerate_spawn.py",
        "probe2_input_bytes_typing.py",
        "probe3_frame_grid_readback.py",
    ):
        path = PROBE_DIR / name
        source = path.read_text()
        assert 'b"hello\\n"' in source, f"{name} missing b\"hello\\n\""
