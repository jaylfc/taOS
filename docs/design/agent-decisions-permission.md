# Agent-posted decisions: permission plumbing

## Goal

Let a registered agent (starting with @taOS-dev) raise decisions in the Decisions
app through a scoped, consent-granted permission, instead of only humans being
able to create them. Requesting the permission goes through the existing consent
flow, so the owner approves it once before any agent can post.

## Background (what already exists)

The Decisions app is built end to end: `decisions/decision_store.py`,
`routes/decisions.py` (`POST /api/decisions`, `GET /api/decisions`,
`POST /api/decisions/{id}/answer`, `GET /api/decisions/{id}/history`),
`tools/decision_tools.py`, a `taosctl decisions` CLI, plus `DecisionsApp.tsx`
(all projects) and `ProjectsApp/ProjectDecisions.tsx` (per-project section). It
already unifies interactive notifications, actionable-notification routing, and
the external-agent consent flow into one surface.

The grant and consent machinery is also built: `POST /api/agents/auth-requests`
plus `/approve` and `/deny`, per-agent grants keyed
`UNIQUE(canonical_id, scope, project_id)`, and
`check_agent_scope_for_project(scope, project_id)` in `agent_token_auth.py:151`.

Two gaps stop an agent from posting a decision:

1. There is no decisions scope. `_ALLOWED_SCOPES` in
   `routes/agent_registry.py:90` is `memory_read/write, a2a_send/receive,
   files_read/write, tools_execute, registry_feeds_read, project_tasks,
   canvas_read/write`. Nothing covers decisions.
2. `create_decision` (`routes/decisions.py:123`) is human-only
   (`Depends(current_user)`) and hard-codes `user_id=user.user_id`. There is no
   agent-token path, so even with a grant an agent could not post.

## Design

### 1. Scope vocabulary

Add two scopes to `_ALLOWED_SCOPES`: `decisions_read` and `decisions_write`.
`decisions_write` authorizes posting a decision; `decisions_read` authorizes
listing decisions the agent raised (for its own follow-up). @taOS-dev only needs
`decisions_write` for the immediate goal; `decisions_read` is added in the same
change for symmetry and to avoid a second migration later.

### 2. Grant scoping: global plus per-project

Grants already allow a null `project_id` (global). The owner-approved model is:

- A global grant `(canonical_id, decisions_write, NULL)` authorizes posting
  OS-level decisions, meaning decisions with `project_id = null` (things like a
  house-rule policy, a usage note, a repo-wide question).
- A per-project grant `(canonical_id, decisions_write, <project_id>)` authorizes
  posting decisions attached to that project.

A global grant is not a skeleton key: it authorizes `project_id = null` posts
only, never posts into a specific project. Each project needs its own grant.
This falls out of `check_agent_scope_for_project` matching `project_id` with a
NULL-safe `IS`, so no new lookup logic is needed:

- `body.project_id is None` requires `check_agent_scope_for_project("decisions_write", None)`.
- `body.project_id == X` requires `check_agent_scope_for_project("decisions_write", X)`.

### 3. Route gating

`create_decision` accepts either identity:

- Human `current_user` (unchanged path): `user_id = user.user_id`,
  `from_agent` = whatever the body carries.
- Agent token bearing `decisions_write` for `body.project_id`: authorized via
  `check_agent_scope_for_project`. On this path:
  - `from_agent` is set from the authenticated agent identity, not trusted from
    the body (an agent cannot impersonate another).
  - `user_id` (the human who must decide) is resolved from the target, not the
    caller: for a project decision it is the project owner; for an OS-level
    (null-project) decision it is the instance owner/admin. This is the one new
    resolver the change introduces.
  - `parent_decision_id` supersede is allowed only when the parent was raised by
    the same agent (mirror of the existing owner check).

Detection of which identity is calling reuses the existing auth middleware: a
valid session cookie is a human; a valid agent bearer token is an agent; neither
is 401.

### 4. Consent bootstrap (the recursion)

To obtain the grant, the agent calls the already-built consent primitive
`POST /api/agents/auth-requests` with `scope = decisions_write` and the target
project (or null for the global grant). That endpoint is agent-initiated and
does not itself require `decisions_write`, so it is a clean bootstrap. The owner
receives the consent request as a notification, approves it, and the grant is
minted. Only after that can the agent post through `create_decision`.

### 5. Answer round-trip (unchanged)

`_route_answer_to_agent` in `routes/decisions.py` already posts the answer back
to the A2A bus addressed to `from_agent`. @taOS-dev reads it on its scheduled
sweep. No change is needed here beyond ensuring `from_agent` is the agent's bus
handle so the post-back is addressed correctly.

## Data flow

1. Agent (once granted) calls `POST /api/decisions` with its bearer token,
   `project_id` (or null), question, type, options.
2. Store persists the decision; a notification is emitted to the resolved owner.
3. The decision appears in the Decisions app (all decisions) and, when
   `project_id` is set, in that project's Decisions section.
4. Owner answers from any surface. `answer_decision` records it and
   `_route_answer_to_agent` posts it to the bus.
5. The agent picks up the answer on its next sweep and acts.

## Error handling

- Agent token without a matching grant for the target: 403.
- Agent posting to a `project_id` it is not granted (even with a global grant):
  403.
- Agent posting with a `project_id` that does not exist: 400.
- Neither session nor agent token: 401.
- Invalid `type` / `priority` / missing options: existing 400s, unchanged.

## Testing

- Agent with a project grant can post to that project; the decision carries
  `from_agent` = the agent and `user_id` = the project owner.
- Agent with only a global grant can post an OS-level (null-project) decision but
  gets 403 posting into a specific project.
- Agent with no grant gets 403.
- Human `current_user` path is unchanged (regression).
- `from_agent` on the agent path cannot be spoofed from the body.
- Answer to an agent-raised decision routes back to the agent handle.
- `POST /api/agents/auth-requests` for `decisions_write` mints the grant on
  approval, and the agent can then post (integration).

## Out of scope

- The ACP / SSE bridge that surfaces a live agent session as a Messages channel
  is a separate design.
- L2 fork-and-replay of decisions (the reserved `checkpoint_ref` /
  `parent_decision_id` / `timeline_id` fields) stays out; only L1 supersede,
  which already exists, is touched.
- Auto-creating a dedicated "taOS development" project. Whether OS-dev decisions
  live under a project or stay null-project is answered by the global grant; a
  taOS-dev project can be added later without changing this plumbing.
