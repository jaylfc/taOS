from __future__ import annotations

from typing import Any

_COMMON_REQUIRED = ("label", "icon", "requires_capability")
_KNOWN_KINDS = {"container-terminal", "tui", "dashboard"}


def validate_shortcuts(entries: list[Any]) -> None:
    """Raise ValueError if any entry in entries has an invalid shape."""
    for i, entry in enumerate(entries):
        prefix = f"shortcuts[{i}]"

        if not isinstance(entry, dict):
            raise ValueError(f"{prefix}: expected dict, got {type(entry).__name__}")

        if "kind" not in entry:
            raise ValueError(f"{prefix}: missing 'kind' field")

        kind = entry["kind"]
        if kind not in _KNOWN_KINDS:
            raise ValueError(
                f"{prefix}: unknown shortcut kind '{kind}'; valid: {sorted(_KNOWN_KINDS)}"
            )

        for field in _COMMON_REQUIRED:
            if field not in entry:
                raise ValueError(f"{prefix}: missing required field '{field}'")

        if kind == "tui":
            if "command" not in entry:
                raise ValueError(f"{prefix}: 'command' is required for kind='tui'")
            if not isinstance(entry["command"], str) or not entry["command"].strip():
                raise ValueError(f"{prefix}: 'command' must be a non-empty string")

        if kind == "dashboard":
            if "port" not in entry:
                raise ValueError(f"{prefix}: 'port' is required for kind='dashboard'")
            if not isinstance(entry["port"], int) or entry["port"] < 1:
                raise ValueError(f"{prefix}: 'port' must be a positive integer")
            if "auth" not in entry:
                raise ValueError(f"{prefix}: 'auth' is required for kind='dashboard'")
            auth = entry["auth"]
            if not isinstance(auth, dict) or "type" not in auth:
                raise ValueError(
                    f"{prefix}: 'auth' must be a dict with a 'type' field"
                )
            if auth["type"] not in ("none", "bearer", "basic"):
                raise ValueError(
                    f"{prefix}: auth.type must be 'none', 'bearer', or 'basic'"
                )
