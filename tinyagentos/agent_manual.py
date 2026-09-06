"""Always-on system context injector.

build_manual returns a markdown string that is prepended as a system-role
message to every agent's context window at chat-dispatch time.  It is a
pure function: no IO, no globals beyond the constant strings below.
"""
from __future__ import annotations

_HEADER = """\
# taOS — operating manual (always-on system context)

You are running inside taOS. You communicate with humans and other agents
via chat channels. Read these primitives carefully — getting them right
is the difference between productive collaboration and silent dropouts.
"""

_SECTION_MENTIONS = """\
## 1. @-mentions are how messages route

This channel uses @-mention routing. A reply with no @-tag reaches NO ONE.

- `@<name>` — addresses one agent by name
- `@all` — addresses every agent in the channel
- `@humans` — pings the human(s); no agent reply

When handing off work, ALWAYS @-tag the next agent in your reply.
Without an @-tag the channel does not route and the conversation stalls.

If you @-tag a teammate and they go silent for two turns, tag them again
with a polite nudge.
"""

_SECTION_TASK_VERBS = """\
## 2. Project task verbs (kanban board)

In a project chat channel, drive the kanban board with these single-line
verbs anywhere in your message:

- `/new "<title>" [@<assignee>]` — create a new task; optional assignee
- `/claim <tsk-id>` — claim a ready task as your own
- `/release <tsk-id>` — release a claimed task back to ready
- `/close <tsk-id> [<note>]` — close a task with optional outcome note

The kanban board updates live. Task ids look like `tsk-abc123` and the
system announces them when created.
"""

_SECTION_LEAD_IS = """\
## 3. Lead vs non-lead

You ARE designated lead on this project. You receive every message in
this channel regardless of @-mentions. Drive task allocation; chase
silent teammates with @-mentions; see the project through to delivery.
"""

_SECTION_NON_LEAD = """\
## 3. Lead vs non-lead

You are NOT a lead on this project. You only see messages where you're
explicitly @-tagged. Always @-tag the next agent or the lead when you
hand off your work.
"""

_SECTION_QUICK_REF = """\
## 4. Quick reference

| You want to… | Do this |
|---|---|
| Address one agent | `@<name>` |
| Address everyone | `@all` |
| Create a task | `/new "<title>" @<name>` |
| Claim a task | `/claim tsk-XXX` |
| Close a task | `/close tsk-XXX <note>` |
| Hand off work | Reply with `@<next-agent>` + deliverable / task id |

Full chat guide: `/docs/chat-guide`.
"""

_DM_STUB = """\
This is a 1-on-1 direct-message channel. Both you and the human always
see every message here. No @-mention routing or kanban verbs apply.
"""


def build_manual(channel: dict, agent_name: str, leads: list[str]) -> str:
    """Return the operating manual for *agent_name* in *channel*.

    Args:
        channel:    The channel dict (needs ``type``, ``settings``,
                    ``project_id`` keys — missing keys are handled safely).
        agent_name: Recipient agent's slug/name.
        leads:      List of lead agent names from ``channel.settings.leads``.

    Returns:
        A markdown string suitable for a ``role: "system"`` message.
    """
    channel_type = channel.get("type") or ""
    project_id = channel.get("project_id")
    settings = channel.get("settings") or {}
    is_a2a = settings.get("kind") == "a2a"

    # DM: header + stub only.
    if channel_type == "dm":
        return _HEADER + _DM_STUB

    # Group / topic channels.
    parts = [_HEADER]

    # Section 1: @-mention routing (group and topic channels).
    if channel_type in ("group", "topic"):
        parts.append(_SECTION_MENTIONS)

    # Sections 2 + 3: project a2a only.
    if project_id and is_a2a:
        parts.append(_SECTION_TASK_VERBS)
        if agent_name in leads:
            parts.append(_SECTION_LEAD_IS)
        else:
            parts.append(_SECTION_NON_LEAD)

    # Section 4: always for non-DM.
    parts.append(_SECTION_QUICK_REF)

    return "\n".join(parts)
