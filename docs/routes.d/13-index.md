# Routes Source Index

<!-- Lists source files in compile order. Edit these files, not docs/routes.md. -->

## Compile order

Run `python3 scripts/build-routes-doc.py` to compile these into `docs/routes.md`.

| File | Contents |
|---|---|
| `01-project-tasks.md` | Project tasks (kanban board) and `project_tasks` scope |
| `02-agent-api.md` | Agent API surface (scoped registry JWT) |
| `03-device-bearer.md` | Device bearer self-service (narrower passthrough) |
| `04-project-invite.md` | Project invite redeem route (link + PIN) |
| `05-os-events.md` | OS change-event stream (SSE) |
| `06-lora-studio.md` | LoRA Studio routes (session-only) |
| `07-decisions-return.md` | What `GET /api/decisions/agent` returns (grant scoping) |
| `08-config-save-restore.md` | Config save and restore (`/api/config`) |
| `09-agent-memory.md` | Agent memory mode (deploy + PATCH memory) |
| `10-cluster-admin.md` | Cluster node revoke, block and unblock (admin-only) |
| `11-select-decision.md` | Answering a select decision with free text (`other_value`) |
| `12-share-routes.md` | User resource sharing (share routes) |