# Apps

<!-- One-line description of every taOS app. -->

## The apps (one line each)

- **Messages**: the main chat. Talk to one agent (DM), several (group), or topic channels.
- **Agents**: deploy or import agents (e.g. Hermes), configure, start, stop. Pick framework, model, and base images.
- **Projects**: kanban boards and docs; agents can join a project's channel.
- **Files**: browse agent workspaces, user workspace, shared folders. Upload and download.
- **Store**: one-click install of community apps. Each app gets its own container and a safe port.
- **Models**: see and pull local models; pin cloud models.
- **Providers**: add cloud API keys (OpenAI, Anthropic, and compatible).
- **Cluster**: pair other machines into the compute mesh with a six-digit code.
- **Memory**: browse and manage what agents remember.
- **Settings**: theme, providers, backends, updates, backups, container runtime.
- **Activity**: live feed of everything agents do (tool calls, model calls, errors).
- **Decisions**: your inbox for agent approvals and questions.
- **Observatory**: watch the agent fleet; pause or throttle work lanes.
- Other bundled apps (Library, Channels, Secrets, Tasks, Images, MCP, Guides and more); if you do not know one, guess from its name and point to Guides.

## Project Files

Agents that are project members can read and write the project's Files through the HTTP API. All routes are keyed on the project SLUG (not the internal project id) and the agent authenticates with its registry JWT via `Authorization: Bearer <token>`. Access is granted per project, so a token issued for a different project returns 404.

- `GET /api/projects/{slug}/files?path=<subdir>` - list files (requires the `files_read` scope)
- `GET /api/projects/{slug}/files/{path}` - download one file (`files_read`)
- `POST /api/projects/{slug}/files/upload?path=<subdir>` - upload, multipart form field `file` (requires `files_write`)
- `POST /api/projects/{slug}/mkdir` with body `{"path": "<subdir>"}` (`files_write`)
