"""Per-user shell allow/deny profile for the taOS Agent's Claude Code harness.

The PreToolUse hook (:mod:`tinyagentos.claude_code_guard`) refuses any shell
command the chatting user has not allowed, and queues the undecided ones as
asks. After the turn, :func:`raise_shell_asks` turns each new ask into an
approve/deny Decision addressed to that user, which the taOS Agent chat also
shows inline; answering it either way (the ``shell_command`` applier in
:mod:`tinyagentos.routes.decisions`) writes an allow or deny rule here.

A rule is a program (``curl``), or a program plus its subcommand for the tools
in :data:`tinyagentos.claude_code_guard.SUBCOMMAND_PROGRAMS` (``npm test``,
``git status``); a bare-program rule covers every subcommand. Rules are
listed, changed and revoked from the taOS Agent settings
(``/api/taos-agent/shell-profile``).

Stored as a desktop-settings preference blob per user:
``{"allow": [...], "deny": [...], "asked": {rule: decision_id}}``. A new
profile starts from :data:`DEFAULT_ALLOW`; after that the user's own list wins,
so a revoked default stays revoked.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

NAMESPACE = "taos_agent_shell"
EFFECTS = ("allow", "deny")
DECISION_KIND = "shell_command"
SERVER_RAISED_KEY = "_server_raised"
AGENT_NAME = "taos-agent"

# Read-only commands a new profile allows without asking.
DEFAULT_ALLOW = ("cd", "pwd", "ls", "cat", "head", "tail", "wc", "grep", "echo",
                 "printf", "sort", "uniq", "true", "false",
                 "git status", "git log", "git diff", "git show")

_RULE = re.compile(r"[A-Za-z0-9._+][A-Za-z0-9._+-]{0,63}( [A-Za-z0-9][A-Za-z0-9._:+-]{0,63})?")
# ``git -c`` (config overrides) is the one rule whose second word is an option.
_EXTRA_RULES = frozenset({"git -c"})


def valid_program(program: str) -> bool:
    """A rule key: a program name, optionally followed by one subcommand."""
    if program in _EXTRA_RULES:
        return True
    return bool(_RULE.fullmatch(program or "")) and program not in (".", "..")


def _normalise(raw: dict | None) -> dict:
    if not raw or "allow" not in raw:
        return {"allow": list(DEFAULT_ALLOW), "deny": [], "asked": {}}
    asked = raw.get("asked")
    return {
        "allow": sorted({p for p in raw.get("allow") or () if isinstance(p, str)}),
        "deny": sorted({p for p in raw.get("deny") or () if isinstance(p, str)}),
        "asked": dict(asked) if isinstance(asked, dict) else {},
    }


async def load_profile(store, user_id: str) -> dict:
    return _normalise(await store.get_preference(user_id, NAMESPACE))


async def _save(store, user_id: str, profile: dict) -> dict:
    profile = _normalise(profile)
    await store.save_preference(user_id, NAMESPACE, profile)
    return profile


async def rules_for(store, user_id: str) -> dict:
    """The allow/deny lists the hook reads for one turn."""
    profile = await load_profile(store, user_id)
    return {"allow": profile["allow"], "deny": profile["deny"]}


async def set_rule(store, user_id: str, program: str, effect: str) -> dict:
    if effect not in EFFECTS:
        raise ValueError(f"invalid effect: {effect!r}")
    if not valid_program(program):
        raise ValueError(f"invalid program: {program!r}")
    profile = await load_profile(store, user_id)
    other = "deny" if effect == "allow" else "allow"
    profile[other] = [p for p in profile[other] if p != program]
    profile[effect] = [*profile[effect], program]
    profile["asked"].pop(program, None)
    return await _save(store, user_id, profile)


async def revoke(store, user_id: str, program: str) -> dict | None:
    """Remove the rule for ``program`` so the next use asks again; None when
    there was no rule."""
    profile = await load_profile(store, user_id)
    if program not in profile["allow"] and program not in profile["deny"]:
        return None
    profile["allow"] = [p for p in profile["allow"] if p != program]
    profile["deny"] = [p for p in profile["deny"] if p != program]
    return await _save(store, user_id, profile)


async def raise_shell_asks(app_state, harness, user_id: str) -> list[dict]:
    """Turn the asks the hook queued during a turn into one approve/deny
    Decision per new rule for ``user_id``. Rules already decided or already
    pending are skipped. Returns ``{"program", "decision_id", "command"}``
    for each new ask, for the chat to show inline."""
    asks = harness.take_shell_asks()
    if not asks:
        return []
    store = getattr(app_state, "desktop_settings", None)
    decisions = getattr(app_state, "decision_store", None)
    if store is None or decisions is None:
        return []
    profile = await load_profile(store, user_id)
    # An ask whose decision is gone or no longer pending (withdrawn, expired)
    # may be asked again.
    for program, decision_id in list(profile["asked"].items()):
        try:
            existing = await decisions.get(decision_id)
        except Exception:  # noqa: BLE001 - an unreadable ask is asked again
            existing = None
        if existing is None or existing.get("status") != "pending":
            profile["asked"].pop(program)
    examples: dict[str, str] = {}
    for ask in asks:
        program = ask["program"]
        if (valid_program(program) and program not in profile["allow"]
                and program not in profile["deny"] and program not in profile["asked"]):
            examples.setdefault(program, ask["command"])
    asked: list[dict] = []
    for program, command in examples.items():
        try:
            d = await decisions.create(
                from_agent=AGENT_NAME,
                question=f"Let the taOS Agent run `{program}` in its shell?",
                type="approve_deny",
                context=(f"The agent tried to run: {command[:500]}\n"
                         f"Approve allows every `{program}` command; deny refuses it. "
                         "Change or revoke this later in Agents > taOS Agent > Shell."),
                user_id=user_id,
                metadata={SERVER_RAISED_KEY: True, "kind": DECISION_KIND,
                          "program": program, "user_id": user_id},
            )
        except Exception:
            logger.warning("shell profile: could not raise a decision for %s", program, exc_info=True)
            continue
        profile["asked"][program] = d["id"]
        asked.append({"program": program, "decision_id": d["id"], "command": command[:500]})
    await _save(store, user_id, profile)
    return asked
