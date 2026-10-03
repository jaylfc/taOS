# Project tasks (kanban board)

<!-- GET /api/projects/{pid}/tasks, .../tasks/ready, .../tasks/{id}, .../tasks/{id}/comments, lifecycle + comments -->
<!-- POST .../tasks/{id}/(claim|release|close|reopen), .../tasks/{id}/context -->

## Project tasks

Access the kanban board for a project. Granting `project_tasks` also makes the agent a project member.

### API endpoints

- `GET /api/projects/{pid}/tasks` — list tasks
- `GET /api/projects/{pid}/tasks/ready` — list ready
- `GET /api/projects/{pid}/tasks/{id}` — get task
- `GET /api/projects/{pid}/tasks/{id}/comments` — list comments
- `POST /api/projects/{pid}/tasks/{id}/claim` — claim (LEAD-only)
- `POST /api/projects/{pid}/tasks/{id}/release` — release claimed
- `POST /api/projects/{pid}/tasks/{id}/close` — close
- `POST /api/projects/{pid}/tasks/{id}/reopen` — reopen
- `GET /api/projects/tasks/{id}/context` — get context

### PATCH body semantics

`PATCH /api/projects/{pid}/tasks/{id}` writes exactly fields sent, returns stored task. Omitted = unchanged. `assignee_id`, `parent_task_id`, `element_id` accept `null` as real clear (`element_id` also legacy `"none"`). `null` elsewhere, unknown key, or read-only (`id`, `created_by`, `claimed_by`) → `422`, never `200` echo.

### LEAD-only extensions

- `POST .../tasks/{id}/claimable` — add/remove `claimable` label (LEAD-only)
- `POST .../tasks/{id}/unquarantine` — return quarantined card to open pool (LEAD-only)