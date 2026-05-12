"""Permission-scope matching for agent API tokens.

Scope is a list of glob patterns matching the noun.verb structure:
  ["*"]            — full access (default for issue() if no scope supplied)
  ["agents.*"]     — all agents.* verbs, no other namespaces
  ["agents.list"]  — only the list verb
  ["agents.token.*"] — both agents.token.issue and agents.token.revoke

`scope_matches` is plain boolean glob matching; the decorator
`require_scope(action)` is applied per-route and returns the
standardised 403 with a fix + doc_url when the bearer's scope doesn't
cover the action. Session-cookie callers (no `request.state.token_scope`)
are not gated — scope is a delegated-access mechanism for tokens.
"""
from __future__ import annotations

import functools
import fnmatch
from typing import Callable

from fastapi import Request

from tinyagentos.errors import error_response


def scope_matches(scope: list[str], action: str) -> bool:
    """True if any pattern in `scope` (fnmatch-style) matches `action`."""
    for pattern in scope:
        if fnmatch.fnmatchcase(action, pattern):
            return True
    return False


def require_scope(action: str) -> Callable:
    """Decorator: returns the standardised 403 when the bearer-token scope
    doesn't cover `action`. No-op when the request has no token scope
    (session-cookie auth or other non-bearer paths)."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(request: Request, *args, **kwargs):
            scope = getattr(request.state, "token_scope", None)
            if scope is None:
                # No bearer token in play — session-cookie / other auth handles it.
                return await fn(request, *args, **kwargs)
            if not scope_matches(scope, action):
                return error_response(
                    status_code=403,
                    error="scope_denied",
                    detail=f"Token scope does not cover {action!r}.",
                    fix=(
                        "Reissue the token with a wider scope (e.g. ['*'] for full "
                        "access) via POST /api/agents/{name}/token/issue, or have "
                        "the operator widen the agent's permissions."
                    ),
                    doc_url="/docs/agents/concepts/permissions",
                )
            return await fn(request, *args, **kwargs)
        return wrapper
    return decorator
