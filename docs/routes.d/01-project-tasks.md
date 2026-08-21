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

### Grant requirements

Granting `project_tasks` also makes the agent a project member.

### LEAD-only extensions

- `POST .../tasks/{id}/claimable` — add/remove the `claimable` label (LEAD-only)
- `POST .../tasks/{id}/unquarantine` — return a quarantined card to the open pool (LEAD-only)