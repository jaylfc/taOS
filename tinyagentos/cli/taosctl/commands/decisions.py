"""taosctl decisions -- the human-in-the-loop decision inbox.

Agents post the choices they need from the user (single/multi select, free text,
approve/deny), list what is pending, record an answer, and trace the L1
supersession lineage of a decision. Thin wrapper over the /api/decisions routes;
@taOS-dev uses `list --status pending` to poll for answers between turns.
"""
from __future__ import annotations

import json
from urllib.parse import quote

from ..argtypes import positive_int

NOUN = "decisions"

# Mirrors tinyagentos.decisions.decision_store; kept inline so the CLI stays
# free of server imports (matching the other command groups).
_TYPES = ("single_select", "multi_select", "free_text", "approve_deny")
_PRIORITIES = ("normal", "blocking")
_STATUSES = ("pending", "answered", "expired", "superseded")


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Post, list, answer, and trace decisions")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List decisions (filter by status/project)")
    lp.add_argument("--status", choices=list(_STATUSES), help="Filter by status")
    lp.add_argument("--project", dest="project_id", help="Filter by project id")
    lp.add_argument("--limit", type=positive_int, default=200, help="Max rows (default 200)")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one decision by id")
    gp.add_argument("decision_id", help="Decision id")
    gp.set_defaults(func=_get)

    hp = verbs.add_parser("history", help="Show the supersession lineage (oldest first)")
    hp.add_argument("decision_id", help="Decision id")
    hp.set_defaults(func=_history)

    ap = verbs.add_parser("answer", help="Record an answer for a decision")
    ap.add_argument("decision_id", help="Decision id")
    ap.add_argument("--value", required=True,
                    help="Answer value. For multi_select pass a JSON array, "
                         'e.g. \'["a","b"]\'')
    ap.add_argument("--answered-by", dest="answered_by",
                    help="Who answered (defaults to the caller)")
    ap.set_defaults(func=_answer)

    pp = verbs.add_parser("post", help="Post a new decision to the inbox")
    pp.add_argument("--from-agent", dest="from_agent", required=True,
                    help="Asking agent, e.g. @taOS-dev")
    pp.add_argument("--question", required=True, help="The decision question")
    pp.add_argument("--type", required=True, choices=list(_TYPES), help="Decision type")
    pp.add_argument("--context", default="", help="Supporting detail / the why")
    pp.add_argument("--priority", choices=list(_PRIORITIES), default="normal",
                    help="normal | blocking")
    pp.add_argument("--project", dest="project_id", help="Project id this decision is scoped to")
    pp.add_argument("--deadline", type=float, help="Optional deadline (epoch seconds)")
    pp.add_argument("--parent", dest="parent_decision_id",
                    help="Parent decision id this one supersedes (L1)")
    pp.add_argument("--options-json", dest="options_json",
                    help="Options as a JSON array of "
                         "{label,value,recommended,rationale} (select types)")
    pp.add_argument("--checkpoint-ref", dest="checkpoint_ref",
                    help="State pointer the decision is made against (e.g. a git commit)")
    pp.add_argument("--timeline-id", dest="timeline_id", help="Timeline/branch this belongs to")
    pp.set_defaults(func=_post)


def _list(args, client):
    params = {"limit": args.limit}
    if args.status:
        params["status"] = args.status
    if args.project_id:
        params["project_id"] = args.project_id
    return client.get("/api/decisions", params=params)


def _get(args, client):
    return client.get(f"/api/decisions/{quote(str(args.decision_id), safe='')}")


def _history(args, client):
    return client.get(f"/api/decisions/{quote(str(args.decision_id), safe='')}/history")


def _answer(args, client):
    value = _coerce_value(args.value)
    body = {"value": value}
    if args.answered_by:
        body["answered_by"] = args.answered_by
    return client.post(
        f"/api/decisions/{quote(str(args.decision_id), safe='')}/answer", body=body
    )


def _post(args, client):
    options = json.loads(args.options_json) if args.options_json else []
    body = {
        "from_agent": args.from_agent,
        "question": args.question,
        "type": args.type,
        "options": options,
        "context": args.context,
        "priority": args.priority,
    }
    for key, val in (
        ("project_id", args.project_id),
        ("deadline", args.deadline),
        ("parent_decision_id", args.parent_decision_id),
        ("checkpoint_ref", args.checkpoint_ref),
        ("timeline_id", args.timeline_id),
    ):
        if val is not None:
            body[key] = val
    return client.post("/api/decisions", body=body)


def _coerce_value(raw: str):
    """A multi_select answer is a JSON array; everything else is the raw string.
    Parse only when the value looks like JSON so a plain string answer that
    happens to contain a bracket is not mangled."""
    text = raw.strip()
    if text[:1] in ("[", "{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return raw
    return raw
