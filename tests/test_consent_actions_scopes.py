"""The consent picker's "this scope needs a project" list has exactly one home:
the server.

It used to have two -- ``_PROJECT_SCOPES`` here and a ``PROJECT_SCOPES`` Set in
``desktop/src/components/ConsentActions.tsx`` -- and the copy fell six scopes
behind, so approving ``files_write`` (and five others) rendered no project
picker, POSTed no ``project_id``, and got back a 400 the operator could not act
on. Pinning the two lists to each other only narrows the window; the mechanism
that produced the drift is the copy itself.

So this module asserts the durable shape instead:

  * the server PUBLISHES the vocabulary at ``GET /api/agents/scope-vocabulary``,
    and what it publishes is ``_PROJECT_SCOPES`` itself, not a restatement;
  * the route is reachable under that exact path (``/api/agents/{name}`` in
    routes/agents.py would happily swallow it if the routers were reordered);
  * the client holds NO local copy to drift, and does read the published one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tinyagentos.routes.agent_auth_requests import (
    VALID_SCOPES,
    _PROJECT_SCOPES,
)

_CONSENT_ACTIONS = Path("desktop/src/components/ConsentActions.tsx")
_VOCABULARY_PATH = "/api/agents/scope-vocabulary"


@pytest.mark.asyncio
async def test_scope_vocabulary_publishes_the_server_project_scopes(client):
    """The endpoint the picker reads returns the server's own set, verbatim."""
    resp = await client.get(_VOCABULARY_PATH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["project_scopes"] == sorted(_PROJECT_SCOPES)
    assert body["valid_scopes"] == sorted(VALID_SCOPES)
    # Every project scope is a grantable scope; a typo in one list would
    # otherwise publish a scope that can never be granted.
    assert set(body["project_scopes"]) <= set(body["valid_scopes"])


@pytest.mark.asyncio
async def test_scope_vocabulary_is_not_shadowed_by_the_agent_name_route(client):
    """``/api/agents/{name}`` must not capture the vocabulary path.

    Both routes live under ``/api/agents/``; which one wins depends on router
    include order in routes/__init__.py. A 404 (or an agent-shaped body) here
    means a reorder silently took the picker's data source away.
    """
    resp = await client.get(_VOCABULARY_PATH)
    assert resp.status_code == 200, resp.text
    assert "project_scopes" in resp.json()


def test_consent_actions_keeps_no_local_project_scope_list():
    """No second copy of the list may exist in the client."""
    ts = _CONSENT_ACTIONS.read_text()
    stale = re.search(r"PROJECT_SCOPES\s*=\s*new Set\(", ts)
    assert stale is None, (
        "ConsentActions.tsx declares its own PROJECT_SCOPES set again. That copy "
        "is what drifted from the backend _PROJECT_SCOPES and made Approve 400 "
        f"with no remedy; render the picker from {_VOCABULARY_PATH} instead."
    )


def test_consent_actions_reads_the_published_vocabulary():
    """...and it reads the server's, so a new server scope needs no client edit."""
    ts = _CONSENT_ACTIONS.read_text()
    assert _VOCABULARY_PATH in ts, (
        f"ConsentActions.tsx never requests {_VOCABULARY_PATH}; the project "
        "picker is being gated by something other than the server's vocabulary."
    )
