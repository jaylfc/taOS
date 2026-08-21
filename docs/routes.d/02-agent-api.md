# Agent API surface (scoped registry JWT)

<!-- The middleware allowlist is a closed set, no skeleton key. CONSENT KEY: GET /v1/models, POST /v1/chat/completions -->

## Scoped allowlist

Agents authenticate with their registry JWT (`Authorization: Bearer`) and reach exactly the routes their granted SCOPES allow, nothing else.

### project_tasks (the kanban board)

Granting `project_tasks` also makes the agent a project member.

### project_tasks_create

`POST /api/projects/{pid}/tasks` — author new cards. SEPARATE scope from `project_tasks`; off by default.

### project_tasks_update

`PATCH /api/projects/{pid}/tasks/{tid}` — whitelisted fields (title, body, labels, priority). own-or-lead cards only. SEPARATE from `project_tasks`; plain project_tasks token gets 403.

### canvas_read & canvas_write

Canvas routes require `canvas_read` or `canvas_write` scope. `GET .../canvas/elements`, `POST|PATCH|DELETE .../canvas/elements/{id}`.

### files_read & files_write

Files routes key on the project SLUG. `GET .../files/{path}`, `POST .../files/upload`, `DELETE .../files/{path}`.

### decisions_write

`POST /api/decisions` — raise a human-in-the-loop decision. `POST /api/decisions/{id}/answer/agent` — mirror an answer.

### a2a bus surface

`GET /api/a2a/bus/channels`, `GET /api/a2a/bus/messages`, `GET|POST /api/a2a/bus/stream`. a2a_receive token cannot post; a2a_send token is not thereby a reader.

### CONSENT KEY surface

`GET /v1/models` and `POST /v1/chat/completions` reachable without a session using a CONSENT KEY. No key, no resolution, OpenAI-shaped 401 otherwise. Only those two exact method+path pairs pass the middleware.