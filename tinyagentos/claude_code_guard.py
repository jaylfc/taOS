"""PreToolUse hook for the Claude Code harness of the system taOS Agent.

Claude Code runs this script before every shell tool call (wired by the
settings file :mod:`tinyagentos.claude_code_runtime` passes with ``--settings``).
It refuses ``git push`` and any ``gh`` call so the only route from the agent
to GitHub is the gated taOS endpoint. Exit 2 blocks the call and hands stderr
back to the model; unreadable input is refused too (fail closed).

Run as a plain script (``python claude_code_guard.py``): stdlib only, so it
never imports taOS.
"""
from __future__ import annotations

import json
import re
import sys

REFUSAL = ("Refused by taOS: the agent may not run `git push` or `gh` from the shell. "
           "GitHub writes go through the taOS tool, which asks the user to confirm.")

# Word edges that also treat path separators as boundaries, so `/usr/bin/gh`
# and `bash -c 'git push'` match while `ghost` or `git-pushed` do not.
_GH = re.compile(r"(?<![\w.-])gh(?![\w.-])")
_GIT_PUSH = re.compile(r"(?<![\w.-])git(?![\w.-])[^;&|\n]*?(?<![\w.-])push(?![\w.-])")


def refusal(payload: object) -> str | None:
    """The refusal message for one PreToolUse payload, or None to allow it."""
    if not isinstance(payload, dict):
        return REFUSAL
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return REFUSAL
    if _GH.search(command) or _GIT_PUSH.search(command):
        return REFUSAL
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        payload = None
    message = refusal(payload)
    if message is None:
        return 0
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
