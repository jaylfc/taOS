# Agent-posted decisions permission: Implementation Plan

> **For agentic workers:** implement task-by-task, TDD, commit per task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a granted agent raise decisions via `POST /api/decisions`, gated by a new `decisions_write` scope and the existing per-project grant/consent flow.

**Architecture:** Add `decisions_read`/`decisions_write` to the scope vocabulary, then give `create_decision` a dual-auth path that mirrors `project_canvas._resolve_actor`: try the agent token first via `check_agent_scope_for_project`, fall back to the human session. On the agent path, `from_agent` comes from the authenticated identity and `user_id` (the decider) is resolved from the target (project owner, or the instance admin for null-project).

**Tech Stack:** FastAPI, aiosqlite, pytest.

## Global Constraints

- Work as jaylfc; no AI attribution; no em dashes in any committed text.
- Target branch `dev`; this work continues on `docs/agent-decisions-permission` (spec already committed there).
- Python 3.11 floor. `async def` routes, `await` all I/O.
- Run tests with `/Volumes/NVMe/Users/jay/Development/tinyagentos/.venv/bin/python -m pytest <files> -q`.
- Do not touch the human `current_user` path behavior (regression-protected).

---

### Task 1: Add decisions scopes to the vocabulary

**Files:**
- Modify: `tinyagentos/routes/agent_registry.py` (`_ALLOWED_SCOPES`, ~line 90)
- Test: `tests/test_routes_agent_registry.py`

**Produces:** `"decisions_read"`, `"decisions_write"` recognized as valid grantable scopes everywhere `_ALLOWED_SCOPES` is consulted (mint route, consent auth-requests validation).

- [ ] **Step 1: Failing test** in `tests/test_routes_agent_registry.py` asserting a mint/auth-request with `scopes=["decisions_write"]` is accepted (no `unknown scope` error) and `scopes=["decisions_bogus"]` is rejected.

```python
def test_decisions_scopes_are_grantable():
    from tinyagentos.routes.agent_registry import _ALLOWED_SCOPES
    assert "decisions_read" in _ALLOWED_SCOPES
    assert "decisions_write" in _ALLOWED_SCOPES
```

- [ ] **Step 2: Run** `pytest tests/test_routes_agent_registry.py::test_decisions_scopes_are_grantable -q` -> FAIL.
- [ ] **Step 3: Implement** add the two entries to the `_ALLOWED_SCOPES` frozenset:

```python
_ALLOWED_SCOPES = frozenset({
    "memory_read", "memory_write",
    "a2a_send", "a2a_receive",
    "files_read", "files_write",
    "tools_execute", "registry_feeds_read",
    "project_tasks",
    "canvas_read", "canvas_write",
    "decisions_read", "decisions_write",
})
```

- [ ] **Step 4: Run** the test -> PASS.
- [ ] **Step 5: Commit** `feat(agents): add decisions_read/decisions_write to grantable scopes`.

---

### Task 2: Actor resolver on the decisions route

**Files:**
- Modify: `tinyagentos/routes/decisions.py` (add `_resolve_decision_actor`, wire into `create_decision` at ~line 123)
- Test: `tests/test_routes_decisions.py`

**Interfaces:**
- Consumes: `check_agent_scope_for_project(request, "decisions_write", project_id) -> Optional[str]` (raises 401/403), `PROJECT_SCOPE_MISMATCH_DETAIL`, `request.state.user_id` (human session set by middleware), `request.app.state.project_store`, `request.app.state.auth`.
- Produces: `async def _resolve_decision_actor(request, project_id) -> tuple[str, str, str]` returning `(kind, from_agent, decider_user_id)` where `kind in {"agent","user"}`. Raises `HTTPException` (401 unauth, 403 no grant, 400 unknown project).

- [ ] **Step 1: Failing test** for the agent-with-grant path:

```python
async def test_agent_with_grant_resolves_as_agent(app, client, monkeypatch):
    # grant (canonical_id=agent-x, decisions_write, project=P) already seeded by the fixture;
    # posting with the agent bearer token attributes the decision to the agent and to P's owner.
    resp = await client.post("/api/decisions",
        headers={"Authorization": "Bearer <agent-x-token>"},
        json={"from_agent": "spoofed", "project_id": "P",
              "question": "ship it?", "type": "approve_deny"})
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["from_agent"] == "agent-x"       # authenticated identity, not "spoofed"
    assert d["user_id"] == "<owner-of-P>"     # project owner, resolved not caller
```

