import pytest
from tinyagentos.computer_use import ComputerUseManager, ComputerUseAction


class TestComputerUseManager:
    def test_initial_state(self):
        cu = ComputerUseManager()
        state = cu.get_state("s1")
        assert state["enabled"] is False
        assert state["mcp_failures"] == 0

    def test_enable_disable(self):
        cu = ComputerUseManager()
        cu.enable("s1")
        assert cu.is_enabled("s1")
        cu.disable("s1")
        assert not cu.is_enabled("s1")

    def test_mcp_failure_escalation(self):
        cu = ComputerUseManager()
        assert cu.record_mcp_failure("s1") is None
        assert cu.record_mcp_failure("s1") is None
        result = cu.record_mcp_failure("s1")
        assert result is not None
        assert result["suggest_computer_use"] is True

    def test_no_escalation_when_enabled(self):
        cu = ComputerUseManager()
        cu.enable("s1")
        cu.record_mcp_failure("s1")
        cu.record_mcp_failure("s1")
        result = cu.record_mcp_failure("s1")
        assert result is None  # Already enabled

    def test_parse_keyboard_action(self):
        cu = ComputerUseManager()
        actions = cu.parse_actions("ACTION: keyboard ctrl+s")
        assert len(actions) == 1
        assert actions[0].action_type == "keyboard"
        assert actions[0].params["keys"] == "ctrl+s"

    def test_parse_mouse_action(self):
        cu = ComputerUseManager()
        actions = cu.parse_actions("ACTION: mouse 500 300")
        assert len(actions) == 1
        assert actions[0].action_type == "mouse"
        assert actions[0].params["x"] == 500

    def test_parse_multiple_actions(self):
        cu = ComputerUseManager()
        response = "ACTION: screenshot\nACTION: mouse 100 200\nACTION: keyboard Return\nACTION: wait 2"
        actions = cu.parse_actions(response)
        assert len(actions) == 4
        assert actions[0].action_type == "screenshot"
        assert actions[3].action_type == "wait"

    def test_clear_session(self):
        cu = ComputerUseManager()
        cu.enable("s1")
        cu.clear_session("s1")
        assert not cu.is_enabled("s1")
