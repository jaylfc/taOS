"""Notification helpers for todo agent actions.

When collaboration lands for TodoStore, this module will mirror
routes/notes.py's _trigger_agent_notifications pattern — iterating agent
members and sending messages to their channels. For now, since TodoStore is
owner-based without agent membership, notifications are a no-op placeholder.
"""

from __future__ import annotations

import logging

from fastapi import Request

logger = logging.getLogger(__name__)


async def _trigger_todo_agent_notifications(
    request: Request,
    doc: dict,
    entry_text: str,
    skip_agent: str | None = None,
) -> None:
    """Placeholder: notify agent members about a new todo item.

    Currently a no-op because TodoStore does not yet have agent membership.
    When collaboration is added to TodoStore (gh #1923 C1 follow-up), this
    function will iterate agent members and send channel messages, skipping
    the agent named in ``skip_agent``.
    """
    # TODO(#1923): wire up when TodoStore gains agent membership / collaboration
    # NOTE: this 32-line module is a deliberate no-op placeholder (see module
    # docstring). It exists so collaboration-trigger wiring has a marked seam
    # without requiring a file-create + import plumbing change later.
    pass
