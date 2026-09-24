"""The gateway's ONE auth and model-permission seam.

Every gateway route takes ``caller: GatewayCaller = Depends(gateway_caller)``
and asks ``caller.may_use(model)``; nothing else in the package looks at
credentials. G2 replaces the body of ``gateway_caller`` with scoped keys
(and adds the matching auth-middleware exemption); the routes do not change.

G1 accepts exactly what the auth middleware already authenticated as a
signed-in session or the host's shared local admin token
(``.auth_local_token``), and grants those callers every model. A per-agent
local token is refused (see below). There is NO middleware exemption for
``/api/llm/`` in G1, so an unauthenticated request never reaches this
function; refusing anything else here is defence in depth, so a future
exemption cannot silently turn into open access.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from tinyagentos.llm_gateway.errors import unauthorized

# request.state.via values set by auth_middleware for the two credentials G1 accepts.
_SESSION = "session"
_LOCAL_TOKEN = "local_token"


@dataclass(frozen=True)
class GatewayCaller:
    caller_id: str
    allowed_models: frozenset[str] | None  # None = every model
    kind: str

    def may_use(self, model: str) -> bool:
        return self.allowed_models is None or model in self.allowed_models


def gateway_caller(request: Request) -> GatewayCaller:
    state = request.state
    via = getattr(state, "via", None)
    user_id = getattr(state, "user_id", None)
    if via == _SESSION and user_id:
        return GatewayCaller(caller_id=f"user:{user_id}", allowed_models=None, kind=_SESSION)
    if via == _LOCAL_TOKEN and not getattr(state, "agent_name", None):
        caller_id = f"user:{user_id}" if user_id else "local"
        return GatewayCaller(caller_id=caller_id, allowed_models=None, kind=_LOCAL_TOKEN)
    # A per-agent local token (deployer-minted, bound to request.state.agent_name)
    # also reads as via="local_token", but it is an AGENT's credential, and an
    # agent's models are scoped (its LiteLLM key's allowlist). Granting it every
    # model here would sidestep that scope, so G1 refuses it; G2 gives agents
    # scoped gateway keys.
    raise unauthorized()
