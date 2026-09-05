# Project tasks (kanban board)

<!-- GET /api/projects/{pid}/tasks, .../tasks/ready, .../tasks/{id}, .../tasks/{id}/comments, lifecycle + comments -->
<!-- POST .../tasks/{id}/(claim|release|close|reopen), .../tasks/{id}/context -->

## Project tasks

Access the kanban board for a project. Granting `project_tasks` also makes the agent a project member.

### API endpoints

- `GET /api/projects/{pid}/tasks` — list tasks in a project
- `GET /api/projects/{pid}/tasks/ready` — list ready tasks
- `GET /api/projects/{pid}/tasks/{id}` — get a specific task
- `GET /api/projects/{pid}/tasks/{id}/comments` — list task comments
- `POST /api/projects/{pid}/tasks/{id}/claim` — claim a task (LEAD-only)
- `POST /api/projects/{pid}/tasks/{id}/release` — release a claimed task
- `POST /api/projects/{pid}/tasks/{id}/close` — close a task
- `POST /api/projects/{pid}/tasks/{id}/reopen` — reopen a closed task
- `GET /api/projects/tasks/{id}/context` — get task context

### PATCH body semantics

`PATCH /api/projects/{pid}/tasks/{id}` writes exactly the fields the body sends
and answers with the stored task:

- an omitted field is left unchanged;
- `assignee_id`, `parent_task_id` and `element_id` accept `null` as a real edit
  (unassign / orphan / untag); `element_id` also accepts the legacy `"none"`
  string for the same clear;
- `null` on any other field is a `422` — it cannot be written, so it is not
  silently ignored;
- a key the route cannot write (a misspelling, or a read-only column such as
  `id`, `created_by`, `claimed_by`) is a `422`, never a `200` that echoes back
  an unchanged task.

### Grant requirements

Granting `project_tasks` also makes the agent a project member.

### LEAD-only extensions

- `POST .../tasks/{id}/claimable` — add/remove the `claimable` label (LEAD-only)
- `POST .../tasks/{id}/unquarantine` — return a quarantined card to the open pool (LEAD-only)