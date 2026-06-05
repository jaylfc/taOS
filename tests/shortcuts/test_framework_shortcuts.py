"""Verify the shortcuts field on each beta framework matches the spec."""
import pytest
from tinyagentos.frameworks import FRAMEWORKS
from tinyagentos.shortcuts.validation import validate_shortcuts


def _shortcuts(name: str) -> list:
    fw = FRAMEWORKS.get(name)
    assert fw is not None, f"Framework '{name}' not in FRAMEWORKS"
    return fw.get("shortcuts", [])


@pytest.mark.parametrize("fw_name", [
    "openclaw", "hermes", "smolagents", "pocketflow", "langroid", "openai-agents-sdk",
])
def test_framework_shortcuts_present(fw_name):
    """Each beta framework declares at least one shortcut entry."""
    assert len(_shortcuts(fw_name)) >= 1, f"{fw_name} has no shortcuts"


def test_openclaw_shortcuts_exact():
    """openclaw has 4 shortcuts: shell, tui x2, dashboard."""
    shortcuts = _shortcuts("openclaw")
    assert len(shortcuts) == 4
    kinds = [s["kind"] for s in shortcuts]
    assert kinds == ["container-terminal", "tui", "tui", "dashboard"]
    dash = shortcuts[3]
    assert dash["port"] == 18789
    assert dash["auth"]["type"] == "bearer"
    assert dash["auth"]["token_source"]["kind"] == "container_file"
    assert dash["auth"]["token_source"]["path"] == "/root/.openclaw/openclaw.json"
    assert dash["auth"]["token_source"]["json_pointer"] == "/gateway/auth/token"


def test_hermes_shortcuts_exact():
    """hermes: shell + 2 tui."""
    shortcuts = _shortcuts("hermes")
    assert len(shortcuts) == 3
    assert shortcuts[0]["kind"] == "container-terminal"
    assert shortcuts[1]["command"] == "hermes --tui"
    assert shortcuts[2]["command"] == "hermes doctor"


def test_smolagents_shortcuts_exact():
    """smolagents: shell + 1 tui."""
    shortcuts = _shortcuts("smolagents")
    assert len(shortcuts) == 2
    assert shortcuts[1]["command"] == "smolagent"


def test_shell_only_frameworks():
    """pocketflow, langroid, openai-agents-sdk: 1 shortcut each (container-terminal)."""
    for fw in ("pocketflow", "langroid", "openai-agents-sdk"):
        shortcuts = _shortcuts(fw)
        assert len(shortcuts) == 1, f"{fw} should have 1 shortcut"
        assert shortcuts[0]["kind"] == "container-terminal"


@pytest.mark.parametrize("fw_name", [
    "openclaw", "hermes", "smolagents", "pocketflow", "langroid", "openai-agents-sdk",
])
def test_all_framework_shortcuts_validate(fw_name):
    """validate_shortcuts() passes without raising for every beta framework."""
    validate_shortcuts(_shortcuts(fw_name))
