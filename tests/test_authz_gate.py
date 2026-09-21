"""Gate test: every mutating endpoint on a global router must carry an
admin/owner authorization dependency so a plain invited-member session can
never reach the handler.

This test introspects the route modules directly (no HTTP round-trip) so it
runs in a fraction of a second and catches the absence of the dependency at
import time, before any test server starts.

Mutating methods: POST, PUT, PATCH, DELETE.

Allowed dependency callables (by __name__):
  - require_admin
  - require_owner_or_admin
  - require_agent_owner_or_admin
  - _require_admin_or_local_token
  - _require_admin_or_loopback
  - _require_admin  (routes.auth and routes.project_invites variants)

For owner-scoped routes where the dependency is enforced inside the handler
body (e.g. agents.py uses require_agent_owner_or_admin as a regular call
because the path param name differs from the dependency signature), the
handler source is inspected for the authz call.

Routes exempt from the gate:
  - /api/agents/me/model  (agent self-service, authed by LiteLLM bearer)
  - Any path under /api/agents/registry/  (registry JWT gated in handler)
  - Any path under /api/agents/auth-requests/  (consent loop, admin-gated per-route)
  - /api/a2a/bus/*  (registry JWT or HMAC gated in handler)
  - /api/a2a/gpu/*  (registry JWT gated in handler)
  - /api/observatory/*  (registry JWT gated in handler)
  - /api/containers/requests/*  (registry JWT gated in handler)
  - /api/projects/*  (project-scoped, owner-gated in handler)
"""
from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path

import pytest

# Modules whose mutating endpoints must carry an authz dependency.
_TARGET_MODULES = [
    "tinyagentos.routes.agents",
    "tinyagentos.routes.account_proxy",
    "tinyagentos.routes.chat_admin",
    "tinyagentos.routes.providers",
    "tinyagentos.routes.models",
    "tinyagentos.routes.github_oauth",
    "tinyagentos.routes.memory_management",
    "tinyagentos.routes.knowledge_graph",
    "tinyagentos.routes.cluster_migrate",
    "tinyagentos.routes.store",
    "tinyagentos.routes.tasks",
    "tinyagentos.routes.desktop",
    "tinyagentos.routes.agent_browsers",
]

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_ALLOWED_DEP_NAMES = {
    "require_admin",
    "require_owner_or_admin",
    "require_agent_owner_or_admin",
    "_require_admin_or_local_token",
    "_require_admin_or_loopback",
    "_require_admin",
}

_BODY_AUTHZ_CALLS = re.compile(
    r"require_(agent_)?owner_or_admin\(",
)

# Path prefixes that are exempt because their authz is enforced inside the
# handler (registry JWT, project owner, agent bearer, etc.) rather than via
# a FastAPI dependency on the route itself.
_EXEMPT_PREFIXES = (
    "/api/agents/me/",
    "/api/agents/registry/",
    "/api/agents/auth-requests/",
    "/api/a2a/bus/",
    "/api/a2a/gpu/",
    "/api/observatory/",
    "/api/containers/requests/",
    "/api/container-requests/",
    "/api/projects/",
    "/api/decisions/",
    "/api/devices/",
    "/api/agents/containers/quota",
    "/api/agent-model-keys",
    "/api/agent-model-keys/",
    "/v1/models",
    "/v1/chat/completions",
)


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_PREFIXES:
        return True
    for prefix in _EXEMPT_PREFIXES:
        if prefix.endswith("/"):
            if path.startswith(prefix):
                return True
        elif path.startswith(prefix):
            return True
    return False


def _collect_dep_names(route) -> set[str]:
    """Return the set of dependency callable __name__ values for *route*."""
    names: set[str] = set()
    for dep in getattr(route.dependant, "dependencies", []):
        call = getattr(dep, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
    return names


def _router_has_global_admin(router) -> bool:
    """True when the router itself declares a global admin dependency."""
    for dep in getattr(router, "dependencies", []):
        call = getattr(dep, "call", None)
        if call is not None and getattr(call, "__name__", "") in _ALLOWED_DEP_NAMES:
            return True
    return False


def _handler_has_body_authz(route) -> bool:
    """True when the route's endpoint source contains an owner/admin authz call."""
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return False
    try:
        source = inspect.getsource(endpoint)
    except (OSError, TypeError):
        return False
    return bool(_BODY_AUTHZ_CALLS.search(source))


@pytest.mark.parametrize("module_name", _TARGET_MODULES)
def test_mutating_endpoints_have_authz_dependency(module_name: str):
    mod = importlib.import_module(module_name)
    router = getattr(mod, "router", None)
    assert router is not None, f"{module_name} has no `router` attribute"

    router_has_admin = _router_has_global_admin(router)
    failures: list[str] = []

    for route in getattr(router, "routes", []):
        methods = {m.upper() for m in getattr(route, "methods", set())}
        mutating = methods & _MUTATING_METHODS
        if not mutating:
            continue
        path = getattr(route, "path", "") or ""
        if _is_exempt(path):
            continue
        dep_names = _collect_dep_names(route)
        has_dep = bool(dep_names & _ALLOWED_DEP_NAMES)
        has_body = _handler_has_body_authz(route)
        if not has_dep and not has_body and not router_has_admin:
            failures.append(
                f"  {module_name}: {','.join(sorted(mutating))} {path} -- no authz dependency"
            )

    if failures:
        pytest.fail(
            "Mutating endpoints without admin/owner dependency:\n"
            + "\n".join(failures)
        )
