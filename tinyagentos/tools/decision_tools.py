"""Agent tool for the human-in-the-loop Decisions inbox.

Lets an agent ask the user a question (single/multi select, approve/deny, or free
text) that queues in the Decisions app until answered, so the agent can stop
guessing and hand a real choice to the user. This is the programmatic counterpart
to the Decisions app: it calls the same DecisionStore the REST route uses, so the
card appears live in the inbox and fires the same notification.

Answering is asynchronous (the user replies later in the app); this tool only
RAISES the decision and returns its id so the agent can poll or move on.
"""
from __future__ import annotations

from fastapi import Request

from tinyagentos.decisions.decision_store import DECISION_TYPES, PRIORITIES

REQUEST_DECISION_TOOL = {
    "name": "request_decision",
    "description": (
        "Ask the user a question that queues in their Decisions inbox until they "
        "answer, instead of guessing. Use for a real choice you cannot resolve "
        "yourself: 'single_select' / 'multi_select' need an options list; "
        "'approve_deny' is a yes/no; 'free_text' is an open answer. Set priority "
        "'blocking' only when you genuinely cannot proceed without the answer. "
        "Returns a decision_id; the answer arrives later, so poll or move on."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question to ask the user."},
            "type": {
                "type": "string",
                "enum": list(DECISION_TYPES),
                "description": "single_select, multi_select, approve_deny, or free_text.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Choices (required for single_select / multi_select).",
            },
            "context": {"type": "string", "description": "Optional background to help the user decide."},
            "priority": {
                "type": "string",
                "enum": list(PRIORITIES),
                "description": "'normal' (default) or 'blocking' (you cannot proceed without it).",
            },
            "from_agent": {"type": "string", "description": "Your agent name, shown as the asker."},
        },
        "required": ["question", "type"],
    },
}


def _user_id(request: Request) -> str | None:
    return getattr(request.state, "user_id", None) or None


def _normalize_options(raw) -> list[dict]:
    """Coerce options to [{label, value}] with distinct values. Accepts a list of
    strings or of {label, value} dicts; value defaults to the label, and a later
    collision is suffixed so the inbox (which keys on value) keeps each distinct,
    mirroring the create_decision route's dedupe."""
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw or []:
        if isinstance(item, dict):
            label = str(item.get("label", item.get("value", "")))
            base = str(item.get("value", label) or label)
        else:
            label = str(item)
            base = label
        value = base
        n = 2
        while value in seen:
            value = f"{base} ({n})"
            n += 1
        seen.add(value)
        out.append({"label": label, "value": value})
    return out


async def execute_request_decision(args: dict, request: Request) -> dict:
    args = args or {}
    question = args.get("question")
    dtype = args.get("type")
    if not isinstance(question, str) or not question.strip():
        return {"error": "request_decision requires a 'question' string"}
    if dtype not in DECISION_TYPES:
        return {"error": f"type must be one of {DECISION_TYPES}"}
    priority = args.get("priority", "normal")
    if priority not in PRIORITIES:
        return {"error": f"priority must be one of {PRIORITIES}"}
    options = _normalize_options(args.get("options"))
    if dtype in ("single_select", "multi_select") and not options:
        return {"error": "select types require a non-empty 'options' list"}
    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user to ask"}

    from_agent = args.get("from_agent")
    if not isinstance(from_agent, str) or not from_agent.strip():
        from_agent = "@agent"

    store = request.app.state.decision_store
    decision = await store.create(
        from_agent=from_agent,
        question=question,
        type=dtype,
        options=options,
        context=str(args.get("context", "")),
        priority=priority,
        user_id=user_id,
    )

    # Best effort: surface it in the notification bell like the REST route does;
    # a notification failure must not fail the queued decision.
    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        try:
            await notifs.add(
                title="Decision needed",
                message=f"{from_agent} needs a decision: {question[:120]}",
                level="warning" if priority == "blocking" else "info",
                source="decisions",
            )
        except Exception:
            pass

    return {"ok": True, "decision_id": decision["id"], "status": decision["status"]}
