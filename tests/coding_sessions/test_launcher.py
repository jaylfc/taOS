"""Unit tests for the coding-session tmux launcher (no real tmux needed)."""
import pytest

from tinyagentos.coding_sessions.launcher import CodingSessionLauncher, tmux_name


class FakeRunner:
    """Records argv and returns a queued (rc, out, err) per call."""

    def __init__(self, results=None):
        self.calls = []
        self._results = list(results or [])

    def __call__(self, argv):
        self.calls.append(argv)
        return self._results.pop(0) if self._results else (0, "", "")


def test_start_host_folder_issues_tmux_new_session(tmp_path):
    runner = FakeRunner([(0, "", "")])
    launcher = CodingSessionLauncher(run=runner)
    name = launcher.start_host_folder("cs-abc", str(tmp_path), "opencode")
    assert name == "taos-cs-cs-abc" == tmux_name("cs-abc")
    assert runner.calls[0] == [
        "tmux", "new-session", "-d", "-s", "taos-cs-cs-abc",
        "-c", str(tmp_path), "opencode",
    ]


def test_start_unknown_cli_raises():
    launcher = CodingSessionLauncher(run=FakeRunner())
    with pytest.raises(ValueError):
        launcher.start_host_folder("cs-x", "/repo", "nope")


def test_start_missing_workdir_raises_filenotfound(tmp_path):
    # tmux returns 0 even for a bad workdir, so the launcher validates it itself.
    launcher = CodingSessionLauncher(run=FakeRunner([(0, "", "")]))
    with pytest.raises(FileNotFoundError):
        launcher.start_host_folder("cs-x", str(tmp_path / "nope"), "opencode")


def test_start_tmux_failure_raises_runtimeerror(tmp_path):
    runner = FakeRunner([(1, "", "tmux server error")])
    launcher = CodingSessionLauncher(run=runner)
    with pytest.raises(RuntimeError) as exc:
        launcher.start_host_folder("cs-x", str(tmp_path), "opencode")
    assert "tmux server error" in str(exc.value)


def test_is_running_maps_has_session_returncode():
    assert CodingSessionLauncher(run=FakeRunner([(0, "", "")])).is_running("cs-x") is True
    assert CodingSessionLauncher(run=FakeRunner([(1, "", "")])).is_running("cs-x") is False


def test_stop_issues_kill_session():
    runner = FakeRunner([(0, "", "")])
    CodingSessionLauncher(run=runner).stop("cs-abc")
    assert runner.calls[0] == ["tmux", "kill-session", "-t", "taos-cs-cs-abc"]


def test_capture_returns_output_or_empty():
    assert CodingSessionLauncher(run=FakeRunner([(0, "hello\n", "")])).capture("cs-x") == "hello\n"
    assert CodingSessionLauncher(run=FakeRunner([(1, "", "gone")])).capture("cs-x") == ""


def test_send_input_types_and_enters():
    runner = FakeRunner([(0, "", "")])
    CodingSessionLauncher(run=runner).send_input("cs-abc", "yes")
    assert runner.calls[0] == ["tmux", "send-keys", "-t", "taos-cs-cs-abc", "yes", "Enter"]
