"""Computer use manager — coordinates vision + action loops for app control."""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ComputerUseAction:
    action_type: str  # "keyboard", "mouse", "type", "screenshot", "wait"
    params: dict
    description: str = ""


class ComputerUseManager:
    """Manages computer-use sessions — screenshot, analyze, act, repeat."""

    MAX_RETRIES = 5
    ESCALATION_THRESHOLD = 3  # suggest enabling after this many MCP failures

    def __init__(self):
        self._session_states: dict[str, dict] = {}

    def get_state(self, session_id: str) -> dict:
        if session_id not in self._session_states:
            self._session_states[session_id] = {
                "enabled": False,
                "mcp_failures": 0,
                "actions_taken": 0,
                "last_screenshot": None,
            }
        return self._session_states[session_id]

    def record_mcp_failure(self, session_id: str) -> dict | None:
        """Record an MCP tool failure. Returns escalation suggestion if threshold reached."""
        state = self.get_state(session_id)
        state["mcp_failures"] += 1
        if state["mcp_failures"] >= self.ESCALATION_THRESHOLD and not state["enabled"]:
            return {
                "suggest_computer_use": True,
                "message": "I've had trouble completing this action through the API. "
                           "Would you like me to try computer use to see what's happening on screen?",
                "failures": state["mcp_failures"],
            }
        return None

    def enable(self, session_id: str) -> None:
        self.get_state(session_id)["enabled"] = True

    def disable(self, session_id: str) -> None:
        state = self.get_state(session_id)
        state["enabled"] = False
        state["mcp_failures"] = 0

    def is_enabled(self, session_id: str) -> bool:
        return self.get_state(session_id)["enabled"]

    def record_action(self, session_id: str) -> None:
        self.get_state(session_id)["actions_taken"] += 1

    def clear_session(self, session_id: str) -> None:
        self._session_states.pop(session_id, None)

    def parse_actions(self, llm_response: str) -> list[ComputerUseAction]:
        """Parse LLM response into structured actions.

        Expected format from LLM:
        ACTION: keyboard ctrl+s
        ACTION: mouse 500 300
        ACTION: type hello world
        ACTION: screenshot
        ACTION: wait 2
        """
        actions = []
        for line in llm_response.strip().split("\n"):
            line = line.strip()
            if not line.upper().startswith("ACTION:"):
                continue
            parts = line[7:].strip().split(None, 1)
            if not parts:
                continue
            action_type = parts[0].lower()
            param_str = parts[1] if len(parts) > 1 else ""

            if action_type == "keyboard":
                actions.append(ComputerUseAction("keyboard", {"keys": param_str}, f"Press {param_str}"))
            elif action_type == "mouse":
                coords = param_str.split()
                if len(coords) >= 2:
                    actions.append(ComputerUseAction("mouse", {"x": int(coords[0]), "y": int(coords[1])}, f"Click at {coords[0]},{coords[1]}"))
            elif action_type == "type":
                actions.append(ComputerUseAction("type", {"text": param_str}, f"Type: {param_str}"))
            elif action_type == "screenshot":
                actions.append(ComputerUseAction("screenshot", {}, "Take screenshot"))
            elif action_type == "wait":
                try:
                    seconds = float(param_str) if param_str else 1
                except ValueError:
                    seconds = 1
                actions.append(ComputerUseAction("wait", {"seconds": seconds}, f"Wait {seconds}s"))

        return actions