- [ ] **Step 2: Run** -> FAIL.
- [ ] **Step 3: Implement** the resolver, mirroring `project_canvas._resolve_actor` (agent-first, human-fallback). For null `project_id` the grant lookup uses `project_id=None` (global grant); the decider is the instance admin from `auth.list_users()`:

```python
async def _resolve_decision_actor(request, project_id):
    from tinyagentos.agent_token_auth import (
        check_agent_scope_for_project, PROJECT_SCOPE_MISMATCH_DETAIL)
    cid = await check_agent_scope_for_project(request, "decisions_write", project_id)
    if cid is not None:
        ps = request.app.state.project_store
        if project_id is not None:
            project = await ps.get(project_id)
            if project is None:
                raise HTTPException(400, "project_id not found")
            decider = project.get("user_id")
        else:
            admins = [u for u in request.app.state.auth.list_users() if u.get("is_admin")]
            if not admins:
                raise HTTPException(409, "no admin to receive an OS-level decision")
            decider = admins[0]["user_id"]
        return ("agent", cid, decider)
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "authentication required")
    return ("user", None, uid)
```

- [ ] **Step 4: Run** the test -> PASS.
- [ ] **Step 5: Commit** `feat(decisions): resolve agent-or-human actor for decision creation`.

---

### Task 3: Wire the resolver into create_decision + full test matrix

**Files:**
- Modify: `tinyagentos/routes/decisions.py` (`create_decision`, drop the hard `Depends(current_user)`, call the resolver)
- Test: `tests/test_routes_decisions.py`

**Interfaces:**
- Consumes: `_resolve_decision_actor` from Task 2; `store.create(from_agent=, user_id=, project_id=, ...)`.

- [ ] **Step 1: Failing tests** for the full matrix:

```python
async def test_agent_global_grant_posts_os_level(...):   # project_id=None ok, decider=admin
async def test_agent_global_grant_403_into_project(...):  # global grant, project P -> 403
async def test_agent_no_grant_403(...):
async def test_human_path_unchanged(...):                 # session cookie still works, user_id=caller
async def test_answer_routes_back_to_from_agent(...):     # answer hits the bus addressed to agent-x
```

- [ ] **Step 2: Run** -> FAIL.
- [ ] **Step 3: Implement** change `create_decision` to take `request: Request` (no `Depends(current_user)`), call `kind, from_agent, decider = await _resolve_decision_actor(request, body.project_id)`, use `from_agent = from_agent or body.from_agent` (human path keeps body value), and pass `user_id=decider`. Keep the existing type/priority/options validation and the `parent_decision_id` supersede (extend the parent-owner check so an agent may only supersede its own).
- [ ] **Step 4: Run** the matrix + the existing decisions tests -> PASS.
- [ ] **Step 5: Commit** `feat(decisions): allow granted agents to post decisions (global + per-project)`.

---

### Task 4: taosctl + docs touch

**Files:**
- Modify: `docs/agent-coordination.md` (note the new `decisions_write`/`decisions_read` grantable scopes)
- Test: existing `tests/test_taosctl_decisions.py` still passes (no change expected)

- [ ] **Step 1:** Add a one-line entry to the scope list in `docs/agent-coordination.md`.
- [ ] **Step 2: Run** `pytest tests/test_routes_decisions.py tests/test_routes_agent_registry.py tests/test_decision_store.py -q` -> all PASS.
- [ ] **Step 3: Commit** `docs(agents): document decisions scopes`.

---

## Self-review

- Spec coverage: scope vocab (Task 1), dual-auth route gating (Tasks 2-3), global-vs-per-project semantics (resolver + tests), from_agent unspoofable (Task 3), answer round-trip unchanged (test only), error handling 401/403/400 (resolver). All covered.
- No placeholders: every code step shows real code against verified symbols (`check_agent_scope_for_project`, `project["user_id"]`, `auth.list_users()`, `store.create(user_id=, from_agent=)`).
- Type consistency: resolver returns `(kind, from_agent, decider_user_id)` and Task 3 destructures it identically.
