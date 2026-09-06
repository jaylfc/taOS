"""taosctl frameworks -- inspect and manage agent frameworks.

Reference noun: follows the same shape as agents.py and projects.py.
"""
from __future__ import annotations

NOUN = "frameworks"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage agent frameworks")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List all registered frameworks")
    lp.set_defaults(func=_list)

    # GET /api/frameworks/{id} -- not yet implemented in routes/frameworks.py
    # POST /api/frameworks -- not yet implemented in routes/frameworks.py
    # PATCH /api/frameworks/{id} -- not yet implemented in routes/frameworks.py
    # DELETE /api/frameworks/{id} -- not yet implemented in routes/frameworks.py


def _list(args, client):
    return client.get("/api/frameworks")
